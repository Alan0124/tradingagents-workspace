"""Offline tests for source receipts, concurrent reuse, and honest quality."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.evidence import EvidenceLedger, record_source


@pytest.mark.unit
def test_same_news_is_fetched_once_and_keeps_one_receipt(tmp_path, monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args)
        time.sleep(0.02)
        return "### Published event\nhttps://example.test/one"

    monkeypatch.setattr(router, "get_vendor", lambda *a: "yfinance")
    monkeypatch.setitem(router.VENDOR_METHODS, "get_news", {"yfinance": fetch})
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news", "social"])
    with ledger.activate(), ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(copy_context().run, router.route_to_vendor,
                               "get_news", "AAPL", "2026-10-14", "2026-10-21") for _ in range(2)]
        outputs = [future.result() for future in futures]
    assert len(calls) == 1
    assert outputs[0] == outputs[1]
    assert outputs[0].startswith("[Evidence E-")
    saved = json.loads(ledger.path.read_text())
    assert len(saved["evidence"]) == 1
    assert saved["evidence"][0]["urls"] == ["https://example.test/one"]
    assert saved["quality"]["reports"]["sentiment_report"]["status"] == "available"
    # A resume serves the acquired response, not a changed provider result.
    with EvidenceLedger(tmp_path, "2026-10-21", ["news", "social"]).activate():
        assert router.route_to_vendor("get_news", "AAPL", "2026-10-14", "2026-10-21") == outputs[0]
    assert len(calls) == 1


@pytest.mark.unit
def test_quality_is_not_report_completion_and_empty_is_not_available(tmp_path):
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["social", "fundamentals"])
    assert ledger.quality()["status"] == "unknown"
    with ledger.activate():
        record_source("sentiment_report", "eastmoney.community", {"items": []},
                      status="available", sample_count=0)
        record_source("fundamentals_report", "eastmoney", "withheld", status="unavailable")
        assert ledger.path.exists()  # each completed retrieval survives a process kill
    assert ledger.quality()["status"] == "unavailable"
    assert "payload" not in json.dumps(ledger.quality())


@pytest.mark.unit
def test_changed_archive_or_run_date_cannot_be_resumed(tmp_path):
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news"])
    ledger.record("news_report", "source", {"items": ["original"]}, status="available")
    with pytest.raises(ValueError, match="does not match"):
        EvidenceLedger(tmp_path, "2026-10-20", ["news"])
    saved = json.loads(ledger.path.read_text())
    saved["evidence"][0]["payload"] = "changed"
    ledger.path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="checksum"):
        EvidenceLedger(tmp_path, "2026-10-21", ["news"])


@pytest.mark.unit
def test_failed_request_is_receipted_and_can_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "get_vendor", lambda *a: "yfinance")
    def fail(*args):
        raise TimeoutError("private provider URL should not be archived")
    monkeypatch.setitem(router.VENDOR_METHODS, "get_news", {"yfinance": fail})
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news"])
    with ledger.activate(), pytest.raises(TimeoutError):
        router.route_to_vendor("get_news", "AAPL", "2026-10-14", "2026-10-21")
    raw = ledger.path.read_text()
    assert "private provider URL" not in raw
    assert ledger.quality()["status"] == "unavailable"


@pytest.mark.unit
@pytest.mark.parametrize("receipts", [
    [],
    [("fundamentals_report", "unavailable")],
    [("fundamentals_report", "unavailable"), ("news_report", "unavailable")],
    [("fundamentals_report", "unknown")],
    [("fundamentals_report", "unavailable"), ("news_report", "unknown")],
    [("market_report", "available")],  # unrelated to the selected dimensions
])
def test_no_evidence_cannot_be_promoted_to_a_directional_final_rating(tmp_path, receipts):
    from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager

    class Model:
        def with_structured_output(self, *args):
            raise NotImplementedError

        def invoke(self, *args):
            pytest.fail("missing evidence should not spend another model call on a rating")

    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals", "news"])
    with ledger.activate():
        for report, status in receipts:
            record_source(report, "fixture", "offline receipt", status=status)
        result = create_portfolio_manager(Model())({})
    assert result["final_rating"] == "REVIEW"
    assert "Price Target" not in result["final_trade_decision"]


@pytest.mark.unit
@pytest.mark.parametrize("status", ["available", "sparse"])
@pytest.mark.parametrize("analysts", [["fundamentals"], ["fundamentals", "news"]])
def test_usable_evidence_still_allows_a_model_rating(tmp_path, status, analysts):
    from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager

    class Model:
        def with_structured_output(self, *args):
            raise NotImplementedError

        def invoke(self, *args):
            return SimpleNamespace(content="**Rating**: Buy\nOffline decision.")

    state = {
        "company_of_interest": "AAPL",
        "investment_plan": "offline research",
        "trader_investment_plan": "offline proposal",
        "risk_debate_state": {
            **dict.fromkeys(("history", "aggressive_history", "conservative_history",
                            "neutral_history", "current_aggressive_response",
                            "current_conservative_response", "current_neutral_response"), ""),
            "count": 1,
        },
    }
    # A covered dimension may support a rating even when another is unverified.
    with EvidenceLedger(tmp_path, "2026-10-21", analysts).activate():
        record_source("fundamentals_report", "fixture", "offline data", status=status)
        result = create_portfolio_manager(Model())(state)
    assert result["final_rating"] == "Buy"


@pytest.mark.unit
def test_archive_freezes_mutable_payload_and_whitespace_is_no_evidence(tmp_path):
    from tradingagents.dataflows.evidence import capture_result
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news"])
    payload = {"items": [{"title": "original"}]}
    ledger.record("news_report", "source", payload, status="available")
    payload["items"][0]["title"] = "changed after retrieval"
    with ledger.activate():
        capture_result("get_news", "empty-source", (), {}, " \n\t")
    saved = json.loads(ledger.path.read_text())
    assert saved["evidence"][0]["payload"]["items"][0]["title"] == "original"
    assert saved["evidence"][1]["status"] == "unavailable"
    EvidenceLedger(tmp_path, "2026-10-21", ["news"])


@pytest.mark.unit
def test_resume_cache_keeps_configured_chain_separate_from_actual_vendor(tmp_path, monkeypatch):
    calls = []
    def source(*args):
        calls.append(args)
        return "Published balance sheet snapshot"
    monkeypatch.setattr(router, "get_vendor", lambda *a: "sec_edgar,yfinance")
    monkeypatch.setitem(router.VENDOR_METHODS, "get_balance_sheet", {"sec_edgar": source})
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate():
        output = router.route_to_vendor("get_balance_sheet", "AAPL", "quarterly", "2026-10-21")
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate():
        assert router.route_to_vendor("get_balance_sheet", "AAPL", "quarterly", "2026-10-21") == output
    assert len(calls) == 1
