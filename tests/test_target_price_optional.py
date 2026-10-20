"""Optional target-price handling and unavailable-sentiment rendering."""

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.portfolio_manager import (
    create_portfolio_manager,
    omit_unavailable_price_target,
)
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    SentimentBand,
    SentimentReport,
    render_pm_decision,
    render_sentiment_report,
)


def _decision(price_target=None):
    return PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="s",
        investment_thesis="t",
        price_target=price_target,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("$1,150.25", 1150.25),
        ("＄1,150.25", 1150.25),
        ("USD 1,150.25", 1150.25),
        ("CNY 1,150.25", 1150.25),
        ("RMB￥1,150.25", 1150.25),
        ("人民币 1,150.25 元/股", 1150.25),
        ("每股人民币1,150.25元", 1150.25),
        ("目标价为人民币1,150.25元", 1150.25),
        ("HK$ 88.50", 88.5),
        ("HKD88.50", 88.5),
        ("88.50 港元", 88.5),
        ("目标价：HK$88.50", 88.5),
    ],
)
def test_explicit_single_price_and_common_currency_forms_parse(written, expected):
    assert _decision(written).price_target == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "written",
    [
        "150-160",
        "150 160",
        "1元2",
        "150 to 160",
        "target price: $150 or $160",
        "100到120元",
        "around 150",
        "目标价约150元",
        "current price $100; target $120",
        "web quote: HK$88.50",
        "15%",
        "N/A",
        "¥100港元",
    ],
)
def test_range_hedge_or_multi_number_is_not_guessed_into_a_target(written):
    assert _decision(written).price_target is None


@pytest.mark.unit
@pytest.mark.parametrize("written", [float("inf"), float("nan"), True])
def test_non_finite_and_boolean_targets_are_not_prices(written):
    assert _decision(written).price_target is None


@pytest.mark.unit
def test_missing_target_is_omitted_but_a_real_target_stays_in_the_report():
    missing = render_pm_decision(_decision())
    assert "Price Target" not in missing
    assert "N/A" not in missing

    present = render_pm_decision(_decision("HK$88.50"))
    assert "**Price Target**: 88.5" in present


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["not provided", "N/A", "null", "未提供", "暂无"])
def test_free_text_cleanup_removes_only_a_standalone_missing_target_line(missing):
    text = (
        "**Rating**: Hold\n\n"
        f"**Price Target**: {missing}\n\n"
        "**Investment Thesis**: The price target was not provided because evidence is thin.\n"
    )
    cleaned = omit_unavailable_price_target(text)
    assert "**Price Target**:" not in cleaned
    assert "The price target was not provided because evidence is thin." in cleaned


@pytest.mark.unit
def test_free_text_cleanup_preserves_numeric_targets_and_explanatory_target_lines():
    numeric = "**Price Target**: 88.5\n"
    explanation = "Price Target: not provided because no valuation evidence exists.\n"
    assert omit_unavailable_price_target(numeric) == numeric
    assert omit_unavailable_price_target(explanation) == explanation


@pytest.mark.unit
def test_portfolio_manager_prompt_does_not_force_a_target():
    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or _decision()
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    create_portfolio_manager(llm)(_portfolio_state())

    prompt = captured["prompt"]
    assert "return null" in prompt
    assert "Do not treat the current or web quoted price" in prompt
    assert "entry price" in prompt and "stop-loss" in prompt
    assert "A range or approximate wording is not a single target" in prompt


@pytest.mark.unit
def test_portfolio_manager_fallback_omits_only_missing_target_line():
    llm = MagicMock()
    llm.with_structured_output.side_effect = NotImplementedError("unsupported")
    llm.invoke.return_value = MagicMock(content=(
        "**Rating**: Hold\n\n"
        "**Price Target**: 未提供\n\n"
        "The price target was not provided because the evidence is thin."
    ))

    result = create_portfolio_manager(llm)(_portfolio_state())
    report = result["final_trade_decision"]
    assert "**Price Target**:" not in report
    assert "The price target was not provided because the evidence is thin." in report


def _portfolio_state():
    return {
        "company_of_interest": "NVDA",
        "risk_debate_state": {
            "history": "risk debate",
            "aggressive_history": "aggressive",
            "conservative_history": "conservative",
            "neutral_history": "neutral",
            "latest_speaker": "Neutral",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "investment_plan": "research plan",
        "trader_investment_plan": "transaction proposal",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("band", "score"),
    [
        (SentimentBand.INSUFFICIENT_DATA, 5.0),
        (SentimentBand.NEUTRAL, None),
        (SentimentBand.NEUTRAL, "N/A"),
        (SentimentBand.NEUTRAL, "$5"),
        (SentimentBand.NEUTRAL, float("nan")),
    ],
)
def test_missing_sentiment_normalizes_to_insufficient_data_and_low_confidence(band, score):
    report = SentimentReport(
        overall_band=band,
        overall_score=score,
        confidence="high",
        narrative="No usable source data.",
    )

    rendered = render_sentiment_report(report)
    assert report.overall_band is SentimentBand.INSUFFICIENT_DATA
    assert report.overall_score is None
    assert report.confidence == "low"
    assert "**Overall Sentiment:** **Insufficient Data**" in rendered
    assert "**Confidence:** Low" in rendered
    assert "Score:" not in rendered and "/10" not in rendered


@pytest.mark.unit
def test_valid_sentiment_score_keeps_the_existing_report_format():
    report = SentimentReport(
        overall_band=SentimentBand.BULLISH,
        overall_score=7.2,
        confidence="high",
        narrative="Sources align.",
    )
    rendered = render_sentiment_report(report)
    assert "**Overall Sentiment:** **Bullish** (Score: 7.2/10)" in rendered
    assert "**Confidence:** High" in rendered


@pytest.mark.unit
def test_optional_scores_are_exposed_as_nullable_for_structured_output():
    sentiment_score = SentimentReport.model_json_schema()["properties"]["overall_score"]
    price_target = PortfolioDecision.model_json_schema()["properties"]["price_target"]
    for field_schema in (sentiment_score, price_target):
        assert any(
            option.get("type") == "null"
            for option in field_schema.get("anyOf", [])
        )
