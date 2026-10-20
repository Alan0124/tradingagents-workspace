"""Bounded A-share financial statements with explicit disclosure/vintage rules.

Eastmoney's current database supplies report and notice dates, but does not
prove that an old row is the version originally disclosed. Past-date runs keep
the existing strict point-in-time guard; live runs gain native CNY statements.
"""
from __future__ import annotations

import json
import math
from datetime import date

import requests

from tradingagents.dataflows.date_window import get_current_date, is_historical
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.evidence import current_ledger
from tradingagents.dataflows.symbols import mainland_symbol

_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORTS = {
    "income": ("RPT_DMSK_FN_INCOME", {
        "TOTAL_OPERATE_INCOME": "营业总收入", "OPERATE_INCOME": "营业收入",
        "TOTAL_OPERATE_COST": "营业总成本", "OPERATE_PROFIT": "营业利润",
        "NETPROFIT": "净利润", "PARENT_NETPROFIT": "归母净利润", "BASIC_EPS": "基本每股收益（元/股）",
    }),
    "balance": ("RPT_DMSK_FN_BALANCE", {
        "TOTAL_ASSETS": "资产总额", "TOTAL_LIABILITIES": "负债总额",
        "TOTAL_EQUITY": "所有者权益", "MONETARYFUNDS": "货币资金",
        "TOTAL_CURRENT_ASSETS": "流动资产", "TOTAL_CURRENT_LIAB": "流动负债",
    }),
    "cashflow": ("RPT_DMSK_FN_CASHFLOW", {
        "NETCASH_OPERATE": "经营活动现金流量净额", "NETCASH_INVEST": "投资活动现金流量净额",
        "NETCASH_FINANCE": "筹资活动现金流量净额", "CCE_ADD": "现金及现金等价物净增加额",
        "END_CCE": "期末现金及现金等价物余额",
    }),
}


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _fetch_rows(report_name, secucode):
    params = {"reportName": report_name, "columns": "ALL",
              "filter": f'(SECUCODE="{secucode}")', "pageSize": 40,
              "sortColumns": "REPORT_DATE,NOTICE_DATE", "sortTypes": "-1,-1",
              "source": "WEB", "client": "WEB"}
    try:
        with requests.get(_URL, params=params, timeout=(4, 12), stream=True,
                          headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(64_000):
                size += len(chunk)
                if size > 2_000_000:
                    raise VendorUnavailableError("Eastmoney financial response exceeded size limit")
                chunks.append(chunk)
        data = json.loads(b"".join(chunks))
        rows = (data.get("result") or {}).get("data")
        if not data.get("success") or not isinstance(rows, list):
            raise VendorUnavailableError("Eastmoney financial response has no valid table")
        return rows[:40]
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise VendorUnavailableError("Eastmoney financial statements temporarily unavailable") from exc


def _statement(ticker, freq, as_of_date, kind):
    canonical = mainland_symbol(ticker)
    if canonical is None:
        raise VendorUnavailableError("Eastmoney financials only supports mainland A-share codes")
    if freq not in {"annual", "quarterly"}:
        raise ValueError("frequency must be annual or quarterly")
    as_of_date = as_of_date or get_current_date()
    if _day(as_of_date) != as_of_date or as_of_date > get_current_date():
        raise ValueError("Invalid financial analysis date")
    if is_historical(as_of_date):
        return (
            f"# A-share financial statements for {canonical}\n"
            f"Financial statements are withheld for this date ({as_of_date}). "
            "Eastmoney provides a current database with report and notice dates, "
            "but no verified as-filed vintage. Filtering a notice date cannot rule out "
            "later restatements. Historical point-in-time financial coverage is unavailable; "
            "do not invent financial figures, valuation or a price target."
        )
    secucode = canonical.replace(".SS", ".SH")
    report_name, fields = _REPORTS[kind]
    ledger = current_ledger()
    rows = (
        ledger.memoize(["eastmoney_financial_rows", report_name, secucode],
                       lambda: _fetch_rows(report_name, secucode))
        if ledger else _fetch_rows(report_name, secucode)
    )
    selected = []
    for row in rows:
        if not isinstance(row, dict) or row.get("SECUCODE") != secucode:
            continue
        period, notice = _day(row.get("REPORT_DATE")), _day(row.get("NOTICE_DATE"))
        if not period or not notice or max(period, notice) > as_of_date:
            continue
        if freq == "annual" and not period.endswith("12-31"):
            continue
        values = {}
        for key, label in fields.items():
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                values[label] = value
        if values:
            selected.append((period, notice, values))
    selected.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = selected[:4]
    if not selected:
        raise VendorUnavailableError("Eastmoney returned no dated, usable financial statement rows")
    source_url = f"https://data.eastmoney.com/stockdata/{canonical.split('.')[0]}.html"
    lines = [f"# {canonical} A股财务报表（{kind} / {freq}）",
             "来源：东方财富数据中心；金额：人民币 CNY 元，每股收益为元/股。",
             "口径：供应商当前数据库快照，可能包含追溯重述，不等同历史原始披露版本。",
             ("资产负债表为报告期末时点值，不是期间累计值。" if kind == "balance" else
              "利润与现金流为报告期累计值，不能当作单季值或直接年化。"),
             f"来源链接：{source_url}", f"查询截至：{as_of_date}", ""]
    for period, notice, values in selected:
        lines += [f"## 报告期 {period} · 公告日期 {notice}", "| 项目 | 数值 |", "|---|---:|"]
        lines += [f"| {label} | {value:,.2f} |" for label, value in values.items()]
        lines.append("")
    return "\n".join(lines)


def get_fundamentals(ticker, as_of_date=None):
    return _statement(ticker, "quarterly", as_of_date, "income")


def get_balance_sheet(ticker, freq="quarterly", as_of_date=None):
    return _statement(ticker, freq, as_of_date, "balance")


def get_income_statement(ticker, freq="quarterly", as_of_date=None):
    return _statement(ticker, freq, as_of_date, "income")


def get_cashflow(ticker, freq="quarterly", as_of_date=None):
    return _statement(ticker, freq, as_of_date, "cashflow")
