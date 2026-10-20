"""Provider usage accounting keeps CLI counters compatible and marks unknowns."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from cli.stats_handler import StatsCallbackHandler


def _response(*, usage_metadata=None):
    # model_construct lets the callback see provider metadata exactly as it is
    # received, including missing or incomplete fields LangChain would reject.
    message = AIMessage.model_construct(content="ok", usage_metadata=usage_metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_explicit_zero_usage_is_reported_and_cli_stats_keep_four_keys():
    stats = StatsCallbackHandler()
    stats.on_chat_model_start({}, [])
    stats.on_llm_end(
        _response(usage_metadata={"input_tokens": 0, "output_tokens": 0})
    )

    assert stats.get_stats() == {
        "llm_calls": 1,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    assert stats.usage_known_calls == 1
    assert stats.usage_reported is True
    assert stats.usage_complete is True


def test_missing_or_partial_usage_does_not_count_as_reported():
    stats = StatsCallbackHandler()
    stats.on_llm_start({}, ["one"])
    stats.on_llm_start({}, ["two"])
    stats.on_llm_end(_response())
    stats.on_llm_end(
        _response(usage_metadata={"input_tokens": 12})
    )

    assert stats.get_stats() == {
        "llm_calls": 2,
        "tool_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
    }
    assert stats.usage_known_calls == 0
    assert stats.usage_reported is False
    assert stats.usage_complete is False


def test_partially_reported_calls_keep_subtotals_and_are_marked_incomplete():
    stats = StatsCallbackHandler()
    for _ in range(3):
        stats.on_chat_model_start({}, [])
    stats.on_llm_end(
        _response(usage_metadata={"input_tokens": 100, "output_tokens": 25})
    )
    stats.on_llm_end(
        _response(usage_metadata={"input_tokens": 0, "output_tokens": 0})
    )
    stats.on_llm_end(
        _response(usage_metadata={"input_tokens": 5, "output_tokens": -1})
    )

    assert stats.get_stats() == {
        "llm_calls": 3,
        "tool_calls": 0,
        "tokens_in": 100,
        "tokens_out": 25,
    }
    assert stats.usage_known_calls == 2
    assert stats.usage_reported is True
    assert stats.usage_complete is False


def test_usage_is_not_complete_before_any_model_call():
    stats = StatsCallbackHandler()

    assert stats.usage_known_calls == 0
    assert stats.usage_reported is False
    assert stats.usage_complete is False
