import io
import json
import unittest
from unittest.mock import patch

from webapp.instruments import _display_name, instrument_name


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
