"""FastAPI 入口：对话接口 / 工具列表 / 前端页面托管。

启动方式（项目根目录下）：
    uvicorn app.main:app --reload
然后浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import re
import time
from collections import deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm, metrics
from .agent import _all_schemas, run_agent, run_agent_stream
from .approvals import registry
from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, RATE_LIMIT_PER_MIN, STATIC_DIR
from .mcp_tools import get_manager
from .recorder import make_recorder

app = FastAPI(title="Agent 工具调用", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

recorder = make_recorder()

# 限流：每个 IP 每分钟最多 RATE_LIMIT_PER_MIN 个 /api 请求（health 除外），进程内存储
_rate_buckets: dict[str, deque] = {}


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    path = request.url.path
    if RATE_LIMIT_PER_MIN > 0 and path.startswith("/api/") and not path.startswith("/api/health"):
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            return PlainTextResponse(
                json.dumps({"detail": "请求太频繁了，稍后再试"}, ensure_ascii=False),
                status_code=429,
                media_type="application/json",
            )
        bucket.append(now)
    response = await call_next(request)
    response.headers["X-Request-Id"] = f"{int(time.time() * 1000) % 10**9:09d}"
    return response


class ApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=4, max_length=64)
    approve: bool


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[dict] | None = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "model": DEEPSEEK_MODEL, "has_key": bool(DEEPSEEK_API_KEY)}


@app.get("/api/tools")
def tools() -> list[dict]:
    return [
        {
            "name": schema["function"]["name"],
            "description": schema["function"]["description"],
        }
        for schema in _all_schemas()
    ]


@app.get("/api/mcp/status")
def mcp_status() -> dict:
    """MCP 服务器状态：哪些起来了、提供了几个工具、失败的为什么失败。"""
    return get_manager().status()


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    started = time.perf_counter()
    metrics.incr("agent_chat_total")
    try:
        result = run_agent(payload.message, payload.history)
    except ValueError as exc:
        metrics.incr("agent_chat_error")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm.LLMError as exc:
        metrics.incr("agent_chat_error")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    metrics.incr("agent_chat_ok")
    metrics.incr("agent_tool_calls_total", len(result.get("steps", [])))
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:  # 记录失败不影响对话本身
        recorder.record(payload.message, result["answer"], result.get("steps", []), result.get("tokens"), latency_ms)
    except Exception as exc:  # noqa: BLE001
        print(f"[recorder] 记录失败：{exc}")
    return result


@app.post("/api/chat/stream")
def chat_stream(payload: ChatRequest):
    """流式对话：SSE 推送工具调用过程与回答增量。"""
    started = time.perf_counter()
    metrics.incr("agent_stream_total")

    def event_stream():
        answer = ""
        steps: list[dict] = []
        tokens = None

        def send(event: dict) -> str:
            return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

        try:
            for event in run_agent_stream(payload.message, payload.history):
                if event.get("type") == "done":
                    answer = event.get("answer") or ""
                    steps = event.get("steps") or []
                    tokens = event.get("tokens")
                yield send(event)
        except ValueError as exc:
            metrics.incr("agent_stream_error")
            yield send({"type": "error", "detail": str(exc)})
        except llm.LLMError as exc:
            metrics.incr("agent_stream_error")
            yield send({"type": "error", "detail": str(exc)})
        else:
            metrics.incr("agent_stream_ok")
            metrics.incr("agent_tool_calls_total", len(steps))
            latency_ms = int((time.perf_counter() - started) * 1000)
            try:  # 记录失败不影响对话本身
                recorder.record(payload.message, answer, steps, tokens, latency_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"[recorder] 记录失败：{exc}")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/approve")
def approve(payload: ApprovalRequest) -> dict:
    """人工审批：允许/拒绝一个挂起的工具调用（前端点按钮时调用）。"""
    if not registry.decide(payload.approval_id, payload.approve):
        raise HTTPException(status_code=404, detail="审批不存在或已过期")
    metrics.incr("agent_approvals_total")
    return {"ok": True, "approved": payload.approve}


@app.get("/api/logs")
def logs(limit: int = 20) -> dict:
    """最近的调用记录（审计 / 回放用）。"""
    limit = max(1, min(limit, 50))
    try:
        runs = recorder.list_runs(limit)
        totals = recorder.totals()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"读取调用记录失败：{exc}") from exc
    return {"storage": recorder.backend, "totals": totals, "runs": runs}


@app.get("/api/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """Prometheus 文本格式指标（调用量 / 工具调用数 / 错误数）。"""
    metrics.set_gauge("agent_tools", len(_all_schemas()))
    metrics.set_gauge("agent_pending_approvals", registry.pending_count())
    return metrics.render_prometheus()
