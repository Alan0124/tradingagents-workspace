"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  buildCoverageItems,
  buildMarketGeometry,
  buildUsageItems,
  prepareMarketSeries,
} = require("../static/visuals.js");

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
});

test("market geometry handles flat and single-point histories with finite coordinates", () => {
  const geometry = buildMarketGeometry([{ date: "2026-01-01", close: 25 }]);
  assert.equal(geometry.points.length, 1);
  assert.equal(geometry.points[0].x, 64 + (720 - 64 - 18) / 2);
  assert.equal(Number.isFinite(geometry.points[0].y), true);
  assert.equal(geometry.max > geometry.min, true);
});
