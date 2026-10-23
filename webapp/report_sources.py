"""Readable report citations, separate from the unchanged research archive."""

from __future__ import annotations

import json
import re
from html import escape

from markdown_it import MarkdownIt

_ID = r"E-[0-9a-f]{16}"
_VENDORS = {
    "eastmoney": "东方财富", "eastmoney.news": "东方财富新闻",
    "eastmoney.community": "东方财富社区", "yfinance": "Yahoo 财经",
    "yahoo": "Yahoo 财经", "tencent": "腾讯财经", "sina": "新浪财经",
    "fred": "FRED 宏观数据", "polymarket": "Polymarket",
}
_DIMENSIONS = {
    "market_report": "行情与指标", "fundamentals_report": "财务数据",
    "news_report": "新闻资料", "sentiment_report": "市场情绪",
}
_AUDIT = re.compile(
    r"(?m)^#{2,4}\s+(?:结论与证据引用|Decision evidence references|证据引用说明|Evidence reference check)\s*$"
)


def source_title(source, report_key=None):
    vendor = _VENDORS.get(source, "数据来源") if isinstance(source, str) else "数据来源"
    dimension = _DIMENSIONS.get(report_key)
    return f"{vendor}·{dimension}" if dimension else vendor


def source_catalog(run):
    catalog = {}
    quality = run.get("quality") or {}
    if not isinstance(quality, dict) or not isinstance(quality.get("reports", {}), dict):
        return catalog
    for key, report in (quality.get("reports") or {}).items():
        if not isinstance(report, dict):
            continue
        for source in report.get("sources") or []:
            if not isinstance(source, dict):
                continue
            evidence_id = source.get("id", "")
            if isinstance(evidence_id, str) and re.fullmatch(_ID, evidence_id):
                catalog.setdefault(evidence_id, source_title(source.get("source"), key))
    return catalog


def readable_report(text, run=None, *, links=False):
    """Project saved reports for readers without changing model output or audits."""
    parts = _AUDIT.split(text, maxsplit=1)
    body = parts[0].rstrip()
    if len(parts) > 1:
        audit = parts[1]
        if re.search(r"引用待核对|正文引用待核对|未提供逐项结论引用|Unverified", audit):
            body += "\n\n部分结论的来源尚未逐项核对，请结合原始资料阅读。"
        if "非结构化报告" in audit or "Unstructured report" in audit:
            body += "\n\n本报告未逐项核对结论与来源的对应关系。"
        if "目标价已省略" in audit or "Target withheld" in audit:
            body += "\n\n目标价已省略：缺少估值说明或可用来源。"
    catalog = source_catalog(run or {})
    run_id = (run or {}).get("id", "")
    can_link = links and isinstance(run_id, str) and bool(re.fullmatch(r"[0-9a-f]{32}", run_id))
    replacements = []
    original_links = []

    def keep_link(match):
        token = f"\x00LINK{len(original_links)}\x00"
        original_links.append(match.group())
        return token

    body = re.sub(r"!?\[[^\]\n]*\]\([^\n)]*\)", keep_link, body)

    def replace(ids, *, parentheses=False):
        names = []
        for evidence_id in dict.fromkeys(ids):
            title = catalog.get(evidence_id)
            if title:
                label = (f"[{title}](/api/runs/{run_id}/evidence.html?source={evidence_id})"
                         if can_link else title)
                if label not in names:
                    names.append(label)
        value = "；".join(names)
        if value and parentheses:
            value = f"（{value}）"
        token = f"\x00SOURCE{len(replacements)}\x00"
        replacements.append(value)
        return token

    group = re.compile(
        rf"[\[（(]\s*(?:Evidence\s+)?`?{_ID}`?"
        rf"(?:\s*[;；,，、]\s*(?:Evidence\s+)?`?{_ID}`?)*\s*[\]）)]", re.I,
    )
    body = group.sub(lambda m: replace(re.findall(_ID, m.group()), parentheses=True), body)
    single = re.compile(
        rf"(?:Evidence\s+)?`?({_ID})`?(?:\s*\((?:{'|'.join(re.escape(v) for v in _VENDORS)})\))?",
        re.I,
    )
    body = single.sub(lambda m: replace([m.group(1)]), body)
    for index, value in enumerate(replacements):
        body = body.replace(f"\x00SOURCE{index}\x00", value)
    for index, value in enumerate(original_links):
        body = body.replace(f"\x00LINK{index}\x00", value)
    return body


def render_report(run, key, text, renderer, *, sources_available=True):
    if key == "final_trade_decision":
        from tradingagents.agents.managers.portfolio_manager import omit_unavailable_price_target

        text = omit_unavailable_price_target(text)
    html = renderer.render(readable_report(text, run if sources_available else None, links=True))
    return re.sub(
        r'<a href="(/api/runs/[0-9a-f]{32}/evidence\.html\?source=E-[0-9a-f]{16})">',
        r'<a class="report-source-link" href="\1" target="_self" rel="noopener noreferrer">',
        html,
    )


def source_page(entry, run):
    keys = entry.get("report_keys") or []
    title = source_title(entry.get("source"), next(iter(keys), None))
    raw = entry.get("payload")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)
    text = readable_report(text)
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")
    dates = entry.get("observed_dates") or {}
    coverage = (f"资料覆盖：{dates.get('start') or '未提供'} 至 {dates.get('end') or '未提供'}"
                if dates.get("start") or dates.get("end") else "来源未提供完整的覆盖日期")
    ticker = escape(str(run["config"]["ticker"]))
    date = escape(str(run["config"]["date"]))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)} · {ticker}</title><link rel="stylesheet" href="/style.css">'
        '</head><body><main class="source-page">'
        f'<header><a href="/">返回研究报告</a><h1>{escape(title)}</h1><p>{ticker} · 研究日期 {date}</p>'
        f'<p>{escape(coverage)}</p>'
        '<p>这是本次研究保存的来源快照；采集成功不代表内容已独立验证。</p></header>'
        f'<article class="markdown-body">{renderer.render(text)}</article>'
        '</main></body></html>'
    )
