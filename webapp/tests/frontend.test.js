"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const {
  api,
  canResumeRun,
  CUSTOM_MODEL_VALUE,
  decisionQualityWarning,
  decisionRating,
  decisionSummary,
  inferAssetType,
  modelChoices,
  normalizeRunPage,
  orderReportKeys,
  reportPlaceholder,
  reportRenderKey,
  reportTabTarget,
  selectRun,
  settingsRequestIsCurrent,
  state,
  usageStatus,
} = require("../static/app.js");

test("report tabs wrap when moving left and right", () => {
  assert.equal(reportTabTarget(2, "ArrowRight", 3), 0);
  assert.equal(reportTabTarget(0, "ArrowLeft", 3), 2);
});

test("report tabs support Home and End navigation", () => {
  assert.equal(reportTabTarget(2, "Home", 4), 0);
  assert.equal(reportTabTarget(0, "End", 4), 3);
});

test("report tab navigation ignores unrelated keys and empty tab lists", () => {
  assert.equal(reportTabTarget(0, "Enter", 2), -1);
  assert.equal(reportTabTarget(0, "ArrowRight", 0), -1);
});

test("final conclusion is first in the report directory", () => {
  assert.deepEqual(
    orderReportKeys(["market_report", "final_trade_decision", "news_report"]),
    ["final_trade_decision", "market_report", "news_report"],
  );
  assert.deepEqual(orderReportKeys(["market_report"]), ["market_report"]);
});

test("conclusion summary uses report text without inventing a conclusion", () => {
  assert.equal(
    decisionSummary({
      reports: {
        final_trade_decision: "# 最终决策\n\n维持观察，等待更多成交量确认。\n\n其他说明。",
      },
    }),
    "维持观察，等待更多成交量确认。",
  );
  assert.equal(decisionSummary({ reports: {} }), "");
});

test("conclusion summary prioritizes the explicit NIO executive summary", () => {
  assert.equal(
    decisionSummary({
      reports: {
        final_trade_decision:
          "**Rating**: Underweight\n\n**Time Horizon**: 12 months\n\n**Executive Summary**\n\nNIO faces a slow recovery as deliveries and margins remain under pressure.",
      },
    }),
    "NIO faces a slow recovery as deliveries and margins remain under pressure.",
  );
});

test("rendered conclusion summary skips rating metadata and uses the executive summary", () => {
  const originalDocument = global.document;
  const paragraphs = [
    { textContent: "Rating: Underweight" },
    { textContent: "Executive Summary" },
    {
      textContent:
        "NIO faces a slow recovery as deliveries and margins remain under pressure.",
    },
  ];
  global.document = {
    createElement: (name) => {
      assert.equal(name, "template");
      return {
        content: { querySelectorAll: () => paragraphs },
        set innerHTML(value) {
          assert.match(value, /Executive Summary/);
        },
      };
    },
  };
  try {
    assert.equal(
      decisionSummary({
        reports: { final_trade_decision: "markdown source" },
        rendered: {
          final_trade_decision:
            "<p><strong>Rating</strong>: Underweight</p><p><strong>Executive Summary</strong></p><p>NIO faces a slow recovery as deliveries and margins remain under pressure.</p>",
        },
      }),
      "NIO faces a slow recovery as deliveries and margins remain under pressure.",
    );
  } finally {
    if (originalDocument === undefined) delete global.document;
    else global.document = originalDocument;
  }
});

test("summary metadata detection does not discard prose beginning with a metadata word", () => {
  assert.equal(
    decisionSummary({
      reports: {
        final_trade_decision:
          "# 最终决策\n\n建议降低风险，等待更多证据。",
      },
    }),
    "建议降低风险，等待更多证据。",
  );
});

test("token status distinguishes explicit zero usage, partial, missing, and legacy values", () => {
  assert.deepEqual(
    usageStatus({
      tokens_in: 0,
      tokens_out: 0,
      llm_calls: 2,
      usage_known_calls: 2,
      usage_reported: true,
      usage_complete: true,
    }),
    { state: "complete", label: "完整用量统计" },
  );
  assert.equal(
    usageStatus({
      tokens_in: 18,
      tokens_out: 9,
      llm_calls: 4,
      usage_known_calls: 2,
      usage_reported: true,
      usage_complete: false,
    }).label,
    "部分统计 · 2 / 4 次模型调用报告用量",
  );
  assert.deepEqual(usageStatus({ tokens_in: null, tokens_out: null }), {
    state: "unreported",
    label: "未报告用量",
  });
  assert.deepEqual(usageStatus({ tokens_in: 0, tokens_out: 0 }), {
    state: "unknown",
    label: "用量状态未验证",
  });
});

test("unavailable evidence suppresses the model rating while unknown preserves history", () => {
  assert.equal(
    decisionRating({ rating: "Buy", quality: { status: "unavailable" } }),
    "资料不足 · 暂不评级",
  );
  assert.match(
    decisionQualityWarning({
      quality: { status: "unavailable", summary: "没有可用的基本面来源。" },
    }),
    /没有可用的基本面来源/,
  );
  assert.equal(
    decisionRating({ rating: "Hold", quality: { status: "unknown" } }),
    "持有 · Hold",
  );
  assert.equal(decisionQualityWarning({ rating: "Hold" }), "");
});

