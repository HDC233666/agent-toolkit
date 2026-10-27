"""调用记录：每次对话 + 每一步工具调用落库（MySQL），MySQL 不可用时写 JSONL 文件。

用途：
- **审计**：谁在什么时候调用了什么工具、参数是什么、成功还是失败；
- **回放**：复盘 Agent 的每一步决策，定位「为什么它调了三次 calculator」；
- **统计**：工具调用次数、成功率，后续可以做监控告警。

MySQL 用两张表（一对多）：

    agent_runs(id, question, answer, tokens, latency_ms, created_at)
    tool_calls(id, run_id FK→agent_runs 级联删除, step, tool, args, ok, result_preview, created_at)

一张「对话」表 + 一张「工具调用」表：查一次对话看上面那张，查工具使用情况看下面那张。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from .config import (
    AGENT_DB_HOST,
    AGENT_DB_NAME,
    AGENT_DB_PASSWORD,
    AGENT_DB_PORT,
    AGENT_DB_USER,
)

LOGS_PATH = Path(__file__).resolve().parent.parent / "data" / "logs.jsonl"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        question   TEXT            NOT NULL,
        answer     MEDIUMTEXT      NOT NULL,
        tokens     INT             NULL,
        latency_ms INT             NOT NULL DEFAULT 0,
        created_at TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        run_id         BIGINT UNSIGNED NOT NULL,
        step           INT             NOT NULL,
        tool           VARCHAR(64)     NOT NULL,
        args           JSON            NOT NULL,
        ok             TINYINT(1)      NOT NULL DEFAULT 1,
        result_preview VARCHAR(600)    NOT NULL DEFAULT '',
        created_at     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_tool_calls_run (run_id),
        CONSTRAINT fk_tool_calls_run FOREIGN KEY (run_id)
            REFERENCES agent_runs (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def _to_display_step(index: int, step: dict) -> dict:
    """把 run_agent 的步骤（tool/args/result）转成展示用的统一结构。"""
    result = step.get("result") or {}
    return {
        "step": index,
        "tool": step.get("tool", ""),
        "args": step.get("args") or {},
        "ok": bool(result.get("ok")),
        "result_preview": json.dumps(result, ensure_ascii=False)[:480],
    }


class MySQLRecorder:
    backend = "mysql"

    def __init__(self) -> None:
        self._init_schema()

    def _connect(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=AGENT_DB_HOST,
            port=AGENT_DB_PORT,
            user=AGENT_DB_USER,
            password=AGENT_DB_PASSWORD,
            database=AGENT_DB_NAME,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
        )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for sql in SCHEMA_STATEMENTS:
                    cur.execute(sql)

    def record(
        self,
        question: str,
        answer: str,
        steps: list[dict],
        tokens: int | None,
        latency_ms: int,
    ) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_runs (question, answer, tokens, latency_ms) "
                    "VALUES (%s, %s, %s, %s)",
                    (question, answer, tokens, latency_ms),
                )
                run_id = int(cur.lastrowid or 0)
                rows = [
                    (
                        run_id,
                        index,
                        str(step.get("tool", "")),
                        json.dumps(step.get("args") or {}, ensure_ascii=False),
                        1 if (step.get("result") or {}).get("ok") else 0,
                        json.dumps(step.get("result") or {}, ensure_ascii=False)[:480],
                    )
                    for index, step in enumerate(steps, start=1)
                ]
                if rows:
                    cur.executemany(
                        "INSERT INTO tool_calls (run_id, step, tool, args, ok, result_preview) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        rows,
                    )
        return run_id

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, question, answer, tokens, latency_ms, created_at "
                    "FROM agent_runs ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                runs = cur.fetchall()
                if not runs:
                    return []
                ids = [row["id"] for row in runs]
                placeholders = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"SELECT run_id, step, tool, args, ok, result_preview FROM tool_calls "
                    f"WHERE run_id IN ({placeholders}) ORDER BY run_id DESC, step",
                    ids,
                )
                calls = cur.fetchall()

        steps_by_run: dict[int, list[dict]] = {row["id"]: [] for row in runs}
        for row in calls:
            args = row["args"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            steps_by_run[row["run_id"]].append(
                {
                    "step": int(row["step"]),
                    "tool": row["tool"],
                    "args": args,
                    "ok": bool(row["ok"]),
                    "result_preview": row["result_preview"],
                }
            )

        return [
            {
                "id": int(row["id"]),
                "question": row["question"],
                "answer": row["answer"],
                "tokens": row["tokens"],
                "latency_ms": row["latency_ms"],
                "created_at": row["created_at"].timestamp() if row["created_at"] else None,
                "steps": steps_by_run.get(row["id"], []),
            }
            for row in runs
        ]

    def totals(self) -> dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT (SELECT COUNT(*) FROM agent_runs) AS runs, "
                    "(SELECT COUNT(*) FROM tool_calls) AS tool_calls"
                )
                row = cur.fetchone() or {}
        return {"runs": int(row.get("runs") or 0), "tool_calls": int(row.get("tool_calls") or 0)}


class JSONLRecorder:
    """降级方案：每行一个 JSON，不需要数据库也能看历史。"""

    backend = "jsonl"

    def __init__(self, path: Path = LOGS_PATH) -> None:
        self.path = Path(path)

    def record(
        self,
        question: str,
        answer: str,
        steps: list[dict],
        tokens: int | None,
        latency_ms: int,
    ) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "question": question,
            "answer": answer,
            "tokens": tokens,
            "latency_ms": latency_ms,
            "steps": [_to_display_step(index, step) for index, step in enumerate(steps, start=1)],
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0

    def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        items = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items

    def list_runs(self, limit: int = 20) -> list[dict]:
        items = self._read_all()[-limit:]
        return [
            {
                "id": index + 1,
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "tokens": item.get("tokens"),
                "latency_ms": item.get("latency_ms", 0),
                "created_at": item.get("ts"),
                "steps": item.get("steps", []),
            }
            for index, item in enumerate(reversed(items))
        ]

    def totals(self) -> dict:
        items = self._read_all()
        return {
            "runs": len(items),
            "tool_calls": sum(len(item.get("steps", [])) for item in items),
        }


def make_recorder() -> MySQLRecorder | JSONLRecorder:
    """配置了数据库就用 MySQL，否则（或连不上时）降级 JSONL。"""
    if AGENT_DB_HOST and AGENT_DB_NAME and AGENT_DB_USER:
        try:
            recorder = MySQLRecorder()
            print(f"[recorder] 调用记录写入 MySQL {AGENT_DB_USER}@{AGENT_DB_HOST}:{AGENT_DB_PORT}/{AGENT_DB_NAME}")
            return recorder
        except Exception as exc:  # noqa: BLE001 —— 演示项目：记录失败不该影响对话
            print(f"[recorder] MySQL 不可用，降级为 JSONL 文件：{exc}")
    return JSONLRecorder()
