"""Whole-frame fallback, immutable replay and provider/price-basis receipts."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pandas as pd
import pytest

from tradingagents.agents import tools
from tradingagents.dataflows import market_data, router
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.evidence import EvidenceLedger
from webapp import market as chart

DAY = "2026-10-22"


def _frame(close=12.0):
    dates = pd.bdate_range(end=DAY, periods=250)
    return pd.DataFrame({"Date": dates, "Open": close, "Close": close,
                         "High": close + 1, "Low": close - 1, "Volume": 10000.0})


def _providers(monkeypatch, selected):
    calls = {}
    for provider, module, function in (
        ("eastmoney", market_data.eastmoney_market, "fetch_daily"),
        ("tencent", market_data.china_daily, "fetch_tencent"),
        ("sina", market_data.china_daily, "fetch_sina"),
        ("yfinance", market_data.yahoo, "load_ohlcv"),
    ):
        fake = Mock(return_value=_frame(), side_effect=None if provider == selected else VendorUnavailableError("unavailable"))
        monkeypatch.setattr(module, function, fake)
        calls[provider] = fake
    return calls


@pytest.mark.unit
@pytest.mark.parametrize("selected", market_data.AUTO_CHAIN)
def test_every_fallback_source_is_shared_by_all_consumers(monkeypatch, tmp_path, selected):
    calls = _providers(monkeypatch, selected)
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}
    ledger = EvidenceLedger(tmp_path / "evidence", DAY, ["market"])
    with run_config(config), ledger.activate():
        stock = router.route_to_vendor("get_stock_data", "SH600522", "2026-10-01", DAY)
        indicator = router.route_to_vendor("get_indicators", "600522.SH", "close_10_ema", DAY, 0)
        verified = tools.get_verified_market_snapshot.invoke({"symbol": "600522.SS", "curr_date": DAY})
        plotted = chart.get_market_history({**config, "ticker": "600522", "date": DAY})
        frame = market_data.load_ohlcv("600522.SS", DAY)
    digest = frame.attrs["data_sha256"]
    assert len(digest) == 64
    assert plotted["provider"] == frame.attrs["provider"] == selected
    assert plotted["data_sha256"] == digest
    assert plotted["price_basis"] == frame.attrs["price_basis"]
    assert plotted["series"][-1] == {"date": DAY, "close": 12.0}
    for text in (stock, indicator, verified):
        assert digest in text
        assert frame.attrs["price_basis"] in text
        assert frame.attrs["source"] in text
    receipts = json.loads(ledger.path.read_text(encoding="utf-8"))["evidence"]
    assert len(receipts) == 3
    assert all(receipt["source"] == selected for receipt in receipts)
    for index, provider in enumerate(market_data.AUTO_CHAIN):
        assert calls[provider].call_count == int(index <= market_data.AUTO_CHAIN.index(selected))


@pytest.mark.unit
def test_earlier_tool_cutoff_uses_same_run_acquisition(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, "tencent")
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}
    ledger = EvidenceLedger(tmp_path, DAY, ["market"])
    with run_config(config), ledger.activate():
        earlier = market_data.load_ohlcv("600522", "2026-10-20")
        latest = market_data.load_ohlcv("600522", DAY)
        assert earlier["Date"].max() == pd.Timestamp("2026-10-20")
        assert latest["Date"].max() == pd.Timestamp(DAY)
        assert earlier.attrs["data_sha256"] == latest.attrs["data_sha256"]
    calls["tencent"].assert_called_once_with("600522.SS", DAY)
    # Cross-process/restart equivalent: the file's source remains Tencent,
    # even after Eastmoney recovers and its ordinary cache expires.
    calls["eastmoney"].side_effect = None
    calls["eastmoney"].return_value = _frame(99.0)
    monkeypatch.setattr(market_data.yahoo, "_cache_is_fresh", lambda *args: False)
    with run_config(config):
        replay = market_data.load_ohlcv("600522", DAY)
    assert replay.attrs["provider"] == "tencent"
    assert replay["Close"].iloc[-1] == 12.0
    assert replay.attrs["data_sha256"] == latest.attrs["data_sha256"]
    calls["eastmoney"].assert_called_once()


@pytest.mark.unit
def test_bad_or_stale_primary_is_rejected_as_a_whole(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, "tencent")
    calls["eastmoney"].side_effect = None
    calls["eastmoney"].return_value = _frame(99.0).assign(Date="2020-01-01")
    with run_config({"data_cache_dir": str(tmp_path)}):
        data = market_data.load_ohlcv("600522", DAY)
    assert data.attrs["provider"] == "tencent"
    assert set(data["Close"]) == {12.0}
    assert data.attrs["fallback_attempts"][0]["status"] == "no_data"


@pytest.mark.unit
def test_all_sources_fail_without_poisoning_the_cache(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, None)
    with run_config({"data_cache_dir": str(tmp_path)}):
        with pytest.raises(VendorUnavailableError, match="Automatic daily"):
            market_data.load_ohlcv("600522", DAY)
        assert not list(tmp_path.glob("*.json"))
        for fake in calls.values():
            fake.side_effect = NoMarketDataError("600522")
        with pytest.raises(NoMarketDataError):
            market_data.load_ohlcv("600522", DAY)
        assert not list(tmp_path.glob("*.json"))


@pytest.mark.unit
def test_explicit_source_never_falls_back(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, "tencent")
    with run_config({"data_cache_dir": str(tmp_path)}), pytest.raises(VendorUnavailableError):
        market_data.load_ohlcv("600522", DAY, provider="eastmoney")
    calls["tencent"].assert_not_called()


@pytest.mark.unit
def test_concurrent_consumers_and_corrupt_snapshot(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, "sina")
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}

    def read(_):
        with run_config(config):
            data = market_data.load_ohlcv("600522", DAY)
        fingerprint = data.attrs["data_sha256"]
        data.loc[0, "Close"] = 999.0
        return fingerprint

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(read, range(8)))) == 1
    for provider in ("eastmoney", "tencent", "sina"):
        calls[provider].assert_called_once()
    path = next((tmp_path / "pinned").glob("*.json"))
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["csv"] = saved["csv"].replace("12.0", "99.0")
    path.write_text(json.dumps(saved), encoding="utf-8")
    with run_config(config), pytest.raises(VendorUnavailableError, match="checksum"):
        market_data.load_ohlcv("600522", DAY)
    calls["yfinance"].assert_not_called()


@pytest.mark.unit
def test_legacy_frame_is_replayed_before_trying_healthy_primary(monkeypatch, tmp_path):
    calls = _providers(monkeypatch, "eastmoney")
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}
    with run_config(config):
        directory = tmp_path / "pinned"
        directory.mkdir()
        path = directory / f"600522.SS-yfinance-{market_data.yahoo.PRICE_BASIS}-{DAY}-v1.csv"
        _frame(50.0).to_csv(path, index=False)
        replay = market_data.load_ohlcv("600522", DAY)
    assert replay.attrs["provider"] == "yfinance"
    assert replay["Close"].iloc[-1] == 50.0
    for fake in calls.values():
        fake.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("corrupt", ["not-json", "[]", '{"csv": 123}', '{"csv": ""}'])
def test_invalid_saved_bundle_is_not_silently_replaced(monkeypatch, tmp_path, corrupt):
    calls = _providers(monkeypatch, "eastmoney")
    config = {"data_cache_dir": str(tmp_path), "market_snapshot_dir": str(tmp_path / "pinned")}
    with run_config(config):
        market_data.load_ohlcv("600522", DAY)
        path = next((tmp_path / "pinned").glob("*.json"))
        path.write_text(corrupt, encoding="utf-8")
        with pytest.raises(VendorUnavailableError, match="Saved market frame"):
            market_data.load_ohlcv("600522", DAY)
    calls["eastmoney"].assert_called_once()
    calls["tencent"].assert_not_called()
