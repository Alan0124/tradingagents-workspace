"""Verify the UI worker against the actual upstream graph, without network or API fees."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from tradingagents.default_config import DEFAULT_CONFIG
from webapp.core import PREFIX
from webapp.worker import execute


class OfflineModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, *args, **kwargs):
        raise NotImplementedError

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="离线集成测试。\n\n**Rating**: Hold\n\n无市场数据，仅检查工作流连接。"
                    )
                )
            ]
        )


class EngineIntegrationTests(unittest.TestCase):
    def test_worker_reaches_real_graph_decision_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config.update(
                memory_log_path=str(Path(tmp) / "memory.md"),
                data_cache_dir=str(Path(tmp) / "cache"),
            )
            client = type("Client", (), {"get_llm": lambda _: OfflineModel()})()
            chosen = {
                "ticker": "AAPL",
                "date": "2026-09-01",
                "asset_type": "stock",
                "provider": "ollama",
                "quick_model": "offline-test",
                "deep_model": "offline-test",
                "backend_url": "",
                "analysts": ["market", "news", "fundamentals"],
                "rounds": 1,
                "language": "Chinese",
            }
            output = io.StringIO()
            with (
                patch("tradingagents.default_config.DEFAULT_CONFIG", config),
                patch("tradingagents.graph.trading_graph.create_tier_client", return_value=client),
                patch(
                    "tradingagents.graph.trading_graph.TradingAgentsGraph.resolve_instrument_context",
                    return_value="AAPL · OFFLINE TEST",
                ),
                contextlib.redirect_stdout(output),
            ):
                execute({"config": chosen, "directory": tmp})
            events = [
                json.loads(s[len(PREFIX) :])
                for s in output.getvalue().splitlines()
                if s.startswith(PREFIX)
            ]
            reports = {e["key"]: e["text"] for e in events if e["type"] == "report"}
            self.assertIn("final_trade_decision", reports)
            self.assertIn("fundamentals_report", reports)
            self.assertIn("bull", reports)
            self.assertIn("conservative", reports)
            self.assertEqual(next(e["rating"] for e in events if e["type"] == "result"), "Hold")
            self.assertTrue(list(Path(tmp).glob("reports/*/complete_report.html")))
            self.assertTrue(list(Path(tmp).glob("AAPL/TradingAgentsStrategy_logs/*.json")))
            self.assertTrue((Path(tmp) / "memory.md").exists())


if __name__ == "__main__":
    unittest.main()
