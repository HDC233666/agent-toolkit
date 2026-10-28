# Agent 工具调用 · Agent Toolkit

> [English version](README.en.md)

一个从零手写的 **AI Agent** 小项目：让大模型自己决定什么时候查时间、什么时候算数、什么时候读写文件、什么时候抓网页、什么时候查数据库，并把每一步工具调用都展示出来。

> 求职向项目 2/2：项目 1 是 [RAG 知识库问答](../rag-knowledge-base)，两个项目覆盖「检索增强」和「工具调用」两块大模型应用核心能力。

- 🧠 **模型只负责决策**：OpenAI 兼容接口 + `function calling`
- 🛠️ **6 个真实工具**：计算器（ast 安全求值）、当前时间、列文件、读文件、写文件、抓网页
- 🔁 **Agent 循环**：调工具 → 观察结果 → 再决策，最多 6 步，防止死循环
- ⚡ **并行工具调用**：模型一次返回多个 `tool_calls` 时用线程池并发执行（属于 Function Calling 进阶用法）
- 🗄️ **SQL 工具**：`sql_query` 对 MySQL 示例库 `rag_kb` 做**只读**查询（白名单 + 禁写关键字），和 RAG 项目共用同一份数据
- 🔒 **安全边界**：文件工具被限制在 `data/workspace` 沙箱内，不执行任意代码
- 🖥️ **可视化**：前端直接展示「思考过程」（每步调了什么、参数、结果），回答支持 Markdown 渲染
- 🗂️ **调用记录**：每次对话与工具调用落库 MySQL（`agent_runs` / `tool_calls` 两张表），前端展示「最近记录」；没配数据库时自动降级为本地 JSONL，测试照样跑
- ✅ **可测试**：不依赖真实模型和网络也能跑测试，GitHub Actions 自动跑

![界面截图](docs/screenshots/chat.png)

## 快速开始

```bash
# 1. 创建并激活虚拟环境（Windows PowerShell）
python -m venv .venv
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 Key（复制模板，填入你的 DeepSeek API Key）
copy .env.example .env

# 4. 启动
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000> 即可。也可以直接跑一个真实任务验证：

```bash
python scripts/smoke.py
```

想启用 MySQL 调用记录：在 `.env` 里填 `AGENT_DB_*`（建库 `agent_toolkit` 并授权，见「调用记录」一节）；不填时自动写入 `data/logs.jsonl`，不影响使用。

## 工作流程

```
用户提问
   │
   ▼
┌─────────────────────────────┐
│ 模型（DeepSeek）              │
│ 输出要么是最终回答            │
│ 要么是 tool_calls            │
└──────────┬──────────────────┘
           │ tool_calls
           ▼
┌─────────────────────────────┐
│ 工具执行器（tool）           │
│ calculator / now /           │
│ list_files / read_file /     │
│ write_file / http_get        │
└──────────┬──────────────────┘
           │ 观察结果（JSON）
           ▼
   把结果作为 role=tool 消息
   放回对话，再次请求模型
           │
           ▼
      直到模型给出最终回答
```

- **普通聊天**：模型直接输出文本。
- **Agent**：模型可以「多次调用工具」，程序执行后把结果喂回模型，直到任务完成。

## 内置工具

| 工具 | 说明 | 安全措施 |
|---|---|---|
| `calculator` | 计算数学表达式 | ast 白名单，只允许 `+ - * / ** %` 和数字，绝不 `eval` |
| `now` | 当前日期/时间/星期 | 无副作用 |
| `list_files` | 列出工作目录文件 | 只读 `data/workspace` |
| `read_file` | 读取工作目录文本 | 路径越界直接拒绝 |
| `write_file` | 写入工作目录文件 | 路径沙箱 + 200K 字符上限 |
| `http_get` | 抓取网页文本 | 只允许 http(s)，8 秒超时，截断 20K 字符 |
| `sql_query` | 对 MySQL 示例库 `rag_kb` 做只读查询 | 白名单只放行 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH；禁写关键字、限单语句、限 200 行 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 当前模型 |
| GET | `/api/tools` | 工具列表（前端侧边栏用） |
| POST | `/api/chat` | `{message, history}` → `{answer, steps, tokens}` |
| GET | `/api/logs?limit=20` | 最近调用记录 `{storage, totals, runs}` |

请求示例：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"算一下 1234*56，把结果写进 result.txt\"}"
```

响应示例（节选）：

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

## 调用记录（MySQL）

每次 `/api/chat` 结束后，把这次对话写入两张表（DDL 见 `app/recorder.py`）：

| 表 | 作用 |
|---|---|
| `agent_runs` | 一次对话：问题、最终回答、耗时、tokens |
| `tool_calls` | 这次对话里的每一步工具调用：工具名、参数、是否成功、结果摘要 |

`GET /api/logs?limit=20` 返回最近记录（时间倒序），前端「最近记录」面板就是调它。

