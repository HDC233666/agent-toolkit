"""冒烟测试：用真实 Key 让 Agent 完成一个多步任务（算数 + 写文件）。

用法（项目根目录、已激活 venv、已配置 DEEPSEEK_API_KEY）：
    python scripts/smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import run_agent  # noqa: E402


def main() -> None:
    question = "现在几点了？帮我算一下 1234*56，把结果写进 calc.md 文件，最后把文件内容读出来给我看。"
    result = run_agent(question)
    print("问题:", question)
    print("走了", len(result["steps"]), "步：")
    for index, step in enumerate(result["steps"], 1):
        status = "成功" if step["result"].get("ok") else "失败"
        print(f"  {index}. {step['tool']}({step['args']}) -> {status}")
    print("最终回答:", result["answer"][:300].replace("\n", " "))
    if not result["steps"]:
        raise SystemExit("Agent 没有调用任何工具，function calling 可能没生效")
    print("OK")


if __name__ == "__main__":
    main()
