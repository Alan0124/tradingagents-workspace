"""Shared daily OHLCV contract for stock tools, indicators, verification and charts.

Normalize query -> fetch -> normalize output (inspired by OpenBB). No synthetic
price/volume filling. Web runs pin their first valid frame on disk for replay.
"""
from __future__ import annotations

import hashlib
import json
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.evidence import current_ledger
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.symbols import mainland_symbol, normalize_symbol, safe_ticker_component
from tradingagents.dataflows.vendors import china_daily, eastmoney_market
from tradingagents.dataflows.vendors.yahoo import ohlcv as yahoo


def resolve_provider(symbol: str, provider: str = "auto") -> str:
    if provider == "auto":
        return "auto" if mainland_symbol(symbol) else "yfinance"
    return provider


def configured_provider(symbol: str, config: dict | None = None) -> str:
    """Verification/chart need matching stock and indicator selection policies.

    Legacy third-party providers/chains remain usable through the tool router,
    but cannot be silently replaced with Yahoo for the verification chart.
    """
    config = get_config() if config is None else config
    tools, vendors = config.get("tool_vendors", {}), config.get("data_vendors", {})
    sources = [resolve_provider(symbol, tools.get(method, vendors.get(category, "auto")))
               for method, category in (("get_stock_data", "core_stock_apis"),
                                        ("get_indicators", "technical_indicators"))]
    if sources[0] != sources[1] or sources[0] not in {"auto", "eastmoney", "tencent", "sina", "yfinance"}:
        raise VendorUnavailableError("Shared market data requires matching shared stock and indicator providers")
    return sources[0]


def metadata(symbol: str, provider: str) -> dict:
    if provider == "auto":
        return {"provider": "auto", "source": "Automatic market selection",
                "price_basis": "pending_provider_selection", "price_basis_note": "No successful source selected yet."}
    if provider in {"tencent", "sina"}:
        return {"provider": provider, "source": {"tencent": "Tencent Finance", "sina": "Sina Finance"}[provider],
                "currency": "CNY", "volume_unit": "shares", "price_basis": f"{provider}_unadjusted_daily",
                "price_basis_note": china_daily.PRICE_CAVEAT + (" Tencent mainland volumes may be rounded to lots." if provider == "tencent" else "")}
    if provider == "eastmoney":
        return {"provider": provider, "source": "Eastmoney", "currency": "CNY",
                "volume_unit": "shares", "amount_unit": "CNY",
                "price_basis": eastmoney_market.PRICE_BASIS,
                "price_basis_note": eastmoney_market.PRICE_BASIS_CAVEAT}
    return {"provider": provider, "source": "Yahoo Finance",
            "currency": "CNY" if mainland_symbol(symbol) else "vendor quote currency",
            "volume_unit": "shares" if mainland_symbol(symbol) else "vendor native volume (shares for equities)",
            "price_basis": yahoo.PRICE_BASIS, "price_basis_note": yahoo.PRICE_BASIS_CAVEAT}


class MarketDataText(str):
    """Rendered tool text retaining the actual source for evidence receipts."""

    def __new__(cls, content, info):
        result = super().__new__(cls, content)
        result.provider = info.get("provider", "yfinance")
        return result


def describe(symbol: str, provider: str, info: dict | None = None) -> str:
    info = info or metadata(symbol, provider)
    return (f"Source: {info['source']} ({info['provider']}); currency: {info['currency']}; "
            f"volume unit: {info['volume_unit']}\n"
            f"Price basis: {info['price_basis']}\nPrice caveat: {info['price_basis_note']}\n"
            f"Data SHA-256: {info.get('data_sha256', 'not recorded')}")


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


def _fetch(symbol, as_of_date, provider):
    if provider == "eastmoney":
        return eastmoney_market.fetch_daily(symbol, as_of_date)
    if provider == "tencent":
        return china_daily.fetch_tencent(symbol, as_of_date)
    if provider == "sina":
        return china_daily.fetch_sina(symbol, as_of_date)
    return yahoo.load_ohlcv(symbol, as_of_date, fill_gaps=False)


