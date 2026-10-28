# Agent Toolkit · Function Calling Demo

> [中文版](README.md)

A hand-written **AI Agent** mini-project: the LLM decides on its own when to check the time, do math, read/write files, or fetch a web page — and every tool call is shown to the user.

> Job-hunting project 2/2: project 1 is [RAG Knowledge Base Q&A](../rag-knowledge-base). Together they cover the two core LLM-application skills: **retrieval augmentation** and **tool calling**.

- 🧠 **The model only decides**: OpenAI-compatible API + `function calling`
- 🛠️ **9 real tools**: 7 local (calculator with safe `ast` evaluation, current time, list/read/write files, fetch URL, read-only SQL) + 2 MCP tools
- 🔁 **Agent loop**: call tool → observe result → decide again, max 6 steps to prevent infinite loops
- 📡 **Streaming (SSE)**: tool progress and the final answer are pushed to the browser in real time, token by token
- ⚡ **Parallel tool calls**: when the model returns several `tool_calls` at once, they run concurrently in a thread pool
- 🧩 **MCP integration**: external stdio tool servers (JSON-RPC 2.0) are discovered and mounted as `mcp__<server>__<tool>`; a broken server degrades gracefully
- 🛡️ **Human-in-the-loop approval**: `write_file` (configurable via `AGENT_APPROVAL_TOOLS`) pauses and waits for an explicit Allow before executing; timeout counts as deny
- 🛡️ **Rate limiting & observability**: per-IP rate limit (`AGENT_RATE_LIMIT`, 429 + `Retry-After`), Prometheus metrics at `/api/metrics`, `X-Request-Id` on every response
- 🔒 **Safety boundaries**: file tools are confined to the `data/workspace` sandbox; SQL is whitelist-guarded; no arbitrary code execution
- 🖥️ **Visualized**: the frontend shows the "thought process" (which tool, arguments, result at every step)
- 🗂️ **Call logging**: every conversation and tool call is persisted to MySQL (`agent_runs` / `tool_calls`); the UI shows a "Recent runs" panel. Without a database it falls back to local JSONL, so tests and demos never break.
- ✅ **Testable**: tests run without a real model or network; GitHub Actions runs them automatically

| Tool-call trace | API docs (Swagger UI) |
|---|---|
| ![Chat UI](docs/screenshots/chat.png) | ![API docs](docs/screenshots/api.png) |

Human-in-the-loop approval before sensitive tools run:

![Approval card](docs/screenshots/approval.png)

## Quick start

```bash
# 1. Create and activate a virtual environment (Windows PowerShell)
python -m venv .venv
.venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the key (copy the template, fill in your DeepSeek API Key)
copy .env.example .env

# 4. Run
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. You can also run a real task right away:

```bash
python scripts/smoke.py
```

To enable MySQL call logging, set `AGENT_DB_*` in `.env` (create the `agent_toolkit` database and grant access — see "Call log"); without it, runs are written to `data/logs.jsonl`.

## How it works

```
User question
   │
   ▼
┌─────────────────────────────┐
│ Model (DeepSeek)            │
│ Output is either the final  │
│ answer or tool_calls        │
└──────────┬──────────────────┘
           │ tool_calls
           ▼
┌─────────────────────────────┐
│ Tool executor               │
│ calculator / now /          │
│ list_files / read_file /    │
│ write_file / http_get /     │
│ sql_query                   │
└──────────┬──────────────────┘
           │ observation (JSON)
           ▼
    Append the result as a
    role=tool message and
    call the model again
           │
           ▼
      until the model produces
      the final answer
