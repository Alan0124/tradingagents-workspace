"""All Yahoo price consumers use Close rather than dividend-adjusted Adj Close."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pandas as pd
import pytest

from tradingagents.dataflows.vendors.yahoo import market, ohlcv, snapshot
from webapp import market as chart_market

DAY = "2026-10-30"


def _action_divergence_frame() -> pd.DataFrame:
    """Model an ex-dividend/split history where Adj Close is visibly different."""
    dates = pd.bdate_range(end=DAY, periods=30)
    close = [50.0] * len(dates)
    frame = pd.DataFrame({
        "Open": [49.0] * len(dates),
        "High": [51.0] * len(dates),
        "Low": [48.0] * len(dates),
        "Close": close,
        "Adj Close": [37.5] * len(dates),
        "Volume": [1000] * len(dates),
        "Dividends": [0.0] * (len(dates) - 1) + [1.0],
        "Stock Splits": [0.0] * (len(dates) - 1) + [2.0],
    }, index=dates)
    frame.index.name = "Date"
    return frame


@pytest.mark.unit
def test_yahoo_stock_indicators_snapshot_and_chart_share_close_basis(monkeypatch, tmp_path):
    frame = _action_divergence_frame()
    history_calls = []

    def history(**kwargs):
        history_calls.append(kwargs)
        return frame.copy()

    monkeypatch.setattr(market.yf, "Ticker", lambda symbol: SimpleNamespace(history=history))
    monkeypatch.setattr(ohlcv, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(ohlcv.pd.Timestamp, "today", staticmethod(lambda: pd.Timestamp(DAY)))

    stock_output = market.get_YFin_data_online("AAPL", "2026-10-01", DAY)
    stock_frame = pd.read_csv(StringIO(stock_output), comment="#", index_col=0)
    assert stock_frame["Close"].iloc[-1] == 50.0
    assert stock_frame["Adj Close"].iloc[-1] == 37.5
    assert history_calls[-1]["auto_adjust"] is False
    assert history_calls[-1]["actions"] is False

    indicators = market.get_stock_stats_indicators_window("AAPL", "close_10_ema", DAY, 0)
    assert "2026-10-30: 50.0" in indicators
    assert (tmp_path / f"AAPL-YFin-data-{ohlcv.OHLCV_CACHE_VERSION}.csv").exists()
    assert history_calls[-1]["auto_adjust"] is False
    assert history_calls[-1]["actions"] is False

    verified = snapshot.build_verified_market_snapshot("AAPL", DAY, look_back_days=1)
    assert "| Close | 50.00 |" in verified
    assert "| 2026-10-30 | 50.00 |" in verified
    assert f"Price basis: {ohlcv.PRICE_BASIS}" in verified

    chart_market._CACHE.clear()
    monkeypatch.setattr(chart_market.yf, "download", lambda **kwargs: frame.copy())
    chart = chart_market.get_market_history({"ticker": "AAPL", "date": DAY})
    assert chart["series"][-1] == {"date": DAY, "close": 50.0}
    assert chart["price_basis"] == ohlcv.PRICE_BASIS
