-- MySQL 初始化脚本（docker compose --profile mysql 时自动执行一次）
--
-- 应用启动时也会自己建表（app/recorder.py 的 DDL），这份脚本用于：
--   1) 显式写出表结构，方便审阅；
--   2) 让 DBA 先建好库再让应用连上来。
-- 改表结构时两边一起改。

CREATE DATABASE IF NOT EXISTS agent_toolkit DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE agent_toolkit;

-- 一次问答一行
CREATE TABLE IF NOT EXISTS agent_runs (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    question   TEXT            NOT NULL,
    answer     MEDIUMTEXT      NOT NULL,
    tokens     INT             NULL,
    latency_ms INT             NOT NULL DEFAULT 0,
    created_at TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 一次工具调用一行：谁（tool）、第几步（step）、参数、是否成功、结果预览
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
