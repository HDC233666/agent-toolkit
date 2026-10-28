"""全局配置 —— 全部从环境变量 / .env 读取。"""

from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv 是可选依赖
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
WORKSPACE = BASE_DIR / "data" / "workspace"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "6"))

# ===== 限流 =====
# 每个 IP 每分钟最多 RATE_LIMIT_PER_MIN 个 /api 请求（health 除外）；0 = 关闭
RATE_LIMIT_PER_MIN = int(os.getenv("AGENT_RATE_LIMIT", "60"))

# ===== 调用记录存储（可选）=====
# 留空 = 写 data/logs.jsonl（默认，零配置）；填了 = 写 MySQL，连不上自动降级
AGENT_DB_HOST = os.getenv("AGENT_DB_HOST", "").strip()
AGENT_DB_PORT = int(os.getenv("AGENT_DB_PORT", "3306"))
AGENT_DB_USER = os.getenv("AGENT_DB_USER", "root").strip()
AGENT_DB_PASSWORD = os.getenv("AGENT_DB_PASSWORD", "")
AGENT_DB_NAME = os.getenv("AGENT_DB_NAME", "agent_toolkit").strip()

# ===== SQL 查询工具的数据源（可选）=====
# 留空 = sql_query 工具不可用（会明确报错）；填了 = Agent 可对指定库做只读查询
# 推荐指向 rag-knowledge-base 的 rag_kb 库，两个项目就串起来了
SQL_DB_HOST = os.getenv("SQL_DB_HOST", "").strip()
SQL_DB_PORT = int(os.getenv("SQL_DB_PORT", "3306"))
SQL_DB_USER = os.getenv("SQL_DB_USER", "root").strip()
SQL_DB_PASSWORD = os.getenv("SQL_DB_PASSWORD", "")
SQL_DB_NAME = os.getenv("SQL_DB_NAME", "rag_kb").strip()

# ===== MCP 工具服务器（可选）=====
# 配置文件里列出的 stdio 工具服务器会在首次调用时自动启动；
# 设 MCP_ENABLED=0 可整体关闭（本地工具不受影响）
MCP_ENABLED = os.getenv("MCP_ENABLED", "1").strip() not in ("0", "false", "False")
MCP_SERVERS_FILE = os.getenv("MCP_SERVERS_FILE", str(BASE_DIR / "data" / "mcp_servers.json"))
MCP_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "20"))
