"""Local-market routing and legacy-report compatibility, without paid calls."""

import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

from webapp.core import display_report, document
from webapp.worker import execute


class ChinaWorkspaceTests(unittest.TestCase):
    def test_a_share_worker_selects_chinese_news_preserving_explicit_override(self):
        class ConfigCaptured(Exception):
            pass

        class Stats:
            def get_stats(self):
                return {
                    "llm_calls": 0,
                    "tool_calls": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                }

        captured = {}

        class Graph:
            def __init__(self, **kwargs):
                captured.update(kwargs["config"])
                raise ConfigCaptured

        request = {
            "ticker": "600519.SS",
            "date": "2026-10-20",
            "provider": "ollama",
            "quick_model": "offline",
            "deep_model": "offline",
            "analysts": ["news"],
        }
        with tempfile.TemporaryDirectory() as directory:
            for override in ({}, {"get_news": "yfinance"}):
                with (
                    self.subTest(override=override),
                    patch(
                        "tradingagents.default_config.DEFAULT_CONFIG", {"tool_vendors": override}
                    ),
                    contextlib.redirect_stdout(io.StringIO()),
                    self.assertRaises(ConfigCaptured),
                ):
                    execute(
                        {"config": request, "directory": directory},
                        graph_class=Graph,
                        stats_class=Stats,
                    )
                self.assertEqual(
                    captured["tool_vendors"]["get_news"], override.get("get_news", "eastmoney")
                )
                self.assertEqual(override, {} if not override else {"get_news": "yfinance"})

    def test_saved_report_hides_missing_target_in_display_and_exports_without_mutation(self):
        original = (
            "**Rating**: Hold\n\n**Price Target**: not provided\n\n**Time Horizon**: long term"
        )
        run = {
            "config": {
                "ticker": "600519.SS",
                "date": "2026-10-20",
                "provider": "ollama",
                "quick_model": "offline",
                "deep_model": "offline",
            },
            "status": "completed",
            "rating": "Hold",
            "reports": {"final_trade_decision": original},
        }
        self.assertNotIn("Price Target", display_report("final_trade_decision", original))
        self.assertNotIn("Price Target", document(run))
        self.assertIn("Time Horizon", document(run))
        self.assertEqual(run["reports"]["final_trade_decision"], original)
        valid = "**Price Target**: 1550.0"
        self.assertEqual(display_report("final_trade_decision", valid), valid)
