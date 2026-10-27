"""Agent 循环测试：用 monkeypatch 假装模型，验证「调工具 → 回灌 → 回答」逻辑。"""

from __future__ import annotations

import json

import pytest

from app import agent


def scripted(*responses: dict, tokens: int = 10):
    """按顺序返回预置响应，模拟模型的多轮决策。"""
    state = {"n": 0}

    def fake(messages, tools=None):  # noqa: ANN001
        index = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return {"message": responses[index], "tokens": tokens}

    return fake


def tool_call_message(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def test_agent_calls_tool_then_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "chat_with_tools",
        scripted(
            tool_call_message("calculator", {"expression": "6*7"}),
            {"role": "assistant", "content": "结果是 42"},
        ),
    )

    result = agent.run_agent("算一下 6*7")

    assert result["answer"] == "结果是 42"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["tool"] == "calculator"
    assert result["steps"][0]["result"]["result"] == 42
    assert result["tokens"] == 10


def test_agent_direct_answer_without_tools(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "chat_with_tools",
        scripted({"role": "assistant", "content": "你好呀"}),
    )

    result = agent.run_agent("hi")

    assert result["answer"] == "你好呀"
    assert result["steps"] == []


def test_agent_stops_after_max_steps(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "chat_with_tools",
        scripted(tool_call_message("now", {})),
    )

    result = agent.run_agent("一直调工具", max_steps=2)

    assert "步数用完" in result["answer"]
    assert len(result["steps"]) == 2


def test_agent_handles_bad_tool_arguments(monkeypatch) -> None:
    bad_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calculator", "arguments": "{not json"},
            }
        ],
    }
    monkeypatch.setattr(
        agent,
        "chat_with_tools",
        scripted(bad_call, {"role": "assistant", "content": "参数错了，我重试"}),
    )

    result = agent.run_agent("算一下")

    assert result["steps"][0]["result"]["ok"] is False


def test_agent_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        agent.run_agent("   ")
