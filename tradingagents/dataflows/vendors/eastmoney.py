"""Bounded, point-in-time A-share news and Eastmoney community sampling."""

from __future__ import annotations

import html
import json
import re
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.symbols import mainland_symbol

_PAGE_URL = "https://mguba.eastmoney.com/mguba/list/{code}"
_API_URL = "https://mguba.eastmoney.com/mguba2020/interface/GetData.aspx?path=/webarticlelist/api/Article/WebArticleList"
_REFERER = "https://mguba.eastmoney.com/"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TradingAgents/1.0)",
    "Referer": _REFERER,
}
_TIMEOUT = (4, 10)
_MAX_HTML_BYTES = 900_000
_MAX_API_BYTES = 1_000_000
_MAX_ROWS = 60
_MAX_ITEMS_PER_KIND = 12
_MAX_ITEM_TITLE = 180
_MAX_ITEM_TEXT = 900
_MAX_TOTAL_CHARS = 28_000
_CACHE_TTL_SECONDS = 180
_CACHE_MAX_ENTRIES = 64
_CN_TZ = ZoneInfo("Asia/Shanghai")

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


class _FetchError(Exception):
    """A bounded request or decoding failure suitable for a public note."""


def _normalize_a_share(ticker: str) -> str | None:
    symbol = mainland_symbol(ticker)
    return symbol[:6] if symbol else None


def is_china_equity(ticker: str) -> bool:
    """Return whether a bare or exchange-qualified mainland A-share code is valid."""
    return _normalize_a_share(ticker) is not None


def _date_arg(value: str) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _published_at(value) -> datetime | None:
    """Parse only the original publish field, normalized to China local time."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            published_date = date.fromisoformat(value)
        except ValueError:
            return None
        return datetime.combine(published_date, datetime.min.time(), tzinfo=_CN_TZ)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CN_TZ)
    return parsed.astimezone(_CN_TZ)


def _published_date(value) -> date | None:
    """Use only the original publish field; source timestamps are China local."""
    published = _published_at(value)
    return published.date() if published is not None else None


def _clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", value)
    value = re.sub(r"(?is)<!--.*?-->", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6])\s*>", " ", value)
    value = re.sub(r"<[^>]*>", " ", value)
    value = html.unescape(value).replace("\x00", " ")
    return " ".join(value.split())[:limit].strip()


def _bounded_body(response, limit: int) -> bytes:
    chunks = []
    size = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        parts = iterator(chunk_size=64 * 1024)
    else:
        parts = (getattr(response, "content", b""),)
    for chunk in parts:
        if not chunk:
            continue
        size += len(chunk)
        if size > limit:
            raise _FetchError("response exceeded the size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _response_text(response, limit: int) -> str:
    raw = _bounded_body(response, limit)
    encoding = getattr(response, "encoding", None) or "utf-8"
    if encoding.lower() in {"iso-8859-1", "latin-1", "ascii"}:
        encoding = "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _extract_window_list(source: str) -> dict:
    marker = re.search(r"\bwindow\s*\.\s*list\s*=", source)
    if not marker:
        raise _FetchError("mobile page did not contain window.list")
    start = marker.end()
    while start < len(source) and source[start].isspace():
        start += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(source, start)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _FetchError("mobile page window.list could not be parsed") from exc
    if not isinstance(value, dict) or not isinstance(value.get("re"), list):
        raise _FetchError("mobile page window.list had an unexpected shape")
    return value


def _fetch_mobile_page(code: str) -> dict:
    response = None
    try:
        response = requests.get(
            _PAGE_URL.format(code=code),
            headers=_HEADERS,
            timeout=_TIMEOUT,
            stream=True,
        )
        if response.status_code != 200:
            raise _FetchError(f"mobile page returned HTTP {response.status_code}")
        text = _response_text(response, _MAX_HTML_BYTES)
        data = _extract_window_list(text)
        return {"ok": True, "rows": data["re"][:_MAX_ROWS], "bar_info": data.get("bar_info") or {}}
    except _FetchError:
        raise
    except requests.RequestException as exc:
        raise _FetchError(f"mobile page request failed ({type(exc).__name__})") from exc
    finally:
        if response is not None:
            response.close()


def _fetch_latest_posts(code: str) -> dict:
    """Call the first page and sort mode declared by the mobile page's list.js."""
    response = None
    api_path = "/webarticlelist/api/Article/WebArticleList"
    form = {
        "param": f"code={code}&p=1&ps=20&sorttype=0",
        "plat": "wap",
        "version": "200",
        "path": api_path,
        "env": "1",
        "origin": "",
        "ctoken": "",
        "utoken": "",
    }
    try:
        response = requests.post(
            _API_URL,
            data=form,
            headers=_HEADERS,
            timeout=_TIMEOUT,
            stream=True,
        )
        if response.status_code != 200:
            raise _FetchError(f"latest-post page returned HTTP {response.status_code}")
        text = _response_text(response, _MAX_API_BYTES)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise _FetchError("latest-post response could not be parsed") from exc
        if (
            not isinstance(data, dict)
            or data.get("rc") != 1
            or not isinstance(data.get("re"), list)
        ):
            raise _FetchError("latest-post response had an unexpected shape")
        return {"ok": True, "rows": data["re"][:_MAX_ROWS]}
    except _FetchError:
        raise
    except requests.RequestException as exc:
        raise _FetchError(f"latest-post request failed ({type(exc).__name__})") from exc
    finally:
        if response is not None:
            response.close()


