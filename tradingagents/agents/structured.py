"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_TRANSIENT_ERROR_NAMES = {
    "apiconnectionerror",
    "apitimeouterror",
    "authenticationerror",
    "internalservererror",
    "ratelimiterror",
    "serviceunavailableerror",
}
_TRANSIENT_ERROR_TEXT = re.compile(
    r"\b(?:authentication|unauthori[sz]ed|forbidden|rate limit|too many requests|"
    r"timed? out|timeout|connection (?:error|reset|refused)|server error|"
    r"internal server error|service unavailable|HTTP\s*50[0-9])\b",
    re.I,
)
_STRUCTURED_FEATURE_TEXT = re.compile(
    r"structured (?:output|response)|json[_ -]?schema|response[_ -]?format|"
    r"function[_ -]?calling|tool[_ -]?calling|tool call|schema",
    re.I,
)
_UNSUPPORTED_TEXT = re.compile(
    r"not supported|unsupported|does not support|doesn't support|unavailable|"
    r"unknown (?:parameter|field|tool)|unrecognized|invalid (?:json|schema|format)|"
    r"failed to parse|malformed (?:json|output)|parse error|validation error",
    re.I,
)

# Schema-only structured output binds exactly one tool (the schema itself), so a
# model that reaches for a search tool emits an unknown tool call and the whole
# structured attempt is discarded for a free-text retry. Agents on this path
# state the constraint explicitly rather than relying on the binding alone
# (#1130).
NO_EXTERNAL_TOOLS = (
    "Use only the evidence provided in this prompt. Do not call external tools "
    "or search the web; if something is missing, say so explicitly."
)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Any | None:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except Exception as exc:
        if not is_structured_compatibility_error(exc, binding=True):
            raise
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def _exception_chain(exc: BaseException):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_structured_compatibility_error(exc: BaseException, *, binding=False) -> bool:
    """Whether a failure is safe to retry once as plain text.

    Transient and account errors must propagate immediately: a free-text retry
    would spend another request without fixing them. Transport status codes are
    authoritative; only explicit structured-format rejection at 400/422 is
    eligible there.
    """
    chain = list(_exception_chain(exc))
    messages = " ".join(str(item) for item in chain)
    for item in chain:
        name = type(item).__name__.casefold()
        if name in _TRANSIENT_ERROR_NAMES or name.endswith("timeouterror"):
            return False
        if _TRANSIENT_ERROR_TEXT.search(str(item)):
            return False

        status = getattr(item, "status_code", None)
        if status is None:
            status = getattr(item, "http_status", None)
        response = getattr(item, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        if status is not None:
            try:
                status = int(status)
            except (TypeError, ValueError):
                status = None
            if status is not None:
                return status in (400, 422) and bool(
                    _STRUCTURED_FEATURE_TEXT.search(messages)
                    and _UNSUPPORTED_TEXT.search(messages)
                )

    if binding and any(isinstance(item, (NotImplementedError, AttributeError)) for item in chain):
        return True
    if any(isinstance(item, (ValidationError, OutputParserException, json.JSONDecodeError)) for item in chain):
        return True
    if any(isinstance(item, ValueError) for item in chain):
        # LangChain's structured parsers report malformed JSON/schema output as
        # ValueError. Transport failures above have already been excluded.
        return True
    if any(isinstance(item, NotImplementedError) for item in chain):
        return True
    return bool(_STRUCTURED_FEATURE_TEXT.search(messages) and _UNSUPPORTED_TEXT.search(messages))


def invoke_structured(structured_llm: Any | None, prompt: Any, agent_name: str) -> T | None:
    """Run the structured call; ``None`` only for parse/format incompatibility.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape), so a caller can forward the same value to its free-text fallback.
    """
    if structured_llm is None:
        return None
    try:
        result = structured_llm.invoke(prompt)
        if result is None:
            # A thinking model can answer in plain text instead of calling
            # the tool, leaving the parser with nothing to return. Treat it
            # as a structured miss and fall back, with a clear reason.
            raise ValueError("structured output returned no parsed result")
        return result
    except Exception as exc:
        if not is_structured_compatibility_error(exc):
            raise
        logger.warning(
            "%s: structured-output response could not be parsed (%s); retrying once as free text",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Any | None,
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """Run structured output and retry as free text only for parse/format failures."""
    result = invoke_structured(structured_llm, prompt, agent_name)
    if result is not None:
        return render(result)
    return plain_llm.invoke(prompt).content
