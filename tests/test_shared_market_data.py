"""Offline contract/regressions for shared daily prices and the A-share adapter."""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from tradingagents.agents import tools
from tradingagents.dataflows import market_data, router
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.evidence import EvidenceLedger
from tradingagents.dataflows.vendors import eastmoney_market
from webapp import market as chart

DAY = "2026-10-21"


def _payload(code="600519", rows=None):
    return {"rc": 0, "data": {"code": code, "klines": rows if rows is not None else [
        f"{DAY},10,12,13,9,123,45678,1,2,3,4",
    ]}}


def _response(monkeypatch, payload):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [json.dumps(payload).encode()]
    get = Mock(return_value=response)
    monkeypatch.setattr(eastmoney_market.requests, "get", get)
    return get


@pytest.mark.unit
@pytest.mark.parametrize(("symbol", "code", "secid"), [
    ("600519.SH", "600519", "1.600519"), ("SH600519", "600519", "1.600519"),
    ("600519.SS", "600519", "1.600519"), ("000001", "000001", "0.000001"),
    ("SZ000001", "000001", "0.000001"), ("300750.SZ", "300750", "0.300750"),
])
def test_mainland_query_and_lot_conversion(monkeypatch, symbol, code, secid):
    get = _response(monkeypatch, _payload(code))
    data = eastmoney_market.fetch_daily(symbol, DAY)
    assert data.iloc[0]["Close"] == "12"
    assert data.iloc[0]["Volume"] == 12300
    assert data.iloc[0]["Amount"] == 45678
    query = get.call_args.kwargs
    assert query["params"]["secid"] == secid
    assert query["params"]["fqt"] == "0"
    assert query["params"]["end"] == "20261021"
    assert query["timeout"] == (4, 12)


@pytest.mark.unit
@pytest.mark.parametrize("symbol", ["AAPL", "000001.SH", "BJ920001", "830799.BJ"])
def test_unsupported_symbols_never_request(monkeypatch, symbol):
    get = _response(monkeypatch, _payload())
    with pytest.raises(NoMarketDataError):
        eastmoney_market.fetch_daily(symbol, DAY)
    get.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("payload", [[], {"rc": -1}, {"rc": 0, "data": []},
    _payload("000001"), _payload(rows=["bad"]), {"rc": 0, "data": {"code": "600519", "klines": None}}])
def test_bad_provider_response_is_unavailable(monkeypatch, payload):
    _response(monkeypatch, payload)
    with pytest.raises(VendorUnavailableError):
        eastmoney_market.fetch_daily("600519", DAY)


@pytest.mark.unit
def test_empty_timeout_and_size_limit(monkeypatch):
    _response(monkeypatch, {"rc": 0, "data": None})
    with pytest.raises(NoMarketDataError):
        eastmoney_market.fetch_daily("600519", DAY)
    get = _response(monkeypatch, _payload())
    get.side_effect = requests.Timeout("secret")
    with pytest.raises(VendorUnavailableError, match="temporarily unavailable"):
        eastmoney_market.fetch_daily("600519", DAY)
    get = _response(monkeypatch, _payload())
    get.return_value.iter_content.return_value = [b" " * 2_000_001]
    with pytest.raises(VendorUnavailableError, match="size limit"):
        eastmoney_market.fetch_daily("600519", DAY)


@pytest.mark.unit
def test_chart_tools_indicators_snapshot_share_pinned_frame_and_receipts(monkeypatch, tmp_path):
    dates = pd.bdate_range(end=DAY, periods=40)
    rows = [f"{day:%Y-%m-%d},10,12,13,9,123,45678" for day in dates]
    get = _response(monkeypatch, _payload(rows=rows))
    config = {"data_cache_dir": str(tmp_path / "cache"), "market_snapshot_dir": str(tmp_path / "pinned")}
    ledger = EvidenceLedger(tmp_path / "evidence", DAY, ["market"])
    with run_config(config), ledger.activate():
        stock = router.route_to_vendor("get_stock_data", "SH600519", "2026-10-01", DAY)
        indicator = router.route_to_vendor("get_indicators", "600519.SH", "close_10_ema", DAY, 0)
        verified = tools.get_verified_market_snapshot.invoke({"symbol": "600519.SS", "curr_date": DAY, "look_back_days": 1})
        output = chart.get_market_history({**config, "ticker": "600519", "date": DAY})
        frame = market_data.load_ohlcv("600519", DAY)
    parsed = pd.read_csv(StringIO(stock[stock.index("# Stock data"):]), comment="#")
    assert parsed.iloc[-1]["Close"] == 12
    assert parsed.iloc[-1]["Volume"] == 12300
    assert f"{DAY}: 12.0" in indicator
    assert "| Close | 12 |" in verified or "| Close | 12.00 |" in verified
    assert "| Volume | 12300 |" in verified or "| Volume | 12300.00 |" in verified
    assert "Source: Eastmoney" in stock and "Source: Eastmoney" in verified
    assert output["series"][-1] == {"date": DAY, "close": 12.0}
    assert output["currency"] == "CNY" and output["volume_unit"] == "shares"
    assert output["price_basis"] == frame.attrs["price_basis"] == eastmoney_market.PRICE_BASIS
    get.assert_called_once()
    # A replay in a fresh context reads the original acquisition even after TTL.
    monkeypatch.setattr(market_data.yahoo, "_cache_is_fresh", lambda *a: False)
    with run_config(config):
        assert market_data.load_ohlcv("600519.SH", DAY)["Close"].iloc[-1] == 12
    get.assert_called_once()
    receipts = json.loads(ledger.path.read_text(encoding="utf-8"))["evidence"]
    assert all(record["source"] == "eastmoney" for record in receipts)


