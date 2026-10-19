"""Yahoo Finance history used by the local research charts."""

from __future__ import annotations

import copy
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta

import yfinance as yf

from tradingagents.dataflows.symbols import normalize_symbol

SOURCE = "Yahoo Finance"
MARKET_TIMEOUT_SECONDS = 8
MARKET_LOOKBACK_DAYS = 120
MARKET_CACHE_TTL_SECONDS = 15 * 60
EMPTY_CACHE_TTL_SECONDS = 60
MAX_CACHE_ENTRIES = 256

_SYMBOL_RE = re.compile(r"[A-Z0-9^][A-Z0-9.^=\-]{0,30}\Z")
_CACHE: OrderedDict[tuple[str, str], tuple[float, dict]] = OrderedDict()
_CACHE_LOCK = threading.RLock()


def _date_from_index(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _read_cache(key: tuple[str, str]) -> dict | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= now:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _write_cache(key: tuple[str, str], value: dict, ttl: int) -> None:
    now = time.monotonic()
    with _CACHE_LOCK:
        for old_key, (expires_at, _) in list(_CACHE.items()):
            if expires_at <= now:
                del _CACHE[old_key]
        _CACHE[key] = (now + ttl, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > MAX_CACHE_ENTRIES:
            _CACHE.popitem(last=False)


def _history_series(history, as_of: date) -> list[dict]:
    if history is None or getattr(history, "empty", True):
        return []

    try:
        closes = history["Close"]
    except (KeyError, TypeError, IndexError):
        return []
    if getattr(closes, "ndim", 1) > 1:
        if getattr(closes, "shape", (0,))[1] == 0:
            return []
        closes = closes.iloc[:, 0]

    by_date: dict[str, float] = {}
    for raw_date, raw_close in closes.items():
        trading_date = _date_from_index(raw_date)
        if (
            trading_date is None
            or trading_date > as_of
            or trading_date < as_of - timedelta(days=MARKET_LOOKBACK_DAYS - 1)
        ):
            continue
        if isinstance(raw_close, bool):
            continue
        try:
            close = float(raw_close)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(close):
            by_date[trading_date.isoformat()] = close
    return [{"date": day, "close": by_date[day]} for day in sorted(by_date)]


def get_market_history(config: dict) -> dict:
    """Return up to 120 calendar days of daily closes through config.date.

    Empty Yahoo responses are returned as an empty series. Invalid configs raise
    ``ValueError``; provider/network failures raise a short ``RuntimeError`` so
    the HTTP layer can return an error response to the chart.
    """
    if not isinstance(config, dict):
        raise ValueError("研究配置格式不正确。")

    raw_ticker = config.get("ticker")
    if not isinstance(raw_ticker, str) or not raw_ticker.strip():
        raise ValueError("研究配置缺少有效标的代码。")
    ticker = raw_ticker.strip().upper()
    symbol = normalize_symbol(ticker)
    if not isinstance(symbol, str) or not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("研究配置中的标的代码无效。")

    raw_as_of = config.get("date", config.get("analysis_date"))
    if not isinstance(raw_as_of, str):
        raise ValueError("研究配置缺少有效分析日期。")
    try:
        as_of = datetime.strptime(raw_as_of, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("研究配置中的分析日期无效。") from None
    if as_of.isoformat() != raw_as_of:
        raise ValueError("研究配置中的分析日期无效。")

    as_of_text = as_of.isoformat()
    key = (symbol, as_of_text)
    cached = _read_cache(key)
    if cached is not None:
        return cached

    # The start and exclusive end span exactly 120 calendar days, including
    # the configured analysis date in Yahoo's daily history request.
    start = as_of - timedelta(days=MARKET_LOOKBACK_DAYS - 1)
    end = as_of + timedelta(days=1)
    try:
        history = yf.download(
            tickers=symbol,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
            timeout=MARKET_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise RuntimeError("Yahoo Finance 行情暂时不可用。") from exc

    payload = {
        "series": _history_series(history, as_of),
        "symbol": symbol,
        "as_of": as_of_text,
        "source": SOURCE,
    }
    ttl = MARKET_CACHE_TTL_SECONDS if payload["series"] else EMPTY_CACHE_TTL_SECONDS
    _write_cache(key, payload, ttl)
    return copy.deepcopy(payload)
