"""MCP 相关测试：服务器协议、客户端往返、工具命名与挂载、状态接口。"""

import json
import os
import sys

import pytest

import app.mcp_server as mcp_server
from app.mcp_client import MCPError, StdioMCPClient
from app.mcp_tools import MCPManager, TOOL_PREFIX

PYTHON = sys.executable


# ---------- 服务器协议（进程内直接调用 handle） ----------


def test_server_tools_list():
    result = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = {tool["name"] for tool in result["result"]["tools"]}
    assert {"kb_search", "kb_stats"} <= names
    for tool in result["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_server_initialize_and_unknown_method():
    init = mcp_server.handle({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"]
    unknown = mcp_server.handle({"jsonrpc": "2.0", "id": 8, "method": "nope", "params": {}})
    assert unknown["error"]["code"] == -32601


def test_server_kb_stats_without_db(monkeypatch):
    monkeypatch.setitem(mcp_server.DB_CONFIG, "host", "")
    result = mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "kb_stats", "arguments": {}},
        }
    )
    assert result["result"]["isError"] is True
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["ok"] is False
    assert "SQL_DB_HOST" in payload["error"]


# ---------- 客户端 ↔ 服务器（真实子进程） ----------


def test_client_roundtrip():
    env = {"SQL_DB_HOST": ""}  # 明确「没有数据库」，避免依赖本机 MySQL
    client = StdioMCPClient("kb", PYTHON, ["-m", "app.mcp_server"], env=env, timeout=20)
    try:
        client.start()
        names = {tool["name"] for tool in client.list_tools()}
        assert {"kb_search", "kb_stats"} <= names

        failed = client.call_tool("kb_stats", {})
        assert failed["ok"] is False
        assert "SQL_DB_HOST" in failed["error"]
    finally:
        client.stop()


def test_client_call_unknown_tool_is_error():
    client = StdioMCPClient("kb", PYTHON, ["-m", "app.mcp_server"], env={"SQL_DB_HOST": ""}, timeout=20)
    try:
        client.start()
        with pytest.raises(MCPError):
            client.call_tool("not_exists", {})
    finally:
        client.stop()


# ---------- 工具挂载与状态 ----------


def test_manager_mounts_prefixed_tools(tmp_path):
    manifest = tmp_path / "mcp_servers.json"
    manifest.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "kb",
                        "command": PYTHON,
                        "args": ["-m", "app.mcp_server"],
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manager = MCPManager()
    # 绕过配置模块级常量，直接告诉 manager 用临时清单
    import app.mcp_tools as tools_module

    old_file, old_enabled = tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED
    tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED = str(manifest), True
    try:
        schemas = manager.schemas()
        names = {schema["function"]["name"] for schema in schemas}
        assert f"{TOOL_PREFIX}kb__kb_search" in names
        assert all(schema["function"]["description"].startswith("[MCP/kb]") for schema in schemas)

        status = manager.status()
        assert status["tool_count"] == len(schemas)
        assert any(server["name"] == "kb" for server in status["servers"])
    finally:
        manager.reset()
        tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED = old_file, old_enabled


def test_manager_bad_server_is_skipped(tmp_path):
    manifest = tmp_path / "mcp_servers.json"
    manifest.write_text(
        json.dumps({"servers": [{"name": "broken", "command": "definitely-not-a-command", "args": []}]}),
        encoding="utf-8",
    )
    manager = MCPManager()
    import app.mcp_tools as tools_module

    old_file, old_enabled = tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED
    tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED = str(manifest), True
    try:
        assert manager.schemas() == []
        status = manager.status()
        assert status["tool_count"] == 0
        assert status["errors"] and "broken" in status["errors"][0]
    finally:
        manager.reset()
        tools_module.MCP_SERVERS_FILE, tools_module.MCP_ENABLED = old_file, old_enabled


def test_run_tool_routes_mcp_names(monkeypatch):
    from app import tools

    monkeypatch.setattr(
        "app.mcp_tools.get_manager",
        lambda: type("M", (), {"call": lambda self, name, args: {"ok": True, "echo": name}})(),
    )
    assert tools.run_tool("mcp__kb__kb_stats", {}) == {"ok": True, "echo": "mcp__kb__kb_stats"}
