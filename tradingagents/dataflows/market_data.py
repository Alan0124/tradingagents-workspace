"""Shared daily OHLCV contract for stock tools, indicators, verification and charts.

Normalize query -> fetch -> normalize output (inspired by OpenBB). No synthetic
price/volume filling. Web runs pin their first valid frame on disk for replay.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.symbols import mainland_symbol, normalize_symbol, safe_ticker_component
from tradingagents.dataflows.vendors import eastmoney_market
from tradingagents.dataflows.vendors.yahoo import ohlcv as yahoo


def resolve_provider(symbol: str, provider: str = "auto") -> str:
    if provider == "auto":
        return "eastmoney" if mainland_symbol(symbol) else "yfinance"
    return provider


def configured_provider(symbol: str, config: dict | None = None) -> str:
    """Verification/chart need one explicit, matching stock and indicator source.

    Legacy third-party providers/chains remain usable through the tool router,
    but cannot be silently replaced with Yahoo for the verification chart.
    """
    config = get_config() if config is None else config
    tools, vendors = config.get("tool_vendors", {}), config.get("data_vendors", {})
    sources = [resolve_provider(symbol, tools.get(method, vendors.get(category, "auto")))
               for method, category in (("get_stock_data", "core_stock_apis"),
                                        ("get_indicators", "technical_indicators"))]
    if sources[0] != sources[1] or sources[0] not in {"eastmoney", "yfinance"}:
        raise VendorUnavailableError("Shared market data requires matching yfinance/eastmoney stock and indicator providers")
    return sources[0]


def metadata(symbol: str, provider: str) -> dict:
    if provider == "eastmoney":
        return {"provider": provider, "source": "Eastmoney", "currency": "CNY",
                "volume_unit": "shares", "amount_unit": "CNY",
                "price_basis": eastmoney_market.PRICE_BASIS,
                "price_basis_note": eastmoney_market.PRICE_BASIS_CAVEAT}
    return {"provider": provider, "source": "Yahoo Finance", "currency": "vendor quote currency",
            "volume_unit": "vendor native volume (shares for equities)",
            "price_basis": yahoo.PRICE_BASIS, "price_basis_note": yahoo.PRICE_BASIS_CAVEAT}


def describe(symbol: str, provider: str) -> str:
    info = metadata(symbol, provider)
    return (f"Source: {info['source']} ({provider}); currency: {info['currency']}; "
            f"volume unit: {info['volume_unit']}\n"
            f"Price basis: {info['price_basis']}\nPrice caveat: {info['price_basis_note']}")


def _normalize(data, symbol, as_of_date, provider):
    if data is None or data.empty or not {"Date", "Close"}.issubset(data.columns):
        raise NoMarketDataError(symbol, normalize_symbol(symbol), "no daily price rows")
    data = yahoo._clean_dataframe(data.copy())
    columns = [c for c in ("Open", "High", "Low", "Close", "Volume", "Amount") if c in data]
    data[columns] = data[columns].replace([np.inf, -np.inf], np.nan)
    data = data[data["Date"] <= pd.Timestamp(as_of_date)]
    data = data.dropna(subset=["Close"]).sort_values("Date", kind="stable").drop_duplicates("Date", keep="last")
    if data.empty:
        raise NoMarketDataError(symbol, normalize_symbol(symbol), "no closing prices through analysis date")
    yahoo._assert_ohlcv_not_stale(data, as_of_date, symbol, normalize_symbol(symbol))
    data.attrs = metadata(symbol, provider)
    return data.reset_index(drop=True)


def load_ohlcv(symbol: str, as_of_date: str, fill_gaps: bool = False, *, provider: str | None = None) -> pd.DataFrame:
    """Daily bars through as_of_date, inclusive, with five years of warmup.

    ``fill_gaps`` is accepted for compatibility; all shared consumers use the
    original reported cells. Missing values stay missing, no invented volume.
    """
    if pd.Timestamp(as_of_date).strftime("%Y-%m-%d") != as_of_date:
        raise ValueError("Analysis date must be YYYY-MM-DD")
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)
    provider = configured_provider(symbol) if provider is None else resolve_provider(symbol, provider)
    if provider not in {"eastmoney", "yfinance"}:
        raise VendorUnavailableError("Unsupported shared daily price provider")
    config = get_config()
    snapshot_dir = config.get("market_snapshot_dir")
    if provider == "yfinance" and not snapshot_dir:
        return _normalize(yahoo.load_ohlcv(canonical, as_of_date, fill_gaps=False), canonical, as_of_date, provider)
    directory = Path(snapshot_dir or config["data_cache_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_symbol}-{provider}-{metadata(symbol, provider)['price_basis']}-{as_of_date}-v1.csv"
    with locked(path):
        now = pd.Timestamp.today()
        if path.exists() and (snapshot_dir or yahoo._cache_is_fresh(path, pd.Timestamp(as_of_date), now)):
            data = pd.read_csv(path, encoding="utf-8")
        else:
            data = (eastmoney_market.fetch_daily(canonical, as_of_date) if provider == "eastmoney"
                    else yahoo.load_ohlcv(canonical, as_of_date, fill_gaps=False))
            data = _normalize(data, canonical, as_of_date, provider)
            replace_file(path, lambda temp: data.to_csv(temp, index=False, encoding="utf-8"))
        return _normalize(data, canonical, as_of_date, provider)
