"""极简指标收集：进程内计数器，供 /api/metrics 以 Prometheus 文本格式暴露。"""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_counters: Counter[str] = Counter()
_gauges: dict[str, float] = {}


def incr(name: str, value: int = 1) -> None:
    with _lock:
        _counters[name] += value


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = float(value)


def snapshot() -> dict:
    with _lock:
        return {"counters": dict(_counters), "gauges": dict(_gauges)}


def reset() -> None:
    with _lock:
        _counters.clear()
        _gauges.clear()


def render_prometheus() -> str:
    data = snapshot()
    lines: list[str] = []
    for name, value in sorted(data["counters"].items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")
    for name, value in sorted(data["gauges"].items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value:g}")
    if not lines:
        lines.append("# no metrics yet")
    return "\n".join(lines) + "\n"
