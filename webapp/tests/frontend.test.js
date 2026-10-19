"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  api,
  CUSTOM_MODEL_VALUE,
  inferAssetType,
  modelChoices,
  reportPlaceholder,
  reportRenderKey,
  reportTabTarget,
  selectRun,
  state,
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
