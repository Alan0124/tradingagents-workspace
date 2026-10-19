/* Charts use only run data and an explicit Yahoo Finance request. */
"use strict";

(function installTradingVisuals(root) {
  const ANALYST_REPORT_KEYS = {
    market: "market_report",
    fundamentals: "fundamentals_report",
    news: "news_report",
    social: "sentiment_report",
  };
  const ANALYST_FALLBACK_LABELS = {
    market: "技术面分析",
    fundamentals: "基本面分析",
    news: "新闻分析",
    social: "市场情绪",
  };
  const TOKEN_FIELDS = [
    { key: "tokens_in", label: "输入 Token", color: "#cbb58c" },
    { key: "tokens_out", label: "输出 Token", color: "#91adbe" },
  ];

  function isIsoDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value))
      return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    );
  }

  function prepareMarketSeries(series, asOf) {
    if (!Array.isArray(series)) return [];
    const limit = isIsoDate(asOf) ? asOf : null;
    const byDate = new Map();
    for (const point of series) {
      if (!point || !isIsoDate(point.date)) continue;
      if (limit && point.date > limit) continue;
      if (typeof point.close !== "number" || !Number.isFinite(point.close))
        continue;
      byDate.set(point.date, { date: point.date, close: point.close });
    }
    return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  }

  function buildMarketGeometry(series, width = 720, height = 260) {
    const points = prepareMarketSeries(series);
    if (!points.length) return null;
    const plot = { left: 64, top: 18, right: 18, bottom: 38 };
    const plotWidth = width - plot.left - plot.right;
    const plotHeight = height - plot.top - plot.bottom;
    let min = Math.min(...points.map((point) => point.close));
    let max = Math.max(...points.map((point) => point.close));
    if (min === max) {
      const padding = Math.max(Math.abs(min) * 0.01, 1);
      min -= padding;
      max += padding;
    }
    const coordinates = points.map((point, index) => {
      const x =
        points.length === 1
          ? plot.left + plotWidth / 2
          : plot.left + (index * plotWidth) / (points.length - 1);
      const y = plot.top + ((max - point.close) / (max - min)) * plotHeight;
      return { ...point, x, y };
    });
    return {
      width,
      height,
      plot,
      min,
      max,
      points: coordinates,
      yTicks: [max, (max + min) / 2, min].map((value, index) => ({
        value,
        y: plot.top + (index * plotHeight) / 2,
      })),
    };
  }

  function buildCoverageItems(run, labels = {}) {
    const config = run?.config;
    const reports = run?.reports || {};
    if (!config || !Array.isArray(config.analysts)) return [];
    return [...new Set(config.analysts)]
      .filter((key) => Object.hasOwn(ANALYST_REPORT_KEYS, key))
      .map((key) => {
        const reportKey = ANALYST_REPORT_KEYS[key];
        const label = labels[reportKey] || ANALYST_FALLBACK_LABELS[key];
        const report = reports[reportKey];
        return {
          key,
          reportKey,
          label: String(label),
          complete: typeof report === "string" && report.trim().length > 0,
        };
      });
  }

  function buildUsageItems(run) {
    const stats = run?.stats;
    return TOKEN_FIELDS.map((field) => {
      const value = stats?.[field.key];
      const available =
        typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
      return {
        ...field,
        available,
        value: available ? value : null,
      };
    });
  }

  function escapeHtml(value) {
    return String(value).replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  }

  function numberText(value) {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(
      value,
    );
  }

  function countText(value) {
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(
      value,
    );
  }

  function priceText(value, currency) {
    if (typeof currency === "string" && /^[A-Z]{3}$/.test(currency)) {
      try {
        return new Intl.NumberFormat("zh-CN", {
          style: "currency",
          currency,
          maximumFractionDigits: 2,
        }).format(value);
      } catch (_) {
        // Unknown currency codes fall back to the numeric close.
      }
    }
    return numberText(value);
  }

  function emptyState(message, kind = "") {
    return `<p class="chart-empty-state${kind ? ` ${kind}` : ""}" role="status">${escapeHtml(message)}</p>`;
  }

  function renderCoverage(run, labels) {
    const el = document.querySelector("#coverage-chart");
    if (!el) return;
    const items = buildCoverageItems(run, labels);
    if (!items.length) {
      el.innerHTML = emptyState(
        run
          ? "当前研究没有已选分析维度。"
          : "开始研究后，展示所选维度的报告完成情况。",
      );
      return;
    }
    const completeCount = items.filter((item) => item.complete).length;
    const height = 38 + items.length * 34;
    const rows = items
      .map((item, index) => {
        const y = 46 + index * 34;
        const status = item.complete ? "已完成" : "待生成";
        const fill = item.complete ? 176 : 0;
        const color = item.complete ? "#cbb58c" : "#586779";
        return `<g><text x="8" y="${y + 4}">${escapeHtml(item.label)}</text><rect x="150" y="${y - 8}" width="176" height="10" rx="5" fill="#373635"/><rect x="150" y="${y - 8}" width="${fill}" height="10" rx="5" fill="${color}"/><text x="342" y="${y + 4}">${status}</text></g>`;
      })
      .join("");
    el.innerHTML = `<svg viewBox="0 0 410 ${height}" role="img" aria-labelledby="coverage-title coverage-desc" preserveAspectRatio="xMinYMin meet"><title id="coverage-title">研究覆盖：${completeCount} / ${items.length} 个已选分析维度完成</title><desc id="coverage-desc">${items.map((item) => `${item.label}${item.complete ? "已完成" : "待生成"}`).join("，")}</desc><text x="8" y="20">已完成 ${completeCount} / ${items.length} 个已选维度</text>${rows}</svg>`;
  }

  function renderUsage(run) {
    const el = document.querySelector("#usage-chart");
    if (!el) return;
    const items = buildUsageItems(run);
    if (!items.some((item) => item.available && item.value > 0)) {
      el.innerHTML = emptyState("模型尚未返回实际 token 用量数据。");
      return;
    }
    const knownValues = items
      .filter((item) => item.available)
      .map((item) => item.value);
    const maxValue = Math.max(...knownValues, 1);
    const rows = items
      .map((item, index) => {
        const y = 40 + index * 43;
        if (!item.available) {
          return `<g><text x="8" y="${y + 4}">${item.label}</text><text x="154" y="${y + 4}">暂无数据</text></g>`;
        }
        const barWidth = (item.value / maxValue) * 220;
        return `<g><text x="8" y="${y + 4}">${item.label}</text><rect x="116" y="${y - 8}" width="220" height="12" rx="6" fill="#373635"/><rect x="116" y="${y - 8}" width="${barWidth.toFixed(2)}" height="12" rx="6" fill="${item.color}"/><text x="350" y="${y + 4}">${countText(item.value)}</text></g>`;
      })
      .join("");
    el.innerHTML = `<svg viewBox="0 0 430 126" role="img" aria-labelledby="usage-title usage-desc" preserveAspectRatio="xMinYMin meet"><title id="usage-title">模型实际 token 用量</title><desc id="usage-desc">${items.map((item) => `${item.label}：${item.available ? countText(item.value) : "暂无数据"}`).join("；")}</desc>${rows}</svg>`;
  }

  function renderMarket(data, run) {
    const el = document.querySelector("#market-chart");
    const sourceEl = document.querySelector("#market-chart-source");
    if (!el) return;
    const symbol = data.symbol || run?.config?.ticker || "标的";
    const asOf = data.as_of || run?.config?.date || "分析日期未知";
    const source = data.source || "Yahoo Finance";
    const currency = data.currency;
    if (sourceEl) {
      sourceEl.textContent = `${source} · ${symbol} · 未复权 · 原始报价 · 截至 ${asOf}${currency ? ` · ${currency}` : ""}`;
    }
    const points = prepareMarketSeries(data.series, asOf);
    if (!points.length) {
      el.innerHTML = emptyState(
        `Yahoo Finance 未返回截至 ${asOf} 可用的收盘价。`,
      );
      return;
    }
    const geometry = buildMarketGeometry(points);
    const path = geometry.points
      .map(
        (point, index) =>
          `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`,
      )
      .join(" ");
    const grid = geometry.yTicks
      .map(
        (tick) =>
          `<g><line x1="${geometry.plot.left}" x2="${geometry.width - geometry.plot.right}" y1="${tick.y.toFixed(2)}" y2="${tick.y.toFixed(2)}" stroke="#3b3935" stroke-dasharray="3 5"/><text x="${geometry.plot.left - 8}" y="${(tick.y + 4).toFixed(2)}" text-anchor="end">${escapeHtml(priceText(tick.value, currency))}</text></g>`,
      )
      .join("");
    const markers = geometry.points
      .map(
        (point) =>
          `<circle cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="2.4" fill="#cbb58c"><title>${escapeHtml(point.date)}：${escapeHtml(priceText(point.close, currency))}</title></circle>`,
      )
      .join("");
    const first = geometry.points[0];
    const last = geometry.points[geometry.points.length - 1];
    const min = Math.min(...points.map((point) => point.close));
    const max = Math.max(...points.map((point) => point.close));
    const desc = `${symbol} 截至 ${asOf} 共 ${points.length} 个交易日；最低收盘价 ${priceText(min, currency)}，最高收盘价 ${priceText(max, currency)}。`;
    el.innerHTML = `<svg viewBox="0 0 ${geometry.width} ${geometry.height}" role="img" aria-labelledby="market-title market-desc" preserveAspectRatio="none"><title id="market-title">${escapeHtml(symbol)} 历史收盘价</title><desc id="market-desc">${escapeHtml(desc)}</desc>${grid}<path d="${path}" fill="none" stroke="#cbb58c" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>${markers}<text x="${geometry.plot.left}" y="${geometry.height - 10}">${escapeHtml(first.date)}</text><text x="${geometry.width - geometry.plot.right}" y="${geometry.height - 10}" text-anchor="end">${escapeHtml(last.date)}</text></svg><p class="chart-summary">${escapeHtml(desc)}</p>`;
  }

  function setMarketState(message, kind = "") {
    const el = document.querySelector("#market-chart");
    if (el) el.innerHTML = emptyState(message, kind);
  }

  function installRenderer() {
    const button = document.querySelector("#load-market-chart");
    const marketEl = document.querySelector("#market-chart");
    if (!button || !marketEl) return { render() {} };

    const marketCache = new Map();
    let selectedRun = null;
    let selectedRunId;
    let labels = {};
    let requestVersion = 0;
    let loadingRunId = null;

    async function loadMarket() {
      const run = selectedRun;
      const runId = run?.id;
      if (!runId || loadingRunId) return;
      const version = ++requestVersion;
      loadingRunId = runId;
      button.disabled = true;
      button.textContent = "正在加载…";
      setMarketState(
        `正在加载截至 ${run.config?.date || "分析日"} 的 Yahoo Finance 收盘价…`,
        "is-loading",
      );
      try {
        const response = await fetch(
          `/api/runs/${encodeURIComponent(runId)}/market`,
        );
        let body = {};
        try {
          body = await response.json();
        } catch (_) {
          body = {};
        }
        if (!response.ok) {
          throw new Error(body.error || "行情加载失败，请稍后重试。");
        }
        if (!body || !Array.isArray(body.series)) {
          throw new Error("行情响应格式不正确。");
        }
        if (version !== requestVersion || selectedRun?.id !== runId) return;
        marketCache.set(runId, body);
        renderMarket(body, run);
      } catch (error) {
        if (version !== requestVersion || selectedRun?.id !== runId) return;
        setMarketState(
          error?.message || "行情加载失败，请稍后重试。",
          "is-error",
        );
        const sourceEl = document.querySelector("#market-chart-source");
        if (sourceEl) sourceEl.textContent = "行情来源：Yahoo Finance";
      } finally {
        if (version === requestVersion && selectedRun?.id === runId) {
          loadingRunId = null;
          button.disabled = false;
          button.textContent = marketCache.has(runId) ? "重新加载" : "重试加载";
        }
      }
    }

    button.addEventListener("click", loadMarket);

    return {
      render({ run = null, runs = [], labels: nextLabels = {} } = {}) {
        void runs;
        selectedRun = run && typeof run === "object" ? run : null;
        labels = nextLabels && typeof nextLabels === "object" ? nextLabels : {};
        const nextRunId = selectedRun?.id || null;
        const selectionChanged = nextRunId !== selectedRunId;
        if (selectionChanged) {
          selectedRunId = nextRunId;
          requestVersion += 1;
          loadingRunId = null;
        }
        const isLoadingSelectedRun = Boolean(
          selectedRunId && loadingRunId === selectedRunId,
        );
        button.disabled = !selectedRunId || isLoadingSelectedRun;
        button.textContent = isLoadingSelectedRun
          ? "正在加载…"
          : marketCache.has(selectedRunId)
            ? "重新加载"
            : "加载行情";
        if (!selectionChanged) {
          renderCoverage(selectedRun, labels);
          renderUsage(selectedRun);
          return;
        }
        if (selectedRunId && marketCache.has(selectedRunId)) {
          renderMarket(marketCache.get(selectedRunId), selectedRun);
        } else if (selectedRunId) {
          setMarketState(
            `点击“加载行情”获取截至 ${selectedRun.config?.date || "分析日"} 的历史收盘价。`,
          );
          const sourceEl = document.querySelector("#market-chart-source");
          if (sourceEl)
            sourceEl.textContent =
              "Yahoo Finance · 日线 · 原始报价 · 缓存最长 15 分钟";
        } else {
          setMarketState("开始研究后，可加载截至分析日的历史收盘价。");
          const sourceEl = document.querySelector("#market-chart-source");
          if (sourceEl)
            sourceEl.textContent =
              "Yahoo Finance · 日线 · 行情缓存最长 15 分钟";
        }
        renderCoverage(selectedRun, labels);
        renderUsage(selectedRun);
      },
    };
  }

  const pure = {
    buildCoverageItems,
    buildMarketGeometry,
    buildUsageItems,
    prepareMarketSeries,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = pure;
  if (root) {
    const renderer =
      typeof document !== "undefined" ? installRenderer() : { render() {} };
    root.TradingVisuals = { ...pure, render: renderer.render };
  }
})(typeof window !== "undefined" ? window : globalThis);
