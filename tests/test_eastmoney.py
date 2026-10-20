"""Offline tests for the public Eastmoney A-share source adapter."""

import json

import pytest

from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors import eastmoney


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.status_code = status_code
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def clear_cache():
    eastmoney._CACHE.clear()
    yield
    eastmoney._CACHE.clear()


def row(
    post_id,
    post_type,
    *,
    bar="600519",
    title="标题",
    text="内容",
    published="2026-10-20 10:00:00",
    related=None,
    media=None,
    nickname="用户",
):
    return {
        "post_id": post_id,
        "post_type": post_type,
        "post_title": title,
        "post_content": text,
        "post_abstract": "",
        "post_publish_time": published,
        "post_display_time": "2026-10-20 10:00:00",
        "post_last_time": "2026-10-21 10:00:00",
        "post_guba": {"stockbar_type": 100, "stockbar_code": bar},
        "selected_relate_guba": related or [],
        "extend": {"MediaName": media} if media else {},
        "post_user": {
            "user_nickname": nickname,
            "user_extendinfos": {"is_enterprise": "false"},
        },
    }


def install_feeds(
    monkeypatch, page_rows, api_rows=None, *, page_status=200, api_status=200, bar_name="贵州茅台"
):
    payload = {
        "bar_info": {"OuterCode": "600519", "ShortName": bar_name},
        "re": page_rows,
    }
    page = "<script>window.list = " + json.dumps(payload, ensure_ascii=False) + ";</script>"
    api = json.dumps({"rc": 1, "re": api_rows if api_rows is not None else page_rows})
    calls = {"get": [], "post": []}

    def fake_get(url, **kwargs):
        calls["get"].append((url, kwargs))
        return FakeResponse(page, page_status)

    def fake_post(url, **kwargs):
        calls["post"].append((url, kwargs))
        return FakeResponse(api, api_status)

    monkeypatch.setattr(eastmoney.requests, "get", fake_get)
    monkeypatch.setattr(eastmoney.requests, "post", fake_post)
    return calls


@pytest.mark.parametrize(
    "ticker",
    [
        "600519",
        "SH600519",
        "600519.SH",
        "600519.SS",
        "000001.SZ",
        "430047.BJ",
    ],
)
def test_recognizes_mainland_a_share_codes(ticker):
    assert eastmoney.is_china_equity(ticker)


@pytest.mark.parametrize(
    "ticker",
    [
        "09992.HK",
        "BTCUSD",
        "600519.SZ",
        "300001.SH",
        "200001",
        "abc",
        "",
    ],
)
def test_rejects_non_a_share_or_wrong_market_codes(ticker):
    assert not eastmoney.is_china_equity(ticker)


def test_fetches_dated_relevant_news_and_community_without_last_time_fallback(monkeypatch):
    news = row(
        "1001",
        "1",
        title="公司资讯",
        text="<p>公告 <a href='/x'>摘要</a><script>忽略</script></p>",
        published="2026-10-19T18:00:00Z",
        media="证券时报网",
        nickname="资讯号",
    )
    repost = row("1002", "1", bar="000333", title="其他股票新闻", media="数据宝")
    self_media = row(
        "2001",
        "20",
        bar="cfhpl",
        title="相关长文",
        text="正文含关联代码。",
        related=[{"stockbar_code": "600519"}],
        nickname="专栏作者",
    )
    investor = row(
        "2002",
        "0",
        bar="cfhpl",
        title="股民发言",
        text="我关注 SH600519 的经营。",
        nickname="普通用户",
    )
    other_stock = row("2003", "0", bar="000001", title="其他股票推荐", text="看好这只股票")
    late_reply = row("2004", "0", title="旧帖刚回复", published="2026-10-19 23:59:59")
    unknown_date = row("2005", "0", title="无原始时间", published=None)
    calls = install_feeds(
        monkeypatch,
        [news, repost, self_media, investor, other_stock, late_reply, unknown_date],
        [news, repost, self_media, investor, other_stock, late_reply, unknown_date],
    )

    result = eastmoney.fetch_china_sources("600519.SH", "2026-10-20", "2026-10-20")

    assert result["news"]["status"] == "ok"
    assert [item["title"] for item in result["news"]["items"]] == ["公司资讯"]
    assert (
        result["news"]["items"][0]["date"] == "2026-10-20"
    )  # UTC timestamp converted to China time
    assert result["news"]["items"][0]["source"] == "新闻转载·证券时报网"
    assert result["news"]["items"][0]["source_category"] == "media_reprint"
    assert result["news"]["items"][0]["published_at"] == "2026-10-20T02:00:00+08:00"
    assert "忽略" not in result["news"]["items"][0]["text"]
    assert result["community"]["status"] == "ok"
    community = {item["title"]: item for item in result["community"]["items"]}
    assert set(community) == {"相关长文", "股民发言"}
    assert community["相关长文"]["source"].startswith("自媒体观点·")
    assert community["相关长文"]["source_category"] == "self_media_opinion"
    assert community["股民发言"]["source"].startswith("股民观点·")
    assert community["股民发言"]["source_category"] == "unverified_forum_user_opinion"
    assert all(
        "2004" not in item["url"] and "2005" not in item["url"] for item in community.values()
    )
    assert all(
        item["url"].startswith("https://mguba.eastmoney.com/mguba/article/0/")
        for bucket in result.values()
        for item in bucket["items"]
    )
    assert "不代表完整7天覆盖" in result["community"]["note"]
    assert calls["get"][0][1]["timeout"] == eastmoney._TIMEOUT
    assert calls["post"][0][1]["data"]["param"] == "code=600519&p=1&ps=20&sorttype=0"
    # The same post in the bootstrap and latest page is returned once.
    assert len(result["news"]["items"]) == 1