```

- **Plain chat**: the model replies with text directly.
- **Agent**: the model may "call tools multiple times"; the program executes each call and feeds the result back until the task is done.
- **Parallel calls**: one model turn can request several tools at once — they run in a thread pool (up to 4 workers) and the observations are appended in the original order.
- **Read-only SQL**: `sql_query` talks to MySQL through the same `pymysql` connection settings as the call log; if no `SQL_DB_*` is configured it returns a friendly hint instead of failing.

## MCP tools (external tool servers)

Declare stdio servers in `data/mcp_servers.json`; they are started lazily on first use:

```json
{
  "servers": [
    { "name": "kb", "command": "python", "args": ["-m", "app.mcp_server"], "enabled": true }
  ]
}
```

- Protocol: JSON-RPC 2.0 over stdio with `initialize` / `tools/list` / `tools/call` / `ping` (a deliberately small MCP subset so the chain is readable);
- Naming: `mcp__<server>__<tool>`, e.g. `mcp__kb__kb_search`;
- The bundled `app/mcp_server.py` reuses `SQL_DB_*` to query the RAG project's `rag_kb` and exposes `kb_search` / `kb_stats`;
- A server that fails to start is skipped and reported in `/api/mcp/status` → `errors`; local tools keep working;
- Disable with `MCP_ENABLED=0`.

## Built-in tools

| Tool | Description | Safety |
|---|---|---|
| `calculator` | Evaluate a math expression | `ast` whitelist, only `+ - * / ** %` and numbers, never `eval` |
| `now` | Current date / time / weekday | No side effects |
| `list_files` | List files in the workspace | Read-only, `data/workspace` only |
| `read_file` | Read a text file in the workspace | Path traversal rejected |
| `write_file` | Write a file in the workspace | Path sandbox + 200K character limit |
| `http_get` | Fetch a web page as text | http(s) only, 8 s timeout, truncated to 20K chars |
| `sql_query` | Read-only SQL against MySQL (`rag_kb`) | SELECT/SHOW/DESC/EXPLAIN/WITH only, single statement, no comments, LIMIT ≤ 200 |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check + current model |
| GET | `/api/tools` | Tool list, local + MCP (used by the frontend sidebar) |
| GET | `/api/mcp/status` | MCP server status (tool count, startup errors) |
| POST | `/api/chat` | `{message, history}` → `{answer, steps, tokens}` (one shot) |
| POST | `/api/chat/stream` | Same body, **SSE stream**: `tool_call` / `tool_result` / `delta` / `done` events |
| GET | `/api/logs?limit=20` | Recent runs `{storage, totals, runs}` |
| GET | `/api/metrics` | Prometheus text metrics (chats / tool calls / errors) |
| POST | `/api/approve` | Submit approval decision `{approval_id, approve}` |

SSE event shape (`data: {...}`, one JSON per event):

| Event | Payload | Meaning |
|---|---|---|
| `tool_call` | `{step, tool, args}` | The model decided to call a tool |
| `tool_result` | `{step, ok, result}` | Tool finished (result is fed back to the model) |
| `delta` | `{text}` | Incremental answer text |
| `done` | `{answer, steps, tokens}` | Turn finished with the final result |
| `error` | `{detail}` | Model or tool failure |

Request example:

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"算一下 1234*56，把结果写进 result.txt\"}"
```

Response example (excerpt):

```json
{
  "answer": "已经算好并写入 result.txt：1234 * 56 = 69104",
  "steps": [
    {"tool": "calculator", "args": {"expression": "1234*56"}, "result": {"ok": true, "result": 69104}},
    {"tool": "write_file", "args": {"name": "result.txt", "content": "1234*56=69104"}, "result": {"ok": true, "chars": 12}}
  ],
  "tokens": 512
}
```

## Call log (MySQL)

After every `/api/chat`, the run is written to two tables (DDL lives in `app/recorder.py`):

| Table | Purpose |
|---|---|
| `agent_runs` | One conversation: question, final answer, latency, tokens |
| `tool_calls` | Each tool step of that run: tool name, arguments, success flag, result preview |

`GET /api/logs?limit=20` returns the latest runs (newest first) — this powers the "Recent runs" panel in the UI.

Without `AGENT_DB_*` configured, or when MySQL is unreachable, it automatically falls back to `data/logs.jsonl`, so nothing breaks.

## Project structure

```
agent-toolkit/
├── app/
│   ├── main.py        # FastAPI entry (chat API, tool list, static page)
│   ├── agent.py       # Agent loop: model decision + tool execution + result feedback
│   ├── llm.py         # DeepSeek client wrapper (retries, tool_calls normalization)
│   ├── tools.py       # 7 tools + JSON Schema specs
│   ├── sql_tool.py    # Read-only MySQL query tool (validation + pymysql)
│   ├── recorder.py    # Call logging: MySQL, with JSONL fallback
│   └── config.py      # Environment configuration (including DB connection)
├── static/            # Chat UI (vanilla HTML/CSS/JS, no build step)
├── data/workspace/    # File-tool sandbox (files generated by the Agent go here)
├── data/logs.jsonl    # Fallback call log when no database is configured
├── tests/             # Three test layers: tools / agent / API
├── scripts/smoke.py   # Real-model smoke test
└── .github/workflows/ci.yml
```