test("history page parser accepts cursor envelopes and legacy arrays", () => {
  assert.deepEqual(
    normalizeRunPage({
      items: [{ id: "new" }],
      next_cursor: "opaque-next",
      has_more: true,
      total: 153,
    }),
    {
      items: [{ id: "new" }],
      mode: "cursor",
      nextCursor: "opaque-next",
      hasMore: true,
      total: 153,
    },
  );
  assert.deepEqual(normalizeRunPage([{ id: "old" }], 100), {
    items: [{ id: "old" }],
    mode: "offset",
    nextCursor: null,
    hasMore: false,
    total: null,
  });
});

test("settings responses from a closed or changed modal session are ignored", () => {
  const request = {
    session: 4,
    provider: "chatgpt",
    requestVersion: 9,
  };
  assert.equal(
    settingsRequestIsCurrent(request, {
      ...request,
      open: true,
    }),
    true,
  );
  assert.equal(
    settingsRequestIsCurrent(request, {
      ...request,
      session: 5,
      open: true,
    }),
    false,
  );
  assert.equal(
    settingsRequestIsCurrent(request, {
      ...request,
      provider: "openai",
      open: true,
    }),
    false,
  );
  assert.equal(
    settingsRequestIsCurrent(request, {
      ...request,
      open: false,
    }),
    false,
  );
});

test("resume is an explicit action for failed, cancelled, or interrupted runs", () => {
  for (const status of ["failed", "cancelled", "interrupted"])
    assert.equal(canResumeRun({ status }), true);
  for (const status of ["queued", "running", "completed"])
    assert.equal(canResumeRun({ status }), false);
  assert.equal(canResumeRun(null), false);
});

test("settings dialog has an accessible name", () => {
  const html = fs.readFileSync(require.resolve("../static/index.html"), "utf8");
  assert.match(html, /<dialog[^>]*aria-labelledby="settings-dialog-title"/);
  assert.match(html, /<h2 id="settings-dialog-title">/);
});

test("report placeholder follows terminal status and rerenders after status changes", () => {
  const run = { id: "run-1", status: "running", reports: {} };
  const runningKey = reportRenderKey(run, null);
  assert.match(reportPlaceholder(run), /正在收集证据/);

  run.status = "failed";
  assert.notEqual(reportRenderKey(run, null), runningKey);
  assert.match(reportPlaceholder(run), /未能完成/);

  run.status = "cancelled";
  assert.match(reportPlaceholder(run), /已停止/);
});

test("an older run selection cannot replace the latest selection", async () => {
  const originalFetch = global.fetch;
  const pending = new Map();
  const rendered = [];
  const fakeDom = { selectedRun: { textContent: "" } };
  global.fetch = (path) => new Promise((resolve) => pending.set(path, resolve));
  state.run = null;

  try {
    const render = (run) => {
      rendered.push(run.id);
      fakeDom.selectedRun.textContent = run.id;
    };
    const olderSelection = selectRun("older", { render });
    const latestSelection = selectRun("latest", { render });
    pending.get("/api/runs/latest")({
      ok: true,
      json: async () => ({ id: "latest" }),
    });
    assert.equal(await latestSelection, true);
    pending.get("/api/runs/older")({
      ok: true,
      json: async () => ({ id: "older" }),
    });
    assert.equal(await olderSelection, false);

    assert.equal(state.run.id, "latest");
    assert.deepEqual(rendered, ["latest"]);
    assert.equal(fakeDom.selectedRun.textContent, "latest");
  } finally {
    global.fetch = originalFetch;
    state.run = null;
  }
});

test("API errors show the server message", async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({
    ok: false,
    json: async () => ({ error: "配置无效" }),
  });
  try {
    await assert.rejects(api("/api/settings", {}), /配置无效/);
  } finally {
    global.fetch = originalFetch;
  }
});

test("asset hints recognize normalized crypto pairs without an asset selector", () => {
  assert.equal(inferAssetType("BTC-USD"), "crypto");
  assert.equal(inferAssetType("BTCUSDT"), "crypto");
  assert.equal(inferAssetType("SOL-USDC"), "crypto");
  assert.equal(inferAssetType("AAPL"), "stock");
  assert.equal(inferAssetType("XAUUSD"), "stock");
});

test("model choices keep catalog entries and leave custom selection explicit", () => {
  const previousMeta = state.meta;
  state.meta = {
    providers: [
      {
        id: "openai",
        models: {
          quick: [
            { label: "Fast model", id: "fast-model" },
            { label: "Duplicate label", id: "fast-model" },
            { label: "Custom model ID", id: "custom" },
          ],
          deep: [{ label: "Saved model", id: "saved-deep" }],
        },
      },
    ],
  };
  try {
    assert.deepEqual(modelChoices("openai", "quick"), [
      { label: "Fast model", id: "fast-model" },
    ]);
    assert.deepEqual(modelChoices("openai", "deep"), [
      { label: "Saved model", id: "saved-deep" },
    ]);
    assert.notEqual(CUSTOM_MODEL_VALUE, "custom");
  } finally {
    state.meta = previousMeta;
  }
});
