const $ = (selector) => document.querySelector(selector);

const chatList = $("#chat-list");
const input = $("#message");
const sendBtn = $("#send");
const statusEl = $("#status");
const toolsEl = $("#tools");
const healthEl = $("#health");
const logsEl = $("#logs");

let history = [];
let busy = false;

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderSteps(steps) {
  if (!steps || !steps.length) return "";
  const items = steps.map((step, index) => {
    const ok = step.result && step.result.ok;
    const args = JSON.stringify(step.args || {});
    const result = JSON.stringify(step.result || {}).slice(0, 600);
    return `
      <li class="step ${ok ? "ok" : "fail"}">
        <span class="step-index">${index + 1}</span>
        <span class="step-tool">${escapeHtml(step.tool)}</span>
        <span class="step-status">${ok ? "成功" : "失败"}</span>
        <div class="step-line">参数：<code>${escapeHtml(args)}</code></div>
        <div class="step-line">结果：<code>${escapeHtml(result)}</code></div>
      </li>`;
  }).join("");
  return `
    <details class="steps">
      <summary>调用过程（${steps.length} 步）</summary>
      <ol>${items}</ol>
    </details>`;
}

function addBubble(role, html) {
  const row = document.createElement("div");
  row.className = `row ${role}`;
  row.innerHTML = `<div class="bubble ${role}">${html}</div>`;
  chatList.appendChild(row);
  chatList.scrollTop = chatList.scrollHeight;
  return row;
}

function multiline(text) {
  return escapeHtml(text).replace(/\n/g, "<br>");
}

