"""HTTP helpers shared by the vendors."""

import json

import requests

from tradingagents.dataflows.errors import VendorUnavailableError


def get_json_bounded(url: str, *, params: dict, source: str):
    """Read public market JSON with bounded time/size and credential-free errors."""
    try:
        with requests.get(url, params=params, timeout=(4, 10), stream=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(64_000):
                size += len(chunk)
                if size > 2_000_000:
                    raise VendorUnavailableError(f"{source} response exceeded size limit")
                chunks.append(chunk)
        return json.loads(b"".join(chunks))
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise VendorUnavailableError(f"{source} daily prices temporarily unavailable") from exc


def get_scrubbed(url: str, *, params: dict, timeout: float, secret: str, passthrough=()):
    """``requests.get`` plus ``raise_for_status``, with ``secret`` kept out of errors.

    Vendors that authenticate with a query parameter put the key in the URL, and
    requests quotes the full URL in HTTP, connection and timeout errors, so any
    log or traceback that records one would carry the key (#1324). A requests
    error is re-raised as the same class with the key replaced and nothing
    attached: no request or response (both hold the URL) and no exception chain,
    which is why this raises after the ``except`` block rather than inside it.
    Statuses in ``passthrough`` are returned for the caller to handle.
    """
    try:
        response = requests.get(url, params=params, timeout=timeout)
        if response.status_code not in passthrough:
            response.raise_for_status()
        return response
    except requests.RequestException as exc:
        error = type(exc)(str(exc).replace(secret, "***")) if secret else exc
    raise error


def vendor_reachable(url: str, timeout: float = 5.0) -> bool:
    """Whether the vendor answers at all, for telling silence from an outage.

    A client that returns an empty result instead of raising leaves those two
    cases indistinguishable. Called only when a result is empty.
    """
    try:
        requests.head(url, timeout=timeout, allow_redirects=True)
        return True
    except requests.RequestException:
        return False
