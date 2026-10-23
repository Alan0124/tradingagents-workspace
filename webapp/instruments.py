"""Cached Chinese display names; ticker remains the research identifier."""

import json
import re
import time
import urllib.request
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
