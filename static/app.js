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

async function send() {
  if (busy) return;
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addBubble("user", multiline(text));
  busy = true;
  sendBtn.disabled = true;
  statusEl.textContent = "Agent 思考中…";

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);

    addBubble("assistant", renderMarkdown(data.answer) + renderSteps(data.steps));
    history.push({ role: "user", content: text }, { role: "assistant", content: data.answer });
    if (history.length > 20) history = history.slice(-20);
    statusEl.textContent = data.tokens ? `完成 · 约 ${data.tokens} tokens` : "完成";
    loadLogs();
  } catch (error) {
    addBubble("error", `出错了：${multiline(error.message)}`);
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
