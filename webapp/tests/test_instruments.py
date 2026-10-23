import io
import json
import tempfile
import unittest
from unittest.mock import patch

from webapp.core import Store
from webapp.instruments import _display_name, instrument_name, named_summaries


class InstrumentNameTests(unittest.TestCase):
    def setUp(self):
        _display_name.cache_clear()

    def fetch(self, rows):
        return patch("webapp.instruments.urllib.request.urlopen", return_value=io.BytesIO(
            json.dumps({"QuotationCodeTable": {"Data": rows}}).encode(),
        ))

    def test_exact_exchange_match_and_daily_cache(self):
        rows = [
            {"Code": "600519", "MktNum": "0", "Classify": "AStock", "Name": "错误市场"},
            {"Code": "600519", "MktNum": "1", "Classify": "AStock", "Name": "贵州茅台"},
        ]
        with self.fetch(rows) as fetch:
            self.assertEqual(instrument_name("600519.SS"), "贵州茅台")
            self.assertEqual(instrument_name("600519.SS"), "贵州茅台")
            fetch.assert_called_once()

    def test_hong_kong_and_us_exact_security_not_similar_code_or_bond(self):
        for ticker, rows, name in [
            ("0700.HK", [
                {"Code": "000700", "MktNum": "0", "Classify": "AStock", "Name": "模糊匹配"},
                {"Code": "00700", "MktNum": "116", "Classify": "HK", "Name": "腾讯控股"},
            ], "腾讯控股"),
            ("AAPL", [
                {"Code": "AAPL22", "MktNum": "105", "Classify": "UsStock", "Name": "债券"},
                {"Code": "AAPL", "MktNum": "105", "Classify": "UsStock", "Name": "苹果"},
            ], "苹果"),
        ]:
            with self.subTest(ticker=ticker), self.fetch(rows):
                self.assertEqual(instrument_name(ticker), name)

    def test_offline_malformed_or_missing_names_fall_back_to_ticker(self):
        with patch("webapp.instruments.urllib.request.urlopen", side_effect=TimeoutError):
            self.assertEqual(instrument_name("600519.SS"), "600519.SS")
        for payload in [b"bad json", b"{}", b"x" * 131073]:
            _display_name.cache_clear()
            with patch("webapp.instruments.urllib.request.urlopen", return_value=io.BytesIO(payload)):
                self.assertEqual(instrument_name("600519.SS"), "600519.SS")
        _display_name.cache_clear()
        with self.fetch([]):
            self.assertEqual(instrument_name("600519.SS"), "600519.SS")
        with patch("webapp.instruments.urllib.request.urlopen") as fetch:
            self.assertEqual(instrument_name("BTC-USD"), "BTC-USD")
            fetch.assert_not_called()

    def test_names_search_across_unloaded_pages_and_survive_reopen_without_changing_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            for i, ticker in enumerate(["600519.SS", "0700.HK", "600519.SS", "NIO"]):
                store.put({"id": str(i), "status": "completed", "created_at": i + 1,
                           "config": {"ticker": ticker}, "reports": {"final": "原始报告"}})
            original = store.all()
            names = {"600519.SS": "贵州茅台", "0700.HK": "腾讯控股", "NIO": "蔚来"}
            with patch("webapp.instruments.instrument_name", side_effect=names.__getitem__) as lookup:
                first = named_summaries(store, limit=1, cursor_page=True)
                self.assertEqual(first["items"][0]["display_name"], "蔚来")
                self.assertEqual(lookup.call_count, 1)
                page = named_summaries(store, query="茅台", limit=1, cursor_page=True)
                self.assertEqual(page["total"], 2)
                self.assertEqual(page["items"][0]["id"], "2")
                next_page = named_summaries(store, query="茅台", limit=1,
                                            cursor=page["next_cursor"], cursor_page=True)
                self.assertEqual(next_page["items"][0]["id"], "0")
                self.assertFalse(next_page["has_more"])
                self.assertEqual(lookup.call_count, 3)
            reopened = Store(tmp)
            with patch("webapp.instruments.instrument_name") as lookup:
                self.assertEqual(named_summaries(reopened, query="腾讯")[0]["display_name"], "腾讯控股")
                self.assertEqual(named_summaries(reopened, query="600519")[0]["display_name"], "贵州茅台")
                lookup.assert_not_called()
            self.assertEqual(store.all(), original)

    def test_refresh_failure_keeps_last_known_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.put({"id": "old", "status": "completed", "created_at": 1,
                       "config": {"ticker": "600519.SS"}})
            with store.db() as db:
                db.execute("INSERT INTO instrument_names VALUES (?, ?, ?)",
                           ("600519.SS", "贵州茅台", 0))
            with patch("webapp.instruments.instrument_name", return_value="600519.SS"):
                self.assertEqual(named_summaries(store)[0]["display_name"], "贵州茅台")
