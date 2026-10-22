"""Unadjusted Tencent/Sina daily OHLCV; independent public-API adapters.

AKShare's Tencent adapter informed this integration. We use Tencent's older
date-bounded endpoint: the new endpoint can return 640 bars extending before
the requested start, and minimal queries without its JSONP variable ignored
the historical end in live probes. Never substitute qfqday/hfqday for raw day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.net import get_json_bounded
from tradingagents.dataflows.symbols import mainland_symbol

TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
PRICE_CAVEAT = (
    "Unadjusted daily OHLC in CNY; volume in shares. Cash dividends and splits are "
    "not adjusted; ex-rights gaps affect indicators. Current-session bars may be "
    "provisional. This current vendor history is not a guaranteed point-in-time vintage."
)


def _symbol(symbol):
    canonical = mainland_symbol(symbol)
    if canonical is None or canonical.endswith(".BJ"):
        raise NoMarketDataError(symbol, canonical, "Tencent/Sina daily adapters support SH/SZ A-shares only")
    code, market = canonical.split(".")
    return ("sh" if market == "SS" else "sz") + code


def _frame(records, symbol, source):
    frame = pd.DataFrame(records, columns=["Date", "Open", "Close", "High", "Low", "Volume"])
    if frame.empty:
        raise NoMarketDataError(symbol, detail=f"{source} returned no daily bars")
    frame["Date"] = pd.to_datetime(frame["Date"], format="%Y-%m-%d", errors="coerce")
    numeric = ["Open", "Close", "High", "Low", "Volume"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="coerce")
    if (frame.isna().any().any() or not np.isfinite(frame[numeric]).all().all()
            or (frame[numeric[:-1]] <= 0).any().any() or (frame["Volume"] < 0).any()
            or (frame["Low"] > frame[["Open", "Close", "High"]].min(axis=1)).any()
            or (frame["High"] < frame[["Open", "Close", "Low"]].max(axis=1)).any()):
        raise VendorUnavailableError(f"{source} returned invalid daily OHLCV")
    return frame


def fetch_tencent(symbol: str, as_of_date: str) -> pd.DataFrame:
    ticker = _symbol(symbol)
    end = pd.Timestamp(as_of_date)
    start = end - pd.DateOffset(years=5)
    records = []
    # At most six calendar-year requests; each stays below Tencent's 640-bar cap.
    for year in range(start.year, end.year + 1):
        first = max(start, pd.Timestamp(year=year, month=1, day=1)).strftime("%Y-%m-%d")
        last = min(end, pd.Timestamp(year=year, month=12, day=31)).strftime("%Y-%m-%d")
        payload = get_json_bounded(TENCENT_URL, params={"param": f"{ticker},day,{first},{last},640,"}, source="Tencent")
        if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            raise VendorUnavailableError("Tencent daily response is invalid")
        item = payload["data"].get(ticker)
        if item is None and not payload["data"]:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("day"), list):
            raise VendorUnavailableError("Tencent did not return unadjusted daily bars for the requested symbol")
        for row in item["day"]:
            if not isinstance(row, list) or len(row) < 6:
                raise VendorUnavailableError("Tencent daily response has a malformed row")
            # Reject ignored query bounds instead of silently accepting a truncated history.
            if not isinstance(row[0], str) or not first <= row[0] <= last:
                raise VendorUnavailableError("Tencent daily response ignored requested date bounds")
            records.append(row[:6])
    frame = _frame(records, symbol, "Tencent")
    # Live probes: SH688/689 report shares; other SH/SZ stocks (including
    # SZ000001, which must not be confused with an index) report rounded lots.
    if not ticker.startswith(("sh688", "sh689")):
        frame["Volume"] *= 100
    return frame


def fetch_sina(symbol: str, as_of_date: str) -> pd.DataFrame:
    ticker = _symbol(symbol)
    payload = get_json_bounded(SINA_URL, params={"symbol": ticker, "scale": "240", "ma": "no", "datalen": "2000"}, source="Sina")
    if not isinstance(payload, list):
        raise VendorUnavailableError("Sina daily response is invalid")
    fields = ("day", "open", "close", "high", "low", "volume")
    if any(not isinstance(row, dict) or not set(fields).issubset(row) for row in payload):
        raise VendorUnavailableError("Sina daily response has a malformed row")
    frame = _frame([[row[field] for field in fields] for row in payload], symbol, "Sina")
    # Sina returns its recent history, rather than honoring an end-date query.
    return frame[frame["Date"].between(pd.Timestamp(as_of_date) - pd.DateOffset(years=5), pd.Timestamp(as_of_date))]