## Tests

```bash
python -m pytest -q
```

Tests inject a scripted fake model via `monkeypatch`, so they need **no API key and no network**. Coverage:

- Calculator: normal / malicious / division-by-zero inputs
- File sandbox and path-traversal blocking
- SQL tool: read-only whitelist, blocked writes/multi-statements/comments, clear error when unconfigured
- Agent flow "call tool → feed back → answer", malformed arguments, step limit
- **Parallel tool calls** (two `tool_calls` in one turn, ordered back-fill)
- API health check, tool list, chat, parameter validation, model-error mapping
- **SSE stream**: event order (`tool_call` → `tool_result` → `delta` → `done`), in-stream errors, call logging still works
- Call log: both MySQL and JSONL backends (the MySQL integration test is skipped when no database is available)
- Approvals: pending → allow/deny/timeout, and the guard that blocks non-stream calls
- Rate limit / metrics: per-IP 429, Prometheus counters, `X-Request-Id`

**52 tests** at the moment (including MCP server protocol, client round-trip, mounting and fallback, approvals, rate limiting and metrics).

## Design decisions

1. **Why not `eval`**: model output is untrusted — `__import__('os').system('rm -rf /')` would delete everything. Hence `ast` parsing + an operator whitelist.
2. **Why a sandbox**: file tools may only touch `data/workspace`; `Path.resolve()` checks whether the final path escapes, blocking `../` traversal.
3. **Why return tool errors to the model**: seeing `{"ok": false, "error": "路径越界"}` lets the model fix its arguments and retry — smarter than throwing at the user.
4. **Why limit steps**: the model can loop "call tool → fail → call again"; `AGENT_MAX_STEPS` is the last line of defense.
5. **Why tool descriptions matter**: `function.description` and parameter descriptions are instructions for the model — the more precise, the fewer wrong-tool calls.
6. **Why log calls**: debugging production means reading logs. `agent_runs` stores latency/tokens/steps per conversation; `tool_calls` stores each tool step with its result. When MySQL is down it degrades to JSONL so demos and tests never block.
7. **Why guard SQL ourselves**: model-generated SQL cannot go straight to the database — a single `DROP` is enough. Strip comments, enforce one statement, whitelist read-only prefixes, word-scan write keywords, cap rows and timeouts; invalid SQL becomes a structured error the model can fix.
8. **Why run tools in parallel**: when the model returns independent calls (time + math), running them serially wastes seconds; a thread pool finishes them concurrently and back-fills in order without breaking `tool_call_id` pairing.
9. **Why stream**: an Agent step can take seconds; SSE pushes `tool_call`/`tool_result` the moment they happen and `delta` chunks as the answer is generated. The one-shot `/api/chat` stays for scripts and tests.

**Why MCP?** Tools hard-coded in-process mean every new tool needs a code change and a restart. MCP moves tools into external servers: the agent just calls them by name. I implemented the stdio client and server myself (`initialize → tools/list → tools/call`), namespaced tools with `mcp__`, and made failures non-fatal — closer to how plugins work in production.

## Next steps

- [x] Parallel tool calls (execute multiple `tool_calls` concurrently)
- [x] SQL read-only tool over the RAG project's `rag_kb`
- [x] Persist call logs (MySQL, automatic JSONL fallback)
- [x] MCP external tool servers (stdio + JSON-RPC, bundled kb server, graceful fallback)
- [x] SSE streaming (tool progress + answer deltas)
- [ ] Rewrite the Agent loop with LangGraph (nodes + conditional edges + checkpoint memory)
- [ ] Add more practical tools (`read_pdf`, `search_web`, ...)
- [x] Human-in-the-loop approval (pause before `write_file`, configurable)
- [ ] Support HTTP/SSE MCP transport and third-party community servers

## Code reading order

A suggested reading order:

1. Run `python scripts/smoke.py` and watch which tools it calls (`data/workspace/calc.md` is created);
2. Read `app/tools.py`: each tool is just a plain function + a JSON Schema;
3. Read `run_agent` in `app/agent.py`: the Agent main loop, under 60 lines;
4. Read `app/main.py` and `app/recorder.py`: HTTP endpoints and call recording;
5. Add a new tool (e.g. `random_number`), then run the tests and the UI.

## License

[MIT](LICENSE)
