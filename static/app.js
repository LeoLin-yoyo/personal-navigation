/* 个人导航站前端：导航 / 进程管理 / 工具管理 三个页签，5 秒轮询刷新状态。 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  tools: [],
  procs: { active: [], history: [] },
  tab: "home",
  q: "",
  starting: new Set(),   // 正在启动中的工具 id（前端瞬态）
  logUrl: null,
};

/* ---------- 基础工具 ---------- */

async function api(path, opts) {
  const res = await fetch("/api" + path, Object.assign({
    headers: { "Content-Type": "application/json" },
  }, opts || {}));
  if (!res.ok) {
    let msg = res.status + " " + res.statusText;
    try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) {}
    throw new Error(msg);
  }
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* 没写协议前缀的链接（github.com / 127.0.0.1:8000）自动补 http:// */
function normalizeUrl(u) {
  u = String(u || "").trim();
  if (!u) return u;
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(u) ? u : "http://" + u;
}

function toast(msg, type = "info", ms = 4000) {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

function confirmDlg(text) {
  return new Promise((resolve) => {
    const dlg = $("#dlg-confirm");
    $("#confirm-text").textContent = text;
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    $("#confirm-yes").onclick = () => { done(true); dlg.close(); };
    $("#confirm-no").onclick = () => { done(false); dlg.close(); };
    dlg.onclose = () => done(false);
    dlg.showModal();
  });
}

function fmtTime(iso) {
  return iso ? iso.replace("T", " ") : "-";
}

function statusInfo(t) {
  const r = t.runtime || {};
  if (r.status === "running") {
    if (r.port_ready === false) return { cls: "starting", label: "启动中（端口未就绪）" };
    return r.source === "external"
      ? { cls: "running", label: "运行中 · 外部" }
      : { cls: "running", label: "运行中" };
  }
  if (state.starting.has(t.id)) return { cls: "starting", label: "启动中" };
  if (!t.start_command && t.url) return { cls: "link", label: "链接" };
  return { cls: "stopped", label: "未运行" };
}

/* ---------- 数据刷新与轮询 ---------- */

async function refresh() {
  try {
    const data = await api("/tools");
    state.tools = data.tools;
    updateProcBadge();
    if (state.tab === "home") renderHome();
    else if (state.tab === "admin") renderAdmin();
    else if (state.tab === "procs") refreshProcs();
  } catch (e) {
    toast("加载失败：" + e.message, "error");
  }
}

function updateProcBadge() {
  const n = state.tools.filter((t) => t.runtime && t.runtime.status === "running").length;
  $("#nav-procs").textContent = n ? `进程管理 (${n})` : "进程管理";
}

async function refreshProcs() {
  try {
    state.procs = await api("/processes?limit=100");
    renderProcs();
  } catch (e) { /* 静默 */ }
}

/* ---------- 导航页 ---------- */

function cardHtml(t) {
  const st = statusInfo(t);
  const r = t.runtime || {};
  const meta = [st.label];
  if (st.cls === "running" || st.cls === "starting") {
    if (r.pid) meta.push("PID " + r.pid);
    if (t.port) meta.push("端口 " + t.port);
    if (r.source === "external") meta.push("非导航站启动");
  } else {
    if (t.port) meta.push("端口 " + t.port);
  }
  const hasBackend = !!t.start_command || !!t.port;
  const stopBtn = (hasBackend && (st.cls === "running" || st.cls === "starting"))
    ? `<button class="btn small" data-action="stop" data-id="${t.id}">停止</button>` : "";
  const startBtn = (!t.url && t.start_command && st.cls === "stopped")
    ? `<button class="btn small primary" data-action="start" data-id="${t.id}">启动</button>` : "";
  const openBtn = t.url
    ? `<button class="btn small primary" data-action="open" data-id="${t.id}">打开</button>` : "";
  return `<div class="card" data-id="${t.id}">
    <div class="card-top"><span class="icon">${esc(t.icon)}</span><span class="dot ${st.cls}" title="${esc(st.label)}"></span></div>
    <div class="tname">${esc(t.name)}</div>
    <div class="tdesc">${esc(t.description || "")}</div>
    <div class="tmeta">${esc(meta.join(" · "))}</div>
    <div class="actions">${openBtn}${startBtn}${stopBtn}
      <button class="btn small ghost" data-action="logs" data-id="${t.id}">日志</button></div>
  </div>`;
}

function renderHome() {
  const q = state.q.trim().toLowerCase();
  const tools = state.tools.filter((t) => t.enabled &&
    (!q || (t.name + " " + t.description + " " + t.url + " " + t.group_name).toLowerCase().includes(q)));
  if (!tools.length) {
    $("#main").innerHTML = `<div class="empty">${q ? "没有匹配的工具" : "还没有工具 —— 点击右上角「工具管理」添加"}</div>`;
    return;
  }
  const groups = new Map();
  for (const t of tools) {
    if (!groups.has(t.group_name)) groups.set(t.group_name, []);
    groups.get(t.group_name).push(t);
  }
  let html = "";
  for (const [g, list] of groups) {
    html += `<section class="group"><h2>${esc(g)}</h2><div class="cards">` +
      list.map(cardHtml).join("") + `</div></section>`;
  }
  $("#main").innerHTML = html;
}

/* ---------- 进程管理页 ---------- */

function renderProcs() {
  const { active, history } = state.procs;
  let html = `<div class="panel-title"><h1>进程管理</h1>
    <span class="hint">运行中的后端进程；导航站关闭后它们会继续运行，重新打开可重新识别。</span></div>`;

  html += `<h2 style="font-size:14px;margin:10px 0 8px">运行中（${active.length}）</h2>`;
  if (!active.length) {
    html += `<div class="hint" style="padding:6px 0 14px">当前没有运行中的后端进程。</div>`;
  } else {
    html += `<table><thead><tr><th>工具</th><th>PID</th><th>来源</th><th>启动时间</th><th>操作</th></tr></thead><tbody>` +
      active.map((p) => `<tr>
        <td>${esc(p.tool_name || ("工具#" + p.tool_id))}</td>
        <td class="mono">${p.pid ?? "-"}</td>
        <td>${p.started_by === "external"
          ? `<span class="badge ext">外部进程</span>`
          : `<span class="badge ok">导航站启动</span>`}</td>
        <td class="dim">${esc(fmtTime(p.started_at))}</td>
        <td><div class="row-actions">
          <button class="btn small" data-action="stop" data-id="${p.tool_id}">停止</button>
          ${p.log_file ? `<button class="btn small ghost" data-action="plogs" data-pid="${p.id}">日志</button>` : ""}
        </div></td></tr>`).join("") + `</tbody></table>`;
  }

  html += `<h2 style="font-size:14px;margin:26px 0 8px">历史记录（最近 ${history.length} 条）</h2>`;
  if (!history.length) {
    html += `<div class="hint">还没有历史记录。</div>`;
  } else {
    html += `<table><thead><tr><th>工具</th><th>PID</th><th>状态</th><th>启动时间</th><th>结束时间</th><th>操作</th></tr></thead><tbody>` +
      history.map((p) => {
        const badge = p.status === "failed" ? `<span class="badge err">启动失败</span>`
          : p.status === "stopped" ? `<span class="badge">已停止</span>`
          : `<span class="badge">已退出</span>`;
        return `<tr>
          <td>${esc(p.tool_name || ("工具#" + p.tool_id))}</td>
          <td class="mono">${p.pid ?? "-"}</td>
          <td>${badge}</td>
          <td class="dim">${esc(fmtTime(p.started_at))}</td>
          <td class="dim">${esc(fmtTime(p.stopped_at))}</td>
          <td>${p.log_file ? `<button class="btn small ghost" data-action="plogs" data-pid="${p.id}">日志</button>` : ""}</td>
        </tr>`;
      }).join("") + `</tbody></table>`;
  }
  $("#main").innerHTML = html;
}

/* ---------- 工具管理页 ---------- */

function renderAdmin() {
  let html = `<div class="panel-title"><h1>工具管理</h1><span class="spacer"></span>
    <button class="btn primary" data-action="add">＋ 新增工具</button></div>
    <p class="hint" style="margin:0 0 12px">配置存于 SQLite（data/nav.db）。启动命令会在后台静默执行；填了端口就能自动探测就绪并防止重复启动。</p>
    <table><thead><tr><th></th><th>名称</th><th>分组</th><th>端口</th><th>启动命令</th><th>状态</th><th>操作</th></tr></thead><tbody>` +
    state.tools.map((t, i) => {
      const st = statusInfo(t);
      return `<tr style="${t.enabled ? "" : "opacity:.5"}">
        <td>${esc(t.icon)}</td>
        <td>${esc(t.name)}${t.enabled ? "" : ` <span class="badge">已禁用</span>`}</td>
        <td class="dim">${esc(t.group_name)}</td>
        <td class="mono">${t.port ?? "-"}</td>
        <td class="mono dim truncate" title="${esc(t.start_command)}">${esc(t.start_command || "-")}</td>
        <td><span class="dot ${st.cls}" style="display:inline-block;vertical-align:middle;margin-right:6px"></span>${esc(st.label)}</td>
        <td><div class="row-actions">
          <button class="btn small ghost" data-action="up" data-id="${t.id}" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="btn small ghost" data-action="down" data-id="${t.id}" ${i === state.tools.length - 1 ? "disabled" : ""}>↓</button>
          <button class="btn small" data-action="edit" data-id="${t.id}">编辑</button>
          <button class="btn small danger" data-action="del" data-id="${t.id}">删除</button>
        </div></td></tr>`;
    }).join("") + `</tbody></table>`;
  $("#main").innerHTML = html;
}

/* ---------- 工具编辑对话框 ---------- */

/* 内置 emoji 选择器：按场景分组，覆盖本地工具常见命名 */
const EMOJI_GROUPS = [
  ["常用", "🚀 ⚡ 🔥 ⭐ ✨ 💡 📌 🎯 🧭 🎨 🌈 🎉 💎 🔑 🏷️ 📎 🧩 🗂️ 💼 🏠"],
  ["开发", "💻 🖥️ ⌨️ 🖱️ 🖲️ 💾 📟 🧑‍💻 🛠️ ⚙️ 🧰 🔧 🔨 📦 🧱 🏗️ 🧪 🐛 🔍 📝 📄 📜 📋 ✏️"],
  ["AI 与模型", "🤖 🧠 🪄 🔮 🧿 👁️ 🗣️ ⚛️ 💬 💭 🧮 📡 ✳️ 🎛️ 👾 🦾 🧑‍🏫 🧸"],
  ["网络与安全", "🌐 🕸️ ☁️ 🛰️ 🔌 🔒 🔓 🛡️ 🔐 🌍 📨 📬 📮 🔔 📢 📧 📞 🚦 🛜 🔗"],
  ["数据与文件", "🗄️ 🛢️ 📊 📈 📉 🧾 📁 📂 🗃️ 📇 📚 📖 📰 🔖 🗒️ 🗓️ 📅 ⏳ ⏰ ⏱️"],
  ["媒体与娱乐", "📱 📺 📷 📸 🎥 🎬 🎵 🎧 🎤 🎮 🕹️ 🎲 🎭 🎟️ 📻 🖨️ 🪪 🔊 🎦 🪄"],
  ["状态标记", "✅ ❌ ⚠️ 🚫 ❗ ❓ 💤 🟢 🟡 🔴 🔵 🟣 🟠 ⚫ ⚪ 🆗 🆒 🆕"],
  ["下载与传输", "⬇️ ⬆️ ➡️ 🔄 🔁 ♻️ 📥 📤 🧲 🚚 🛫 🛬 🧹 🗑️ 🏃 📡"],
];

let emojiTab = 0;

function renderEmojiPicker() {
  $("#emoji-tabs").innerHTML = EMOJI_GROUPS.map((g, i) =>
    `<button type="button" class="${i === emojiTab ? "active" : ""}" data-i="${i}">${esc(g[0])}</button>`).join("");
  const cur = $("#f-icon").value.trim();
  $("#emoji-grid").innerHTML = EMOJI_GROUPS[emojiTab][1].split(/\s+/).filter(Boolean).map((e) =>
    `<button type="button" class="emoji${e === cur ? " sel" : ""}" data-e="${esc(e)}">${e}</button>`).join("");
}

function openEmojiPicker() {
  const cur = $("#f-icon").value.trim();
  const gi = EMOJI_GROUPS.findIndex((g) => g[1].split(/\s+/).includes(cur));
  emojiTab = gi >= 0 ? gi : 0;
  renderEmojiPicker();
  $("#dlg-emoji").showModal();
}

function openToolDlg(t) {
  const f = (id, v) => { $(id).value = v ?? ""; };
  $("#tool-form-title").textContent = t ? "编辑工具" : "新增工具";
  f("#f-name", t?.name); f("#f-desc", t?.description); f("#f-group", t?.group_name);
  f("#f-icon", t?.icon); f("#f-url", t?.url); f("#f-cmd", t?.start_command);
  f("#f-cwd", t?.work_dir); f("#f-stop", t?.stop_command);
  $("#f-port").value = t?.port ?? "";
  $("#f-timeout").value = t?.startup_timeout_ms ?? 30000;
  $("#f-nowait").value = t?.no_port_wait_ms ?? 1000;
  $("#f-sort").value = t?.sort_order ?? 0;
  $("#f-enabled").checked = t ? !!t.enabled : true;
  $("#f-name").dataset.editing = t ? t.id : "";
  const groups = [...new Set(state.tools.map((x) => x.group_name))];
  $("#group-list").innerHTML = groups.map((g) => `<option value="${esc(g)}">`).join("");
  $("#dlg-tool").showModal();
}

function collectForm() {
  const port = $("#f-port").value.trim();
  return {
    name: $("#f-name").value.trim(),
    description: $("#f-desc").value.trim(),
    group_name: $("#f-group").value.trim() || "默认分组",
    icon: $("#f-icon").value.trim() || "🔧",
    url: $("#f-url").value.trim(),
    start_command: $("#f-cmd").value.trim(),
    work_dir: $("#f-cwd").value.trim(),
    port: port ? parseInt(port, 10) : null,
    startup_timeout_ms: parseInt($("#f-timeout").value, 10) || 30000,
    no_port_wait_ms: parseInt($("#f-nowait").value, 10) || 1000,
    stop_command: $("#f-stop").value.trim(),
    sort_order: parseInt($("#f-sort").value, 10) || 0,
    enabled: $("#f-enabled").checked,
  };
}

async function saveTool() {
  const data = collectForm();
  if (!data.name) { toast("名称必填", "error"); return; }
  const editing = $("#f-name").dataset.editing;
  try {
    if (editing) await api("/tools/" + editing, { method: "PUT", body: JSON.stringify(data) });
    else await api("/tools", { method: "POST", body: JSON.stringify(data) });
    $("#dlg-tool").close();
    toast("已保存", "success");
    refresh();
  } catch (e) {
    toast("保存失败：" + e.message, "error", 6000);
  }
}

function toolPayload(t) {
  return {
    name: t.name, description: t.description, group_name: t.group_name, icon: t.icon,
    url: t.url, start_command: t.start_command, work_dir: t.work_dir, port: t.port,
    startup_timeout_ms: t.startup_timeout_ms, no_port_wait_ms: t.no_port_wait_ms,
    stop_command: t.stop_command, sort_order: t.sort_order, enabled: !!t.enabled,
  };
}

async function moveTool(t, dir) {
  const same = state.tools.filter((x) => x.group_name === t.group_name);
  const i = same.findIndex((x) => x.id === t.id);
  const j = i + dir;
  if (j < 0 || j >= same.length) return;
  const other = same[j];
  try {
    await api("/tools/" + t.id, { method: "PUT", body: JSON.stringify({ ...toolPayload(t), sort_order: other.sort_order }) });
    await api("/tools/" + other.id, { method: "PUT", body: JSON.stringify({ ...toolPayload(other), sort_order: t.sort_order }) });
    refresh();
  } catch (e) { toast("排序失败：" + e.message, "error"); }
}

async function deleteTool(t) {
  const ok = await confirmDlg(`删除工具「${t.name}」？\n若其后端正在运行会先尝试停止。删除后配置不可恢复。`);
  if (!ok) return;
  try {
    const r = await api("/tools/" + t.id, { method: "DELETE" });
    toast(r.message || "已删除", "success");
    refresh();
  } catch (e) { toast("删除失败：" + e.message, "error"); }
}

/* ---------- 启动 / 停止 / 打开 ---------- */

async function startTool(id, name) {
  if (state.starting.has(id)) return;
  state.starting.add(id);
  if (state.tab === "home") renderHome();
  try {
    const r = await api(`/tools/${id}/start`, { method: "POST" });
    if (!r.ok) {
      // 不自动弹出日志窗口，需要排查时点卡片上的「日志」按钮
      toast(`「${name}」启动失败：${r.message || "未知原因"}`, "error", 9000);
    } else if (r.already) {
      toast(r.message || "已在运行");
    } else {
      toast(`已启动：${name}`, "success");
    }
  } catch (e) {
    toast(`「${name}」启动失败：${e.message}`, "error", 9000);
  } finally {
    state.starting.delete(id);
    refresh();
  }
}

async function stopTool(id, name) {
  const t = state.tools.find((x) => x.id === id);
  const ext = t && t.runtime && t.runtime.source === "external";
  const ok = await confirmDlg(`确定停止「${name}」的后端进程？\n` +
    (ext ? "注意：该进程不是导航站启动的，将强制结束其整个进程树。"
         : "将先尝试优雅停止，3 秒未退出则强制结束整棵进程树。"));
  if (!ok) return;
  try {
    const r = await api(`/tools/${id}/stop`, { method: "POST" });
    toast(r.message || (r.ok ? "已停止" : "停止失败"), r.ok ? "success" : "error", 6000);
  } catch (e) {
    toast("停止失败：" + e.message, "error");
  }
  refresh();
}

async function openTool(t) {
  if (!t.url) { if (t.start_command) startTool(t.id, t.name); return; }
  const url = normalizeUrl(t.url);
  // 纯链接工具（无启动命令）：与运行状态无关，直接打开
  if (!t.start_command) {
    window.open(url, "_blank");
    return;
  }
  const st = t.runtime || {};
  if (st.status === "running" && st.port_ready !== false && !state.starting.has(t.id)) {
    window.open(url, "_blank");
    return;
  }
  // 需要启动：先同步占一个空白标签页，避免启动耗时后被浏览器拦截弹窗
  let w = null;
  try { w = window.open("", "_blank"); } catch (e) {}
  if (w) {
    try {
      w.document.title = "启动中…";
      w.document.body.innerHTML = `<p style="font:14px/1.6 sans-serif;padding:28px;color:#555">⏳ 正在启动「${esc(t.name)}」…<br>启动完成后会自动跳转。</p>`;
    } catch (e) {}
  }
  await startTool(t.id, t.name);
  const fresh = (await api("/tools")).tools.find((x) => x.id === t.id);
  const ready = fresh && fresh.runtime && fresh.runtime.status === "running"
    && (fresh.runtime.port_ready !== false);
  if (ready) {
    if (w && !w.closed) w.location.href = url;
    else window.open(url, "_blank");
  } else {
    if (w) w.close();
    toast(`「${t.name}」未就绪，可查看日志排查`, "error", 8000);
  }
}

/* ---------- 日志 ---------- */

let logTimer = null;

function showLogDlg(title, urlPath, presetContent) {
  $("#log-title").textContent = title;
  state.logUrl = urlPath;
  $("#log-content").textContent = presetContent ?? "加载中…";
  $("#log-meta").textContent = "";
  $("#dlg-log").showModal();
  pollLog();
  clearInterval(logTimer);
  logTimer = setInterval(() => { if ($("#log-auto").checked) pollLog(); }, 2000);
}

async function pollLog() {
  if (!state.logUrl) return;
  try {
    const d = await api(state.logUrl + "?lines=500");
    $("#log-content").textContent = d.content || "（空）";
    $("#log-meta").textContent = (d.pid ? `PID ${d.pid} · ` : "") + (d.log_file || "");
  } catch (e) {
    $("#log-content").textContent = "日志加载失败：" + e.message;
  }
}

/* ---------- 事件绑定 ---------- */

$("#main").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  const card = e.target.closest(".card");
  const id = btn ? btn.dataset.id : (card ? card.dataset.id : null);
  const t = state.tools.find((x) => x.id == id);
  if (btn && !t && !["add", "plogs"].includes(btn.dataset.action)) return;

  const act = btn ? btn.dataset.action
    : (t ? (t.url || t.start_command ? (t.url ? "open" : "start") : null) : null);
  if (!act) return;

  switch (act) {
    case "open": openTool(t); break;
    case "start": startTool(t.id, t.name); break;
    case "stop": stopTool(Number(btn.dataset.id), t ? t.name : "该工具"); break;
    case "logs": showLogDlg(`日志 · ${t.name}`, `/tools/${t.id}/logs`); break;
    case "plogs": showLogDlg("历史日志", `/processes/${btn.dataset.pid}/logs`); break;
    case "edit": openToolDlg(t); break;
    case "del": deleteTool(t); break;
    case "up": moveTool(t, -1); break;
    case "down": moveTool(t, 1); break;
    case "add": openToolDlg(null); break;
  }
});

