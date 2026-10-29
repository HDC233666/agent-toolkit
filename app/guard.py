"""演示访问保护（可选）：访问口令 + 每日提问预算。

环境变量（不设置 = 完全关闭，行为与之前完全一致）：
- DEMO_PASSWORD     访问口令；设置后所有 /api（health/unlock 除外）都要先解锁
- DEMO_DAILY_BUDGET 每天允许的「会调用大模型」的请求数；0 或不设置 = 不限
- DEMO_COOKIE_NAME  口令 cookie 名（默认 demo_pass）

解锁方式（任选其一）：
1. 浏览器打开 ?k=口令         （自动种 cookie，之后正常用）
2. POST /api/unlock {"password": "口令"}
3. 前端遇到 401 时会弹窗提示输入口令（static/app.js 里实现）
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from starlette.requests import Request

PASSWORD = os.getenv("DEMO_PASSWORD", "").strip()
DAILY_BUDGET = int(os.getenv("DEMO_DAILY_BUDGET", "0") or 0)
COOKIE_NAME = os.getenv("DEMO_COOKIE_NAME", "demo_pass").strip() or "demo_pass"

_STATE = Path(__file__).resolve().parent.parent / "data" / "guard.json"
_LOCK = threading.Lock()


def _digest(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()[:32]


def auth_enabled() -> bool:
    return bool(PASSWORD)


def is_unlocked(request: Request) -> bool:
    """口令未开启时永远放行；开启后校验 cookie 或 ?k=。"""
    if not PASSWORD:
        return True
    if request.cookies.get(COOKIE_NAME) == _digest(PASSWORD):
        return True
    key = request.query_params.get("k")
    return bool(key) and key == PASSWORD


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        data = json.loads(_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def budget_left() -> int:
    """今天还剩多少次提问额度（-1 表示不限）。"""
    if DAILY_BUDGET <= 0:
        return -1
    data = _load()
    if data.get("date") != _today():
        return DAILY_BUDGET
    return max(0, DAILY_BUDGET - int(data.get("used", 0)))


def check_budget() -> bool:
    """调用一次大模型前检查并计数；额度用完返回 False。"""
    if DAILY_BUDGET <= 0:
        return True
    with _LOCK:
        data = _load()
        if data.get("date") != _today():
            data = {"date": _today(), "used": 0}
        used = int(data.get("used", 0))
        if used >= DAILY_BUDGET:
            _save(data)
            return False
        data["used"] = used + 1
        _save(data)
        return True
