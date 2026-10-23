"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const {
  buildCoverageItems,
  buildEvidenceItems,
  buildMarketGeometry,
  buildQualitySummary,
  buildUsageItems,
  hasKnownUsageData,
  prepareMarketSeries,
  priceBasisText,
  qualityStatusLabel,
  usageSummary,
} = require("../static/visuals.js");

test("chart labels stay in HTML while loaded market data retains its source", async () => {
  const elements = new Map(
    ["#market-chart", "#market-chart-source", "#coverage-chart", "#usage-chart"]
      .map((id) => [id, { innerHTML: "", textContent: "" }]),
  );
  let loadMarket;
  elements.set("#load-market-chart", {
    addEventListener(event, handler) {
      assert.equal(event, "click");
      loadMarket = handler;
    },
  });
  const window = {};
  const context = vm.createContext({
    window,
    document: { querySelector: (selector) => elements.get(selector) || null },
    fetch: async () => ({
      ok: true,
      json: async () => ({
        symbol: "TEST",
        as_of: "2026-01-03",
        source: "Saved production source",
        price_basis: "saved close",
        currency: "USD",
        series: [
          { date: "2026-01-01", close: 10 },
          { date: "2026-01-03", close: 12 },
        ],
      }),
    }),
  });
  vm.runInContext(fs.readFileSync(require.resolve("../static/visuals.js"), "utf8"), context);
  window.TradingVisuals.render({
    run: {
      id: "test-run",
      config: { ticker: "TEST", date: "2026-01-03", analysts: ["market", "news"] },
      reports: { market_report: "Completed technical report" },
      stats: { tokens_in: 1250, tokens_out: 0, usage_complete: true },
    },
  });

  const coverage = elements.get("#coverage-chart").innerHTML;
  const usage = elements.get("#usage-chart").innerHTML;
  assert.match(coverage, /role="list"/);
  assert.match(coverage, /技术面分析<\/span><span>已完成/);
  assert.match(coverage, /新闻分析<\/span><span>待生成/);
  assert.match(usage, /输入 Token<\/span><span>1,250/);
  assert.match(usage, /输出 Token<\/span><span>0/);
  assert.doesNotMatch(coverage + usage, /<svg|<text/);

  await loadMarket();
  const market = elements.get("#market-chart").innerHTML;
  assert.match(market, /<svg[^>]+role="img"/);
  assert.match(market, /class="market-y-axis"[^>]*><span>US\$12\.00/);
  assert.match(market, /class="market-dates"[^>]*><span>2026-01-01<\/span><span>2026-01-03/);
  assert.doesNotMatch(market, /<text/);
  assert.match(elements.get("#market-chart-source").textContent, /^Saved production source · TEST · saved close/);
});

test("market series is date sorted, bounded by as-of, and drops invalid closes", () => {
  const points = prepareMarketSeries(
    [
      { date: "2026-01-03", close: 13 },
      { date: "2026-01-01", close: 11 },
      { date: "2026-01-04", close: 99 },
      { date: "2026-01-02", close: Number.NaN },
      { date: "not-a-date", close: 5 },
      { date: "2026-01-03", close: 14 },
    ],
    "2026-01-03",
  );

  assert.deepEqual(points, [
    { date: "2026-01-01", close: 11 },
    { date: "2026-01-03", close: 14 },
  ]);
});

test("coverage reflects selected analyst reports and ignores unselected reports", () => {
  const items = buildCoverageItems(
    {
      config: { analysts: ["market", "news", "fundamentals"] },
      reports: {
        market_report: "Technical report",
        news_report: "  ",
        sentiment_report: "Not selected",
      },
    },
    { market_report: "市场分析", news_report: "新闻分析" },
  );

  assert.deepEqual(items, [
    {
      key: "market",
      reportKey: "market_report",
      label: "市场分析",
      complete: true,
    },
    {
      key: "news",
      reportKey: "news_report",
      label: "新闻分析",
      complete: false,
    },
    {
      key: "fundamentals",
      reportKey: "fundamentals_report",
      label: "基本面分析",
      complete: false,
    },
  ]);
});

