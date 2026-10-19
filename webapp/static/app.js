"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const icons = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/>',
  settings:
    '<path d="m9 3-1 3-3 1-2 4 2 2v4l4 3 3-1 3 1 4-3v-4l2-2-2-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  sliders:
    '<path d="M4 7h7m5 0h4M4 17h3m5 0h8"/><circle cx="13" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  chart: '<path d="M4 4v16h17M7 15l4-5 4 2 5-6"/>',
  building:
    '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 7h1m4 0h1M9 11h1m4 0h1M9 15h1m4 0h1M10 21v-3h4v3"/>',
  news: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h3m3 0h2M8 18h8"/>',
  chat: '<path d="M20 15a2 2 0 0 1-2 2H9l-6 4V5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2Z"/><path d="M7 8h9M7 12h6"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="10" y="10" width="4" height="4"/>',
  sparkles:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4M12 14v3"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 10v7m0-11v1"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  target:
    '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
};
const svg = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.grid}</svg>`;
function paintIcons(root = document) {
  root
    .querySelectorAll("[data-icon]")
    .forEach((el) => (el.innerHTML = svg(el.dataset.icon)));
}
function text(el, value) {
  if (el) {
    const next = value === null ? "" : String(value);
    if (el.textContent !== next) el.textContent = next;
  }
}
const state = {
  meta: null,
  token: "",
  runs: [],
  run: null,
  rounds: 1,
  tab: null,
  provider: "openai",
  quick_model: "",
  deep_model: "",
  backend_url: "",
  view: "workspace",
  polling: false,
  busy: false,
  lastReport: "",
  tabSignature: null,
  eventsSignature: "",
  historySignature: null,
  pollFailures: 0,
};
let selectionVersion = 0;
let runsRequestVersion = 0;
let pollTimer = null;
const active = (r) =>
  r && ["queued", "running", "cancelling"].includes(r.status);
const statusLabels = {
  queued: "准备中",
  running: "研究进行中",
  cancelling: "正在停止",
  completed: "研究完成",
  failed: "分析失败",
  cancelled: "已停止",
  interrupted: "已中断",
};
const ratings = {
  Buy: "买入",
  Overweight: "增持",
  Hold: "持有",
  Underweight: "减持",
  Sell: "卖出",
  REVIEW: "待人工复核",
};
const analystKeys = {
  market: "market_report",
  fundamentals: "fundamentals_report",
  news: "news_report",
  social: "sentiment_report",
};
const number = (v) => Number(v || 0).toLocaleString("zh-CN");
const localDate = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
function toast(message) {
  text($("#toast"), message);
  $("#toast").hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => ($("#toast").hidden = true), 3800);
}
function inlineError(id, message) {
  const el = $(id);
  text(el, message);
  el.hidden = !message;
}
function rememberRun(id) {
  try {
    sessionStorage.setItem("selected-run", id);
  } catch {}
}
function recalledRun() {
  try {
    return sessionStorage.getItem("selected-run");
  } catch {
    return null;
  }
}
function forgetRun() {
  try {
    sessionStorage.removeItem("selected-run");
  } catch {}
}
async function api(path, body) {
  const options =
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Workspace-Token": state.token,
          },
          body: JSON.stringify(body),
        };
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  let response, result;
  try {
    response = await fetch(path, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error("本地服务响应超时，请检查服务后重试。");
    throw new Error("无法连接本地服务，请检查服务后重试。");
  } finally {
    clearTimeout(timeout);
  }
  try {
    result = await response.json();
  } catch {
    throw new Error("服务响应异常，请刷新页面。");
  }
  if (!response.ok) throw new Error(result.error || "请求失败，请重试。");
  return result;
}
function providerInfo(id = state.provider) {
  return state.meta.providers.find((p) => p.id === id);
}
function modelReady() {
  const p = providerInfo();
  return (
    p &&
    (!p.key_required || p.configured) &&
    (p.id !== "chatgpt" || state.meta.chatgpt_account) &&
    state.quick_model &&
    state.deep_model &&
    (p.id !== "openai_compatible" || state.backend_url)
  );
}
function renderModelSummary() {
  const p = providerInfo();
  text($("#selected-provider"), p?.name || "模型服务");
  const ready = modelReady();
  text($("#model-status"), ready ? "已配置" : "待配置");
  $("#model-status").classList.toggle("ready", !!ready);
  text(
    $("#selected-models"),
    state.quick_model
      ? `${state.quick_model}  /  ${state.deep_model}`
      : "先连接模型，再开始你的第一份研究。",
  );
}
function updateStart() {
  const running = state.runs.some(active);
  $("#start-analysis").disabled = running || state.busy;
  text(
    $("#start-label"),
    running ? "研究进行中" : state.busy ? "正在启动…" : "开始研究",
  );
}
function defaults(provider) {
  const p = providerInfo(provider);
  return {
    quick: p.models.quick?.[0]?.id || "",
    deep: p.models.deep?.[0]?.id || "",
  };
}
function openSettings() {
  const provider = $("#provider");
  provider.replaceChildren();
  state.meta.providers.forEach((p) => {
    const o = new Option(p.name, p.id);
    provider.add(o);
  });
  provider.value = state.provider;
  $("#api-key").value = "";
  $("#quick-model").value = state.quick_model;
  $("#deep-model").value = state.deep_model;
  $("#backend-url").value = state.backend_url;
  settingsProvider(false);
  inlineError("#settings-error", "");
  $("#settings-dialog").showModal();
}
function settingsProvider(reset) {
  const p = providerInfo($("#provider").value);
  const d = defaults(p.id);
  if (reset) {
    $("#api-key").value = "";
    $("#quick-model").value = d.quick;
    $("#deep-model").value = d.deep;
    $("#backend-url").value =
      p.id === "ollama" ? "http://localhost:11434/v1" : "";
  }
  for (const tier of ["quick", "deep"]) {
    $(`#${tier}-model-options`).replaceChildren(
      ...(p.models[tier] || []).map((m) => new Option(m.label, m.id)),
    );
  }
  $("#key-section").hidden = !p.key_required && p.id !== "openai_compatible";
  text($("#saved-key-status"), p.configured ? "已保存密钥" : "尚未配置");
  $("#chatgpt-section").hidden = p.id !== "chatgpt";
  text(
    $("#chatgpt-status"),
    state.meta.chatgpt_account
      ? `已登录 · ${state.meta.chatgpt_account}`
      : "登录后使用你的 ChatGPT 订阅额度。",
  );
  $("#backend-url").required = p.id === "openai_compatible";
}
async function saveSettings(event) {
  event.preventDefault();
  inlineError("#settings-error", "");
  $("#save-settings").disabled = true;
  try {
    const payload = {
      provider: $("#provider").value,
      api_key: $("#api-key").value,
      quick_model: $("#quick-model").value.trim(),
      deep_model: $("#deep-model").value.trim(),
      backend_url: $("#backend-url").value.trim(),
    };
    const meta = await api("/api/settings", payload);
    state.meta = meta;
    Object.assign(state, meta.preferences);
    $("#api-key").value = "";
    $("#settings-dialog").close();
    renderModelSummary();
    toast("模型配置已保存到本机");
  } catch (e) {
    inlineError("#settings-error", e.message);
  } finally {
    $("#save-settings").disabled = false;
  }
}
function showView(view) {
  state.view = view;
  $("#workspace-view").hidden = view !== "workspace";
  $("#history-view").hidden = view !== "history";
  text($("#page-label"), view === "workspace" ? "投研工作台" : "研究档案");
  $$("[data-view]").forEach((b) => {
    const selected = b.dataset.view === view;
    b.classList.toggle("active", selected);
    if (selected) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (view === "history") renderHistory();
}
function newResearch() {
  showView("workspace");
  const running = state.runs.find(active);
  if (running) {
    selectRun(running.id);
    toast("当前研究仍在运行，可以查看进度或停止研究。");
    return;
  }
  selectionVersion++;
  state.run = null;
  forgetRun();
  state.tab = null;
  state.lastReport = "";
  state.tabSignature = null;
  state.eventsSignature = "";
  $("#ticker").value = "";
  $("#analysis-date").value = localDate();
  inlineError("#form-error", "");
  renderRun();
  renderRecents();
  $("#ticker").focus();
}
function syncForm(config) {
  $("#ticker").value = config.ticker;
  $("#analysis-date").value = config.date;
  $("#asset-type").value = config.asset_type;
  $$("input[name=analysts]").forEach(
    (c) => (c.checked = config.analysts.includes(c.value)),
  );
  assetChanged(false);
  setRounds(config.rounds);
}
function setRounds(rounds) {
  state.rounds = rounds;
  $$("[data-rounds]").forEach((b) => {
    const selected = Number(b.dataset.rounds) === rounds;
    b.classList.toggle("selected", selected);
    b.setAttribute("aria-pressed", String(selected));
  });
}
function assetChanged(shouldCheck = true) {
  const crypto = $("#asset-type").value === "crypto";
  const fundamental = $("input[value=fundamentals]");
  fundamental.disabled = crypto;
  if (crypto) fundamental.checked = false;
  else if (shouldCheck) fundamental.checked = true;
}
async function startAnalysis(event) {
  event.preventDefault();
  inlineError("#form-error", "");
  if (!modelReady()) {
    openSettings();
    return;
  }
  const payload = {
    ticker: $("#ticker").value.trim(),
    date: $("#analysis-date").value,
    asset_type: $("#asset-type").value,
    analysts: $$("input[name=analysts]:checked").map((i) => i.value),
    rounds: state.rounds,
    language: "Chinese",
    provider: state.provider,
    quick_model: state.quick_model,
    deep_model: state.deep_model,
    backend_url: state.backend_url,
  };
  if (!payload.analysts.length) {
    inlineError("#form-error", "请至少选择一位分析师。");
    return;
  }
  state.busy = true;
  updateStart();
  const startSelection = ++selectionVersion;
  try {
    const run = await api("/api/runs", payload);
    state.runs = [run, ...state.runs.filter((r) => r.id !== run.id)];
    if (selectionVersion === startSelection) {
      state.run = run;
      rememberRun(run.id);
      state.tab = null;
      state.lastReport = "";
      state.tabSignature = null;
      state.eventsSignature = "";
      renderRun();
    }
    renderRecents();
    toast("研究已启动，团队开始准备数据");
    refreshRuns().catch(() => {});
  } catch (e) {
    inlineError("#form-error", e.message);
  } finally {
    state.busy = false;
    updateStart();
  }
}
function reportTabTarget(index, key, count) {
  if (!count) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowRight") return (index + 1) % count;
  if (key === "ArrowLeft") return (index - 1 + count) % count;
  return -1;
}
function reportRenderKey(run, tab) {
  return JSON.stringify([
    run?.id || "",
    run?.status || "",
    tab || "",
    run?.reports?.[tab] || "",
  ]);
}
function reportPlaceholder(run) {
  if (active(run))
    return "分析团队正在收集证据。各章节完成后会陆续出现在这里。";
  if (run?.status === "cancelled") return "研究已停止，尚未收到分析报告。";
  if (run?.status === "failed" || run?.status === "interrupted")
    return "研究未能完成，尚未收到分析报告。你可以复用配置后重试。";
  return "尚未收到分析报告。";
}
function renderReportTabs(keys) {
  const tabs = $("#report-tabs");
  const signature = keys.map((k) => `${k}:${state.meta.labels[k]}`).join("|");
  if (signature !== state.tabSignature) {
    const focusedKey = document.activeElement?.dataset?.reportKey;
    tabs.replaceChildren();
    keys.forEach((k) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.id = `tab-${k}`;
      b.dataset.reportKey = k;
      b.setAttribute("aria-controls", "report-body");
      b.textContent = state.meta.labels[k];
      b.onclick = () => activateReportTab(k);
      b.onkeydown = (e) => {
        const buttons = [...tabs.querySelectorAll('[role="tab"]')];
        const target = reportTabTarget(
          buttons.indexOf(b),
          e.key,
          buttons.length,
        );
        if (target < 0) return;
        e.preventDefault();
        buttons[target].focus();
        activateReportTab(keys[target]);
      };
      tabs.append(b);
    });
    state.tabSignature = signature;
    if (focusedKey && keys.includes(focusedKey))
      tabs.querySelector(`[data-report-key="${focusedKey}"]`)?.focus();
  }
  [...tabs.querySelectorAll('[role="tab"]')].forEach((b) => {
    const selected = b.dataset.reportKey === state.tab;
    b.setAttribute("aria-selected", String(selected));
    b.tabIndex = selected ? 0 : -1;
    b.classList.toggle("active", selected);
  });
}
function activateReportTab(key) {
  state.tab = key;
  state.lastReport = "";
  renderRun();
  $("#tab-" + key)?.focus();
}
function renderPipeline() {
  const run = state.run,
    reports = run?.reports || {},
    selected = run?.config.analysts || [
      "market",
      "fundamentals",
      "news",
      "social",
    ];
  const completed = [
    selected.every((a) => reports[analystKeys[a]]),
    !!reports.investment_plan,
    !!reports.trader_investment_plan,
    !!(reports.aggressive && reports.conservative && reports.neutral),
    !!reports.final_trade_decision,
  ];
  const stages = [
    ["chart", "分析师团队", "多维数据研究"],
    ["chat", "多空辩论", "交叉论据验证"],
    ["target", "交易方案", "形成交易计划"],
    ["shield", "风险评估", "多种风险视角"],
    ["check", "最终决策", "组合经理审议"],
  ];
  let current = completed.findIndex((x) => !x);
  if (reports.bull || reports.bear) current = Math.max(current, 1);
  if (reports.aggressive || reports.conservative || reports.neutral)
    current = Math.max(current, 3);
  const holder = $("#pipeline");
  holder.replaceChildren();
  stages.forEach(([icon, title, sub], i) => {
    const item = document.createElement("div");
    item.className =
      "pipeline-node" +
      (completed[i] ? " done" : active(run) && i === current ? " current" : "");
    const symbol = document.createElement("div");
    symbol.innerHTML = svg(completed[i] ? "check" : icon);
    const b = document.createElement("b");
    b.textContent = title;
    const small = document.createElement("small");
    small.textContent = completed[i]
      ? "已完成"
      : active(run) && i === current
        ? "正在进行"
        : sub;
    item.append(symbol, b, small);
    holder.append(item);
  });
}
function renderRun() {
  const run = state.run;
  renderPipeline();
  const badge = $("#run-status");
  badge.className = "status-tag " + (run?.status || "");
  text(badge, run ? statusLabels[run.status] : "等待开始");
  text(
    $("#run-subtitle"),
    run
      ? `${run.config.ticker} · ${run.config.date} · ${providerInfo(run.config.provider)?.name || run.config.provider}`
      : "研究阶段与运行状态会显示在这里。",
  );
  text($("#stage-text"), run?.stage || "团队已就绪，等待研究任务");
  $("#activity-dot").classList.toggle("running", !!active(run));
  $("#cancel-run").hidden = !active(run);
  $("#cancel-run").disabled = run?.status === "cancelling";
  text(
    $("#cancel-run"),
    run?.status === "cancelling" ? "正在停止…" : "停止研究",
  );
  const eventList = run?.events || [];
  const eventSignature = `${run?.id || ""}:${JSON.stringify(eventList)}`;
  if (eventSignature !== state.eventsSignature) {
    const events = $("#events");
    const wasAtBottom =
      events.scrollHeight - events.scrollTop - events.clientHeight < 24;
    const oldScrollTop = events.scrollTop;
    events.replaceChildren();
    if (eventList.length) {
      eventList.forEach((e) => {
        const p = document.createElement("p");
        const t = document.createElement("time");
        t.dateTime = new Date(e.time * 1000).toISOString();
        t.textContent = new Date(e.time * 1000).toLocaleTimeString("zh-CN", {
          hour12: false,
        });
        p.append(t, document.createTextNode(e.text));
        events.append(p);
      });
      if (wasAtBottom) events.scrollTop = events.scrollHeight;
      else events.scrollTop = oldScrollTop;
    } else {
      const p = document.createElement("p");
      p.textContent = "开始研究后，工具调用与阶段进展将在这里显示。";
      events.append(p);
    }
    state.eventsSignature = eventSignature;
  }
  text($("#event-count"), `${eventList.length} 条`);
  ["llm", "tools", "tokens"].forEach((id) => {
    let value = "—";
    if (run) {
      const stats = run.stats;
      if (id === "llm") value = number(stats.llm_calls);
      if (id === "tools") value = number(stats.tool_calls);
      if (id === "tokens")
        value = `${number(stats.tokens_in)} / ${number(stats.tokens_out)}`;
    }
    text($(`#stat-${id}`), value);
  });
  $("#report-empty").hidden = !!run;
  $("#report-content").hidden = !run;
  text(
    $("#report-subtitle"),
    run ? `${run.config.ticker} / ${run.config.date}` : "RESEARCH BRIEF",
  );
  const hasReports = run && Object.keys(run.reports).length > 0;
  for (const ext of ["md", "html"]) {
    const link = $(`#export-${ext}`);
    link.hidden = !hasReports;
    if (run) {
      link.href = `/api/runs/${run.id}/report.${ext}`;
      link.download = `${run.config.ticker}-${run.config.date}.${ext}`;
    }
  }
  const decision = $("#decision-banner");
  decision.hidden = !run?.reports.final_trade_decision;
  text(
    $("#decision-rating"),
    run?.rating
      ? `${ratings[run.rating] || run.rating} · ${run.rating}`
      : "正在整理评级",
  );
  const reuse = $("#reuse-run");
  if (reuse) {
    reuse.hidden = !run || active(run);
    reuse.disabled = state.busy;
  }
  const error = $("#run-error");
  error.hidden =
    !run || !["failed", "interrupted", "cancelled"].includes(run.status);
  error.replaceChildren();
  if (run && !error.hidden) {
    const b = document.createElement("b");
    b.textContent =
      run.status === "cancelled"
        ? "研究已停止，已保留收到的报告。"
        : "这次研究未能完成，已保留收到的报告。";
    error.append(
      b,
      document.createTextNode(
        `${run.error || "你可以调整设置，或使用相同配置重新开始。"} 请使用“复用配置”载入本次参数。`,
      ),
    );
  }
  const keys = Object.keys(state.meta?.labels || {}).filter(
    (k) => run?.reports[k],
  );
  if (!keys.includes(state.tab))
    state.tab = keys.includes("final_trade_decision")
      ? "final_trade_decision"
      : keys[0];
  renderReportTabs(keys);
  const article = $("#report-body");
  article.setAttribute("role", "tabpanel");
  article.tabIndex = 0;
  if (state.tab) article.setAttribute("aria-labelledby", `tab-${state.tab}`);
  else article.removeAttribute("aria-labelledby");
  const key = reportRenderKey(run, state.tab);
  if (state.lastReport !== key) {
    if (run?.rendered?.[state.tab]) {
      article.innerHTML = run.rendered[state.tab];
      article.querySelectorAll("a").forEach((a) => {
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      });
    } else {
      article.replaceChildren();
      const p = document.createElement("p");
      p.textContent = reportPlaceholder(run);
      article.append(p);
    }
    state.lastReport = key;
  }
  updateElapsed();
  updateStart();
}
function updateElapsed() {
  const r = state.run;
  if (!r) {
    text($("#elapsed"), "—");
    return;
  }
  const seconds = Math.max(
    0,
    Math.floor((r.finished_at || Date.now() / 1000) - r.created_at),
  );
  text(
    $("#elapsed"),
    `${Math.floor(seconds / 60)
      .toString()
      .padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`,
  );
}
function renderSelectedRun(run, focus = false) {
  syncForm(run.config);
  showView("workspace");
  renderRun();
  renderRecents();
  if (focus) $("#report-body").focus({ preventScroll: true });
}
async function selectRun(
  id,
  { focus = false, render = renderSelectedRun } = {},
) {
  const requestVersion = ++selectionVersion;
  try {
    const run = await api(`/api/runs/${id}`);
    if (requestVersion !== selectionVersion) return false;
    state.run = run;
    rememberRun(id);
    state.tab = null;
    state.lastReport = "";
    state.eventsSignature = "";
    render(run, focus);
    return true;
  } catch (e) {
    if (requestVersion === selectionVersion) toast(e.message);
    return false;
  }
}
function reuseRun(run) {
  const c = run?.config;
  if (!c) return;
  Object.assign(state, {
    provider: c.provider,
    quick_model: c.quick_model,
    deep_model: c.deep_model,
    backend_url: c.backend_url,
  });
  syncForm(c);
  renderModelSummary();
  inlineError("#form-error", "");
  toast("已载入这次研究的配置，点击“开始研究”重试。");
  $("#analysis-form").scrollIntoView({ behavior: "smooth", block: "center" });
}
let recentKey = "";
function renderRecents() {
  const key = JSON.stringify([
    state.runs.map((r) => [r.id, r.status]),
    state.run?.id || "",
  ]);
  if (key === recentKey) return;
  recentKey = key;
  text($("#history-count"), number(state.runs.length));
  text($("#archive-count"), `${state.runs.length} 份`);
  const holder = $("#recent-runs");
  holder.replaceChildren();
  if (!state.runs.length) {
    const p = document.createElement("p");
    p.className = "sidebar-empty";
    p.textContent = "第一份研究，从这里开始。";
    holder.append(p);
  }
  state.runs.slice(0, 5).forEach((run) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "recent-item";
    if (state.run?.id === run.id) b.setAttribute("aria-current", "page");
    const mark = document.createElement("span");
    mark.className = "symbol-dot";
    mark.textContent = run.config.ticker.slice(0, 2);
    const info = document.createElement("span");
    const title = document.createElement("b");
    title.textContent = run.config.ticker;
    const sub = document.createElement("small");
    sub.textContent = `${run.config.date} · ${statusLabels[run.status] || run.status}`;
    info.append(title, sub);
    b.append(mark, info);
    if (active(run)) b.append(document.createElement("i"));
    b.onclick = () => selectRun(run.id, { focus: true });
    holder.append(b);
  });
}
function renderHistory() {
  const query = $("#history-search").value.trim().toLowerCase();
  const runs = state.runs.filter((r) =>
    `${r.config.ticker} ${r.config.provider} ${providerInfo(r.config.provider)?.name || ""}`
      .toLowerCase()
      .includes(query),
  );
  const signature = JSON.stringify([
    query,
    state.run?.id || "",
    runs.map((r) => [
      r.id,
      r.status,
      r.rating,
      r.created_at,
      r.config.ticker,
      r.config.date,
      r.config.provider,
    ]),
  ]);
  if (signature === state.historySignature) return;
  state.historySignature = signature;
  const focusedRunId = document.activeElement?.dataset?.runId;
  const holder = $("#history-rows");
  holder.replaceChildren();
  $("#history-empty").hidden = runs.length > 0;
  text(
    $("#history-empty"),
    query
      ? "没有匹配的研究记录。"
      : "还没有研究记录。开始第一份研究，结果会自动保存在这里。",
  );
  runs.forEach((r) => {
    const tr = document.createElement("tr");
    if (state.run?.id === r.id) tr.setAttribute("aria-current", "true");
    [
      r.config.ticker,
      r.config.date,
      providerInfo(r.config.provider)?.name || r.config.provider,
      ratings[r.rating] || "—",
      statusLabels[r.status] || r.status,
      new Date(r.created_at * 1000).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }),
    ].forEach((value, i) => {
      const td = document.createElement("td");
      if (i === 3 && r.rating) {
        const span = document.createElement("span");
        span.className = "rating-chip";
        span.textContent = value;
        td.append(span);
      } else td.textContent = value;
      tr.append(td);
    });
    const td = document.createElement("td");
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.runId = r.id;
    b.textContent = "查看研究 ↗";
    b.setAttribute(
      "aria-label",
      `查看 ${r.config.ticker} ${r.config.date} 的研究`,
    );
    b.onclick = () => selectRun(r.id, { focus: true });
    td.append(b);
    tr.append(td);
    holder.append(tr);
  });
  if (focusedRunId && runs.some((run) => run.id === focusedRunId))
    holder.querySelector(`[data-run-id="${focusedRunId}"]`)?.focus();
}
async function refreshRuns() {
  const requestVersion = ++runsRequestVersion;
  const runs = await api("/api/runs");
  if (requestVersion !== runsRequestVersion) return state.runs;
  state.runs = runs;
  renderRecents();
  if (state.view === "history") renderHistory();
  updateStart();
  return state.runs;
}
async function poll() {
  if (state.polling || !state.meta) return;
  state.polling = true;
  const selectedId = state.run?.id;
  const selectedVersion = selectionVersion;
  let succeeded = false;
  try {
    await refreshRuns();
    const stillSelected =
      selectedId &&
      selectionVersion === selectedVersion &&
      state.run?.id === selectedId;
    const latestSummary =
      stillSelected && state.runs.find((run) => run.id === selectedId);
    const needsDetail =
      stillSelected &&
      (active(state.run) ||
        (latestSummary && latestSummary.status !== state.run.status));
    if (needsDetail) {
      const previousStatus = state.run.status;
      const updated = await api(`/api/runs/${selectedId}`);
      if (
        selectionVersion === selectedVersion &&
        state.run?.id === selectedId
      ) {
        state.run = updated;
        renderRun();
        if (active({ status: previousStatus }) && !active(updated)) {
          toast(
            updated.status === "completed"
              ? "研究完成，报告已保存"
              : statusLabels[updated.status] || updated.status,
          );
          if (updated.reports.final_trade_decision) {
            state.tab = "final_trade_decision";
            state.lastReport = "";
            renderRun();
          }
        }
      }
    }
    text($("#engine-version"), `v${state.meta.version}`);
    state.pollFailures = 0;
    succeeded = true;
  } catch (e) {
    text($("#engine-version"), "连接中断");
    state.pollFailures++;
  } finally {
    state.polling = false;
    clearTimeout(pollTimer);
    const delay = succeeded
      ? 1800
      : Math.min(30000, 1800 * 2 ** Math.min(state.pollFailures, 4));
    pollTimer = setTimeout(poll, delay);
  }
}
async function loginChatGPT() {
  let opened = false;
  const button = $("#chatgpt-login");
  button.disabled = true;
  const popup = window.open("about:blank", "_blank");
  if (popup) {
    popup.document.title = "正在准备登录";
    popup.document.body.textContent = "正在准备 ChatGPT 登录链接，请稍候…";
    popup.opener = null;
  }
  try {
    await api("/api/login", {});
    text($("#login-feedback"), "正在准备登录链接…");
    const deadline = Date.now() + 185000;
    while (Date.now() < deadline) {
      const login = await api("/api/login");
      if (login.url && !opened) {
        opened = true;
        if (popup && !popup.closed) popup.location.replace(login.url);
        const link = $("#login-link");
        link.href = login.url;
        link.hidden = false;
        text(
          $("#login-feedback"),
          "请在新窗口完成登录。若未打开窗口，点击下方链接。",
        );
      }
      if (login.status === "failed")
        throw new Error(login.error || "登录失败，请重试。");
      if (login.status === "completed") {
        state.meta = await api("/api/bootstrap");
        state.token = state.meta.token;
        text($("#login-feedback"), "登录成功");
        $("#login-link").hidden = true;
        settingsProvider(false);
        renderModelSummary();
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    throw new Error("登录超时，请重试。");
  } catch (e) {
    text($("#login-feedback"), e.message);
    if (popup && !popup.closed && !opened) popup.close();
  } finally {
    button.disabled = false;
  }
}
async function init() {
  paintIcons();
  setRounds(state.rounds);
  $("#analysis-date").value = localDate();
  $("#analysis-date").max = localDate();
  text(
    $("#today-label"),
    new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "long",
      day: "numeric",
      weekday: "short",
    }).format(new Date()),
  );
  $("#analysis-form").addEventListener("submit", startAnalysis);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#provider").addEventListener("change", () => settingsProvider(true));
  $("#asset-type").addEventListener("change", () => assetChanged());
  $("#history-search").addEventListener("input", renderHistory);
  ["#configure-model", "#settings-nav", "#settings-avatar"].forEach(
    (id) =>
      ($(id).onclick = () => {
        if (state.meta) openSettings();
      }),
  );
  $$(".close-dialog").forEach(
    (b) =>
      (b.onclick = () => {
        $("#api-key").value = "";
        $("#settings-dialog").close();
      }),
  );
  ["#new-research", "#new-sidebar", "#history-new"].forEach(
    (id) => ($(id).onclick = newResearch),
  );
  $$("[data-view]").forEach(
    (b) => (b.onclick = () => showView(b.dataset.view)),
  );
  $$("[data-rounds]").forEach(
    (b) => (b.onclick = () => setRounds(Number(b.dataset.rounds))),
  );
  $$("[data-ticker]").forEach(
    (b) =>
      (b.onclick = () => {
        $("#ticker").value = b.dataset.ticker;
        $("#asset-type").value = "stock";
        assetChanged();
        $("#ticker").focus();
      }),
  );
  $("#cancel-run").onclick = async () => {
    if (!active(state.run)) return;
    try {
      await api(`/api/runs/${state.run.id}/cancel`, {});
      await poll();
    } catch (e) {
      toast(e.message);
    }
  };
  $("#chatgpt-login").onclick = loginChatGPT;
  $("#reuse-run")?.addEventListener("click", () => reuseRun(state.run));
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "1") {
      e.preventDefault();
      showView("workspace");
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "2") {
      e.preventDefault();
      showView("history");
    }
    if ((e.metaKey || e.ctrlKey) && e.key === ",") {
      e.preventDefault();
      if (state.meta) openSettings();
    }
  });
  try {
    state.meta = await api("/api/bootstrap");
    state.token = state.meta.token;
    state.runs = state.meta.runs;
    const prefs = state.meta.preferences;
    state.provider = prefs.provider || "openai";
    const d = defaults(state.provider);
    state.quick_model = prefs.quick_model || d.quick;
    state.deep_model = prefs.deep_model || d.deep;
    state.backend_url = prefs.backend_url || "";
    text($("#engine-version"), `v${state.meta.version}`);
    renderModelSummary();
    renderRecents();
    renderRun();
    const ongoing = state.runs.find(active);
    const remembered = state.runs.find((r) => r.id === recalledRun());
    if (ongoing || remembered) await selectRun((ongoing || remembered).id);
    pollTimer = setTimeout(poll, 1800);
    setInterval(updateElapsed, 1000);
  } catch (e) {
    text($("#engine-version"), "无法连接");
    toast(`本地服务连接失败：${e.message}`);
    $("#start-analysis").disabled = true;
  }
}
if (typeof module !== "undefined" && module.exports)
  module.exports = {
    reportTabTarget,
    reportRenderKey,
    reportPlaceholder,
    api,
    selectRun,
    state,
  };
if (typeof document !== "undefined") init();
