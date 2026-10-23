"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional
# numeric field instead of omitting it. Coerce those to None so the structured
# call validates instead of erroring (#1058). Pydantic still parses real numeric
# strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}
_CURRENCY_FORMS = {
    "USD": r"(?:USD|US\s*\$|\$|＄)",
    "HKD": r"(?:HKD|HK\s*\$|HK\s*[¥￥]|港元|港币|港幣)",
    "CNY": (
        r"(?:CNY|CNH|RMB(?:\s*[¥￥])?|人民币(?:\s*[¥￥])?|人民幣(?:\s*[¥￥])?|"
        r"CN\s*[¥￥]|[¥￥]|元|圆|圓)"
    ),
    "EUR": r"(?:EUR|€)",
    "GBP": r"(?:GBP|£)",
}
_CURRENCY_TOKEN = "(?:" + "|".join(_CURRENCY_FORMS.values()) + ")"
_PRICE_LABEL_RE = re.compile(
    r"^(?:the\s+)?(?:price\s+target|target\s+price|"
    r"目标价格|目标价|目標價格|目標價)"
    r"\s*(?:(?:is|of|为|是|[:：=])\s*)?",
    re.IGNORECASE,
)
_SINGLE_PRICE_RE = re.compile(
    r"^\s*(?P<per_share_prefix>per\s+share|每股)?\s*"
    rf"(?P<prefix>{_CURRENCY_TOKEN})?\s*"
    r"(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    rf"(?P<suffix>{_CURRENCY_TOKEN})?\s*"
    r"(?P<per_share>per\s+share|/\s*share|每股|/\s*股)?\s*$",
    re.IGNORECASE,
)