/* ---- markdown renderer begin（无依赖，先转义再解析，防 XSS） ---- */
function renderInline(text) {
  return text
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderTextBlock(raw) {
  const lines = escapeHtml(raw).split("\n");
  const out = [];
  let para = [];
  let list = null;

  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.join("<br>")}</p>`);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(`<${list.type}>${list.items.map((item) => `<li>${item}</li>`).join("")}</${list.type}>`);
      list = null;
    }
  };
  const flushAll = () => {
    flushPara();
    flushList();
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushAll();
      continue;
    }
    let match;

    if ((match = /^(#{1,6})\s+(.+)$/.exec(line))) {
      flushAll();
      const level = Math.min(match[1].length + 2, 6);
      out.push(`<h${level}>${renderInline(match[2])}</h${level}>`);
      continue;
    }
    if (/^([-*_])\1{2,}$/.test(line)) {
      flushAll();
      out.push("<hr>");
      continue;
    }
    if ((match = /^&gt;\s?(.*)$/.exec(line))) {
      flushAll();
      out.push(`<blockquote>${renderInline(match[1])}</blockquote>`);
      continue;
    }
    if ((match = /^\d+[.)、]\s+(.+)$/.exec(line))) {
      flushPara();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(renderInline(match[1]));
      continue;
    }
    if ((match = /^[-*+]\s+(.+)$/.exec(line))) {
      flushPara();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(renderInline(match[1]));
      continue;
    }
    flushList();
    para.push(renderInline(line));
  }
  flushAll();
  return out.join("\n");
}

function renderMarkdown(text) {
  const source = String(text ?? "");
  const parts = [];
  const codePattern = /```([^\n`]*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;
  while ((match = codePattern.exec(source))) {
    parts.push({ type: "text", value: source.slice(lastIndex, match.index) });
    parts.push({ type: "code", value: match[2].replace(/\n$/, "") });
    lastIndex = match.index + match[0].length;
  }
  parts.push({ type: "text", value: source.slice(lastIndex) });
  return parts
    .map((part) =>
      part.type === "code"
        ? `<pre><code>${escapeHtml(part.value)}</code></pre>`
        : renderTextBlock(part.value)
    )
    .join("\n");
}
/* ---- markdown renderer end ---- */

async function loadLogs() {
  try {
    const response = await fetch("/api/logs?limit=5");
    const payload = await response.json();
    renderLogs(payload);
  } catch (error) {
    // 记录面板加载失败不影响聊天
  }
}

function renderLogs(payload) {
  const runs = payload && payload.runs ? payload.runs : [];
  if (!runs.length) {
    logsEl.innerHTML = `<li class="loading">还没有记录，发一条消息试试</li>`;
    return;
  }
  logsEl.innerHTML = runs
    .map((run) => {
      const when = run.created_at
        ? new Date(run.created_at * 1000).toLocaleString("zh-CN", {
            hour12: false,
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
          })
        : "";
      const question = String(run.question || "");
      const short = question.length > 34 ? question.slice(0, 34) + "…" : question;
      const steps = run.steps ? run.steps.length : 0;
      return `<li><span class="log-q">${escapeHtml(short)}</span><span class="log-meta">${escapeHtml(when)} · ${steps} 步</span></li>`;
    })
    .join("");
}

function renderProgress(steps) {
  if (!steps.length) return "";
  return steps
    .map((step) => {
      const pending = !step.result;
      const ok = step.result && step.result.ok;
      const badge = pending ? "…" : ok ? "✓" : "✗";
      const cls = pending ? "run-step" : `run-step ${ok ? "ok" : "fail"}`;
      return `<div class="${cls}"><span class="run-index">${step.step || ""}</span>${escapeHtml(step.tool)} ${badge}</div>`;
    })
    .join("");
}

/** 读取 SSE 流：每个 data: {...} 事件回调一次 */
async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const line = frame.split("\n").find((item) => item.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)));
    }
  }
}

/** 人工审批卡片：危险工具执行前让用户点「允许 / 拒绝」 */
function renderApproval(event, container, statusEl) {
  const approval = event.approval || {};
  const card = document.createElement("div");
  card.className = "approval";

  const title = document.createElement("div");
  title.className = "approval-title";
  title.innerHTML = `⚠️ 需要你确认：<code>${escapeHtml(approval.tool || "")}</code>`;

  const args = document.createElement("pre");
  args.className = "approval-args";
  args.textContent = JSON.stringify(approval.args || {}, null, 2).slice(0, 600);

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  const allow = document.createElement("button");
  allow.textContent = "允许执行";
  const deny = document.createElement("button");
  deny.className = "ghost";
  deny.textContent = "拒绝";
  actions.append(allow, deny);
  card.append(title, args, actions);
  container.appendChild(card);
  if (statusEl) statusEl.textContent = "等待你确认工具调用…";

  const decide = async (approve) => {
    allow.disabled = true;
    deny.disabled = true;
    card.classList.add(approve ? "approved" : "denied");
    title.innerHTML = approve
      ? `✅ 已允许执行 <code>${escapeHtml(approval.tool || "")}</code>`
      : `⛔ 已拒绝 <code>${escapeHtml(approval.tool || "")}</code>`;
    try {
      await fetch("/api/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_id: approval.id, approve }),
      });
    } catch (error) {
      // 忽略网络错误：审批超时后端会自动按拒绝处理
    }
    if (statusEl) statusEl.textContent = "继续执行…";
  };
  allow.addEventListener("click", () => decide(true));
  deny.addEventListener("click", () => decide(false));
}

async function send() {
  if (busy) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addBubble("user", multiline(text));
  busy = true;
  sendBtn.disabled = true;
  statusEl.textContent = "Agent 思考中…";

  const row = addBubble("assistant", '<div class="progress"></div><div class="answer"></div>');
  const progressEl = row.querySelector(".progress");
  const answerEl = row.querySelector(".answer");

  let answerText = "";
  let steps = [];
  let tokens = null;
  let errorMessage = "";

  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${response.status}`);
    }

    await readSSE(response, (event) => {
      if (event.type === "tool_call") {
        steps.push({ step: event.step, tool: event.tool, args: event.args, result: null });
        progressEl.innerHTML = renderProgress(steps);
        statusEl.textContent = `执行工具：${event.tool}…`;
      } else if (event.type === "tool_result") {
        const target = steps.find((step) => step.step === event.step && !step.result);
        if (target) target.result = event.result;
        progressEl.innerHTML = renderProgress(steps);
      } else if (event.type === "approval_required") {
        renderApproval(event, progressEl, statusEl);
      } else if (event.type === "delta") {
        answerText += event.text;
        answerEl.innerHTML = renderMarkdown(answerText);
        statusEl.textContent = "生成回答中…";
      } else if (event.type === "done") {
        answerText = event.answer || answerText;
        steps = event.steps || steps;
        tokens = event.tokens;
      } else if (event.type === "error") {
        throw new Error(event.detail || "执行失败");
      }
      chatList.scrollTop = chatList.scrollHeight;
    });

    row.querySelector(".bubble").innerHTML = renderMarkdown(answerText) + renderSteps(steps);
    history.push({ role: "user", content: text }, { role: "assistant", content: answerText });
    if (history.length > 20) history = history.slice(-20);
    statusEl.textContent = tokens ? `完成 · 约 ${tokens} tokens` : "完成";
    loadLogs();
  } catch (error) {
    errorMessage = error.message;
    row.remove();
    addBubble("error", `出错了：${multiline(errorMessage)}`);
    statusEl.textContent = "";
  } finally {
    busy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

async function loadTools() {
  try {
    const [healthResponse, toolsResponse] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/tools"),
    ]);
    const health = await healthResponse.json();
    healthEl.textContent = `${health.model} · ${health.has_key ? "Key 已配置" : "缺少 Key"}`;
    healthEl.classList.toggle("warn", !health.has_key);

    const tools = await toolsResponse.json();
    toolsEl.innerHTML = tools
      .map((tool) => `<li><code>${escapeHtml(tool.name)}</code><span>${escapeHtml(tool.description)}</span></li>`)
      .join("");
  } catch (error) {
    statusEl.textContent = `加载状态失败：${error.message}`;
  }
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

document.querySelectorAll("#examples li").forEach((item) => {
  item.addEventListener("click", () => {
    input.value = item.textContent;
    input.focus();
  });
});

loadTools();
loadLogs();