@pytest.mark.unit
def test_shared_normalization_cutoff_duplicates_missing_and_stale(monkeypatch, tmp_path):
    raw = pd.DataFrame({"Date": [DAY, "2026-10-20", DAY, "2026-10-22", "2026-10-19"],
                        "Close": [11, 10, 12, 999, float("inf")],
                        "Open": [9, None, 10, 999, 9], "Volume": [100, None, 200, 999, 1]})
    fetch = Mock(return_value=raw)
    monkeypatch.setattr(eastmoney_market, "fetch_daily", fetch)
    with run_config({"data_cache_dir": str(tmp_path), "data_vendors": {"core_stock_apis": "eastmoney", "technical_indicators": "eastmoney"}}):
        result = market_data.load_ohlcv("600519", DAY)
        assert result["Close"].tolist() == [10, 12]
        assert pd.isna(result["Open"].iloc[0]) and pd.isna(result["Volume"].iloc[0])
        assert len(list(tmp_path.glob("*.csv"))) == 1
        market_data.load_ohlcv("600519.SH", DAY)
        fetch.assert_called_once()
        fetch.return_value = raw.assign(Date="2025-01-01")
        with pytest.raises(NoMarketDataError, match="stale"):
            market_data.load_ohlcv("000001", DAY)
        assert len(list(tmp_path.glob("*.csv"))) == 1


@pytest.mark.unit
def test_explicit_source_and_mismatched_sources(monkeypatch, tmp_path):
    assert market_data.resolve_provider("600519", "yfinance") == "yfinance"
    assert market_data.resolve_provider("AAPL") == "yfinance"
    assert market_data.resolve_provider("600519") == "auto"
    for config in ({"data_vendors": {"core_stock_apis": "default", "technical_indicators": "default"}},
                   {"data_vendors": {"core_stock_apis": "alpha_vantage"}},
                   {"tool_vendors": {"get_stock_data": "yfinance"}}):
        with run_config(config), pytest.raises(VendorUnavailableError, match="matching"):
            market_data.load_ohlcv("600519", DAY)


@pytest.mark.unit
def test_cache_refresh_and_provider_isolation(monkeypatch, tmp_path):
    raw = pd.DataFrame({"Date": [DAY], "Close": [12.0], "Volume": [100]})
    fetch = Mock(return_value=raw)
    monkeypatch.setattr(eastmoney_market, "fetch_daily", fetch)
    monkeypatch.setattr(market_data.yahoo, "load_ohlcv", Mock(return_value=raw.assign(Close=99.0)))
    with run_config({"data_cache_dir": str(tmp_path)}):
        assert market_data.load_ohlcv("600519", DAY)["Close"].iloc[-1] == 12
        monkeypatch.setattr(market_data.yahoo, "_cache_is_fresh", lambda *a: False)
        fetch.return_value = raw.assign(Close=13.0)
        assert market_data.load_ohlcv("600519", DAY)["Close"].iloc[-1] == 13
        assert fetch.call_count == 2
        yahoo = market_data.load_ohlcv("600519", DAY, provider="yfinance")
        assert yahoo["Close"].iloc[-1] == 99
        assert yahoo.attrs["provider"] == "yfinance"
        assert market_data.load_ohlcv("600519", DAY)["Close"].iloc[-1] == 13
        market_data.yahoo.load_ohlcv.assert_called_once_with("600519.SS", DAY, fill_gaps=False)


@pytest.mark.unit
def test_concurrent_snapshot_readers_fetch_once(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    raw = pd.DataFrame({"Date": [DAY], "Close": [12.0], "Volume": [100]})
    fetch = Mock(return_value=raw)
    monkeypatch.setattr(eastmoney_market, "fetch_daily", fetch)
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}

    def read(_):
        with run_config(config):
            frame = market_data.load_ohlcv("600519", DAY)
            value = frame["Close"].iloc[-1]
            frame.loc[0, "Close"] = 999
            return value

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, range(8))) == [12] * 8
    fetch.assert_called_once()
