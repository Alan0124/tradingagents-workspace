"""Offline checks for automatic asset routing and explicit model refresh."""

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from webapp.core import infer_asset_type, metadata, refresh_models, validate_run
from webapp.server import Server


def config(**overrides):
    value = {
        "ticker": "AAPL",
        "date": "2026-09-01",
        "provider": "ollama",
        "quick_model": "test-model",
        "deep_model": "test-model",
        "backend_url": "http://localhost:11434/v1",
        "analysts": ["market", "news", "fundamentals"],
        "rounds": 1,
        "language": "Chinese",
    }
    value.update(overrides)
    return value


class AssetInferenceTests(unittest.TestCase):
    def test_cfd_alias_remains_accepted(self):
        chosen = validate_run(config(ticker="XAUUSD+"), check_credentials=False)
        self.assertEqual(chosen["ticker"], "GC=F")
        for ticker in ("A+APL", "AAPL++", "A" * 32):
            with self.subTest(ticker=ticker), self.assertRaises(ValueError):
                validate_run(config(ticker=ticker), check_credentials=False)

    def test_mainland_aliases_share_a_canonical_ticker(self):
        aliases = ("600519", "SH600519", "600519.SH", "600519.SS")
        chosen = [
            validate_run(config(ticker=ticker), check_credentials=False)
            for ticker in aliases
        ]
        self.assertEqual({item["ticker"] for item in chosen}, {"600519.SS"})
        self.assertEqual({item["asset_type"] for item in chosen}, {"stock"})

    def test_ticker_infers_asset_type_without_request_field(self):
        self.assertEqual(infer_asset_type("BTC-USD"), "crypto")
        self.assertEqual(infer_asset_type("BTCUSDT"), "crypto")
        self.assertEqual(infer_asset_type("AAPL"), "stock")

        crypto = validate_run(
            config(ticker="BTC-USD", analysts=["market", "news", "fundamentals"]),
            check_credentials=False,
        )
        self.assertEqual(crypto["asset_type"], "crypto")
        self.assertEqual(crypto["analysts"], ["market", "news"])

    def test_inferred_type_overrides_legacy_field_but_accepts_it(self):
        crypto = validate_run(
            config(ticker="BTC-USD", asset_type="stock", analysts=["market"]),
            check_credentials=False,
        )
        stock = validate_run(
            config(ticker="AAPL", asset_type="crypto", analysts=["market"]),
            check_credentials=False,
        )
        self.assertEqual(crypto["asset_type"], "crypto")
        self.assertEqual(stock["asset_type"], "stock")

    def test_crypto_requires_an_analyst_other_than_fundamentals(self):
        with self.assertRaisesRegex(ValueError, "至少选择市场、新闻或情绪分析师"):
            validate_run(
                config(ticker="BTC-USD", analysts=["fundamentals"]),
                check_credentials=False,
            )


class ModelCatalogTests(unittest.TestCase):
    def test_bootstrap_stays_offline_and_keeps_saved_model_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preferences.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": "chatgpt",
                        "quick_model": "saved-quick-id",
                        "deep_model": "saved-deep-id",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("webapp.core.DATA", Path(tmp)),
                patch("webapp.core.credentials", return_value=({}, {})),
                patch("webapp.core.chatgpt_account", return_value="account@example.com"),
            ):
                result = metadata()

        chatgpt = next(provider for provider in result["providers"] if provider["id"] == "chatgpt")
        self.assertIn(
            {"label": "已保存 · saved-quick-id", "id": "saved-quick-id"},
            chatgpt["models"]["quick"],
        )
        self.assertIn(
            {"label": "已保存 · saved-deep-id", "id": "saved-deep-id"},
            chatgpt["models"]["deep"],
        )

    def test_chatgpt_refresh_returns_only_account_listed_choices(self):
        listed = [("Account model", "account-model-1"), ("Another model", "account-model-2")]
        with (
            patch("webapp.core.chatgpt_account", return_value="account@example.com"),
            patch("tradingagents.llm_clients.chatgpt_auth.list_models", return_value=listed),
        ):
            result = refresh_models("chatgpt")

        expected = [
            {"label": "Account model", "id": "account-model-1"},
            {"label": "Another model", "id": "account-model-2"},
        ]
        self.assertEqual(result["provider"], "chatgpt")
        self.assertEqual(result["models"], {"quick": expected, "deep": expected})

    def test_chatgpt_refresh_requires_sign_in_and_rejects_other_providers(self):
        with (
            patch("webapp.core.chatgpt_account", return_value=None),
            self.assertRaisesRegex(PermissionError, "先登录 ChatGPT"),
        ):
            refresh_models("chatgpt")
        with self.assertRaisesRegex(ValueError, "仅支持刷新 ChatGPT"):
            refresh_models("ollama")

    def test_http_refresh_route_uses_the_same_response_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            from webapp.core import Manager, Store

            manager = Manager(Store(tmp))
            server = Server(("127.0.0.1", 0), manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            response = {
                "provider": "chatgpt",
                "models": {
                    "quick": [{"label": "Quick", "id": "quick-id"}],
                    "deep": [{"label": "Deep", "id": "deep-id"}],
                },
            }
            try:
                with (
                    patch("webapp.server.refresh_models", return_value=response),
                    urllib.request.urlopen(server.origin + "/api/models?provider=chatgpt") as res,
                ):
                    self.assertEqual(json.load(res), response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
