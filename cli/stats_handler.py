import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


class StatsCallbackHandler(BaseCallbackHandler):
    """Callback handler that tracks LLM calls, tool calls, and token usage."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.llm_calls = 0
        self.tool_calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._usage_known_calls = 0

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when an LLM starts."""
        with self._lock:
            self.llm_calls += 1

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        """Increment LLM call counter when a chat model starts."""
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Extract token usage from LLM response."""
        try:
            generation = response.generations[0][0]
        except (IndexError, TypeError):
            return

        usage_metadata = None
        if hasattr(generation, "message"):
            message = generation.message
            if isinstance(message, AIMessage) and hasattr(message, "usage_metadata"):
                usage_metadata = message.usage_metadata

        if isinstance(usage_metadata, dict):
            input_tokens = usage_metadata.get("input_tokens")
            output_tokens = usage_metadata.get("output_tokens")
            # Count a call as known only when the provider reported both sides
            # of usage. In particular, explicit 0/0 is valid; missing fields,
            # bools, negative values, and partial usage remain unknown.
            if (
                type(input_tokens) is int
                and input_tokens >= 0
                and type(output_tokens) is int
                and output_tokens >= 0
            ):
                with self._lock:
                    self.tokens_in += input_tokens
                    self.tokens_out += output_tokens
                    self._usage_known_calls += 1

    @property
    def usage_known_calls(self) -> int:
        """Number of model calls with complete, provider-reported token usage."""
        with self._lock:
            return self._usage_known_calls

    @property
    def usage_reported(self) -> bool:
        """Whether at least one model call reported complete token usage."""
        with self._lock:
            return self._usage_known_calls > 0

    @property
    def usage_complete(self) -> bool:
        """Whether all started model calls (and at least one) reported usage."""
        with self._lock:
            return self.llm_calls > 0 and self._usage_known_calls == self.llm_calls

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        """Increment tool call counter when a tool starts."""
        with self._lock:
            self.tool_calls += 1

    def get_stats(self) -> dict[str, Any]:
        """Return CLI-compatible numeric counters and reported token subtotals."""
        with self._lock:
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
