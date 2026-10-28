"""人工审批端到端演示：SSE 流里挂起写文件 → 脚本自动点「允许」 → 文件落盘。

用法（agent-toolkit 根目录）：
    .venv/Scripts/python.exe scripts/demo_approval.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8011"
TASK = "帮我把「人工审批已经生效」写进 approve_demo.md 文件。"


def main() -> int:
    approved = False
    with httpx.stream("POST", f"{BASE}/api/chat/stream", json={"message": TASK}, timeout=180) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kind = event.get("type")
            if kind == "approval_required":
                approval = event["approval"]
                print(f"[审批] 工具={approval['tool']} 参数={json.dumps(approval['args'], ensure_ascii=False)}")
                # 相当于用户在界面上点「允许执行」
                r = httpx.post(f"{BASE}/api/approve", json={"approval_id": approval["id"], "approve": True}, timeout=10)
                print(f"[审批] 提交结果 {r.status_code}: {r.text}")
                approved = True
            elif kind == "tool_result":
                print(f"[工具] step={event['step']} ok={event['ok']}")
            elif kind == "done":
                print(f"[完成] {event['answer']}")
    print(f"审批流程触发: {approved}")
    return 0 if approved else 1


if __name__ == "__main__":
    sys.exit(main())
