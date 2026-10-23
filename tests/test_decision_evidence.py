"""Decision citations are real run receipts, never a claim of verified truth."""

from types import SimpleNamespace

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import PortfolioDecision
from tradingagents.dataflows.evidence import EvidenceLedger


def _state():
    return {
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


def _decision(**kwargs):
    return PortfolioDecision(rating="Hold", executive_summary="Wait for more data.",
                             investment_thesis="Research thesis.", **kwargs)


def _model(decision, prompts):
    return SimpleNamespace(with_structured_output=lambda schema: SimpleNamespace(
        invoke=lambda prompt: prompts.append(prompt) or decision))


@pytest.mark.unit
def test_citations_reject_unknown_unavailable_unselected_and_future_receipts(tmp_path):
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news"])
    valid = ledger.record("news_report", "news", "private raw payload", status="available")
    missing = ledger.record("news_report", "missing", "no data", status="unavailable")
    unrelated = ledger.record("market_report", "unselected", "prices", status="available")
    future = ledger.record("news_report", "future", "future news", status="available",
                           observed_dates={"start": "2026-10-20", "end": "2026-10-22"})
    unknown = "E-" + "0" * 16
    checked = ledger.check_citations([valid, valid, missing, unrelated, future, unknown])
    assert [entry["id"] for entry in checked["accepted"]] == [valid]
    assert {entry["id"] for entry in checked["rejected"]} == {missing, unrelated, future, unknown}
    assert "private raw payload" not in str(checked)
    assert [entry["id"] for entry in ledger.citation_catalog()] == [valid]
    # Callers cannot mutate the live ledger through returned nested metadata.
    checked["accepted"][0]["observed_dates"]["end"] = "2099-01-01"
    assert ledger.check_citations([valid])["accepted"]


@pytest.mark.unit
@pytest.mark.parametrize("dates", [
    {"start": "bad date", "end": "2026-10-21"},
    {"start": "2026-10-21", "end": "2026-10-20"},
])
def test_invalid_or_reversed_coverage_dates_are_not_citable(tmp_path, dates):
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["news"])
    evidence_id = ledger.record("news_report", "fixture", "news", status="available",
                                observed_dates=dates)
    assert not ledger.check_citations([evidence_id])["accepted"]


@pytest.mark.unit
def test_citations_and_coverage_survive_resume_without_a_live_provider(tmp_path):
    ledger = EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"])
    evidence_id = ledger.record("fundamentals_report", "filing", "data", status="sparse",
                                observed_dates={"start": "2026-09-30", "end": "2026-09-30"})
    resumed = EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"])
    assert resumed.citation_catalog() == ledger.citation_catalog()
    assert resumed.check_citations([evidence_id])["accepted"][0]["status"] == "sparse"


@pytest.mark.unit
def test_structured_claims_reach_the_report_with_their_real_sources(tmp_path):
    prompts = []
    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        evidence_id = ledger.record("news_report", "fixture.news", "not included in catalog", status="available")
        decision = _decision(evidence_claims=[{"text": "An event needs monitoring.", "evidence_ids": [evidence_id]}])
        result = create_portfolio_manager(_model(decision, prompts))(_state())
    report = result["final_trade_decision"]
    assert evidence_id in prompts[0] and "not included in catalog" not in prompts[0]
    assert "An event needs monitoring." in report
    assert evidence_id in report and "fixture.news" in report
    assert "not verification" in report
    assert result["final_rating"] == "Hold"


@pytest.mark.unit
def test_invented_claim_references_are_reported_as_unverified(tmp_path):
    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        ledger.record("news_report", "real.news", "data", status="available")
        decision = _decision(evidence_claims=[{"text": "A model claim.", "evidence_ids": ["E-" + "0" * 16]}])
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "Unverified" in result["final_trade_decision"]
    assert "receipt not found" in result["final_trade_decision"]


@pytest.mark.unit
@pytest.mark.parametrize("basis,ids", [(None, True), ("A multiple-based assumption.", False)])
def test_target_without_explanation_or_valid_receipt_is_withheld(tmp_path, basis, ids):
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate() as ledger:
        evidence_id = ledger.record("fundamentals_report", "filing", "data", status="available")
        decision = _decision(price_target=88.5, price_target_basis=basis,
                             price_target_evidence_ids=[evidence_id] if ids else ["E-" + "0" * 16])
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "**Price Target**:" not in result["final_trade_decision"]
    assert "Target withheld" in result["final_trade_decision"]
    assert decision.price_target == 88.5  # never mutate a provider's shared result


