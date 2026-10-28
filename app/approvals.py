"""人工审批（human-in-the-loop）。

为什么需要它：
- Agent 能调 write_file 这类"有副作用"的工具，如果模型被提示词诱导（或用户被忽悠），
  文件会被直接写/覆盖；
- 生产环境的做法是：危险操作先挂起，等人点"允许"再执行。

实现思路（最小可用）：
- 需要审批的工具调用 → 生成一个 approval（4 字节随机 id）挂到内存注册表；
- Agent 循环阻塞在 `wait()` 上，直到 HTTP 请求（POST /api/approve）把结果放进来；
- 超时按"拒绝"处理，避免请求永久挂起。
"""

from __future__ import annotations

import secrets
import threading


class Approval:
    def __init__(self, tool: str, args: dict) -> None:
        self.id = secrets.token_hex(4)
        self.tool = tool
        self.args = args
        self.approved: bool | None = None  # None=等待中, True=允许, False=拒绝
        self.event = threading.Event()

    def as_dict(self) -> dict:
        return {"id": self.id, "tool": self.tool, "args": self.args}


class ApprovalRegistry:
    def __init__(self) -> None:
        self._pending: dict[str, Approval] = {}
        self._lock = threading.Lock()

    def create(self, tool: str, args: dict) -> Approval:
        approval = Approval(tool, args)
        with self._lock:
            self._pending[approval.id] = approval
        return approval

    def decide(self, approval_id: str, approve: bool) -> bool:
        """返回是否找到了这个 pending approval。"""
        with self._lock:
            approval = self._pending.get(approval_id)
        if approval is None:
            return False
        approval.approved = bool(approve)
        approval.event.set()
        return True

    def wait(self, approval_id: str, timeout: float) -> bool | None:
        """阻塞等待用户决定；超时返回 None，并从注册表移除。"""
        with self._lock:
            approval = self._pending.get(approval_id)
        if approval is None:
            return None
        approval.event.wait(timeout=timeout)
        with self._lock:
            self._pending.pop(approval_id, None)
        return approval.approved

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()


registry = ApprovalRegistry()
