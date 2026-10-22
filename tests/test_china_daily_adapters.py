"""Provider schema, date-bounded queries and mainland board volume semantics."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from tradingagents.dataflows import net
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors import china_daily

DAY = "2026-10-22"


@pytest.mark.unit
@pytest.mark.parametrize(("symbol", "ticker", "shares"), [
    ("600522.SH", "sh600522", 12300), ("SH600522", "sh600522", 12300),
    ("000001", "sz000001", 12300), ("SZ000001", "sz000001", 12300),
    ("300750.SZ", "sz300750", 12300), ("688981", "sh688981", 123),
    ("689009.SH", "sh689009", 123),
])
def test_tencent_year_windows_and_volume(monkeypatch, symbol, ticker, shares):
    calls = []

    def get(url, *, params, source):
        parts = params["param"].split(",")
        calls.append((url, parts, source))
        return {"code": 0, "data": {ticker: {"day": [[parts[3], "10", "12", "13", "9", "123"]]}}}

    monkeypatch.setattr(china_daily, "get_json_bounded", get)
    frame = china_daily.fetch_tencent(symbol, DAY)
    assert len(calls) == len(frame) == 6
    assert frame["Volume"].tolist() == [shares] * 6
    assert frame["Date"].max() == pd.Timestamp(DAY)
    assert calls[0][1][2] == "2021-10-22"
    assert calls[-1][1][3] == DAY
    assert all(c[1][0] == ticker and c[1][1] == "day" and c[1][-1] == "" for c in calls)
    assert all(c[0] == china_daily.TENCENT_URL for c in calls)


@pytest.mark.unit
@pytest.mark.parametrize("symbol", ["AAPL", "000001.SH", "920001.BJ"])
@pytest.mark.parametrize("fetch", [china_daily.fetch_tencent, china_daily.fetch_sina])
def test_unsupported_codes_never_request(monkeypatch, symbol, fetch):
    get = Mock()
    monkeypatch.setattr(china_daily, "get_json_bounded", get)
    with pytest.raises(NoMarketDataError):
        fetch(symbol, DAY)
    get.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("data", [
    [], {"code": -1}, {"code": 0, "data": []},
    {"code": 0, "data": {"sz000001": {"day": []}}},
    {"code": 0, "data": {"sh600522": {"qfqday": [["2021-12-31", "10", "12", "13", "9", "123"]]}}},
    {"code": 0, "data": {"sh600522": {"day": ["bad"]}}},
    {"code": 0, "data": {"sh600522": {"day": [[DAY, "10", "12", "13", "9", "123"]]}}},
    {"code": 0, "data": {"sh600522": {"day": [["2021-12-31", "10", "12", "11", "9", "123"]]}}},
])
def test_tencent_malformed_adjusted_and_ignored_bounds_are_unavailable(monkeypatch, data):
    monkeypatch.setattr(china_daily, "get_json_bounded", Mock(return_value=data))
    with pytest.raises(VendorUnavailableError):
        china_daily.fetch_tencent("600522", DAY)


@pytest.mark.unit
def test_tencent_answered_empty_is_no_data(monkeypatch):
    monkeypatch.setattr(china_daily, "get_json_bounded", Mock(return_value={"code": 0, "data": {}}))
    with pytest.raises(NoMarketDataError):
        china_daily.fetch_tencent("600522", DAY)


@pytest.mark.unit
def test_sina_daily_units_and_cutoff(monkeypatch):
    rows = [{"day": day, "open": "10", "close": "12", "high": "13", "low": "9", "volume": "12345"}
            for day in ("2020-01-01", "2026-10-21", DAY, "2026-10-23")]
    get = Mock(return_value=rows)
    monkeypatch.setattr(china_daily, "get_json_bounded", get)
    frame = china_daily.fetch_sina("600522.SS", DAY)
    assert frame["Date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-10-21", DAY]
    assert frame["Volume"].tolist() == [12345, 12345]
    assert get.call_args.kwargs["params"] == {"symbol": "sh600522", "scale": "240", "ma": "no", "datalen": "2000"}


@pytest.mark.unit
@pytest.mark.parametrize("data", [{}, [{"day": DAY}],
    [{"day": DAY, "open": "10", "close": "NaN", "high": "13", "low": "9", "volume": "123"}],
    [{"day": DAY, "open": "10", "close": "12", "high": "13", "low": "9", "volume": "-1"}],
    [{"day": DAY, "open": "10", "close": "inf", "high": "13", "low": "9", "volume": "123"}],
])
def test_sina_malformed_response(monkeypatch, data):
    monkeypatch.setattr(china_daily, "get_json_bounded", Mock(return_value=data))
    with pytest.raises(VendorUnavailableError):
        china_daily.fetch_sina("600522", DAY)


@pytest.mark.unit
def test_bounded_http_timeout_size_status_and_invalid_json(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [json.dumps({"code": 0}).encode()]
    get = Mock(return_value=response)
    monkeypatch.setattr(net.requests, "get", get)
    assert net.get_json_bounded("https://example.test/", params={}, source="Tencent") == {"code": 0}
    assert get.call_args.kwargs["timeout"] == (4, 10)
    response.iter_content.return_value = [b" " * 2_000_001]
    with pytest.raises(VendorUnavailableError, match="size limit"):
        net.get_json_bounded("https://example.test/", params={}, source="Tencent")
    response.iter_content.return_value = [b"<html>error</html>"]
    with pytest.raises(VendorUnavailableError):
        net.get_json_bounded("https://example.test/", params={}, source="Tencent")
    get.side_effect = requests.Timeout("private-proxy-url")
    with pytest.raises(VendorUnavailableError) as error:
        net.get_json_bounded("https://example.test/", params={}, source="Tencent")
    assert "private-proxy" not in str(error.value)
    get.side_effect = None
    response.raise_for_status.side_effect = requests.HTTPError("private-proxy-url")
    with pytest.raises(VendorUnavailableError) as error:
        net.get_json_bounded("https://example.test/", params={}, source="Tencent")
    assert "private-proxy" not in str(error.value)
