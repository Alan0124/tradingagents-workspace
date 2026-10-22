"""Disposable, offline browser QA server. Never used by the desktop launcher."""

import copy
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from webapp.tests.test_engine import OfflineModel


class SlowOfflineModel(OfflineModel):
    def _generate(self, *args, **kwargs):
        time.sleep(2)
        return super()._generate(*args, **kwargs)


def worker():
    from tradingagents.default_config import DEFAULT_CONFIG
    from webapp.worker import execute

    request = json.loads(sys.stdin.readline())
    tmp = Path(request["directory"])
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(memory_log_path=str(tmp / "memory.md"), data_cache_dir=str(tmp / "cache"))
    client = type("Client", (), {"get_llm": lambda _: SlowOfflineModel()})()
    with (
        patch("tradingagents.default_config.DEFAULT_CONFIG", cfg),
        patch("tradingagents.graph.trading_graph.create_tier_client", return_value=client),
        patch(
            "tradingagents.graph.trading_graph.TradingAgentsGraph.resolve_instrument_context",
            return_value="AAPL · OFFLINE UI TEST",
        ),
    ):
        execute(request)


def serve():
    import pandas as pd

    import webapp.core as core
    from tradingagents.dataflows.evidence import EvidenceLedger
    from tradingagents.dataflows.vendors import eastmoney_market
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV
    from webapp.server import Server

    with tempfile.TemporaryDirectory(prefix="tradingagents-browser-qa-") as directory:
        # Browser QA may save settings: keep even .env and account state disposable.
        core.ROOT = core.DATA = Path(directory)
        manager = core.Manager(
            core.Store(directory),
            [sys.executable, "-u", "-m", "webapp.tests.browser_fixture", "--worker"],
        )
        today = time.strftime("%Y-%m-%d")
        config = {"ticker": "600519.SS", "date": today, "provider": "ollama",
                  "quick_model": "offline-test", "deep_model": "offline-test",
                  "backend_url": "", "analysts": ["market", "fundamentals", "news", "social"],
                  "rounds": 1, "language": "Chinese", "asset_type": "stock"}
        ledger = EvidenceLedger(Path(directory) / "runs" / "qa-00", today, config["analysts"])
        for report_key in core.ANALYSTS.values():
            ledger.record(report_key, "offline-fixture", "Offline synthetic UI data",
                          status="available", sample_count=30)
        reports = {key: f"# {label}\n\n离线交互验证，非市场研究。\n\n" +
                   "\n\n".join(f"段落 {i}：用于验证长报告滚动与章节切换。" for i in range(12))
                   for key, label in core.LABELS.items()}
        for index in range(103):
            manager.store.put({
                "id": f"qa-{index:02}", "config": {**config, "ticker": "MSFT" if index == 1 else "600519.SS"},
                "status": "completed", "created_at": time.time() - index * 60,
                "finished_at": time.time(), "rating": "REVIEW", "reports": reports,
                "events": [{"time": time.time(), "text": "离线页面测试"}],
                "quality": ledger.quality(), "stats": {"llm_calls": 1, "tool_calls": 1,
                    "tokens_in": 100, "tokens_out": 50, "usage_reported": True, "usage_known_calls": 1},
            })
        dates = pd.bdate_range(end=today, periods=30)
        frame = pd.DataFrame({"Date": dates, "Open": 10.0, "Close": range(10, 40),
                              "High": 40.0, "Low": 9.0, "Volume": 10000})
        server = Server(("127.0.0.1", 18785), manager)
        print(server.origin, flush=True)
        try:
            with (
                patch.object(core, "credentials", return_value=({}, PROVIDER_API_KEY_ENV)),
                patch.object(core, "chatgpt_account", return_value=None),
                patch.object(eastmoney_market, "fetch_daily", return_value=frame),
            ):
                server.serve_forever()
        finally:
            manager.shutdown()
            server.server_close()


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else serve()