def test_full_publish_time_keeps_newer_forum_posts_ahead_of_recommendations(monkeypatch):
    recommendations = [
        row(
            str(6000 + index),
            "20",
            title=f"推荐自媒体{index}",
            published=f"2026-10-20 09:{index:02d}:00",
        )
        for index in range(12)
    ]
    newer_forum_post = row(
        "7000",
        "0",
        title="更新股民帖子",
        published="2026-10-20 15:30:00",
    )
    install_feeds(monkeypatch, recommendations, [newer_forum_post])

    result = eastmoney.fetch_china_sources("600519", "2026-10-20", "2026-10-20")

    community = result["community"]["items"]
    assert len(community) == eastmoney._MAX_ITEMS_PER_KIND
    assert community[0]["title"] == "更新股民帖子"
    assert community[0]["published_at"] == "2026-10-20T15:30:00+08:00"
    assert community[0]["source_category"] == "unverified_forum_user_opinion"
    assert sum(item["source_category"] == "self_media_opinion" for item in community) == 11


def test_labels_enterprise_account_as_view_not_company_announcement(monkeypatch):
    enterprise_view = row("3001", "0", title="企业账号发言", nickname="公司账号")
    enterprise_view["post_user"]["user_extendinfos"]["is_enterprise"] = "true"
    announcement = row("3002", "3", title="公司公告", nickname="资讯")
    install_feeds(monkeypatch, [enterprise_view, announcement])
    result = eastmoney.fetch_china_sources("600519", "2026-10-20", "2026-10-20")
    assert result["community"]["items"][0]["source"] == "企业账号观点·公司账号"
    assert result["community"]["items"][0]["source_category"] == "enterprise_account_opinion"
    assert result["news"]["items"][0]["source"] == "官方公告"
    assert result["news"]["items"][0]["source_category"] == "official_announcement"


def test_labels_verified_community_account_as_its_own_source_category(monkeypatch):
    verified = row("3101", "0", title="认证账号观点", nickname="认证作者")
    verified["post_user"]["user_v"] = 1
    install_feeds(monkeypatch, [verified])

    result = eastmoney.fetch_china_sources("600519", "2026-10-20", "2026-10-20")

    item = result["community"]["items"][0]
    assert item["source"] == "平台认证账号观点·认证作者"
    assert item["source_category"] == "verified_account_opinion"


def test_reports_uncovered_date_window_as_empty(monkeypatch):
    old = row("4001", "1", published="2026-10-20 10:00:00", media="媒体")
    old_post = row("4002", "0", published="2026-10-20 10:00:00")
    install_feeds(monkeypatch, [old, old_post])
    result = eastmoney.fetch_china_sources("600519", "2026-10-01", "2026-10-05")
    for bucket in result.values():
        assert bucket["status"] == "empty"
        assert "窗口未覆盖" in bucket["note"]
        assert "有限样本" in bucket["note"]


def test_distinguishes_request_and_parse_failures(monkeypatch):
    calls = install_feeds(monkeypatch, [], page_status=200, api_status=502)
    monkeypatch.setattr(eastmoney.requests, "get", lambda *a, **k: FakeResponse("no list here"))
    result = eastmoney.fetch_china_sources("600519", "2026-10-20", "2026-10-20")
    assert all(bucket["status"] == "unavailable" for bucket in result.values())
    assert "window.list" in result["news"]["note"]
    assert "HTTP 502" in result["community"]["note"]
    assert len(calls["post"]) == 1


def test_rejects_oversized_response():
    with pytest.raises(eastmoney._FetchError, match="size limit"):
        eastmoney._bounded_body(FakeResponse("0123456789ABC"), 12)


def test_total_output_is_bounded(monkeypatch):
    rows = [row(str(5000 + i), "1", title=f"资讯{i}", text="字" * 3000) for i in range(20)]
    install_feeds(monkeypatch, rows)
    result = eastmoney.fetch_china_sources("600519", "2026-10-20", "2026-10-20")
    total = sum(len(bucket["note"]) for bucket in result.values())
    total += sum(
        len(value)
        for bucket in result.values()
        for item in bucket["items"]
        for value in item.values()
    )
    assert total <= eastmoney._MAX_TOTAL_CHARS
    assert all(
        len(item["text"]) <= eastmoney._MAX_ITEM_TEXT
        for bucket in result.values()
        for item in bucket["items"]
    )


def test_news_adapter_raises_for_empty_or_unavailable(monkeypatch):
    monkeypatch.setattr(
        eastmoney,
        "fetch_china_sources",
        lambda *args: {
            "news": {"status": "empty", "items": [], "note": "窗口未覆盖"},
            "community": {"status": "empty", "items": [], "note": "窗口未覆盖"},
        },
    )
    with pytest.raises(VendorUnavailableError, match="empty"):
        eastmoney.get_news_china("600519", "2026-10-01", "2026-10-07")
