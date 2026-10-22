/* Charts use the same saved daily market snapshot as the research tools. */
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
  const QUALITY_STATUS_LABELS = {
    available: "已获取数据",
    sparse: "覆盖有限",
    unavailable: "资料不足",
    unknown: "未验证",
  };

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

  function hasKnownUsageData(items) {
    return items.some((item) => item.available);
  }

  function usageSummary(run) {
    const stats = run?.stats || {};
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
      const hasValues = buildUsageItems(run).some((item) => item.available);
      return {
        state: hasValues ? "unknown" : "unreported",
        label: hasValues ? "用量状态未验证" : "未报告用量",
      };
    }
    if (stats.usage_reported === false || knownCalls === 0)
      return { state: "unreported", label: "未报告用量" };
    if (stats.usage_complete === true)
      return { state: "complete", label: "完整用量统计" };
    if (stats.usage_reported === true || (knownCalls !== null && knownCalls > 0)) {
      const label =
        knownCalls !== null && totalCalls !== null
          ? `部分统计 · ${countText(knownCalls)} / ${countText(totalCalls)} 次模型调用报告用量`
          : knownCalls !== null
            ? `部分统计 · ${countText(knownCalls)} 次模型调用报告用量 · 总调用数未知`
            : "部分统计 · 有调用报告用量 · 总调用数未知";
      return {
        state: "partial",
        label,
      };
    }
    return { state: "unreported", label: "未报告用量" };
  }

  function qualityStatus(value) {
    return Object.hasOwn(QUALITY_STATUS_LABELS, value) ? value : "unknown";
  }

  function qualityStatusLabel(value) {
    return QUALITY_STATUS_LABELS[qualityStatus(value)];
  }

  function buildQualitySummary(run) {
    const quality = run?.quality || {};
    const evidenceCount = quality.evidence_count;
    return {
      status: qualityStatus(quality.status),
      label: qualityStatusLabel(quality.status),
      summary:
        typeof quality.summary === "string" && quality.summary.trim()
          ? quality.summary.trim()
          : "此研究记录没有经过验证的证据状态。",
      evidenceCount:
        typeof evidenceCount === "number" &&
        Number.isSafeInteger(evidenceCount) &&
        evidenceCount >= 0
          ? evidenceCount
          : null,
      asOf:
        typeof quality.as_of === "string" && quality.as_of.trim()
          ? quality.as_of.trim()
          : null,
    };
  }

  function buildEvidenceItems(run, labels = {}) {
    const analystReportKeys = (run?.config?.analysts || [])
      .map((key) => ANALYST_REPORT_KEYS[key])
      .filter(Boolean);
    const qualityReports = run?.quality?.reports || {};
    const reports = run?.reports || {};
    return [...new Set([...analystReportKeys, ...Object.keys(qualityReports)])]
      .map((key) => {
        const details = qualityReports[key] || {};
        const generated =
          typeof reports[key] === "string" && reports[key].trim().length > 0;
        return {
          key,
          label: String(labels[key] || key.replaceAll("_", " ")),
          generation: generated
            ? "generated"
            : ["queued", "running", "cancelling"].includes(run?.status)
              ? "generating"
              : "missing",
          status: qualityStatus(details.status),
          sources: Array.isArray(details.sources)
            ? details.sources.filter(
                (source) => source && typeof source === "object",
              )
            : [],
          note:
            typeof details.note === "string" && details.note.trim()
              ? details.note.trim()
              : "",
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

  function priceBasisText(data) {
    if (data?.price_basis === "eastmoney_unadjusted_daily")
      return "日线不复权（CNY；成交量为股；除权除息影响指标）";
    if (data?.price_basis === "yahoo_close_auto_adjust_false")
      return "收盘价（不额外调整股息；历史可能按拆股调整）";
    return typeof data?.price_basis === "string" && data.price_basis.trim()
      ? data.price_basis.trim()
      : "报价口径未提供";
  }

  function emptyState(message, kind = "") {
    return `<p class="chart-empty-state${kind ? ` ${kind}` : ""}" role="status">${escapeHtml(message)}</p>`;
  }

  function renderQuality(run, labels) {
    const panel = document.querySelector("#evidence-quality");
    if (!panel) return;
    const summary = buildQualitySummary(run);
    const status = document.querySelector("#evidence-status");
    if (status) {
      status.textContent = summary.label;
      status.className = `quality-tag quality-${summary.status}`;
    }
    const summaryEl = document.querySelector("#evidence-summary");
    if (summaryEl) summaryEl.textContent = summary.summary;
    const meta = document.querySelector("#evidence-meta");
    if (meta) {
      const details = [];
      if (summary.evidenceCount !== null)
        details.push(`${countText(summary.evidenceCount)} 条证据记录`);
      if (summary.asOf) details.push(`数据日期 ${summary.asOf}`);
      meta.textContent = details.join(" · ");
      meta.hidden = details.length === 0;
    }

    const rows = document.querySelector("#evidence-report-rows");
    if (!rows) return;
    const items = buildEvidenceItems(run, labels);
    rows.innerHTML = items
      .map((item) => {
        const generation =
          item.generation === "generated"
            ? "已生成"
            : item.generation === "generating"
              ? "生成中"
              : "未生成";
        const sourceRows = item.sources
          .map((source) => {
            const parts = [];
            if (typeof source.source === "string" && source.source.trim())
              parts.push(`来源：${escapeHtml(source.source.trim())}`);
            if (
              typeof source.sample_count === "number" &&
              Number.isSafeInteger(source.sample_count) &&
              source.sample_count >= 0
            )
              parts.push(`样本：${countText(source.sample_count)}`);
            const dates = source.observed_dates || {};
            if (dates.start || dates.end)
              parts.push(
                `覆盖日期：${escapeHtml(dates.start || "未知")} 至 ${escapeHtml(dates.end || "未知")}`,
              );
            const coverage =
              typeof source.coverage_caveat === "string"
                ? source.coverage_caveat.trim()
                : "";
            return `${parts.length ? `<p class="evidence-sources">${parts.join(" · ")}</p>` : ""}${coverage ? `<p>${escapeHtml(coverage)}</p>` : ""}`;
          })
          .join("");
        const note = item.note ? `<p>${escapeHtml(item.note)}</p>` : "";
        const noSource =
          !sourceRows && !note
            ? '<p>未提供逐来源信息。</p>'
            : "";
        return `<section class="evidence-report-row"><div class="evidence-report-heading"><b>${escapeHtml(item.label)}</b><span class="generation-state">${generation} · ${qualityStatusLabel(item.status)}</span></div>${sourceRows}${note}${noSource}</section>`;
      })
      .join("");
    rows.hidden = items.length === 0;
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
    const summary = usageSummary(run);
    if (!hasKnownUsageData(items)) {
      el.innerHTML = emptyState(
        run ? summary.label : "模型尚未返回实际 token 用量数据。",
      );
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
    const note = ["partial", "unknown"].includes(summary.state)
      ? `<p class="usage-note">${escapeHtml(summary.label)}</p>`
      : "";
    el.innerHTML = `${note}<svg viewBox="0 0 430 126" role="img" aria-labelledby="usage-title usage-desc" preserveAspectRatio="xMinYMin meet"><title id="usage-title">模型实际 token 用量</title><desc id="usage-desc">${items.map((item) => `${item.label}：${item.available ? countText(item.value) : "暂无数据"}`).join("；")}；${escapeHtml(summary.label)}</desc>${rows}</svg>`;
  }

  function renderMarket(data, run) {
    const el = document.querySelector("#market-chart");
    const sourceEl = document.querySelector("#market-chart-source");
    if (!el) return;
    const symbol = data.symbol || run?.config?.ticker || "标的";
    const asOf = data.as_of || run?.config?.date || "分析日期未知";
    const source = data.source || "行情来源未提供";
    const currency = data.currency;
    const priceBasis = priceBasisText(data);
    if (sourceEl) {
      sourceEl.textContent = `${source} · ${symbol} · ${priceBasis} · 截至 ${asOf}${currency ? ` · ${currency}` : ""}`;
    }
    const points = prepareMarketSeries(data.series, asOf);
    if (!points.length) {
      el.innerHTML = emptyState(
        `${source} 未返回截至 ${asOf} 可用的收盘价。`,
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
        `正在加载截至 ${run.config?.date || "分析日"} 的共享行情收盘价…`,
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
        if (sourceEl) sourceEl.textContent = "行情来源尚未确认";
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
        renderQuality(selectedRun, labels);
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
              "共享日线快照 · 来源和口径加载后显示";
        } else {
          setMarketState("开始研究后，可加载截至分析日的历史收盘价。");
          const sourceEl = document.querySelector("#market-chart-source");
          if (sourceEl)
            sourceEl.textContent =
              "共享日线快照 · 来源加载后显示";
        }
        renderCoverage(selectedRun, labels);
        renderUsage(selectedRun);
      },
    };
  }

  const pure = {
    buildCoverageItems,
    buildEvidenceItems,
    buildMarketGeometry,
    buildQualitySummary,
    buildUsageItems,
    hasKnownUsageData,
    usageSummary,
    priceBasisText,
    prepareMarketSeries,
    qualityStatusLabel,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = pure;
  if (root) {
    const renderer =
      typeof document !== "undefined" ? installRenderer() : { render() {} };
    root.TradingVisuals = { ...pure, render: renderer.render };
  }
})(typeof window !== "undefined" ? window : globalThis);