@pytest.mark.unit
def test_a_traceable_target_is_still_labelled_as_a_model_estimate(tmp_path):
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate() as ledger:
        evidence_id = ledger.record("fundamentals_report", "filing", "data", status="available")
        decision = _decision(price_target=88.5, price_target_basis="Assumed EPS 5.9 times an assumed P/E of 15.",
                             price_target_evidence_ids=[evidence_id])
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "**Price Target**: 88.5" in result["final_trade_decision"]
    assert "Model estimate" in result["final_trade_decision"]
    assert "independently validated" in result["final_trade_decision"]
    assert evidence_id in result["final_trade_decision"]


@pytest.mark.unit
def test_free_text_fallback_cannot_publish_an_untraceable_target(tmp_path):
    class Model:
        def with_structured_output(self, schema):
            raise NotImplementedError

        def invoke(self, prompt):
            return SimpleNamespace(content="**Rating**: Buy\n\n**Price Target**: 999999\n\nAn investment thesis.")

    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        ledger.record("news_report", "news", "data", status="available")
        result = create_portfolio_manager(Model())(_state())
    assert result["final_rating"] == "Buy"
    assert "**Price Target**:" not in result["final_trade_decision"]
    assert "Unstructured" in result["final_trade_decision"]


@pytest.mark.unit
@pytest.mark.parametrize("price", [0, -10, "-10", "9" * 400])
def test_zero_and_negative_targets_are_not_prices(price):
    assert _decision(price_target=price).price_target is None


@pytest.mark.unit
def test_future_only_evidence_cannot_trigger_another_model_call(tmp_path):
    class Model:
        def with_structured_output(self, schema):
            raise NotImplementedError

        def invoke(self, prompt):
            pytest.fail("future evidence cannot justify a model rating")

    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        ledger.record("news_report", "future", "news", status="available",
                      observed_dates={"start": "2026-10-22", "end": "2026-10-22"})
        result = create_portfolio_manager(Model())({})
    assert result["final_rating"] == "REVIEW"


@pytest.mark.unit
def test_one_valid_reference_does_not_hide_an_invalid_target_reference(tmp_path):
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate() as ledger:
        evidence_id = ledger.record("fundamentals_report", "filing", "data", status="available")
        decision = _decision(price_target=88.5, price_target_basis="Valuation assumptions.",
                             price_target_evidence_ids=[evidence_id, "E-" + "0" * 16])
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "**Price Target**:" not in result["final_trade_decision"]


@pytest.mark.unit
def test_chinese_report_labels_unknown_dates_and_escapes_source_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr("tradingagents.agents.managers.portfolio_manager.get_config",
                        lambda: {"output_language": "Chinese"})
    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        evidence_id = ledger.record("news_report", "<script>source</script>", "news", status="sparse")
        decision = _decision(evidence_claims=[{"text": "研究观点", "evidence_ids": [evidence_id]}],
                             price_target=88.5, price_target_basis="估值假设",
                             price_target_evidence_ids=[evidence_id])
        result = create_portfolio_manager(_model(decision, []))(_state())
    report = result["final_trade_decision"]
    assert "引用已核对" in report and "未知 — 未知" in report
    assert "模型估计目标价" in report and "尚未独立核验" in report
    assert "<script>" not in report


@pytest.mark.unit
@pytest.mark.parametrize("field", ["thesis", "claim"])
def test_inline_fabricated_reference_is_not_hidden_by_reference_arrays(tmp_path, field):
    with EvidenceLedger(tmp_path, "2026-10-21", ["news"]).activate() as ledger:
        evidence_id = ledger.record("news_report", "news", "data", status="available")
        decision = _decision()
        text = "According to E-0000000000000000, growth is strong."
        if field == "thesis":
            decision.investment_thesis = text
        else:
            decision = _decision(evidence_claims=[{"text": text, "evidence_ids": [evidence_id]}])
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "Unverified" in result["final_trade_decision"]
    assert "receipt not found" in result["final_trade_decision"]


@pytest.mark.unit
def test_fabricated_id_in_valuation_explanation_withholds_the_target(tmp_path):
    with EvidenceLedger(tmp_path, "2026-10-21", ["fundamentals"]).activate() as ledger:
        evidence_id = ledger.record("fundamentals_report", "filing", "data", status="available")
        decision = _decision(price_target=88.5, price_target_evidence_ids=[evidence_id],
                             price_target_basis="Based on E-0000000000000000.")
        result = create_portfolio_manager(_model(decision, []))(_state())
    assert "**Price Target**:" not in result["final_trade_decision"]
    assert "receipt not found" in result["final_trade_decision"]
