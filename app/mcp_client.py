"""MCP 客户端：启动 stdio 子进程，用 JSON-RPC 2.0 完成握手 / 工具发现 / 工具调用。

设计要点（面试可讲）：
- 传输层只负责「一行一个 JSON」的读写，协议层只做请求-响应配对；
- 每个请求带 id，响应按 id 唤醒等待者；服务端可以发通知（无 id），直接忽略；
- 子进程的 stderr 单独收集，避免污染协议流；
- 所有失败都转成可读异常，Agent 层拿到的是结构化错误而不是崩溃。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


class MCPError(RuntimeError):
    """MCP 通信错误（启动失败、超时、stderr 退出等）。"""


class StdioMCPClient:
    """一个 stdio 传输的 MCP 客户端，对应一个服务器子进程。"""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, cwd: str | Path | None = None,
                 timeout: float = 20.0):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout

        self._process: subprocess.Popen | None = None
        self._responses: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._stderr_lines: list[str] = []
        self._next_id = 0
        self._closed = False

    # ---------- 生命周期 ----------

    def _resolve_command(self) -> list[str]:
        command = self.command
        if command in ("python", "python3"):
            command = sys.executable
        return [command, *self.args]

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                self._resolve_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd or str(Path(__file__).resolve().parent.parent),
                env={**__import__("os").environ, **self.env},
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"无法启动 MCP 服务器 {self.name}：{exc}") from exc

        self._reader = threading.Thread(target=self._read_loop, name=f"mcp-{self.name}", daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, name=f"mcp-{self.name}-err", daemon=True).start()

        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "agent-toolkit", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        self._closed = True
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 读写 ----------

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.rstrip()
            if text:
                self._stderr_lines.append(text)
                del self._stderr_lines[:-50]

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id is None:
                continue  # 服务端通知，本客户端不需要
            with self._lock:
                self._responses[int(request_id)] = message

    def _write(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None or self._closed:
            raise MCPError(f"MCP 服务器 {self.name} 未启动")
        process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        deadline = time.time() + (timeout or self.timeout)
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

        while time.time() < deadline:
            with self._lock:
                message = self._responses.pop(request_id, None)
            if message is not None:
                if "error" in message:
                    raise MCPError(f"{self.name} 返回错误：{message['error'].get('message')}")
                return message.get("result") or {}
            if self._process is not None and self._process.poll() is not None:
                stderr = "\n".join(self._stderr_lines[-5:])
                raise MCPError(f"MCP 服务器 {self.name} 已退出：{stderr}")
            time.sleep(0.02)
        raise MCPError(f"MCP 服务器 {self.name} 响应超时（{method}）")

    # ---------- 协议方法 ----------

    def list_tools(self) -> list[dict]:
        result = self.request("tools/list", {})
        return list(result.get("tools") or [])

    def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具，把 MCP 的 content 数组解析成普通 dict。"""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        is_error = bool(result.get("isError"))
        text = ""
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text += str(item.get("text") or "")
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {"ok": not is_error, "text": text}
        if is_error:
            return {"ok": False, "error": payload.get("error", text or "工具执行失败")}
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            return payload
        return {"ok": True, "result": payload}
