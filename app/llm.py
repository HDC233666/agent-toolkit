"""调用 DeepSeek 大模型（支持 function calling），带重试和友好报错。"""

from __future__ import annotations

import time

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI

from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL


class LLMError(RuntimeError):
    """对用户友好的大模型调用错误（API 层会转成 502）。"""


def get_client() -> OpenAI:
    if not DEEPSEEK_API_KEY:
        raise LLMError(
            "没有找到 DEEPSEEK_API_KEY。"
            "请在项目根目录的 .env 里填写，或设置同名的系统环境变量后重启。"
        )
    return OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


def _to_plain_message(message) -> dict:
    """把 SDK 的 message 对象转成普通 dict，并归一化 tool_calls。"""
    data = message.model_dump() if hasattr(message, "model_dump") else dict(message)
    tool_calls = []
    for item in data.get("tool_calls") or []:
        function = item.get("function") or {}
        tool_calls.append(
            {
                "id": item.get("id"),
                "type": "function",
                "function": {
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "{}",
                },
            }
        )
    return {
        "role": "assistant",
        "content": data.get("content"),
        "tool_calls": tool_calls,
    }


def chat_with_tools(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """发一次请求；如果模型想调工具，message.tool_calls 非空。

    返回：{"message": {role, content, tool_calls}, "tokens": int|None}
    """
    client = get_client()
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            kwargs: dict = {"model": DEEPSEEK_MODEL, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            response = client.chat.completions.create(**kwargs)
            usage = getattr(response, "usage", None)
            return {
                "message": _to_plain_message(response.choices[0].message),
                "tokens": getattr(usage, "total_tokens", None) if usage else None,
            }
        except (APIConnectionError, APITimeoutError) as exc:  # 网络抖动：重试
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
        except APIError as exc:  # 鉴权 / 模型名 / 限流：直接报错
            raise LLMError(f"DeepSeek 接口报错：{exc}") from exc

    raise LLMError(f"网络重试 3 次仍然失败：{last_error}")


def chat_stream_with_tools(messages: list[dict], tools: list[dict] | None = None):
    """流式对话（支持 function calling）。

    yield：
        {"delta": "文本增量"}                    模型在说最终答案
        {"tool_call_delta": {...}}               工具调用参数片段（内部拼接用）
        {"done": True, "content", "tool_calls", "tokens"}  本轮结束
    """
    client = get_client()
    kwargs: dict = {"model": DEEPSEEK_MODEL, "messages": messages, "stream": True}
    if tools:
        kwargs["tools"] = tools
    try:
        try:
            stream = client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
        except APIError as exc:
            if "stream_options" not in str(exc):
                raise
            stream = client.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        slot_by_index: dict[int, dict] = {}
        tokens = None
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                tokens = getattr(usage, "total_tokens", None)
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                text = getattr(delta, "content", None)
                if text:
                    content_parts.append(text)
                    yield {"delta": text}
                for item in getattr(delta, "tool_calls", None) or []:
                    slot = slot_by_index.setdefault(item.index, {"id": None, "name": "", "arguments": ""})
                    if item.id:
                        slot["id"] = item.id
                    function = getattr(item, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            slot["name"] += function.name
                        if getattr(function, "arguments", None):
                            slot["arguments"] += function.arguments

        tool_calls = [
            {
                "id": slot["id"] or f"call_{index}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
            }
            for index, slot in sorted(slot_by_index.items())
        ]
        yield {
            "done": True,
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
            "tokens": tokens,
        }
    except (APIConnectionError, APITimeoutError) as exc:
        raise LLMError(f"网络连接失败：{exc}") from exc
    except APIError as exc:
        raise LLMError(f"DeepSeek 接口报错：{exc}") from exc