def _load_fixed(symbol, as_of_date, provider, config):
    snapshot_dir = config.get("market_snapshot_dir")
    if provider == "yfinance" and not snapshot_dir:
        data = _normalize(_fetch(symbol, as_of_date, provider), symbol, as_of_date, provider)
        data.attrs["data_sha256"] = hashlib.sha256(data.to_csv(index=False).encode()).hexdigest()
        return data
    directory = Path(snapshot_dir or config["data_cache_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_ticker_component(symbol)}-{provider}-{metadata(symbol, provider)['price_basis']}-{as_of_date}-v1.csv"
    with locked(path):
        if not path.exists() or (not snapshot_dir and not yahoo._cache_is_fresh(path, pd.Timestamp(as_of_date), pd.Timestamp.today())):
            data = _normalize(_fetch(symbol, as_of_date, provider), symbol, as_of_date, provider)
            replace_file(path, lambda temp: data.to_csv(temp, index=False, encoding="utf-8"))
        raw = path.read_text(encoding="utf-8")
        data = _normalize(pd.read_csv(StringIO(raw)), symbol, as_of_date, provider)
        data.attrs["data_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        return data


AUTO_CHAIN = ("eastmoney", "tencent", "sina", "yfinance")


def _load_auto(symbol, as_of_date, config):
    """Choose a whole frame once; atomically pin both its source and its bytes."""
    snapshot_dir = config.get("market_snapshot_dir")
    directory = Path(snapshot_dir or config["data_cache_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe_ticker_component(symbol)}-auto-{as_of_date}-v2.json"
    with locked(path):
        fresh = path.exists() and (snapshot_dir or yahoo._cache_is_fresh(path, pd.Timestamp(as_of_date), pd.Timestamp.today()))
        if fresh:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:
                raise VendorUnavailableError("Saved market frame is not valid JSON") from exc
        else:
            data, attempts = None, []
            # Replay a snapshot acquired before automatic fallback was added.
            # Never replace an existing research frame with a healthier provider.
            legacy = [directory / f"{safe_ticker_component(symbol)}-{p}-{metadata(symbol, p)['price_basis']}-{as_of_date}-v1.csv"
                      for p in AUTO_CHAIN] if snapshot_dir else []
            existing = [p for p in legacy if p.exists()]
            if len(existing) > 1:
                raise VendorUnavailableError("Ambiguous legacy market snapshots; refusing to mix sources")
            if existing:
                provider = AUTO_CHAIN[legacy.index(existing[0])]
                data = _normalize(pd.read_csv(existing[0], encoding="utf-8"), symbol, as_of_date, provider)
            else:
                for provider in AUTO_CHAIN:
                    try:
                        data = _normalize(_fetch(symbol, as_of_date, provider), symbol, as_of_date, provider)
                        attempts.append({"provider": provider, "status": "available"})
                        break
                    except (NoMarketDataError, VendorUnavailableError) as exc:
                        attempts.append({"provider": provider, "status": "no_data" if isinstance(exc, NoMarketDataError) else "unavailable"})
                if data is None:
                    if all(a["status"] == "no_data" for a in attempts):
                        raise NoMarketDataError(symbol, detail="no usable daily bars from the automatic market chain")
                    raise VendorUnavailableError("Automatic daily market sources unavailable: " + ", ".join(a["provider"] + "=" + a["status"] for a in attempts))
            raw = data.to_csv(index=False)
            saved = {"version": 2, "symbol": symbol, "as_of": as_of_date,
                     "provider": provider, "price_basis": data.attrs["price_basis"], "attempts": attempts,
                     "data_sha256": hashlib.sha256(raw.encode()).hexdigest(), "csv": raw}
            replace_file(path, lambda temp: Path(temp).write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8"))
        if not isinstance(saved, dict) or not isinstance(saved.get("csv"), str):
            raise VendorUnavailableError("Saved market frame has an invalid schema")
        provider = saved.get("provider")
        raw = saved["csv"]
        if (saved.get("version") != 2 or saved.get("symbol") != symbol or saved.get("as_of") != as_of_date
                or provider not in AUTO_CHAIN or saved.get("price_basis") != metadata(symbol, provider)["price_basis"]
                or hashlib.sha256(raw.encode()).hexdigest() != saved.get("data_sha256")):
            raise VendorUnavailableError("Saved market frame identity/checksum mismatch")
        data = _normalize(pd.read_csv(StringIO(raw)), symbol, as_of_date, provider)
        data.attrs.update(data_sha256=saved["data_sha256"], fallback_attempts=saved.get("attempts", []))
        return data


def load_ohlcv(symbol: str, as_of_date: str, fill_gaps: bool = False, *, provider: str | None = None) -> pd.DataFrame:
    """One shared acquisition through the run date, then filter each tool's cutoff.

    Automatic SH/SZ selection: Eastmoney -> Tencent -> Sina -> Yahoo. All cells
    come from the selected provider; no cross-provider row or column filling.
    """
    if pd.Timestamp(as_of_date).strftime("%Y-%m-%d") != as_of_date:
        raise ValueError("Analysis date must be YYYY-MM-DD")
    canonical = normalize_symbol(symbol)
    safe_ticker_component(canonical)
    provider = configured_provider(symbol) if provider is None else resolve_provider(symbol, provider)
    if provider not in {"auto", "eastmoney", "tencent", "sina", "yfinance"}:
        raise VendorUnavailableError("Unsupported shared daily price provider")
    config = get_config()
    ledger = current_ledger()
    anchor = ledger.as_of if ledger else config.get("date", as_of_date)
    if pd.Timestamp(anchor) < pd.Timestamp(as_of_date):
        raise ValueError("Requested market date exceeds the research date")

    def fetch():
        return _load_auto(canonical, anchor, config) if provider == "auto" else _load_fixed(canonical, anchor, provider, config)

    data = ledger.memoize(["shared_daily_frame_v2", canonical, anchor, provider, config.get("market_snapshot_dir"), config["data_cache_dir"]], fetch) if ledger else fetch()
    result = data[data["Date"] <= pd.Timestamp(as_of_date)].copy()
    if result.empty:
        raise NoMarketDataError(symbol, canonical, "no closing prices through requested date")
    yahoo._assert_ohlcv_not_stale(result, as_of_date, symbol, canonical)
    return result
