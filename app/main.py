"""FastAPI 入口：对话接口 / 工具列表 / 前端页面托管。

启动方式（项目根目录下）：
    uvicorn app.main:app --reload
然后浏览器打开 http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import llm
from .agent import run_agent, run_agent_stream
from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, STATIC_DIR
from .recorder import make_recorder
from .tools import TOOL_SCHEMAS

app = FastAPI(title="Agent 工具调用", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

recorder = make_recorder()


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
        for schema in TOOL_SCHEMAS
    ]


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict:
    started = time.perf_counter()
    try:
        result = run_agent(payload.message, payload.history)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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
            yield send({"type": "error", "detail": str(exc)})
        except llm.LLMError as exc:
            yield send({"type": "error", "detail": str(exc)})
        else:
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
