"""人工审批测试：注册表行为 + Agent 流式的「挂起→允许/拒绝」流程。"""

from __future__ import annotations

import json
import threading

import pytest

from app import agent
from app import tools as tools_module
from app.approvals import ApprovalRegistry


@pytest.fixture(autouse=True)
def clean_registry():
    agent.registry.clear()
    yield
    agent.registry.clear()


def tool_call_message(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def scripted_stream(*responses: dict):
    """流式假模型：按顺序返回预置响应（每次 done 事件）。"""
    state = {"n": 0}

    def fake(messages, tools=None):  # noqa: ANN001
        index = min(state["n"], len(responses) - 1)
        state["n"] += 1
        item = responses[index]
        yield {
            "done": True,
            "content": item.get("content") or "",
            "tool_calls": item.get("tool_calls") or [],
            "tokens": 10,
        }

    return fake


def collect(gen):
    return list(gen)


def test_registry_decide_unblocks_wait() -> None:
    reg = ApprovalRegistry()
    approval = reg.create("write_file", {"name": "a.md"})

    threading.Timer(0.05, lambda: reg.decide(approval.id, True)).start()

    assert reg.wait(approval.id, timeout=2) is True
    assert reg.pending_count() == 0


def test_registry_timeout_counts_as_none() -> None:
    reg = ApprovalRegistry()
    approval = reg.create("write_file", {})

    assert reg.wait(approval.id, timeout=0.05) is None
    assert reg.pending_count() == 0


def test_registry_unknown_decision() -> None:
    assert ApprovalRegistry().decide("deadbeef", True) is False


def test_stream_executes_after_approval(monkeypatch, tmp_path) -> None:
    """用户点「允许」后，write_file 真的执行，文件落盘。"""
    monkeypatch.setattr(tools_module, "WORKSPACE", tmp_path)
    monkeypatch.setattr(
        agent,
        "chat_stream_with_tools",
        scripted_stream(
            {"tool_calls": [tool_call_message("write_file", {"name": "ok.md", "content": "hello"})]},
            {"content": "已经写好 ok.md"},
        ),
    )

    gen = agent.run_agent_stream("把 hello 写进 ok.md")
    events = []
    approval_id = None
    for event in gen:
        events.append(event)
        if event["type"] == "approval_required":
            approval_id = event["approval"]["id"]
            assert event["approval"]["tool"] == "write_file"
            assert agent.registry.decide(approval_id, True) is True

    types = [event["type"] for event in events]
    assert approval_id is not None
    assert types.count("approval_required") == 1
    result = next(event for event in events if event["type"] == "tool_result")
    assert result["ok"] is True
    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "已经写好 ok.md"
    assert (tmp_path / "ok.md").read_text(encoding="utf-8") == "hello"


def test_stream_denies_and_does_not_execute(monkeypatch, tmp_path) -> None:
    """用户点「拒绝」时，工具不执行，模型收到失败结果后继续回答。"""
    monkeypatch.setattr(tools_module, "WORKSPACE", tmp_path)
    monkeypatch.setattr(
        agent,
        "chat_stream_with_tools",
        scripted_stream(
            {"tool_calls": [tool_call_message("write_file", {"name": "no.md", "content": "x"})]},
            {"content": "好的，我没有写文件"},
        ),
    )

    gen = agent.run_agent_stream("写一个文件")
    events = []
    for event in gen:
        events.append(event)
        if event["type"] == "approval_required":
            agent.registry.decide(event["approval"]["id"], False)

    result = next(event for event in events if event["type"] == "tool_result")
    assert result["ok"] is False
    assert "未批准" in result["result"]["error"]
    assert not (tmp_path / "no.md").exists()
    assert events[-1]["answer"] == "好的，我没有写文件"


def test_non_stream_agent_rejects_approval_tool(monkeypatch, tmp_path) -> None:
    """普通 /api/chat 接口没有交互通道：需要审批的工具直接拒绝并给出提示。"""
    monkeypatch.setattr(tools_module, "WORKSPACE", tmp_path)

    def fake(messages, tools=None):  # noqa: ANN001
        return {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": json.dumps({"name": "x.md", "content": "y"})},
                    }
                ],
            },
            "tokens": 5,
        }

    monkeypatch.setattr(agent, "chat_with_tools", fake)

    result = agent.run_agent("写文件")
    assert result["steps"][0]["result"]["ok"] is False
    assert "人工审批" in result["steps"][0]["result"]["error"]
    assert not (tmp_path / "x.md").exists()
