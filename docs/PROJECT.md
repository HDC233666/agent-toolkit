# 项目一页纸：Agent 工具调用助手

> 一句话：让大模型「会动手」做多步任务，同时把「模型不可信」当成前提来设计——沙箱、白名单、人工审批、步数上限、可观测。

## 1. 要解决的问题

让模型算数、查时间、读写文件、抓网页、查数据库，本质是给它工具。但工具意味着**真实副作用**：

- 模型可能把 `eval("__import__('os').system('rm -rf /')")` 当成「计算」；
- 可能用 `../../` 穿越出沙箱目录读你的密钥；
- 可能在「调工具 → 失败 → 再调」里打转，把你的 API 额度烧光；
- 可能一言不合就把文件写了，用户根本来不及拦。

所以这个项目的重点是**边界**：每一步都能解释、每一次副作用都可控、每一次调用都可追溯。

## 2. 做了什么

| 层 | 内容 |
| --- | --- |
| Agent 循环 | 「模型决策 → 执行工具 → 结果回灌 → 再决策」闭环；步数上限（默认 6）防死循环 |
| 工具（9 个） | calculator（AST 白名单）、now、list_files、read_file、write_file、http_get、sql_query（只读白名单）、MCP 的 kb_search / kb_stats |
| 并行 | 一次返回多个 `tool_calls` 时用线程池并发执行（上限 4） |
| 安全 | 计算不碰 `eval`；文件沙箱 `data/workspace` + `resolve()` 越界拦截；SQL 语句白名单 + 禁多语句 |
| 人工审批 | `AGENT_APPROVAL_TOOLS=write_file`：写文件前 SSE 推审批卡片，用户点「允许执行」才跑，超时 120s 按拒绝 |
| 可观测 | 每次问答记 `agent_runs`（耗时/tokens/步数）+ 每次工具调用记 `tool_calls`（参数/结果/耗时/是否审批）；MySQL 连不上降级 `data/logs.jsonl` |
| 接口 | 同步 `/api/chat`、SSE 流式 `/api/chat/stream`（tool_call / tool_result / approval_required / delta / done）、`/api/approve`、`/api/logs`、`/api/metrics` |
| 交付 | Dockerfile、GitHub Actions、中英 README、pytest 52 个用例 |

## 3. 结果

- 真实任务「查时间 → 算 1234×56 → 写 demo.md → 读回校验」4 步一次跑通，无需人工纠错；
- 写文件触发审批卡片，允许后落盘并回读验证；拒绝/超时路径有测试覆盖；
- MCP 两个工具（`kb_search` / `kb_stats`）复用 RAG 项目的 MySQL 库，两个项目串起来；
- 52 个测试全部离线可跑（大模型被 monkeypatch，不花钱、不联网）。

## 4. 技术栈

Python 3.13 · FastAPI · OpenAI 兼容 SDK（DeepSeek function calling）· MySQL · 自写 MCP stdio JSON-RPC 客户端 · pytest · Docker · GitHub Actions

## 5. 怎么验证

```bash
python -m pytest -q          # 52 个用例，不联网不花钱
python scripts/smoke.py      # 真实跑一个多步任务
docker compose up -d --build # 起服务：http://127.0.0.1:8011/docs
```

线上演示：<http://8.137.173.141:8011>（口令见简历）

## 6. 设计取舍（面试验证点）

- **为什么不用 `eval`**：模型输出不可信，`eval` 直接给攻击者一个 Python 解释器；AST 白名单只放行数字、四则运算和括号。
- **为什么要沙箱**：路径穿越是最廉价也最常见的越权；`Path.resolve()` 判断最终路径是否在 `data/workspace` 下，物理隔离比字符串过滤靠谱。
- **为什么把工具错误回灌给模型**：模型看到 `{"ok": false, "error": "路径越界"}` 能自己修正重试，比直接把异常甩给用户体验好。
- **为什么要人工审批**：演示给人看时，「模型准备写文件 → 你点允许」比事后审计更有说服力；自动化场景可以配置关闭。
- **为什么自写 MCP 客户端**：协议不复杂（initialize / tools/list / tools/call / ping），自己写一遍比引 SDK 更能讲清楚「工具如何被发现和调用」。

## 7. 还没做的

- 审批记录只进 `tool_calls.approved`，没有独立审计流；
- 并行执行是线程池，工具实现需要自己保证线程安全；
- MCP 只有 stdio 传输，没有 HTTP/SSE。
