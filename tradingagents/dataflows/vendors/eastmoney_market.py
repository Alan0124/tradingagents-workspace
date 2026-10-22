"""沪深 A-share daily bars, using the public endpoint documented by AKShare.

Independent adapter; no AKShare runtime dependency. Volume from the endpoint
is in lots (手), converted here to shares. Prices are unadjusted (fqt=0).
"""
from __future__ import annotations

import json

import pandas as pd
import requests

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.symbols import mainland_symbol

URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
PRICE_BASIS = "eastmoney_unadjusted_daily"
PRICE_BASIS_CAVEAT = (
    "Unadjusted daily OHLC in CNY; volume converted from lots to shares (1 lot = 100 shares). "
    "Cash dividends and splits are not adjusted; ex-rights gaps affect indicators. "
    "Current-session daily bars may be provisional. This is a current vendor history, "
    "not a guaranteed point-in-time vintage."
)


def fetch_daily(symbol: str, as_of_date: str) -> pd.DataFrame:
    canonical = mainland_symbol(symbol)
    if canonical is None or canonical.endswith(".BJ"):
        raise NoMarketDataError(symbol, canonical, "Eastmoney daily adapter supports SH/SZ A-shares only")
    code, exchange = canonical.split(".")
    end = pd.Timestamp(as_of_date)
    params = {
        "secid": f"{1 if exchange == 'SS' else 0}.{code}",
        "klt": "101", "fqt": "0",
        "beg": (end - pd.DateOffset(years=5)).strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    try:
        with requests.get(URL, params=params, timeout=(4, 12), stream=True,
                          headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(64_000):
                size += len(chunk)
                if size > 2_000_000:
                    raise VendorUnavailableError("Eastmoney daily response exceeded size limit")
                chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, dict) or payload.get("rc") != 0:
            raise VendorUnavailableError("Eastmoney daily response is invalid")
        data = payload.get("data")
        if data is None:
            raise NoMarketDataError(symbol, canonical, "no daily bars")
        if not isinstance(data, dict) or str(data.get("code")) != code:
            raise VendorUnavailableError("Eastmoney daily response symbol mismatch")
        rows = data.get("klines")
        if not isinstance(rows, list):
            raise VendorUnavailableError("Eastmoney daily response has no valid table")
        records = []
        for row in rows:
            cells = row.split(",") if isinstance(row, str) else []
            if len(cells) < 7:
                raise VendorUnavailableError("Eastmoney daily response has a malformed row")
            records.append(cells[:7])
        frame = pd.DataFrame(records, columns=["Date", "Open", "Close", "High", "Low", "Volume", "Amount"])
        frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce") * 100
        frame["Amount"] = pd.to_numeric(frame["Amount"], errors="coerce")
        return frame
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise VendorUnavailableError("Eastmoney daily prices temporarily unavailable") from exc