test("usage preserves real zero values and marks missing or invalid values unavailable", () => {
  const items = buildUsageItems({
    stats: { tokens_in: 0, tokens_out: 125, llm_calls: 4 },
  });
  assert.deepEqual(
    items.map(({ key, available, value }) => ({ key, available, value })),
    [
      { key: "tokens_in", available: true, value: 0 },
      { key: "tokens_out", available: true, value: 125 },
    ],
  );
  assert.equal(
    buildUsageItems({ stats: { tokens_in: -1, tokens_out: Infinity } }).every(
      (item) => !item.available,
    ),
    true,
  );
  assert.equal(
    buildUsageItems({}).every((item) => !item.available),
    true,
  );
  const zeroUsage = buildUsageItems({ stats: { tokens_in: 0, tokens_out: 0 } });
  assert.equal(hasKnownUsageData(zeroUsage), true);
  assert.equal(hasKnownUsageData(buildUsageItems({})), false);
});

test("usage summary distinguishes full, partial, missing, and unverified history", () => {
  assert.deepEqual(
    usageSummary({
      stats: {
        tokens_in: 0,
        tokens_out: 0,
        llm_calls: 2,
        usage_known_calls: 2,
        usage_reported: true,
        usage_complete: true,
      },
    }),
    { state: "complete", label: "完整用量统计" },
  );
  assert.equal(
    usageSummary({
      stats: {
        tokens_in: 18,
        tokens_out: 9,
        llm_calls: 4,
        usage_known_calls: 2,
        usage_reported: true,
        usage_complete: false,
      },
    }).label,
    "部分统计 · 2 / 4 次模型调用报告用量",
  );
  assert.deepEqual(
    usageSummary({ stats: { tokens_in: null, tokens_out: null } }),
    { state: "unreported", label: "未报告用量" },
  );
  assert.deepEqual(
    usageSummary({ stats: { tokens_in: 0, tokens_out: 0 } }),
    { state: "unknown", label: "用量状态未验证" },
  );
});

test("evidence status defaults to unknown and never implies sufficiency", () => {
  assert.deepEqual(buildQualitySummary({}), {
    status: "unknown",
    label: "未验证",
    summary: "此研究记录没有经过验证的证据状态。",
    evidenceCount: null,
    asOf: null,
  });
  assert.equal(qualityStatusLabel("available"), "已获取数据");
  assert.equal(qualityStatusLabel("sparse"), "覆盖有限");
  assert.equal(qualityStatusLabel("unavailable"), "资料不足");
  assert.equal(qualityStatusLabel("excellent"), "未验证");
});

test("evidence quality stays separate from whether each report was generated", () => {
  const items = buildEvidenceItems(
    {
      status: "completed",
      config: { analysts: ["market", "news"] },
      reports: { market_report: "Generated market report" },
      quality: {
        status: "sparse",
        reports: {
          market_report: {
            status: "sparse",
            sources: [
              {
                source: "Price API",
                sample_count: 0,
                observed_dates: { start: "2026-01-01", end: "2026-01-02" },
                coverage_caveat: "仅有两个交易日。",
              },
            ],
          },
        },
      },
    },
    { market_report: "技术面分析", news_report: "新闻分析" },
  );
  assert.equal(items[0].generation, "generated");
  assert.equal(items[0].status, "sparse");
  assert.equal(items[0].sources[0].sample_count, 0);
  assert.equal(items[1].generation, "missing");
  assert.equal(items[1].status, "unknown");

  const inProgress = buildEvidenceItems({
    status: "running",
    config: { analysts: ["news"] },
    reports: {},
    quality: {},
  });
  assert.equal(inProgress[0].generation, "generating");
  assert.equal(inProgress[0].status, "unknown");
});

test("market geometry handles flat and single-point histories with finite coordinates", () => {
  const geometry = buildMarketGeometry([{ date: "2026-01-01", close: 25 }]);
  assert.equal(geometry.points.length, 1);
  assert.equal(geometry.points[0].x, 64 + (720 - 64 - 18) / 2);
  assert.equal(Number.isFinite(geometry.points[0].y), true);
  assert.equal(geometry.max > geometry.min, true);
});

test("market source text uses the returned price basis and labels missing metadata", () => {
  for (const provider of ["eastmoney", "tencent", "sina"])
    assert.equal(priceBasisText({ price_basis: `${provider}_unadjusted_daily` }), "日线不复权（CNY；成交量为股；除权除息影响指标）");
  assert.equal(priceBasisText({ price_basis: "yahoo_close_auto_adjust_false" }), "收盘价（不额外调整股息；历史可能按拆股调整）");
  assert.equal(priceBasisText({ price_basis: "split-adjusted close" }), "split-adjusted close");
  assert.equal(priceBasisText({}), "报价口径未提供");
});
