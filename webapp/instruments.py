"""Cached Chinese display names; ticker remains the research identifier."""

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from urllib.parse import urlencode


def _lookup_key(ticker):
    match = re.fullmatch(r"(\d{6})\.(SS|SH|SZ)", ticker)
    if match:
        return match[1], {"1" if match[2] in {"SS", "SH"} else "0"}, "AStock"
    match = re.fullmatch(r"(\d{1,5})\.HK", ticker)
    if match:
        return match[1].zfill(5), {"116"}, "HK"
    if (not ticker.endswith(("-USD", "-USDT", "-USDC"))
            and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", ticker)):
        return ticker, {"105", "106", "107"}, "UsStock"
    return None


@lru_cache(maxsize=512)
def _display_name(ticker, day):
    key = _lookup_key(ticker)
    if key is None:
        return ticker
    code, markets, classification = key
    query = urlencode({"input": code, "type": "14", "count": "10"})
    request = urllib.request.Request(
        "https://searchapi.eastmoney.com/api/suggest/get?" + query,
        headers={"User-Agent": "TradingAgentsWorkspace/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = response.read(131073)
        if len(payload) > 131072:
            return ticker
        rows = json.loads(payload)["QuotationCodeTable"]["Data"]
        for row in rows:
            if (row.get("Code") != code or str(row.get("MktNum")) not in markets
                    or row.get("Classify") != classification):
                continue
            name = row.get("Name")
            if (isinstance(name, str) and 0 < len(name.strip()) <= 64
                    and re.search(r"[\u3400-\u9fff]", name)
                    and not any(ord(char) < 32 for char in name)):
                return name.strip()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return ticker


def instrument_name(ticker):
    """Use exact code AND exchange matches, falling back safely when offline."""
    return _display_name(ticker.upper(), int(time.time() // 86400))


def named_summaries(store, **params):
    """Enrich compact lists and index names for searching across all pages."""
    page = store.summaries(**params)
    items = page["items"] if isinstance(page, dict) else page
    tickers = {run["config"]["ticker"] for run in items}
    with store.lock, store.db() as db:
        if re.search(r"[\u3400-\u9fff]", params.get("query", "")):
            tickers.update(row[0] for row in db.execute(
                "SELECT DISTINCT json_extract(summary_json, '$.config.ticker') FROM runs"
            ) if row[0])
        cached = {row[0] for row in db.execute(
            "SELECT ticker FROM instrument_names WHERE updated_at>=?", (int(time.time() // 86400) * 86400,)
        )}
    missing = sorted(tickers - cached)
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            names = list(pool.map(instrument_name, missing))
        with store.lock, store.db() as db:
            db.executemany(
                "INSERT INTO instrument_names(ticker, name, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET "
                "name=CASE WHEN excluded.name=excluded.ticker THEN instrument_names.name "
                "ELSE excluded.name END, updated_at=excluded.updated_at",
                [(ticker, name, time.time()) for ticker, name in zip(missing, names, strict=True)],
            )
        page = store.summaries(**params)
    return page
