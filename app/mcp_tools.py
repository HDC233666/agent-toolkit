"""MCP 工具管理器：把 data/mcp_servers.json 里配置的服务器接进 Agent 工具箱。

命名规则：mcp__<server>__<tool>，例如 mcp__kb__kb_search。
前缀是为了让模型和我们一眼看出「这是外部 MCP 工具」。

失败是常态：服务器没装、启动失败、数据库没配——一律跳过并记录原因，
绝不让主流程（本地工具）跟着挂。前端可以通过 /api/mcp/status 看状态。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import BASE_DIR, MCP_ENABLED, MCP_SERVERS_FILE, MCP_TIMEOUT_SECONDS
from .mcp_client import MCPError, StdioMCPClient

TOOL_PREFIX = "mcp__"


class MCPManager:
    """懒加载：第一次要用 MCP 工具时才启动服务器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, StdioMCPClient] = {}
        self._schemas: list[dict] = []
        self._errors: list[str] = []
        self._loaded = False

    # ---------- 配置 ----------

    def _manifest(self) -> list[dict]:
        path = Path(MCP_SERVERS_FILE)
        if not path.is_absolute():
            path = BASE_DIR / path
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._errors.append(f"读取 MCP 配置失败：{exc}")
            return []
        servers = data.get("servers") if isinstance(data, dict) else data
        return [item for item in (servers or []) if isinstance(item, dict) and item.get("enabled", True)]

    # ---------- 加载 ----------

    def ensure_loaded(self) -> None:
        if self._loaded or not MCP_ENABLED:
            self._loaded = True
            return
        with self._lock:
            if self._loaded:
                return
            for item in self._manifest():
                name = str(item.get("name") or "").strip()
                command = str(item.get("command") or "").strip()
                if not name or not command:
                    self._errors.append("MCP 配置缺少 name/command")
                    continue
                client = StdioMCPClient(
                    name=name,
                    command=command,
                    args=[str(arg) for arg in item.get("args") or []],
                    env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                    timeout=float(item.get("timeout") or MCP_TIMEOUT_SECONDS),
                )
                try:
                    client.start()
                    tools = client.list_tools()
                except MCPError as exc:
                    self._errors.append(f"{name}: {exc}")
                    client.stop()
                    continue
                self._clients[name] = client
                for tool in tools:
                    tool_name = str(tool.get("name") or "")
                    if not tool_name:
                        continue
                    description = str(tool.get("description") or "").strip()
                    self._schemas.append(
                        {
                            "type": "function",
                            "function": {
                                "name": f"{TOOL_PREFIX}{name}__{tool_name}",
                                "description": f"[MCP/{name}] {description}",
                                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                            },
                        }
                    )
            self._loaded = True

    # ---------- 对外接口 ----------

    def schemas(self) -> list[dict]:
        self.ensure_loaded()
        return list(self._schemas)

    def call(self, full_name: str, arguments: dict) -> dict:
        self.ensure_loaded()
        if not full_name.startswith(TOOL_PREFIX):
            return {"ok": False, "error": f"不是 MCP 工具：{full_name}"}
        body = full_name[len(TOOL_PREFIX):]
        if "__" not in body:
            return {"ok": False, "error": f"工具名不合法：{full_name}"}
        server_name, tool_name = body.split("__", 1)
        client = self._clients.get(server_name)
        if client is None:
            return {"ok": False, "error": f"MCP 服务器「{server_name}」未就绪"}
        try:
            return client.call_tool(tool_name, arguments)
        except MCPError as exc:
            return {"ok": False, "error": str(exc)}

    def status(self) -> dict:
        self.ensure_loaded()
        return {
            "enabled": MCP_ENABLED,
            "servers": [
                {"name": name, "tools": [schema["function"]["name"] for schema in self._schemas]}
                for name in self._clients
            ],
            "tool_count": len(self._schemas),
            "errors": list(self._errors),
        }

    def reset(self) -> None:
        """测试用：停掉所有子进程并清空状态。"""
        for client in self._clients.values():
            client.stop()
        self._clients.clear()
        self._schemas.clear()
        self._errors.clear()
        self._loaded = False


_manager: MCPManager | None = None


def get_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
