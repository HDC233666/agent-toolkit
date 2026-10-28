"""SQL 工具测试：校验只读白名单，以及未配置数据源时的友好报错（不依赖真实数据库）。"""

from __future__ import annotations

import pytest

from app import sql_tool


def test_validate_allows_read_only_queries() -> None:
    assert sql_tool.validate_sql("SELECT 1") == "SELECT 1"
    assert sql_tool.validate_sql("select count(*) from documents;") == "select count(*) from documents"
    assert sql_tool.validate_sql("SHOW TABLES") == "SHOW TABLES"
    assert sql_tool.validate_sql("WITH t AS (SELECT 1) SELECT * FROM t").startswith("WITH")
    assert sql_tool.validate_sql("-- 注释\nSELECT 1") == "SELECT 1"


@pytest.mark.parametrize(
    "bad",
    [
        "DELETE FROM documents",
        "update documents set name='x'",
        "INSERT INTO chunks VALUES (1)",
        "DROP TABLE documents",
        "SELECT 1; DROP TABLE documents",
        "SELECT 1 /* 写操作藏注释里 */ ; DELETE FROM chunks",
        "call do_something()",
        "",
    ],
)
def test_validate_rejects_writes_and_multi_statement(bad: str) -> None:
    with pytest.raises(ValueError):
        sql_tool.validate_sql(bad)


def test_sql_query_reports_missing_config(monkeypatch) -> None:
    monkeypatch.setattr(sql_tool, "SQL_DB_HOST", "")
    result = sql_tool.sql_query("SELECT 1")
    assert result["ok"] is False
    assert "未配置" in result["error"]
