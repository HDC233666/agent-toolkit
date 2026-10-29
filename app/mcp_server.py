"""一个最小的 MCP 风格工具服务器（stdio + JSON-RPC 2.0）。

它单独运行，由 Agent 通过 app/mcp_client.py 启动并用 JSON-RPC 通信。
之所以自己写而不是装官方 SDK：协议本身就几行 JSON，自己实现才能看清
「工具发现（tools/list）→ 工具调用（tools/call）」这条链。

协议约定（简化版 MCP）：
    initialize            → 返回协议版本、serverInfo、capabilities
    tools/list            → 返回工具列表（name / description / inputSchema）
    tools/call            → 执行工具，返回 {content:[{type:"text", text: json}], isError}
    ping                  → {}

内置工具（演示「外部能力接入」）：
    kb_search(query, top_k)  在 MySQL 的 rag_kb 库里做关键字检索
    kb_stats()               返回 rag_kb 的文档数 / 切片数
数据库连接复用 Agent 项目的 SQL_DB_* 环境变量。
"""

from __future__ import annotations

import json
import os
import sys

try:  # 允许在没有 pymysql 的环境里只跑 tools/list
    import pymysql
except ImportError:  # pragma: no cover
    pymysql = None  # type: ignore[assignment]

SERVER_NAME = "kb-mcp-server"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

DB_CONFIG = {
    "host": os.getenv("SQL_DB_HOST", ""),
    "port": int(os.getenv("SQL_DB_PORT", "3306")),
    "user": os.getenv("SQL_DB_USER", "root"),
    "password": os.getenv("SQL_DB_PASSWORD", ""),
    "database": os.getenv("SQL_DB_NAME", "rag_kb"),
}

TOOLS = [
    {
        "name": "kb_search",
        "description": "在 RAG 知识库（MySQL rag_kb）里按关键字检索文档片段，返回最相关的前几条。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，例如「向量检索」"},
                "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_stats",
        "description": "查看 RAG 知识库（MySQL rag_kb）的文档数与切片数统计。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class _ToolError(Exception):
    """工具内部的可预期错误：以 isError 形式返回，不打印 traceback。"""


def _connect():
    if pymysql is None:
        raise _ToolError("当前环境没有安装 pymysql，无法访问 MySQL")
    if not DB_CONFIG["host"]:
        raise _ToolError("未配置 SQL_DB_HOST，MCP 工具拿不到数据库连接")
    return pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )


def kb_search(query: str, top_k: int = 3) -> dict:
    query = (query or "").strip()
    if not query:
        raise _ToolError("query 不能为空")
    top_k = max(1, min(int(top_k or 3), 10))
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.name AS doc_name, c.idx AS chunk_index, c.text
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                WHERE c.text LIKE %s
                ORDER BY c.idx
                LIMIT %s
                """,
                (f"%{query}%", top_k),
            )
            rows = list(cursor.fetchall())
    finally:
        connection.close()
    if not rows:
        return {"ok": True, "query": query, "hits": [], "note": "没有匹配的片段"}
    hits = [
        {
            "doc_name": row["doc_name"],
            "chunk_index": row["chunk_index"],
            "snippet": str(row["text"])[:160],
        }
        for row in rows
    ]
    return {"ok": True, "query": query, "hits": hits}


def kb_stats() -> dict:
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM documents")
            documents = int(cursor.fetchone()["n"])
            cursor.execute("SELECT COUNT(*) AS n FROM chunks")
            chunks = int(cursor.fetchone()["n"])
    finally:
        connection.close()
    return {"ok": True, "documents": documents, "chunks": chunks}


_HANDLERS = {"kb_search": kb_search, "kb_stats": kb_stats}


def _result(request_id: int, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id: int | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict) -> dict | None:
    """处理一条 JSON-RPC 消息；通知（没有 id）返回 None。"""
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = _HANDLERS.get(str(name))
        if handler is None:
            return _error(request_id, -32602, f"未知工具：{name}")
        try:
            payload = handler(**arguments)
            text = json.dumps(payload, ensure_ascii=False)
            return _result(
                request_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )
        except TypeError as exc:
            return _error(request_id, -32602, f"参数不匹配：{exc}")
        except _ToolError as exc:
            text = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            return _result(
                request_id,
                {"content": [{"type": "text", "text": text}], "isError": True},
            )
        except Exception as exc:  # noqa: BLE001 - 连接失败等也以 isError 返回
            text = json.dumps({"ok": False, "error": f"工具执行失败：{exc}"}, ensure_ascii=False)
            return _result(
                request_id,
                {"content": [{"type": "text", "text": text}], "isError": True},
            )
    return _error(request_id, -32601, f"不支持的方法：{method}")


def main() -> None:
    """从 stdin 逐行读 JSON-RPC 请求，把响应写到 stdout（一行一个 JSON）。"""
    # Windows 下 stdio 默认是 GBK，必须显式改成 UTF-8，否则中文描述会乱码
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_error(None, -32700, "JSON 解析失败")) + "\n")
            stdout.flush()
            continue
        response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
