"""SQL 查询工具：让 Agent 能查本机 MySQL（只读）。

设计要点：
- 只放行 SELECT / SHOW / DESCRIBE / EXPLAIN / WITH（只读），其余一律拒绝；
- 只允许单条语句，先去掉注释再校验，防止用注释藏写操作；
- 限行数、限超时，结果转成纯 JSON（datetime/Decimal 都转成字符串/数字）；
- 没配置 SQL_DB_* 时返回明确错误，不抛异常（错误要能交给模型自己判断）。
"""

from __future__ import annotations

import datetime as dt
import decimal
import re

from .config import (
    SQL_DB_HOST,
    SQL_DB_NAME,
    SQL_DB_PASSWORD,
    SQL_DB_PORT,
    SQL_DB_USER,
)

# 只读语句允许的开头
_ALLOWED_PREFIXES = ("select", "show", "describe", "desc", "explain", "with")
# 出现这些词（按整词匹配）就拒绝；注意 select 里可能出现的列名不会误伤
_FORBIDDEN_WORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "rename", "replace", "load", "call", "set", "lock",
}


class ToolError(ValueError):
    """SQL 工具校验失败（错误信息原样返回给模型）。"""


def _strip_comments(sql: str) -> str:
    """去掉 -- 行注释和 /* */ 块注释，避免把写操作藏在注释里。"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"(?m)--[^\n]*", " ", sql)
    return sql


def validate_sql(sql: str) -> str:
    """校验并返回可执行的只读 SQL；不合法则抛 ToolError。"""
    if not sql or not str(sql).strip():
        raise ToolError("SQL 不能为空")
    cleaned = _strip_comments(str(sql)).strip()
    # 允许末尾一个分号，但不允许中间出现多条语句
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        raise ToolError("只允许执行单条 SQL，不能包含多条语句")
    if not cleaned:
        raise ToolError("SQL 不能为空")
    lowered = cleaned.lower()
    if not lowered.startswith(_ALLOWED_PREFIXES):
        raise ToolError("只允许只读查询：SELECT / SHOW / DESCRIBE / EXPLAIN / WITH")
    for word in re.findall(r"[a-zA-Z_]+", lowered):
        if word in _FORBIDDEN_WORDS:
            raise ToolError(f"检测到写操作关键字 {word.upper()}，该工具只读")
    return cleaned


def _json_safe(value):
    """把 MySQL 返回值转成可 JSON 序列化的类型。"""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def sql_query(sql: str, limit: int = 50) -> dict:
    """执行只读 SQL，返回 {ok, columns, rows, row_count}。"""
    try:
        statement = validate_sql(sql)
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}

    if not SQL_DB_HOST:
        return {
            "ok": False,
            "error": "SQL 数据源未配置：在 .env 里填 SQL_DB_HOST/PORT/USER/PASSWORD/NAME（示例：rag_kb）",
        }

    try:
        max_rows = int(limit)
    except (TypeError, ValueError):
        max_rows = 50
    max_rows = max(1, min(max_rows, 200))

    try:
        import pymysql

        conn = pymysql.connect(
            host=SQL_DB_HOST,
            port=SQL_DB_PORT,
            user=SQL_DB_USER,
            password=SQL_DB_PASSWORD,
            database=SQL_DB_NAME,
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"连接数据库失败：{exc}"}

    try:
        with conn.cursor() as cur:
            cur.execute(statement)
            columns = [item[0] for item in (cur.description or [])]
            fetched = cur.fetchmany(max_rows) if columns else []
            rows = [
                {col: _json_safe(value) for col, value in zip(columns, row)}
                for row in fetched
            ]
            return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"SQL 执行失败：{exc}"}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
