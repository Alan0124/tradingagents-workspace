"""Native A-share finance must expose dates/units without weakening PIT rules."""
import pytest

from tradingagents.dataflows.vendors import eastmoney_financials as financials


def _today(monkeypatch):
    monkeypatch.setattr(financials, "get_current_date", lambda: "2026-10-21")
    monkeypatch.setattr(financials, "is_historical", lambda value: value < "2026-10-21")


@pytest.mark.unit
def test_historical_financials_are_withheld_without_request(monkeypatch):
    _today(monkeypatch)
    monkeypatch.setattr(financials, "_fetch_rows", lambda *a: pytest.fail("must not fetch live vintage"))
    for fn in (financials.get_fundamentals, financials.get_balance_sheet,
               financials.get_cashflow, financials.get_income_statement):
        text = fn("SH600519", as_of_date="2025-03-31")
        assert "withheld for this date" in text
        assert "later restatements" in text


@pytest.mark.unit
def test_live_statement_filters_wrong_symbol_future_and_undated_rows(monkeypatch):
    _today(monkeypatch)
    rows = [
        {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30 00:00:00",
         "NOTICE_DATE": "2026-08-15 00:00:00", "PARENT_NETPROFIT": 12345678, "BASIC_EPS": 0},
        {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-09-30", "NOTICE_DATE": "2026-10-22", "PARENT_NETPROFIT": 999},
        {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30", "PARENT_NETPROFIT": 777},
        {"SECUCODE": "000001.SZ", "REPORT_DATE": "2026-06-30", "NOTICE_DATE": "2026-08-15", "PARENT_NETPROFIT": 888},
    ]
    monkeypatch.setattr(financials, "_fetch_rows", lambda report, symbol: rows)
    text = financials.get_income_statement("600519", as_of_date="2026-10-21")
    assert "12,345,678.00" in text and "0.00" in text
    assert "2026-06-30" in text and "2026-08-15" in text
    assert "人民币 CNY 元" in text and "累计值" in text and "不等同历史原始披露版本" in text
    assert all(value not in text for value in ("999.00", "777.00", "888.00"))


@pytest.mark.unit
def test_annual_statement_never_labels_interim_ytd_as_annual(monkeypatch):
    _today(monkeypatch)
    rows = [
        {"SECUCODE": "600519.SH", "REPORT_DATE": "2026-06-30", "NOTICE_DATE": "2026-08-15", "NETCASH_OPERATE": 222},
        {"SECUCODE": "600519.SH", "REPORT_DATE": "2025-12-31", "NOTICE_DATE": "2026-04-01", "NETCASH_OPERATE": 111},
    ]
    monkeypatch.setattr(financials, "_fetch_rows", lambda *a: rows)
    text = financials.get_cashflow("600519.SS", "annual", "2026-10-21")
    assert "111.00" in text and "222.00" not in text
    with pytest.raises(ValueError):
        financials.get_cashflow("600519.SS", "weekly", "2026-10-21")
