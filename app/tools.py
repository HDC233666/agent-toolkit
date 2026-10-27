"""工具箱：每个工具 = 一个普通函数 + 一份 JSON Schema 描述。

Agent 的「手脚」就是这些工具：
- 模型只负责输出「调哪个工具、参数是什么」；
- 真正执行的是这里的 Python 代码；
- 所以工具必须：参数校验、错误信息可读、有边界（只能碰 data/workspace）。
"""

from __future__ import annotations

import ast
import datetime as dt
import operator
from pathlib import Path

import httpx

from .config import WORKSPACE

_MAX_FILE_CHARS = 200_000
_MAX_HTTP_CHARS = 20_000
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


class ToolError(ValueError):
    """工具执行失败（错误信息会原样返回给模型，让它自己判断怎么改）。"""


# ---------------- 计算器（不执行任意代码） ----------------
def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval_node(node.operand)
    raise ToolError("表达式只支持数字、+ - * / ** % 和括号")


def calculator(expression: str) -> dict:
    """安全四则运算：用 ast 解析表达式，绝不 eval。"""
    try:
        value = _eval_node(ast.parse(str(expression), mode="eval"))
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "表达式解析失败，请检查格式"}
    return {"ok": True, "expression": str(expression), "result": value}


# ---------------- 时间 ----------------
def now() -> dict:
    current = dt.datetime.now().astimezone()
    return {
        "ok": True,
        "iso": current.isoformat(timespec="seconds"),
        "date": current.strftime("%Y-%m-%d"),
        "time": current.strftime("%H:%M:%S"),
        "weekday": "一二三四五六日"[current.weekday()],
    }


# ---------------- 文件（沙箱在 data/workspace） ----------------
def _safe_path(name: str) -> Path:
    root = Path(WORKSPACE).resolve()
    target = (root / str(name)).resolve()
    if root not in target.parents and target != root:
        raise ToolError("路径越界：只能访问 data/workspace 目录内的文件")
    return target


def list_files() -> dict:
    root = Path(WORKSPACE)
    root.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    names = [str(path.relative_to(root)).replace("\\", "/") for path in files]
    return {"ok": True, "files": names[:100]}


def read_file(name: str, max_chars: int = 4000) -> dict:
    try:
        path = _safe_path(name)
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    if not path.is_file():
        return {"ok": False, "error": f"文件不存在：{name}"}
    limit = max(1, min(int(max_chars), 20000))
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "name": name,
        "content": text[:limit],
        "truncated": len(text) > limit,
    }


def write_file(name: str, content: str) -> dict:
    try:
        path = _safe_path(name)
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    text = str(content)
    if len(text) > _MAX_FILE_CHARS:
        return {"ok": False, "error": "内容太大（上限 200K 字符）"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "name": name, "chars": len(text)}


# ---------------- 网页 ----------------
def http_get(url: str, max_chars: int = 3000) -> dict:
    target = str(url)
    if not (target.startswith("http://") or target.startswith("https://")):
        return {"ok": False, "error": "只支持 http/https 链接"}
    try:
        response = httpx.get(target, timeout=8, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"请求失败：{exc}"}
    limit = max(1, min(int(max_chars), _MAX_HTTP_CHARS))
    return {
        "ok": True,
        "status": response.status_code,
        "content": response.text[:limit],
    }


# ---------------- 给模型看的工具说明书 ----------------
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "计算数学表达式，支持 + - * / ** % 和括号",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "例如 (12+3)*4"},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now",
            "description": "获取当前日期、时间和星期几",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出工作目录（data/workspace）里的所有文件",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作目录里的文本文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "相对路径，例如 notes.md"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 20000},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把文本写入工作目录（用于生成笔记、报告、结果文件）",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "相对路径，例如 summary.md"},
                    "content": {"type": "string", "description": "要写入的完整文本"},
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "抓取一个 http(s) 网页的文本内容（会截断）",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整网址"},
                    "max_chars": {"type": "integer", "minimum": 1, "maximum": 20000},
                },
                "required": ["url"],
            },
        },
    },
]

TOOL_FUNCS = {
    "calculator": calculator,
    "now": now,
    "list_files": list_files,
    "read_file": read_file,
    "write_file": write_file,
    "http_get": http_get,
}


def run_tool(name: str, args: dict | None = None) -> dict:
    """按名字执行工具。永远返回 dict：错误也返回，交给模型判断下一步。"""
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"ok": False, "error": f"没有这个工具：{name}"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"ok": False, "error": "工具参数必须是 JSON 对象"}
    try:
        return func(**args)
    except TypeError as exc:
        return {"ok": False, "error": f"参数不匹配：{exc}"}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"工具执行出错：{exc}"}