document.querySelectorAll("header nav button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll("header nav button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    state.tab = b.dataset.tab;
    if (state.tab === "procs") refreshProcs(); else refresh();
  });
});

$("#search").addEventListener("input", (e) => {
  state.q = e.target.value;
  if (state.tab !== "home") {
    document.querySelector('nav button[data-tab="home"]').click();
  } else {
    renderHome();
  }
});

$("#tool-save").addEventListener("click", saveTool);
$("#tool-cancel").addEventListener("click", () => $("#dlg-tool").close());
$("#icon-pick").addEventListener("click", openEmojiPicker);
$("#emoji-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-i]");
  if (b) { emojiTab = Number(b.dataset.i); renderEmojiPicker(); }
});
$("#emoji-grid").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-e]");
  if (!b) return;
  $("#f-icon").value = b.dataset.e;
  $("#dlg-emoji").close();
});
$("#emoji-close").addEventListener("click", () => $("#dlg-emoji").close());
$("#emoji-clear").addEventListener("click", () => { $("#f-icon").value = ""; $("#dlg-emoji").close(); });
$("#log-close").addEventListener("click", () => $("#dlg-log").close());
$("#log-refresh").addEventListener("click", pollLog);
$("#dlg-log").addEventListener("close", () => { clearInterval(logTimer); state.logUrl = null; });

/* ---------- 初始化 ---------- */

refresh();
setInterval(refresh, 5000);
