"""指标与限流测试：Prometheus 计数、按 IP 限流、请求 ID 响应头。"""

from fastapi.testclient import TestClient

import app.main as main
from app import metrics

client = TestClient(main.app)


def _reset(monkeypatch):
    metrics.reset()
    main._rate_buckets.clear()
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MIN", 0)


def test_metrics_counts_chat_and_tool_calls(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        main,
        "run_agent",
        lambda message, history=None: {"answer": "ok", "steps": [{}, {}], "tokens": 5},
    )
    response = client.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 200

    body = client.get("/api/metrics").text
    assert "agent_chat_total 1" in body
    assert "agent_chat_ok 1" in body
    assert "agent_tool_calls_total 2" in body
    assert "agent_tools" in body


def test_rate_limit_blocks_after_quota(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MIN", 2)
    main._rate_buckets.clear()

    assert client.get("/api/health").status_code == 200  # health 不限流
    assert client.get("/api/health").status_code == 200

    first = client.get("/api/tools")
    second = client.get("/api/tools")
    third = client.get("/api/tools")
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "请求太频繁" in third.json()["detail"]


def test_request_id_header(monkeypatch):
    _reset(monkeypatch)
    response = client.get("/api/health")
    assert response.headers.get("X-Request-Id")