def _coerce_optional_float(value):
    """Normalise an LLM-written optional numeric field before validation.

    Placeholder strings, percentages, ranges, hedged values, and prose are
    dropped as missing. Currency symbols and names (including common Chinese
    RMB/HKD forms) may surround one number. Only a single number with no other
    substantive text is accepted, so a range or a sentence with several prices
    cannot be guessed into a value.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return value if math.isfinite(float(value)) else None
        except OverflowError:
            return None
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH_FLOAT or "%" in text or "％" in text:
        return None
    # A field label is acceptable framing, but estimates and arbitrary prose
    # are not. In particular, "around 150" and "target is 150 or 160" remain
    # ambiguous and are intentionally rejected.
    text = _PRICE_LABEL_RE.sub("", text).replace("，", ",")
    if text.endswith((".", ";", ",", "。")):
        text = text[:-1].rstrip()
    match = _SINGLE_PRICE_RE.fullmatch(text)
    if match is None:
        return None
    prefix, suffix = match.group("prefix"), match.group("suffix")
    if prefix and suffix and _currency_code(prefix) != _currency_code(suffix):
        return None
    try:
        return float(match.group("number").replace(",", ""))
    except ValueError:
        return None


def _currency_code(value: str) -> str | None:
    """Map a parsed currency form to its family for consistency checks."""
    for code, pattern in _CURRENCY_FORMS.items():
        if re.fullmatch(pattern, value, re.IGNORECASE):
            return code
    return None


def _coerce_optional_score(value):
    """Accept a plain finite numeric score or a nullish placeholder only."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().lower() in _NULLISH_FLOAT:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) else None


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(StrEnum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(StrEnum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Conflicting arguments alone are not a "
            "reason to Hold: commit to the stronger side, sized by how "
            "decisively it wins. Choose Hold only when the evidence is still "
            "balanced after weighing, or too thin to support a call."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including sizing guidance relative to a standard allocation. The "
            "research team does not see the caller's holdings; the trader and "
            "portfolio manager apply the actual position."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description=(
            "Optional entry price target as an absolute number in the instrument's "
            "quote currency (e.g. 189.5), never a percentage or a range. Omit it "
            "if you cannot state a specific level."
        ),
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Optional stop-loss as an absolute price in the instrument's quote "
            "currency (e.g. 172.0), never a percentage. Convert a percentage "
            "distance to the price level it implies, or omit it."
        ),
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    # Named even when absent, so a reader can tell a level the trader chose not
    # to give from one the schema never asked for.
    for label, value in (("Entry Price", proposal.entry_price),
                         ("Stop Loss", proposal.stop_loss),
                         ("Position Sizing", proposal.position_sizing)):
        parts.extend(["", f"**{label}**: {value if value is not None and value != '' else 'not provided'}"])
    parts.extend([
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


EvidenceId = Annotated[str, Field(max_length=80)]


class DecisionEvidenceClaim(BaseModel):
    """A model's claim and its receipt references, checked after generation."""

    text: str = Field(min_length=1, max_length=2000, description="One concrete claim supporting the decision.")
    evidence_ids: list[EvidenceId] = Field(
        default_factory=list, max_length=16,
        description="Exact IDs from this run's evidence catalog; use [] when unsupported. Never invent IDs.",
    )


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate. "
            "Conflicting arguments alone are not a reason to Hold: commit to the "
            "stronger side, sized by how decisively it wins. Choose Hold only "
            "when the evidence is still balanced after weighing, or too thin to "
            "support a call."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description=(
            "Optional explicit target price as one absolute number in the instrument's "
            "quote currency. Return null when the analysis does not support one clear "
            "single target. Do not estimate one just to fill this field, infer it from "
            "the current or web quoted price, or reuse an entry price or stop-loss. "
            "A range or an approximate/ambiguous statement is not a single target."
        ),
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    evidence_claims: list[DecisionEvidenceClaim] = Field(
        default_factory=list, max_length=12,
        description="Key decision claims with their exact supporting receipt IDs from the evidence catalog.",
    )
    price_target_basis: str | None = Field(
        default=None, max_length=4000,
        description=("Explain the valuation method, inputs, currency, period and assumptions behind the target. "
                     "This is a model explanation, not independent numerical verification. Return null if unsupported."),
    )
    price_target_evidence_ids: list[EvidenceId] = Field(
        default_factory=list, max_length=16,
        description="Exact usable receipt IDs supporting the target's inputs; no IDs means no traceable target.",
    )

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)

    @field_validator("price_target")
    @classmethod
    def _positive_target(cls, value):
        return value if value is None or math.isfinite(value) and value > 0 else None


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.extend(["", f"**Price Target**: {decision.price_target}"])
    parts.extend(["", f"**Time Horizon**: {decision.time_horizon or 'not provided'}"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(StrEnum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six direction tiers capture sentiment; Insufficient Data records when the
    available sources do not support an assessment.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"
    INSUFFICIENT_DATA = "Insufficient Data"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable. When the
    evidence is insufficient, the score is null and the header says so.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish / "
            "Insufficient Data. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral for genuinely non-committal evidence. Use Insufficient Data "
            "when sources are missing, placeholders, or too sparse to assess; missing "
            "data is not Neutral."
        ),
    )
    overall_score: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced. Return null and choose "
            "Insufficient Data when there is not enough usable evidence to assess "
            "sentiment; do not assign a neutral score to missing data."
        ),
    )

    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence based on the quality, amount, and independence of the "
            "sources actually available. Use 'low' when evidence is missing, "
            "placeholder-only, or too sparse; 'medium' when usable evidence is "
            "limited; use 'high' only when the available evidence is substantive "
            "and sufficiently broad. Do not assume every possible source was run."
        ),
    )
    narrative: str = Field(
        description=(
            "Concise, evidence-based sentiment analysis using the sources actually "
            "available. Describe useful alignments, divergences, themes, catalysts, "
            "risks, or a signal table only when supported by evidence. When data is "
            "missing or too sparse, state that plainly and keep the report brief; "
            "do not invent sentiment, themes, or sample counts."
        ),
    )

    @field_validator("overall_score", mode="before")
    @classmethod
    def _nullish_score_to_none(cls, value):
        return _coerce_optional_score(value)

    @model_validator(mode="after")
    def _normalise_insufficient_data(self):
        if self.overall_band is SentimentBand.INSUFFICIENT_DATA or self.overall_score is None:
            self.overall_band = SentimentBand.INSUFFICIENT_DATA
            self.overall_score = None
            self.confidence = "low"
        return self


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band, score when available, and confidence) is
    prepended to the narrative so the saved report is both human-readable and
    machine-parseable without regex.
    """
    if report.overall_band is SentimentBand.INSUFFICIENT_DATA or report.overall_score is None:
        headline = "**Overall Sentiment:** **Insufficient Data**"
        confidence = "Low"
    else:
        headline = (
            f"**Overall Sentiment:** **{report.overall_band.value}** "
            f"(Score: {report.overall_score:.1f}/10)"
        )
        confidence = report.confidence.capitalize()
    return "\n".join([headline, f"**Confidence:** {confidence}", "", report.narrative])