def _load_sources(code: str) -> dict:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(code)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        _CACHE.pop(code, None)

    page = None
    latest = None
    page_error = latest_error = None
    try:
        page = _fetch_mobile_page(code)
    except _FetchError as exc:
        page_error = str(exc)
    try:
        latest = _fetch_latest_posts(code)
    except _FetchError as exc:
        latest_error = str(exc)

    loaded = {
        # Prefer the explicit latest-post feed on exact timestamp ties; the
        # bootstrap page can be recommendation-ranked and is only a second
        # finite sample, not a more authoritative ordering.
        "rows": (latest or {}).get("rows", []) + (page or {}).get("rows", []),
        "bar_info": (page or {}).get("bar_info", {}),
        "page_ok": page is not None,
        "latest_ok": latest is not None,
        "page_error": page_error,
        "latest_error": latest_error,
    }
    if loaded["page_ok"] and loaded["latest_ok"]:
        with _CACHE_LOCK:
            _CACHE[code] = (time.monotonic(), loaded)
            if len(_CACHE) > _CACHE_MAX_ENTRIES:
                oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
                _CACHE.pop(oldest, None)
    return loaded


def _row_codes(value) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key in ("stockbar_code", "stockbar_external_code", "stockbar_market", "code"):
            item = value.get(key)
            if isinstance(item, str):
                match = re.search(
                    r"(?i)(?:^|[^0-9])(?:SH|SZ|BJ)?(\d{6})(?:\.(?:SH|SS|SZ|BJ))?(?:$|[^0-9])", item
                )
                if match and _normalize_a_share(match.group(1)):
                    found.add(match.group(1))
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.update(_row_codes(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_row_codes(child))
    return found


def _codes_in_text(value: str) -> set[str]:
    found = set()
    pattern = re.compile(
        r"(?i)(?:\b(?:SH|SZ|BJ)\s*(\d{6})\b|\b(\d{6})\s*\.(?:SH|SS|SZ|BJ)\b|(?<!\d)(\d{6})(?!\d))"
    )
    for match in pattern.finditer(value):
        code = next((part for part in match.groups() if part), None)
        if code and _normalize_a_share(code):
            found.add(code)
    return found


def _is_relevant(row: dict, code: str, bar_name: str) -> bool:
    post_bar = row.get("post_guba") if isinstance(row.get("post_guba"), dict) else {}
    if _row_codes(post_bar) & {code}:
        return True
    if _row_codes(row.get("selected_relate_guba")) & {code}:
        return True
    text = " ".join(
        part
        for part in (row.get("post_title"), row.get("post_content"), row.get("post_abstract"))
        if isinstance(part, str)
    )
    if code in _codes_in_text(text):
        return True
    return bool(bar_name and len(bar_name) >= 2 and bar_name in _clean_text(text, 12_000))


def _account_source(row: dict) -> str | None:
    user = row.get("post_user") if isinstance(row.get("post_user"), dict) else {}
    info = user.get("user_extendinfos") if isinstance(user.get("user_extendinfos"), dict) else {}
    if str(info.get("is_enterprise", "")).lower() == "true":
        return "企业账号观点"
    accreditation = info.get("user_accreditinfos")
    if isinstance(accreditation, str):
        try:
            accreditation = json.loads(accreditation)
        except ValueError:
            accreditation = []
    verified = bool(accreditation) or user.get("user_v") not in (None, 0, "", "0", False)
    return "平台认证账号观点" if verified else None


def _source(row: dict, kind: str) -> str:
    if kind == "news":
        if str(row.get("post_type")) == "3":
            return "官方公告"
        extend = row.get("extend") if isinstance(row.get("extend"), dict) else {}
        media = _clean_text(str(extend.get("MediaName") or "东方财富资讯"), 80)
        return f"新闻转载·{media}"
    user = row.get("post_user") if isinstance(row.get("post_user"), dict) else {}
    nickname = _clean_text(str(user.get("user_nickname") or "东方财富用户"), 80)
    account_source = _account_source(row)
    if account_source:
        return f"{account_source}·{nickname}"
    if str(row.get("post_type")) == "20":
        return f"自媒体观点·{nickname}"
    return f"股民观点·{nickname}"


def _source_category(row: dict, kind: str) -> str:
    """Name the source class evidenced by Eastmoney's row fields."""
    if kind == "news":
        return "official_announcement" if str(row.get("post_type")) == "3" else "media_reprint"
    account_source = _account_source(row)
    if account_source == "企业账号观点":
        return "enterprise_account_opinion"
    if account_source == "平台认证账号观点":
        return "verified_account_opinion"
    if str(row.get("post_type")) == "20":
        return "self_media_opinion"
    return "unverified_forum_user_opinion"


def _item(row: dict, kind: str):
    published = _published_at(row.get("post_publish_time"))
    if published is None:
        return None
    post_id = str(row.get("post_id") or "")
    if not re.fullmatch(r"\d{1,20}", post_id):
        return None
    text = _clean_text(row.get("post_content") or row.get("post_abstract"), _MAX_ITEM_TEXT)
    title = _clean_text(row.get("post_title"), _MAX_ITEM_TITLE)
    if not title and not text:
        return None
    if not title:
        title = text[:_MAX_ITEM_TITLE]
    return {
        "title": title,
        "text": text,
        "date": published.date().isoformat(),
        "published_at": published.isoformat(timespec="seconds"),
        "url": f"https://mguba.eastmoney.com/mguba/article/0/{post_id}",
        "source": _source(row, kind),
        "source_category": _source_category(row, kind),
        "_id": post_id,
    }


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        post_id = row.get("post_id")
        key = (
            str(post_id)
            if post_id
            else (str(row.get("post_title") or ""), str(row.get("post_publish_time") or ""))
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _window_note(start: date, end: date, sample_dates: list[date], items: list[dict]) -> str:
    note = "东方财富移动端公开页面及其最新发帖第一页的有限样本，不代表完整7天覆盖。"
    if items:
        return note
    if not sample_dates:
        return note + " 页面样本没有可确认的原始发布日期，无法覆盖该窗口。"
    earliest, latest = min(sample_dates), max(sample_dates)
    if latest < start:
        detail = f"最新分页最晚发布日期为{latest.isoformat()}，早于所请求窗口起始日，窗口未覆盖。"
    elif earliest > end:
        detail = f"最新分页最早发布日期为{earliest.isoformat()}，晚于所请求窗口结束日，窗口未覆盖。"
    else:
        detail = "分页有窗口内记录，但没有找到日期明确且与该代码相关的条目；不能据此认定没有内容。"
    return note + detail


def _bucket(
    rows: list[dict],
    kind: str,
    code: str,
    bar_name: str,
    start: date,
    end: date,
    available: bool,
    error_note: str,
) -> dict:
    base_note = "东方财富移动端公开页面及其最新发帖第一页的有限样本，不代表完整7天覆盖。"
    if not available:
        return {"status": "unavailable", "items": [], "note": error_note or "请求或解析失败。"}
    expected_types = {"1", "3"} if kind == "news" else {"0", "20"}
    candidates = []
    sample_dates = []
    for row in _dedupe_rows(rows):
        row_type = str(row.get("post_type"))
        if row_type not in expected_types:
            continue
        published = _published_at(row.get("post_publish_time"))
        if published is None:
            continue
        pub_date = published.date()
        sample_dates.append(pub_date)
        if not start <= pub_date <= end or not _is_relevant(row, code, bar_name):
            continue
        extracted = _item(row, kind)
        if extracted is not None:
            candidates.append((published, extracted))
    candidates.sort(key=lambda value: value[0], reverse=True)

    items = []
    used = set()
    for _, value in candidates:
        key = (value["_id"], value["date"])
        if key in used:
            continue
        used.add(key)
        if len(items) >= _MAX_ITEMS_PER_KIND:
            break
        items.append({key: item for key, item in value.items() if not key.startswith("_")})
    note = _window_note(start, end, sample_dates, items) if not items else base_note
    if error_note:
        if not items:
            return {
                "status": "unavailable",
                "items": [],
                "note": f"{error_note}；部分来源失败，无法判定该窗口为空。",
            }
        note += f" 另有来源请求或解析失败，结果为部分样本（{error_note}）。"
    return {
        "status": "ok" if items else "empty",
        "items": items,
        "note": note,
    }


def _limit_total(result: dict) -> None:
    # Reserve the same budget for each category so a long news page cannot
    # crowd community items out of the returned sample.
    per_kind = _MAX_TOTAL_CHARS // 2
    for bucket in result.values():
        budget = max(0, per_kind - len(bucket["note"]))
        kept = []
        for item in bucket["items"]:
            fixed = sum(
                len(item.get(key, ""))
                for key in ("title", "date", "published_at", "url", "source", "source_category")
            )
            room = budget - fixed
            if room < 0:
                break
            item["text"] = item["text"][:room]
            budget -= fixed + len(item["text"])
            kept.append(item)
        bucket["items"] = kept
        if not kept and bucket["status"] == "ok":
            bucket["status"] = "empty"
            bucket["note"] += " 返回内容受长度上限约束。"


def fetch_china_sources(ticker: str, start_date: str, end_date: str) -> dict:
    """Return bounded news and community samples for one A-share date window."""

    def unavailable(note: str) -> dict:
        return {
            "news": {"status": "unavailable", "items": [], "note": note},
            "community": {"status": "unavailable", "items": [], "note": note},
        }

    code = _normalize_a_share(ticker)
    if code is None:
        return unavailable("无法识别为沪深北A股代码。")
    start, end = _date_arg(start_date), _date_arg(end_date)
    if start is None or end is None or start > end:
        return unavailable("日期窗口无效；需要 start_date <= end_date，格式为 YYYY-MM-DD。")

    loaded = _load_sources(code)
    available = loaded["page_ok"] or loaded["latest_ok"]
    errors = [loaded[key] for key in ("page_error", "latest_error") if loaded[key]]
    if not available:
        return unavailable("东方财富移动页面请求或解析失败：" + "；".join(errors))
    rows = loaded["rows"]
    bar_info = loaded.get("bar_info") or {}
    bar_name = _clean_text(str(bar_info.get("ShortName") or bar_info.get("Name") or ""), 80)
    note = "；".join(errors)
    result = {
        "news": _bucket(rows, "news", code, bar_name, start, end, available, note),
        "community": _bucket(rows, "community", code, bar_name, start, end, available, note),
    }
    _limit_total(result)
    return result


def get_news_china(ticker: str, start_date: str, end_date: str) -> str:
    """Vendor adapter for the existing news routing contract."""
    result = fetch_china_sources(ticker, start_date, end_date)["news"]
    if result["status"] != "ok":
        raise VendorUnavailableError(
            f"Eastmoney China news {result['status']} for {ticker}: {result['note']}"
        )
    parts = [f"## {ticker} A股资讯，{start_date} 至 {end_date}\n"]
    for item in result["items"]:
        parts.append(
            f"### {item['title']}（{item['source']}，{item['date']}）\n"
            f"{item['text']}\n链接：{item['url']}\n"
        )
    parts.append(f"采样说明：{result['note']}")
    return "\n".join(parts)
