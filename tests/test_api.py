"""API 接口测试：使用 FastAPI TestClient，不启动真实 uvicorn、不调真实模型。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import agent
from app.llm import LLMError
from app.main import app as fastapi_app


class _StubRecorder:
    """测试用：不写数据库，只记录调用。"""

    backend = "stub"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record(self, question, answer, steps, tokens, latency_ms):  # noqa: ANN001
        self.calls.append({"question": question, "answer": answer, "steps": steps, "tokens": tokens, "latency_ms": latency_ms})
        return 1

    def list_runs(self, limit: int = 20) -> list[dict]:  # noqa: ANN001
        return []

    def totals(self) -> dict:
        return {"runs": 0, "tool_calls": 0}


@pytest.fixture(autouse=True)
def stub_recorder(monkeypatch):
    stub = _StubRecorder()
    monkeypatch.setattr(main_module, "recorder", stub)
    yield stub


def scripted(*responses: dict):
    state = {"n": 0}

    def fake(messages, tools=None):  # noqa: ANN001
        index = min(state["n"], len(responses) - 1)
        state["n"] += 1
        return {"message": responses[index], "tokens": 12}

    return fake


def test_health() -> None:
    with TestClient(fastapi_app) as client:
        body = client.get("/api/health").json()

    assert body["ok"] is True
    assert "model" in body


def test_tools_list() -> None:
    with TestClient(fastapi_app) as client:
        names = [item["name"] for item in client.get("/api/tools").json()]

    assert {"calculator", "now", "list_files", "read_file", "write_file", "http_get"} <= set(names)


def test_chat_returns_answer_and_steps(monkeypatch) -> None:
    tool_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calculator", "arguments": '{"expression": "2**10"}'},
            }
        ],
    }
    monkeypatch.setattr(
        agent,
        "chat_with_tools",
        scripted(tool_call, {"role": "assistant", "content": "1024"}),
    )

    with TestClient(fastapi_app) as client:
        response = client.post("/api/chat", json={"message": "算 2**10"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "1024"
    assert body["steps"][0]["tool"] == "calculator"


def test_chat_rejects_empty_message() -> None:
    with TestClient(fastapi_app) as client:
        assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_logs_endpoint(stub_recorder) -> None:
    with TestClient(fastapi_app) as client:
        response = client.get("/api/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["storage"] == "stub"
    assert body["runs"] == []


def test_chat_records_run(monkeypatch, stub_recorder) -> None:
    monkeypatch.setattr(agent, "chat_with_tools", scripted({"role": "assistant", "content": "ok"}))
    with TestClient(fastapi_app) as client:
        client.post("/api/chat", json={"message": "你好"})

    assert len(stub_recorder.calls) == 1
    assert stub_recorder.calls[0]["question"] == "你好"
    assert stub_recorder.calls[0]["latency_ms"] >= 0


def test_chat_maps_llm_error_to_502(monkeypatch) -> None:
    def boom(messages, tools=None):  # noqa: ANN001
        raise LLMError("模拟网络失败")

    monkeypatch.setattr(agent, "chat_with_tools", boom)

    with TestClient(fastapi_app) as client:
        response = client.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 502
