"""Offline market-data and HTTP integration checks (no optional test libraries)."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from webapp import market
from webapp.core import Manager, Store
from webapp.server import Server


class MarketTests(unittest.TestCase):
    def setUp(self):
        market._CACHE.clear()

    def tearDown(self):
        market._CACHE.clear()

    def test_normalization_date_bounds_invalid_closes_and_cache_isolation(self):
        as_of = date(2026, 1, 10)
        frame = pd.DataFrame(
            {"Close": [10.25, 12.5, 99, float("nan"), float("inf"), "bad", 1]},
            index=[
                as_of - timedelta(days=2),
                as_of,
                as_of + timedelta(days=1),
                as_of - timedelta(days=3),
                as_of - timedelta(days=4),
                as_of - timedelta(days=5),
                as_of - timedelta(days=121),
            ],
        )
        config = {"ticker": "BTCUSD", "date": as_of.isoformat()}
        with patch.object(market.yf, "download", return_value=frame) as download:
            result = market.get_market_history(config)
            original = json.loads(json.dumps(result))
            result["series"].clear()
            self.assertEqual(market.get_market_history(config), original)
        self.assertEqual(
            original["series"],
            [
                {"date": "2026-01-08", "close": 10.25},
                {"date": "2026-01-10", "close": 12.5},
            ],
        )
        self.assertEqual(original["symbol"], "BTC-USD")
        kwargs = download.call_args.kwargs
        self.assertEqual(kwargs["tickers"], "BTC-USD")
        self.assertEqual(kwargs["end"], "2026-01-11")
        self.assertEqual(
            date.fromisoformat(kwargs["end"]) - date.fromisoformat(kwargs["start"]),
            timedelta(days=120),
        )
        self.assertEqual(kwargs["timeout"], market.MARKET_TIMEOUT_SECONDS)
        self.assertFalse(kwargs["threads"])
        self.assertFalse(kwargs["auto_adjust"])
        download.assert_called_once()

    def test_empty_history_is_cached(self):
        config = {"ticker": "AAPL", "date": "2026-01-10"}
        with patch.object(market.yf, "download", return_value=pd.DataFrame()) as download:
            first = market.get_market_history(config)
            self.assertEqual(first, market.get_market_history(config))
        self.assertEqual(first["series"], [])
        download.assert_called_once()

    def test_network_error_does_not_expose_provider_details(self):
        with (
            patch.object(market.yf, "download", side_effect=TimeoutError("private details")),
            self.assertRaisesRegex(RuntimeError, "Yahoo Finance 行情暂时不可用") as error,
        ):
            market.get_market_history({"ticker": "AAPL", "date": "2026-01-10"})
        self.assertNotIn("private details", str(error.exception))

    def test_invalid_configuration_never_calls_provider(self):
        for config in [
            None,
            {"ticker": "", "date": "2026-01-10"},
            {"ticker": "AAPL", "date": "2026-02-30"},
            {"ticker": "AAPL", "date": "2026-01-10T00:00:00"},
        ]:
            with self.subTest(config=config), patch.object(market.yf, "download") as download:
                with self.assertRaises(ValueError):
                    market.get_market_history(config)
                download.assert_not_called()

    def test_http_market_endpoint_uses_saved_config_and_handles_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            config = {"ticker": "AAPL", "date": "2026-01-10"}
            store.put({"id": "saved", "status": "completed", "created_at": 1, "config": config})
            manager = Manager(store)
            server = Server(("127.0.0.1", 0), manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch(
                    "webapp.server.get_market_history", return_value={"series": []}
                ) as helper:
                    with urllib.request.urlopen(
                        server.origin + "/api/runs/saved/market"
                    ) as response:
                        self.assertEqual(json.load(response), {"series": []})
                    helper.assert_called_once_with(config)
                with patch(
                    "webapp.server.get_market_history", side_effect=RuntimeError("行情不可用")
                ):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(server.origin + "/api/runs/saved/market")
                    self.assertEqual(error.exception.code, 502)
                with urllib.request.urlopen(server.origin + "/visuals.js") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"TradingVisuals", response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                manager.shutdown()
