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
  historyRuns: [],
  historyCursor: null,
  historyOffset: 0,
  historyHasMore: false,
  historyTotal: null,
  historyLoading: false,
  historyError: "",
  historyMode: "cursor",
  historyQuery: "",
  historyRequestVersion: 0,
  pollFailures: 0,
};
let selectionVersion = 0;
let runsRequestVersion = 0;
let settingsSessionVersion = 0;
let settingsSaveVersion = 0;
let settingsRefreshVersion = 0;
let processRunId = null;
let previousProcessWasActive = false;
let pollTimer = null;
let historySearchTimer = null;
function settingsRequestIsCurrent(snapshot, current) {
  return (
    snapshot.session === current.session &&
    snapshot.provider === current.provider &&
    snapshot.requestVersion === current.requestVersion &&
    current.open === true
  );
}
function normalizeRunPage(result, limit = HISTORY_PAGE_SIZE) {
  if (Array.isArray(result)) {
    return {
      items: result,
      mode: "offset",
      nextCursor: null,
      hasMore: result.length === limit,
      total: null,
    };
  }
  if (!result || !Array.isArray(result.items)) return null;
  return {
    items: result.items,
    mode: "cursor",
    nextCursor: result.next_cursor ?? null,
    hasMore: Boolean(result.has_more),
    total:
      typeof result.total === "number" && Number.isSafeInteger(result.total)
        ? result.total
        : null,
  };
}
function orderReportKeys(keys) {
  return keys.includes("final_trade_decision")
    ? ["final_trade_decision", ...keys.filter((key) => key !== "final_trade_decision")]
    : [...keys];
}
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
const number = (v) =>
  typeof v === "number" && Number.isFinite(v)
    ? v.toLocaleString("zh-CN")
    : "—";