没配置 `AGENT_DB_*` 或数据库连不上时，自动降级写入 `data/logs.jsonl`，功能不中断、测试不受影响。

## 目录结构

```
agent-toolkit/
├── app/
│   ├── main.py        # FastAPI 入口（对话接口、工具列表、静态页）
│   ├── agent.py       # Agent 循环：模型决策 + 工具执行 + 结果回灌
│   ├── llm.py         # DeepSeek 客户端封装（重试、tool_calls 归一化）
│   ├── tools.py       # 7 个工具 + JSON Schema 说明书
│   ├── sql_tool.py    # SQL 只读查询工具（白名单校验 + 结果 JSON 化）
│   ├── recorder.py    # 调用记录：MySQL 落库，失败降级 JSONL
│   └── config.py      # 环境变量配置（含数据库连接）
├── static/            # 聊天界面（原生 HTML/CSS/JS，无构建步骤）
├── data/workspace/    # 文件工具沙箱（Agent 生成的文件写在这里）
├── data/logs.jsonl    # 未配数据库时的调用记录降级文件
├── tests/             # 工具 / Agent / API 三层测试
├── scripts/smoke.py   # 真实模型冒烟测试
└── .github/workflows/ci.yml
```

## 测试

```bash
python -m pytest -q
```

测试通过 `monkeypatch` 注入脚本化假模型，**不需要 API Key 与网络**，覆盖：

- 计算器的正常/恶意/除零输入
- 文件沙箱与路径穿越拦截
- SQL 工具：只读白名单通过、写语句/多语句/注释藏写操作被拒、未配置数据源时明确报错
- Agent 的「调工具 → 回灌 → 回答」流程、坏参数、步数上限
- **多个工具调用的并行执行**（一次返回两个 `tool_calls`，顺序回填且都进入 steps）
- API 的健康检查、工具列表、对话、参数校验、模型错误映射
- 调用记录的 MySQL / JSONL 两种后端（MySQL 集成测试在没有数据库时自动跳过）

## 设计要点

1. **为什么不用 `eval`**：模型输出不可信，如果直接 `eval`，一句 `__import__('os').system('rm -rf /')` 就能删库。所以用 `ast` 解析 + 白名单运算符。
2. **为什么要沙箱**：文件工具只能访问 `data/workspace`，用 `Path.resolve()` 判断最终路径是否越界，防止 `../` 逃逸。
3. **为什么把工具错误返回给模型**：模型看到 `{"ok": false, "error": "路径越界"}` 后可以自己修正参数重试，这比直接把异常抛给用户更智能。
4. **为什么限制步数**：模型可能陷入「调工具 → 失败 → 再调」的死循环，`AGENT_MAX_STEPS` 是最后一道保险。
5. **为什么工具描述要写清楚**：`function.description` 和参数 `description` 是给模型看的说明，写得越具体，模型调错工具的概率越低。
6. **为什么要记录调用**：线上排查问题靠日志。`agent_runs` 记一次问答的耗时/tokens/步数，`tool_calls` 记每一步工具与结果，按时间倒序直接查。数据库连不上时降级写 JSONL，保证演示和测试不被环境卡住。
7. **为什么 SQL 工具要自己做白名单**：模型生成的 SQL 不能直接送进数据库——`DELETE`/`DROP` 一条就够出事。所以先去注释、再校验单语句、只放行只读前缀、按整词扫描写操作关键字，最后限行数、限超时；不合法就返回结构化错误让模型改写。
8. **为什么并行执行工具**：模型一次给出多个互不依赖的调用（比如同时查时间和算数）时，串行执行纯属浪费；线程池并发跑完再按原顺序回填，既快又不破坏协议的对应关系（`tool_call_id` 一一对应）。

## 下一步扩展

- [x] 多工具并行调用（一次返回多个 `tool_calls` 时并发执行）
- [x] SQL 只读查询工具（连 RAG 项目的 `rag_kb`，白名单防写操作）
- [x] 调用记录落库（MySQL，失败自动降级 `data/logs.jsonl`）
- [ ] 用 LangGraph 重写 Agent 循环（节点 + 条件边 + 检查点记忆）
- [ ] 加上 `read_pdf` / `search_web` 等更实用的工具
- [ ] 人工审批模式（写文件/删文件前先确认）
- [ ] 接入 MCP 协议，复用社区工具

## 代码阅读顺序

想快速理解代码，推荐按这个顺序：

1. 先跑通 `python scripts/smoke.py`，观察它调用了哪些工具（`data/workspace/calc.md` 会生成文件）；
2. 读 `app/tools.py`：每个工具就是一个普通函数 + 一份 JSON Schema；
3. 读 `app/agent.py` 的 `run_agent`：Agent 主循环，不到 60 行；
4. 读 `app/main.py` 与 `app/recorder.py`：HTTP 接口与调用记录；
5. 尝试新增一个工具（比如 `random_number`），跑一遍测试和页面。

## License

[MIT](LICENSE)
