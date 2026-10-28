"""Agent 循环：思考 → 调工具 → 观察结果 → 再思考，直到给出最终回答。

这就是 Agent 和普通聊天的本质区别：
- 普通聊天：问一句，答一句；
- Agent：模型可以多次「决定调用工具」，程序执行后把结果喂回去，直到任务完成。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .config import AGENT_MAX_STEPS
from .llm import chat_stream_with_tools, chat_with_tools
from .mcp_tools import get_manager
from .tools import TOOL_SCHEMAS, run_tool

SYSTEM_PROMPT = """你是一个会使用工具的 AI 助手，帮助用户完成任务。

可用工具：
- calculator：计算数学表达式
- now：获取当前时间
- list_files：列出工作目录文件
- read_file：读取工作目录里的文本文件
- write_file：把文本写入工作目录（生成笔记、报告）
- http_get：抓取网页文本
- sql_query：对 MySQL 示例库 rag_kb 做只读查询（统计、筛选、验证数据）

规则：
1. 能用工具就别猜：算数用 calculator，时间用 now，读文件用 read_file，查数据用 sql_query。
2. 一次可以返回多个工具调用：互不依赖的调用（比如同时查时间和算数）请一次给出，程序会并行执行；有依赖就一步一步来。
3. 工具返回 {"ok": false, ...} 表示失败：读懂错误，再决定重试还是换方法。
4. 最终回答用中文，简洁，并说明你用了哪些工具、结果是什么。

另外，工具名以 mcp__ 开头的是外部 MCP 服务器提供的工具（名字形如 mcp__服务器__工具），
用法和本地工具一样；如果它返回「未就绪 / 未配置」，说明该服务器不可用，换其他方式或如实告诉用户。"""

MAX_HISTORY = 10


def _all_schemas() -> list[dict]:
    """本地工具 + MCP 工具；MCP 服务器起不来时只返回本地工具。"""
    return list(TOOL_SCHEMAS) + get_manager().schemas()


def _execute_call(call: dict) -> tuple[str, dict, dict]:
    """解析参数并执行一个工具调用；参数解析失败也返回结构化错误。"""
    name = str(call["function"]["name"])
    raw_args = call["function"]["arguments"] or "{}"
    try:
        args = json.loads(raw_args)
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象")
    except Exception as exc:  # noqa: BLE001
        return name, {}, {"ok": False, "error": f"参数解析失败：{exc}"}
    return name, args, run_tool(name, args)


def _clean_history(history: list[dict] | None) -> list[dict]:
    """只保留合法的 user/assistant 文本消息，防止脏数据污染上下文。"""
    if not history:
        return []
    cleaned: list[dict] = []
    for item in history[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            cleaned.append({"role": role, "content": content})
    return cleaned


def run_agent(
    user_input: str,
    history: list[dict] | None = None,
    max_steps: int | None = None,
) -> dict:
    """跑一次 Agent，返回 {answer, steps, tokens}。

    steps 里记录每一步：调了什么工具、参数、结果，方便前端展示和排查。
    """
    user_input = (user_input or "").strip()
    if not user_input:
        raise ValueError("消息不能为空")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_clean_history(history))
    messages.append({"role": "user", "content": user_input})

    steps: list[dict] = []
    tokens: int | None = None
    limit = max(1, max_steps or AGENT_MAX_STEPS)

    for _ in range(limit):
        result = chat_with_tools(messages, _all_schemas())
        tokens = tokens or result.get("tokens")
        message = result["message"]
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            answer = (message.get("content") or "").strip() or "（模型没有返回内容）"
            return {"answer": answer, "steps": steps, "tokens": tokens}

        # 把「模型决定调用工具」这条消息放回对话
        messages.append(message)

        # 并行执行：模型一次返回多个 tool_calls 时，互不依赖的调用同时跑（最多 4 个并发）
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as pool:
            executed = list(pool.map(_execute_call, tool_calls))

        for call, (name, args, observation) in zip(tool_calls, executed):
            steps.append(
                {
                    "step": len(steps) + 1,
                    "tool": name,
                    "args": args,
                    "result": observation,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(observation, ensure_ascii=False, default=str),
                }
            )

    return {
        "answer": "步数用完了还没完成任务。可以把任务拆小一点，或把 AGENT_MAX_STEPS 调大。",
        "steps": steps,
        "tokens": tokens,
    }


def run_agent_stream(
    user_input: str,
    history: list[dict] | None = None,
    max_steps: int | None = None,
):
    """流式版 Agent：一边执行一边把过程推给前端。

    yield 事件（接口层原样转 SSE）：
        {"type": "tool_call", "step": n, "tool": ..., "args": {...}}   模型决定调用工具
        {"type": "tool_result", "step": n, "ok": bool, "result": {...}} 工具执行完毕
        {"type": "delta", "text": "..."}                              最终回答增量
        {"type": "done", "answer", "steps", "tokens"}                  全部结束
    """
    user_input = (user_input or "").strip()
    if not user_input:
        raise ValueError("消息不能为空")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_clean_history(history))
    messages.append({"role": "user", "content": user_input})

    steps: list[dict] = []
    tokens: int | None = None
    limit = max(1, max_steps or AGENT_MAX_STEPS)

    for _ in range(limit):
        answer_parts: list[str] = []
        tool_calls: list[dict] = []
        content = ""

        for event in chat_stream_with_tools(messages, _all_schemas()):
            if "delta" in event:
                answer_parts.append(event["delta"])
                yield {"type": "delta", "text": event["delta"]}
            elif event.get("done"):
                content = event.get("content") or ""
                tool_calls = event.get("tool_calls") or []
                tokens = tokens or event.get("tokens")

        if not tool_calls:
            answer = content.strip() or "（模型没有返回内容）"
            yield {"type": "done", "answer": answer, "steps": steps, "tokens": tokens}
            return

        messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})

        # 先告诉前端「模型决定调这些工具」，再并行执行
        parsed: list[tuple[str, dict]] = []
        for call in tool_calls:
            name = str(call["function"]["name"])
            raw = call["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError("参数必须是 JSON 对象")
            except Exception as exc:  # noqa: BLE001
                args = {"_raw": raw, "_error": f"参数解析失败：{exc}"}
            parsed.append((name, args))

        base = len(steps)
        for offset, (name, args) in enumerate(parsed):
            yield {"type": "tool_call", "step": base + offset + 1, "tool": name, "args": args}

        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as pool:
            executed = list(pool.map(_execute_call, tool_calls))

        for offset, ((name, args), call, (_, _, observation)) in enumerate(zip(parsed, tool_calls, executed)):
            step_no = base + offset + 1
            steps.append({"step": step_no, "tool": name, "args": args, "result": observation})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(observation, ensure_ascii=False, default=str),
                }
            )
            yield {
                "type": "tool_result",
                "step": step_no,
                "ok": bool(isinstance(observation, dict) and observation.get("ok")),
                "result": observation,
            }

    yield {
        "type": "done",
        "answer": "步数用完了还没完成任务。可以把任务拆小一点，或把 AGENT_MAX_STEPS 调大。",
        "steps": steps,
        "tokens": tokens,
    }