const HISTORY_PAGE_SIZE = 100;
const ACTIVE_POLL_INTERVAL = 2800;
const IDLE_POLL_INTERVAL = 30000;
const CUSTOM_MODEL_VALUE = "__tradingagents_custom_model__";
const cryptoBases = new Set([
  "BTC",
  "ETH",
  "SOL",
  "XRP",
  "ADA",
  "DOGE",
  "LTC",
  "BCH",
  "DOT",
  "AVAX",
  "LINK",
]);
function inferAssetType(ticker) {
  const symbol = String(ticker || "")
    .trim()
    .toUpperCase()
    .replace(/\++$/, "");
  if (/-\b(?:USD|USDT|USDC|BTC|ETH)$/.test(symbol)) return "crypto";
  const compact = symbol.replaceAll("-", "");
  return ["USDT", "USDC", "USD"].some((quote) => {
    const base = compact.endsWith(quote) ? compact.slice(0, -quote.length) : "";
    return cryptoBases.has(base);
  })
    ? "crypto"
    : "stock";
}
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
  renderSetupSummary();
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
    quick: p?.models?.quick?.find((m) => m.id !== "custom")?.id || "",
    deep: p?.models?.deep?.find((m) => m.id !== "custom")?.id || "",
  };
}
function modelChoices(provider, tier) {
  const models = providerInfo(provider)?.models?.[tier] || [];
  const choices = [];
  const seen = new Set();
  for (const model of models) {
    if (!model || typeof model.id !== "string" || !model.id) continue;
    if (model.id === "custom") continue;
    if (seen.has(model.id)) continue;
    seen.add(model.id);
    choices.push({ label: String(model.label || model.id), id: model.id });
  }
  return choices;
}
function setModelSelect(tier, selected = "", customValue = "") {
  const provider = $("#provider").value;
  const select = $(`#${tier}-model`);
  const customInput = $(`#${tier}-model-custom`);
  const choices = modelChoices(provider, tier);
  const value =
    selected ||
    choices[0]?.id ||
    (provider === "chatgpt" ? "" : CUSTOM_MODEL_VALUE);
  if (
    value &&
    value !== CUSTOM_MODEL_VALUE &&
    !choices.some((choice) => choice.id === value)
  ) {
    choices.push({ label: `已保存 · ${value}`, id: value });
  }
  select.replaceChildren(
    ...(value ? [] : [new Option("刷新可用模型后选择…", "")]),
    ...choices.map((choice) => new Option(choice.label, choice.id)),
    new Option("自定义模型 ID…", CUSTOM_MODEL_VALUE),
  );
  select.value = value;
  const isCustom = select.value === CUSTOM_MODEL_VALUE;
  customInput.hidden = !isCustom;
  customInput.disabled = !isCustom;
  customInput.value = isCustom ? customValue : "";
}
function selectedModel(tier) {
  const value = $(`#${tier}-model`).value;
  return value === CUSTOM_MODEL_VALUE
    ? $(`#${tier}-model-custom`).value.trim()
    : value;
}
function openSettings() {
  settingsSessionVersion++;
  settingsSaveVersion++;
  settingsRefreshVersion++;
  $("#save-settings").disabled = false;
  $("#refresh-models").disabled = false;
  $("#provider").disabled = false;
  const provider = $("#provider");
  provider.replaceChildren();
  state.meta.providers.forEach((p) => {
    const o = new Option(p.name, p.id);
    provider.add(o);
  });
  provider.value = state.provider;
  $("#api-key").value = "";
  $("#backend-url").value = state.backend_url;
  settingsProvider(false);
  inlineError("#settings-error", "");
  $("#settings-dialog").showModal();
}
function invalidateSettingsSession() {
  settingsSessionVersion++;
  settingsSaveVersion++;
  settingsRefreshVersion++;
  $("#api-key").value = "";
  $("#save-settings").disabled = false;
  $("#refresh-models").disabled = false;
  $("#provider").disabled = false;
}
function closeSettingsDialog() {
  invalidateSettingsSession();
  $("#settings-dialog").close();
}
function currentSettingsSession(session, provider, requestVersion, kind) {
  return settingsRequestIsCurrent(
    { session, provider, requestVersion },
    {
      session: settingsSessionVersion,
      provider: $("#provider").value,
      requestVersion: kind === "save" ? settingsSaveVersion : settingsRefreshVersion,
      open: $("#settings-dialog").open,
    },
  );
}
function settingsProvider(reset) {
  const p = providerInfo($("#provider").value);
  const d = defaults(p.id);
  if (reset) {
    $("#api-key").value = "";
    $("#backend-url").value =
      p.id === "ollama" ? "http://localhost:11434/v1" : "";
  }
  for (const tier of ["quick", "deep"]) {
    const selected = reset ? d[tier] : state[`${tier}_model`];
    setModelSelect(tier, selected || "", "");
  }
  $("#refresh-models").hidden = p.id !== "chatgpt";
  text(
    $("#models-feedback"),
    p.id === "chatgpt" ? "可刷新当前 ChatGPT 账户能够使用的模型。" : "",
  );
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
  const session = settingsSessionVersion;
  const requestVersion = ++settingsSaveVersion;
  const provider = $("#provider").value;
  inlineError("#settings-error", "");
  $("#save-settings").disabled = true;
  try {
    const quickModel = selectedModel("quick");
    const deepModel = selectedModel("deep");
    if (!quickModel || !deepModel) {
      inlineError("#settings-error", "请选择模型，或填写自定义模型 ID。");
      const tier = !quickModel ? "quick" : "deep";
      const select = $(`#${tier}-model`);
      $(
        select.value === CUSTOM_MODEL_VALUE
          ? `#${tier}-model-custom`
          : `#${tier}-model`,
      ).focus();
      return;
    }
    const payload = {
      provider,
      api_key: $("#api-key").value,
      quick_model: quickModel,
      deep_model: deepModel,
      backend_url: $("#backend-url").value.trim(),
    };
    const meta = await api("/api/settings", payload);
    if (!currentSettingsSession(session, provider, requestVersion, "save"))
      return;
    state.meta = meta;
    Object.assign(state, meta.preferences);
    closeSettingsDialog();
    renderModelSummary();
    toast("模型配置已保存到本机");
  } catch (e) {
    if (currentSettingsSession(session, provider, requestVersion, "save"))
      inlineError("#settings-error", e.message);
  } finally {
    if (currentSettingsSession(session, provider, requestVersion, "save"))
      $("#save-settings").disabled = false;
  }
}
async function refreshModels() {
  const button = $("#refresh-models");
  const providerSelect = $("#provider");
  const provider = $("#provider").value;
  const session = settingsSessionVersion;
  const requestVersion = ++settingsRefreshVersion;
  button.disabled = true;
  providerSelect.disabled = true;
  text($("#models-feedback"), "正在读取账户可用模型…");
  try {
    const result = await api(
      `/api/models?provider=${encodeURIComponent(provider)}`,
    );
    if (!currentSettingsSession(session, provider, requestVersion, "refresh"))
      return;
    if (!result.models?.quick?.length || !result.models?.deep?.length) {
      throw new Error("服务没有返回可用模型，请稍后重试。");
    }
    if (result.provider && result.provider !== provider) {
      throw new Error("服务返回了其他模型服务的结果，请重试。");
    }
    const currentProvider = providerInfo(provider);
    currentProvider.models = {
      ...currentProvider.models,
      quick: result.models.quick,
      deep: result.models.deep,
    };
    for (const tier of ["quick", "deep"]) {
      const selected = $(`#${tier}-model`).value;
      const custom = $(`#${tier}-model-custom`).value;
      setModelSelect(
        tier,
        selected === CUSTOM_MODEL_VALUE && !custom.trim() ? "" : selected,
        custom,
      );
    }
    text($("#models-feedback"), "已更新当前账户可用的模型。");
  } catch (error) {
    if (currentSettingsSession(session, provider, requestVersion, "refresh"))
      text($("#models-feedback"), error.message);
  } finally {
    if (currentSettingsSession(session, provider, requestVersion, "refresh")) {
      button.disabled = false;
      providerSelect.disabled = false;
    }
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
  if (view === "history") {
    renderHistory();
    loadHistoryPage(true);
  }
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
  $("#analysis-settings").open = true;
  $("#ticker").value = "";
  assetChanged(true);
  $("#analysis-date").value = localDate();
  inlineError("#form-error", "");
  renderRun();
  renderRecents();
  $("#ticker").focus();
}
function syncForm(config) {
  $("#ticker").value = config.ticker;
  $("#analysis-date").value = config.date;
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
  renderSetupSummary();
}
function assetChanged(shouldCheck = true) {
  const crypto = inferAssetType($("#ticker").value) === "crypto";
  const fundamental = $("input[value=fundamentals]");
  if (crypto) {
    fundamental.checked = false;
    fundamental.disabled = true;
    text($("#asset-hint"), "已识别为加密资产，已关闭公司基本面分析。");
  } else {
    const wasDisabled = fundamental.disabled;
    fundamental.disabled = false;
    if (wasDisabled && shouldCheck) fundamental.checked = true;
    text(
      $("#asset-hint"),
      $("#ticker").value.trim()
        ? "将自动识别标的类型，并选择相应的研究方式。"
        : "输入代码后自动识别标的类型，无需选择资产类型。",
    );
  }
  renderSetupSummary();
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
    state.runs = [run, ...state.runs.filter((r) => r.id !== run.id)].slice(0, 5);
    if (!state.historyQuery) {
      state.historyRuns = mergeRunsById(state.historyRuns, [run]);
      if (state.historyTotal !== null) state.historyTotal++;
      state.historySignature = null;
    }
    if (selectionVersion === startSelection) {
      state.run = run;
      rememberRun(run.id);
      $("#analysis-settings").open = false;
      state.tab = null;
      state.lastReport = "";
      state.tabSignature = null;
      state.eventsSignature = "";
      renderRun();
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, ACTIVE_POLL_INTERVAL);
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
function canResumeRun(run) {
  return !!run && ["interrupted", "failed", "cancelled"].includes(run.status);
}
function decisionRating(run) {
  if (run?.quality?.status === "unavailable") return "资料不足 · 暂不评级";
  return run?.rating
    ? `${ratings[run.rating] || run.rating} · ${run.rating}`
    : "评级待生成";
}
function decisionQualityWarning(run) {
  if (run?.quality?.status !== "unavailable") return "";
  return `资料不足，当前评级暂不采纳。${run.quality.summary ? ` ${run.quality.summary}` : ""}`;
}
function plainReportText(value) {
  return String(value)
    .replace(/^\s{0,3}#{1,6}\s+/, "")
    .replace(/^\s{0,3}[-*+]\s+/, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_`>#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
function isReportMetadata(value) {
  const plain = plainReportText(value);
  if (/^(?:final\s+(?:trade\s+)?decision|trade\s+decision|最终决策|最终结论)$/i.test(plain))
    return true;
  return /^(?:rating|time\s*horizon|investment\s*horizon|confidence|recommendation|ticker|symbol|date|评级|投资期限|时间范围|置信度|建议|标的|日期)(?:\s*(?::|：|[-–—])\s*.*)?$/i.test(plain);
}
function summaryCandidate(paragraphs) {
  const blocks = paragraphs.map(plainReportText).filter(Boolean);
  const label = /^(?:executive\s+summary|summary|执行摘要|结论摘要|投资摘要|摘要)\s*[:：\-–—]?\s*(.*)$/i;
  for (let index = 0; index < blocks.length; index += 1) {
    const match = blocks[index].match(label);
    if (!match) continue;
    if (match[1] && !isReportMetadata(match[1])) return match[1];
    for (const next of blocks.slice(index + 1)) {
      if (isReportMetadata(next)) continue;
      return next;
    }
  }
  return blocks.find((paragraph) => !isReportMetadata(paragraph)) || "";
}
function decisionSummary(run) {
  const source = run?.reports?.final_trade_decision;
  if (typeof source !== "string" || !source.trim()) return "";
  const rendered = run?.rendered?.final_trade_decision;
  if (typeof document !== "undefined" && typeof rendered === "string") {
    const template = document.createElement("template");
    template.innerHTML = rendered;
    const paragraphs = [...template.content.querySelectorAll("h1,h2,h3,p")].map(
      (node) => node.textContent.trim(),
    );
    const paragraph = summaryCandidate(paragraphs);
    if (paragraph) return paragraph.slice(0, 420).trimEnd() + (paragraph.length > 420 ? "…" : "");
  }
  const blocks = source
    .split(/\r?\n\s*\r?\n/)
    .map((block) => block.replace(/\r?\n/g, " ").trim())
    .filter((block) => block && !/^```/.test(block) && !/^\|/.test(block) && !/^[-*_]{3,}$/.test(block));
  const plain = summaryCandidate(blocks);
  if (!plain) return "";
  return plain.slice(0, 420).trimEnd() + (plain.length > 420 ? "…" : "");
}
function usageStatus(stats = {}) {
  const validCount = (value) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
  const knownCalls = validCount(stats.usage_known_calls)
    ? stats.usage_known_calls
    : null;
  const totalCalls = validCount(stats.llm_calls) ? stats.llm_calls : null;
  const hasFlags =
    typeof stats.usage_reported === "boolean" ||
    typeof stats.usage_complete === "boolean" ||
    knownCalls !== null;
  if (!hasFlags) {
    const hasValues = validCount(stats.tokens_in) || validCount(stats.tokens_out);
    return {
      state: hasValues ? "unknown" : "unreported",
      label: hasValues ? "用量状态未验证" : "未报告用量",
    };
  }
  if (stats.usage_reported === false || knownCalls === 0) {
    return { state: "unreported", label: "未报告用量" };
  }
  if (stats.usage_complete === true) {
    return { state: "complete", label: "完整用量统计" };
  }
  if (stats.usage_reported === true || (knownCalls !== null && knownCalls > 0)) {
    const label =
      knownCalls !== null && totalCalls !== null
        ? `部分统计 · ${number(knownCalls)} / ${number(totalCalls)} 次模型调用报告用量`
        : knownCalls !== null
          ? `部分统计 · ${number(knownCalls)} 次模型调用报告用量 · 总调用数未知`
          : "部分统计 · 有调用报告用量 · 总调用数未知";
    return {
      state: "partial",
      label,
    };
  }
  return { state: "unreported", label: "未报告用量" };
}
function setupSummaryText() {
  const config = state.run?.config;
  const ticker = $("#ticker")?.value.trim() || "未选择标的";
  const date = $("#analysis-date")?.value || "日期未设置";
  const provider = config
    ? state.meta?.providers?.find((item) => item.id === config.provider)?.name ||
      config.provider
    : $("#selected-provider")?.textContent.trim() || "模型未配置";
  const models = config
    ? [config.quick_model, config.deep_model].filter(Boolean).join(" / ")
    : $("#selected-models")?.textContent.trim() || "";
  const compactModel = models.length > 48 ? `${models.slice(0, 45)}…` : models;
  return [ticker, date, provider, compactModel]
    .filter((value) => value && !value.startsWith("先连接模型"))
    .join(" · ");
}
function renderSetupSummary() {
  text($("#setup-brief"), setupSummaryText());
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
      tabs.querySelector(`[data-report-key="${focusedKey}"]`)?.focus({ preventScroll: true });
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
  renderSetupSummary();
  const processDetails = $("#process-details");
  if (run?.id !== processRunId) {
    processDetails.open = !!active(run);
    processRunId = run?.id || null;
  } else if (previousProcessWasActive && !active(run)) {
    processDetails.open = false;
  }
  previousProcessWasActive = !!active(run);
  renderPipeline();
  const badge = $("#run-status");
  badge.className = "status-tag " + (run?.status || "");
  $(".activity-panel").classList.toggle("is-complete", run?.status === "completed");
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
    let note = "";
    if (run) {
      const stats = run.stats || {};
      if (id === "llm") value = number(stats.llm_calls);
      if (id === "tools") value = number(stats.tool_calls);
      if (id === "tokens") {
        const hasInput = Number.isSafeInteger(stats.tokens_in) && stats.tokens_in >= 0;
        const hasOutput = Number.isSafeInteger(stats.tokens_out) && stats.tokens_out >= 0;
        value =
          !hasInput && !hasOutput
            ? "未报告"
            : `${hasInput ? number(stats.tokens_in) : "未报告"} / ${hasOutput ? number(stats.tokens_out) : "未报告"}`;
        note = usageStatus(stats).label;
      }
    }
    text($(`#stat-${id}`), value);
    if (id === "tokens") text($("#stat-tokens-status"), note);
  });
  $("#report-empty").hidden = !!run;
  $("#report-content").hidden = !run;
  text(
    $("#report-subtitle"),
    run ? `${run.config.ticker} / ${run.config.date}` : "RESEARCH BRIEF",
  );
  const reportData = run?.reports || {};
  const hasReports = run && Object.keys(reportData).length > 0;
  for (const ext of ["md", "html"]) {
    const link = $(`#export-${ext}`);
    link.hidden = !hasReports;
    if (run) {
      link.href = `/api/runs/${run.id}/report.${ext}`;
      link.download = `${run.config.ticker}-${run.config.date}.${ext}`;
    }
  }
  const decision = $("#decision-banner");
  const hasFinalDecision = !!reportData.final_trade_decision;
  decision.hidden = !hasFinalDecision;
  text(
    $("#decision-rating"),
    decisionRating(run),
  );
  const qualityWarning = $("#decision-quality-warning");
  const hasUnavailableEvidence = run?.quality?.status === "unavailable";
  qualityWarning.hidden = !hasUnavailableEvidence;
  text(qualityWarning, decisionQualityWarning(run));
  text(
    $("#decision-summary"),
    decisionSummary(run) || "完整结论请查看下方最终结论章节。",
  );
  const finalButton = $("#view-final-decision");
  finalButton.hidden = !hasFinalDecision;
  $("#research-overview").hidden = !run;
  $("#research-visuals").hidden = !run;
  const evidenceExport = $("#export-evidence");
  const evidenceCount = run?.quality?.evidence_count;
  evidenceExport.hidden =
    !Number.isSafeInteger(evidenceCount) || evidenceCount <= 0;
  if (!evidenceExport.hidden) {
    evidenceExport.href = `/api/runs/${encodeURIComponent(run.id)}/evidence.json`;
    evidenceExport.download = `${run.config.ticker}-${run.config.date}-evidence.json`;
  }
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
        `${run.error || "本次研究未能完成。"} ${canResumeRun(run) ? "可继续本次研究，也可以复用配置后重新开始。" : "你可以复用配置后重新开始。"}`,
      ),
    );
    if (canResumeRun(run)) {
      const resume = document.createElement("button");
      resume.type = "button";
      resume.className = "button small ghost";
      resume.textContent = "继续研究";
      resume.setAttribute("aria-label", `继续 ${run.config.ticker} 的研究`);
      resume.onclick = resumeRun;
      error.append(resume);
    }
  }
  const keys = orderReportKeys(
    Object.keys(state.meta?.labels || {}).filter((k) => reportData[k]),
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
  renderVisuals();
}
function renderVisuals() {
  if (typeof window === "undefined") return;
  window.TradingVisuals?.render?.({
    run: state.run,
    runs: state.runs,
    labels: state.meta?.labels || {},
  });
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
  $("#analysis-settings").open = false;
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
  $("#analysis-settings").open = true;
  toast("已载入这次研究的配置，点击“开始研究”重试。");
  $("#analysis-form").scrollIntoView({ behavior: "smooth", block: "center" });
}
async function resumeRun(event) {
  const run = state.run;
  if (!canResumeRun(run)) return false;
  const selection = selectionVersion;
  const button = event?.currentTarget;
  if (button) button.disabled = true;
  try {
    const updated = await api(`/api/runs/${encodeURIComponent(run.id)}/resume`, {});
    if (selection !== selectionVersion || state.run?.id !== run.id) return false;
    state.run = updated;
    state.runs = mergeRunsById(state.runs, [updated]).slice(0, 5);
    state.historyRuns = mergeRunsById(state.historyRuns, [updated]);
    state.historySignature = null;
    state.eventsSignature = "";
    state.lastReport = "";
    renderRun();
    renderRecents();
    if (state.view === "history") renderHistory();
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, ACTIVE_POLL_INTERVAL);
    toast("已继续研究，已有报告会保留在本次记录中");
    return true;
  } catch (error) {
    if (selection === selectionVersion && state.run?.id === run.id) {
      const message = document.createElement("p");
      message.textContent = error.message;
      $("#run-error").append(message);
      if (button) button.disabled = false;
    }
    return false;
  }
}
let recentKey = "";
function renderRecents() {
  const key = JSON.stringify([
    state.runs.map((r) => [r.id, r.status]),
    state.run?.id || "",
    state.historyQuery ? null : state.historyTotal,
  ]);
  if (key === recentKey) return;
  recentKey = key;
  const count = state.historyQuery ? null : state.historyTotal;
  text(
    $("#history-count"),
    count === null ? (state.runs.length >= 5 ? "5+" : number(state.runs.length)) : number(count),
  );
  text(
    $("#archive-count"),
    count === null ? `最近 ${state.runs.length} 份` : `${number(count)} 份`,
  );
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
  const runs = state.historyRuns.filter((r) =>
    `${r.config.ticker} ${r.config.provider} ${providerInfo(r.config.provider)?.name || ""} ${r.config.quick_model || ""} ${r.config.deep_model || ""}`
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
    state.historyLoading,
    state.historyHasMore,
    state.historyTotal,
  ]);
  const loadMore = $("#history-load-more");
  loadMore.hidden = !state.historyHasMore && !state.historyError;
  loadMore.disabled = state.historyLoading;
  text(
    loadMore,
    state.historyLoading
      ? "正在加载…"
      : state.historyError
        ? "重试加载"
        : "加载更多研究",
  );
  text(
    $("#history-load-status"),
    state.historyLoading
      ? "正在载入研究档案…"
      : state.historyError ||
        (state.historyTotal === null
          ? `${runs.length} 份已载入${state.historyHasMore ? " · 还有更多记录" : ""}`
          : `${number(runs.length)} / ${number(state.historyTotal)} 份研究`),
  );
  if (signature === state.historySignature) return;
  state.historySignature = signature;
  const focusedRunId = document.activeElement?.dataset?.runId;
  const holder = $("#history-rows");
  holder.replaceChildren();
  $("#history-empty").hidden = runs.length > 0 || state.historyLoading;
  text(
    $("#history-empty"),
    state.historyError
      ? "读取失败，可以重试加载。"
      : query
      ? state.historyLoading
        ? "正在搜索研究档案…"
        : "没有匹配的研究记录。"
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
    holder.querySelector(`[data-run-id="${focusedRunId}"]`)?.focus({ preventScroll: true });
  renderVisuals();
}
function mergeRunsById(current, incoming) {
  const merged = new Map();
  for (const run of [...current, ...incoming]) {
    if (run?.id) merged.set(run.id, { ...merged.get(run.id), ...run });
  }
  return [...merged.values()].sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
}
async function loadHistoryPage(reset = false) {
  const searchInput = $("#history-search");
  if (!searchInput) return;
  const query = searchInput.value.trim();
  if (
    !reset &&
    ((!state.historyHasMore && !state.historyError) || state.historyLoading)
  )
    return;
  const requestVersion = reset
    ? ++state.historyRequestVersion
    : state.historyRequestVersion;
  if (reset) {
    state.historyRuns = [];
    state.historyCursor = null;
    state.historyOffset = 0;
    state.historyHasMore = false;
    state.historyTotal = null;
    state.historyMode = "cursor";
    state.historyQuery = query;
    state.historyError = "";
    state.historySignature = null;
  }
  state.historyLoading = true;
  renderHistory();
  const params = new URLSearchParams({ limit: String(HISTORY_PAGE_SIZE) });
  if (state.historyMode === "cursor")
    params.set("cursor", reset ? "first" : state.historyCursor || "first");
  else params.set("offset", String(state.historyOffset));
  if (query) params.set("q", query);
  try {
    const result = await api(`/api/runs?${params.toString()}`);
    if (requestVersion !== state.historyRequestVersion) return;
    const page = normalizeRunPage(result, HISTORY_PAGE_SIZE);
    if (!page) throw new Error("研究档案响应格式不正确。");
    state.historyError = "";
    const items = page.items;
    const beforeCount = state.historyRuns.length;
    state.historyRuns = reset
      ? mergeRunsById([], items)
      : mergeRunsById(state.historyRuns, items);
    state.historyOffset += items.length;
    state.historyMode = page.mode;
    state.historyCursor = page.nextCursor;
    state.historyHasMore = page.hasMore;
    if (page.total !== null) state.historyTotal = page.total;
    else if (!state.historyHasMore)
      state.historyTotal = state.historyRuns.length;
    if (!reset && state.historyRuns.length === beforeCount)
      state.historyHasMore = false;
    if (state.historyQuery === "") renderRecents();
  } catch (error) {
    if (requestVersion === state.historyRequestVersion) {
      state.historyError = error.message;
      state.historyHasMore = state.historyRuns.length > 0;
    }
  } finally {
    if (requestVersion === state.historyRequestVersion) {
      state.historyLoading = false;
      state.historySignature = null;
      renderHistory();
    }
  }
}
async function refreshRuns() {
  const requestVersion = ++runsRequestVersion;
  const result = await api("/api/runs?limit=5&offset=0");
  const runs = Array.isArray(result) ? result : result?.items || [];
  if (requestVersion !== runsRequestVersion) return state.runs;
  state.runs = mergeRunsById([], runs).slice(0, 5);
  if (state.historyQuery === "" && state.historyRuns.length)
    state.historyRuns = mergeRunsById(state.historyRuns, runs);
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
    const activeWork = state.runs.some(active) || active(state.run);
    const baseDelay = activeWork ? ACTIVE_POLL_INTERVAL : IDLE_POLL_INTERVAL;
    const delay = succeeded
      ? document.visibilityState === "hidden"
        ? Math.max(baseDelay, 60000)
        : baseDelay
      : Math.min(60000, baseDelay * 2 ** Math.min(state.pollFailures, 4));
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
  $("#provider").addEventListener("change", () => {
    settingsSessionVersion++;
    settingsSaveVersion++;
    settingsRefreshVersion++;
    settingsProvider(true);
  });
  $("#ticker").addEventListener("input", () => assetChanged());
  $("#analysis-date").addEventListener("input", renderSetupSummary);
  $("#analysis-settings").addEventListener("toggle", renderSetupSummary);
  $("#history-search").addEventListener("input", () => {
    state.historyRequestVersion++;
    state.historyLoading = false;
    state.historySignature = null;
    renderHistory();
    clearTimeout(historySearchTimer);
    if (state.view === "history")
      historySearchTimer = setTimeout(() => loadHistoryPage(true), 220);
  });
  $("#history-load-more").addEventListener("click", () => loadHistoryPage(false));
  $("#view-final-decision").addEventListener("click", () => {
    if (!state.run?.reports?.final_trade_decision) return;
    activateReportTab("final_trade_decision");
    $(".report-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  [
    "#configure-model",
    "#research-settings",
    "#settings-nav",
    "#settings-avatar",
  ].forEach(
    (id) =>
      ($(id).onclick = () => {
        if (state.meta) openSettings();
      }),
  );
  $$(".close-dialog").forEach(
    (b) =>
      (b.onclick = () => {
        closeSettingsDialog();
      }),
  );
  $("#settings-dialog").addEventListener("cancel", invalidateSettingsSession);
  ["#new-research", "#new-sidebar", "#history-new"].forEach(
    (id) => ($(id).onclick = newResearch),
  );
  $$("[data-view]").forEach(
    (b) => (b.onclick = () => showView(b.dataset.view)),
  );
  $$("[data-rounds]").forEach(
    (b) => (b.onclick = () => setRounds(Number(b.dataset.rounds))),
  );
  ["quick", "deep"].forEach((tier) => {
    $(`#${tier}-model`).addEventListener("change", () =>
      setModelSelect(tier, $(`#${tier}-model`).value),
    );
  });
  $("#refresh-models").addEventListener("click", refreshModels);
  $$("[data-ticker]").forEach(
    (b) =>
      (b.onclick = () => {
        $("#ticker").value = b.dataset.ticker;
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
  $("#resume-run")?.addEventListener("click", resumeRun);
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
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !state.meta) return;
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, 0);
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
    pollTimer = setTimeout(
      poll,
      state.runs.some(active) || active(state.run)
        ? ACTIVE_POLL_INTERVAL
        : IDLE_POLL_INTERVAL,
    );
    setInterval(updateElapsed, 1000);
  } catch (e) {
    text($("#engine-version"), "无法连接");
    toast(`本地服务连接失败：${e.message}`);
    $("#start-analysis").disabled = true;
  }
}
if (typeof module !== "undefined" && module.exports)
  module.exports = {
    canResumeRun,
    decisionQualityWarning,
    decisionRating,
    decisionSummary,
    usageStatus,
    normalizeRunPage,
    orderReportKeys,
    reportTabTarget,
    reportRenderKey,
    reportPlaceholder,
    settingsRequestIsCurrent,
    inferAssetType,
    modelChoices,
    CUSTOM_MODEL_VALUE,
    api,
    selectRun,
    renderReportTabs,
    state,
  };
if (typeof document !== "undefined") init();
