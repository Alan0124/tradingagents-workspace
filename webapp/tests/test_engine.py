"""Verify the UI worker against the actual upstream graph, without network or API fees."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, START, StateGraph

from tradingagents.default_config import DEFAULT_CONFIG
from webapp.core import PREFIX
from webapp.worker import RunBudgetCallback, RunBudgetExceeded, execute


class OfflineModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, *args, **kwargs):
        raise NotImplementedError

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="离线集成测试。\n\n**Rating**: Hold\n\n无市场数据，仅检查工作流连接。"
                    )
                )
            ]
        )


class EngineIntegrationTests(unittest.TestCase):
    def test_worker_reaches_real_graph_decision_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = copy.deepcopy(DEFAULT_CONFIG)
            config.update(
                memory_log_path=str(Path(tmp) / "memory.md"),
                data_cache_dir=str(Path(tmp) / "cache"),
            )
            client = type("Client", (), {"get_llm": lambda _: OfflineModel()})()
            chosen = {
                "ticker": "AAPL",
                "date": "2026-09-01",
                "asset_type": "stock",
                "provider": "ollama",
                "quick_model": "offline-test",
                "deep_model": "offline-test",
                "backend_url": "",
                "analysts": ["market", "news", "fundamentals"],
                "rounds": 1,
                "language": "Chinese",
            }
            output = io.StringIO()
            with (
                patch("tradingagents.default_config.DEFAULT_CONFIG", config),
                patch("tradingagents.graph.trading_graph.create_tier_client", return_value=client),
                patch(
                    "tradingagents.graph.trading_graph.TradingAgentsGraph.resolve_instrument_context",
                    return_value="AAPL · OFFLINE TEST",
                ),
                contextlib.redirect_stdout(output),
            ):
                execute({"config": chosen, "directory": tmp})
            events = [
                json.loads(s[len(PREFIX) :])
                for s in output.getvalue().splitlines()
                if s.startswith(PREFIX)
            ]
            reports = {e["key"]: e["text"] for e in events if e["type"] == "report"}
            self.assertIn("final_trade_decision", reports)
            self.assertIn("fundamentals_report", reports)
            self.assertIn("bull", reports)
            self.assertIn("conservative", reports)
            self.assertEqual(next(e["rating"] for e in events if e["type"] == "result"), "REVIEW")
            self.assertNotIn("Price Target", reports["final_trade_decision"])
            quality = [e["quality"] for e in events if e["type"] == "quality"][-1]
            self.assertEqual(quality["evidence_count"], 0)
            self.assertEqual(quality["status"], "unknown")
            saved_reports = list(Path(tmp).glob("reports/*/complete_report.html"))
            self.assertTrue(saved_reports)
            self.assertIn("REVIEW", saved_reports[0].read_text(encoding="utf-8"))
            self.assertTrue(list(Path(tmp).glob("AAPL/TradingAgentsStrategy_logs/*.json")))
            self.assertIn("REVIEW", (Path(tmp) / "memory.md").read_text(encoding="utf-8"))

    def test_worker_resumes_from_same_run_checkpoint_and_clears_on_success(self):
        inputs = []

        class ResumableGraph:
            def __init__(self, selected_analysts, config, callbacks):
                self.config = config
                self.checkpoint = Path(config["data_cache_dir"]) / "checkpoint.json"
                self.propagator = self
                self.resuming = False

            @contextmanager
            def checkpoint_scope(self, ticker, date, asset):
                self.resuming = self.checkpoint.exists()
                yield "same-run-thread"

            def create_run_state(self, ticker, date, asset):
                return {"company_of_interest": ticker, "trade_date": date, "market_report": ""}

            def checkpoint_input(self, initial):
                inputs.append(None if self.resuming else initial)
                return None if self.resuming else initial

            def get_graph_args(self, **kwargs):
                return {"config": {}}

            def stream_run(self, graph_input, **kwargs):
                if not self.resuming:
                    saved = {
                        "company_of_interest": "AAPL",
                        "trade_date": "2026-09-01",
                        "market_report": "partial report",
                    }
                    self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
                    self.checkpoint.write_text(json.dumps(saved))
                    yield [], {"market_report": "partial report"}
                    raise RuntimeError("simulated interruption")
                saved = json.loads(self.checkpoint.read_text())
                saved["final_trade_decision"] = "**Rating**: Hold\nresumed decision"
                yield [], saved

            def record_decision(self, *args):
                pass

            def save_reports(self, *args):
                pass

            def clear_checkpoint_on_success(self, *args):
                self.checkpoint.unlink(missing_ok=True)

        class Stats:
            def get_stats(self):
                return {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}

        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "config": {
                    "ticker": "AAPL",
                    "date": "2026-09-01",
                    "provider": "ollama",
                    "quick_model": "offline-test",
                    "deep_model": "offline-test",
                    "backend_url": "http://localhost:11434/v1",
                    "analysts": ["market"],
                    "rounds": 1,
                    "language": "Chinese",
                },
                "directory": tmp,
                "limits": {"llm_calls": 10, "tokens": 10000},
            }
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "interruption"):
                    execute(request, ResumableGraph, Stats)
                execute({**request, "resume": True}, ResumableGraph, Stats)

            self.assertIsNotNone(inputs[0])
            self.assertIsNone(inputs[1])
            self.assertFalse((Path(tmp) / "cache" / "checkpoint.json").exists())

    def test_worker_resumes_real_langgraph_checkpoint_with_same_evidence_ledger(self):
        from tradingagents.dataflows.evidence import current_ledger
        from tradingagents.graph.checkpointer import (
            checkpoint_step,
            clear_checkpoint,
            get_checkpointer,
            thread_id,
        )

        class State(TypedDict, total=False):
            company_of_interest: str
            trade_date: str
            market_report: str
            final_trade_decision: str
            final_rating: str

        calls = {"research": 0, "decision": 0}
        should_interrupt = [True]

        def research(state):
            calls["research"] += 1
            current_ledger().record(
                "market_report",
                "offline-fixture",
                {"sample": "same run, no network"},
                status="available",
                sample_count=1,
                request={"method": "fixture", "args": [], "kwargs": {}},
            )
            return {"market_report": "saved analyst report"}

        def decide(state):
            calls["decision"] += 1
            if should_interrupt[0]:
                should_interrupt[0] = False
                raise RuntimeError("simulated graph interruption")
            assert state["market_report"] == "saved analyst report"
            return {
                "final_trade_decision": "**Rating**: Hold\nresumed from checkpoint",
                "final_rating": "Hold",
            }

        workflow = StateGraph(State)
        workflow.add_node("research", research)
        workflow.add_node("decide", decide)
        workflow.add_edge(START, "research")
        workflow.add_edge("research", "decide")
        workflow.add_edge("decide", END)

        class CheckpointGraph:
            def __init__(self, selected_analysts, config, callbacks):
                self.config = config
                self.workflow = workflow
                self.graph = workflow.compile()
                self.propagator = self
                self.callbacks = callbacks
                self._saver_context = None
                self._resuming = False

            @contextmanager
            def checkpoint_scope(self, ticker, date, asset):
                signature = "worker-resume-test"
                self._resuming = checkpoint_step(
                    self.config["data_cache_dir"], ticker, date, signature
                ) is not None
                self._saver_context = get_checkpointer(self.config["data_cache_dir"], ticker)
                saver = self._saver_context.__enter__()
                self.graph = self.workflow.compile(checkpointer=saver)
                try:
                    yield thread_id(ticker, date, signature)
                finally:
                    self._saver_context.__exit__(None, None, None)
                    self._saver_context = None
                    self.graph = self.workflow.compile()
                    self._resuming = False

            def create_run_state(self, ticker, date, asset):
                return {"company_of_interest": ticker, "trade_date": date}

            def checkpoint_input(self, initial):
                return None if self._resuming else initial

            def get_graph_args(self, **kwargs):
                return {"config": {}}

            def stream_run(self, graph_input, **kwargs):
                if self._resuming:
                    stats, budget = self.callbacks
                    usage = {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}
                    stats.record_known_call(
                        input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"]
                    )
                    budget.on_chat_model_start({}, [])
                    budget.on_llm_end(
                        SimpleNamespace(
                            generations=[
                                [
                                    SimpleNamespace(
                                        message=AIMessage(content="offline", usage_metadata=usage)
                                    )
                                ]
                            ]
                        )
                    )
                for state in self.graph.stream(graph_input, stream_mode="values", **kwargs):
                    yield [], state

            def record_decision(self, *args):
                pass

            def save_reports(self, *args):
                pass

            def clear_checkpoint_on_success(self, ticker, date, asset):
                clear_checkpoint(
                    self.config["data_cache_dir"], ticker, date, "worker-resume-test"
                )

        class Stats:
            def __init__(self):
                self.llm_calls = 0
                self.tool_calls = 0
                self.tokens_in = 0
                self.tokens_out = 0
                self.usage_known_calls = 0

            def record_known_call(self, input_tokens, output_tokens):
                self.llm_calls += 1
                self.tokens_in += input_tokens
                self.tokens_out += output_tokens
                self.usage_known_calls += 1

            def get_stats(self):
                return {
                    "llm_calls": self.llm_calls,
                    "tool_calls": self.tool_calls,
                    "tokens_in": self.tokens_in,
                    "tokens_out": self.tokens_out,
                    "usage_known_calls": self.usage_known_calls,
                    "usage_reported": self.usage_known_calls > 0,
                    "usage_complete": self.usage_known_calls == self.llm_calls,
                }

        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "config": {
                    "ticker": "AAPL",
                    "date": "2026-09-01",
                    "provider": "ollama",
                    "quick_model": "offline-test",
                    "deep_model": "offline-test",
                    "backend_url": "http://localhost:11434/v1",
                    "analysts": ["market"],
                    "rounds": 1,
                    "language": "Chinese",
                },
                "directory": tmp,
                "limits": {"llm_calls": 10, "tokens": 10000},
            }
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                RuntimeError, "graph interruption"
            ):
                execute(request, CheckpointGraph, Stats)
            resumed_output = io.StringIO()
            with contextlib.redirect_stdout(resumed_output):
                execute(
                    {
                        **request,
                        "resume": True,
                        "stats_base": {
                            "llm_calls": 2,
                            "tool_calls": 0,
                            "tokens_in": 20,
                            "tokens_out": 10,
                            "usage_known_calls": 2,
                            "usage_reported": True,
                        },
                    },
                    CheckpointGraph,
                    Stats,
                )

            self.assertEqual(calls, {"research": 1, "decision": 2})
            evidence = json.loads((Path(tmp) / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(len(evidence["evidence"]), 1)
            self.assertEqual(evidence["evidence"][0]["payload"], {"sample": "same run, no network"})
            events = [
                json.loads(line[len(PREFIX) :])
                for line in resumed_output.getvalue().splitlines()
                if line.startswith(PREFIX)
            ]
            final_stats = [event["stats"] for event in events if event["type"] == "stats"][-1]
            self.assertEqual(final_stats["llm_calls"], 3)
            self.assertEqual(final_stats["usage_known_calls"], 3)
            self.assertTrue(final_stats["usage_complete"])
            self.assertEqual(final_stats["tokens_in"], 25)
            self.assertEqual(final_stats["tokens_out"], 17)
            self.assertEqual(
                checkpoint_step(Path(tmp) / "cache", "AAPL", "2026-09-01", "worker-resume-test"),
                None,
            )

    def test_resume_without_matching_checkpoint_does_not_restart_model_work(self):
        class MissingCheckpointGraph:
            def __init__(self, selected_analysts, config, callbacks):
                self.propagator = self

            def create_run_state(self, ticker, date, asset):
                return {"company_of_interest": ticker, "trade_date": date}

            def checkpoint_input(self, initial):
                return initial

            def get_graph_args(self, **kwargs):
                return {}

            def stream_run(self, *args, **kwargs):
                raise AssertionError("resume must not start the graph from its initial state")

        class Stats:
            def get_stats(self):
                return {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0}

        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "config": {
                    "ticker": "AAPL",
                    "date": "2026-09-01",
                    "provider": "ollama",
                    "quick_model": "offline-test",
                    "deep_model": "offline-test",
                    "backend_url": "http://localhost:11434/v1",
                    "analysts": ["market"],
                    "rounds": 1,
                    "language": "Chinese",
                },
                "directory": tmp,
                "resume": True,
            }
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                RuntimeError, "未重新发起模型请求"
            ):
                execute(request, MissingCheckpointGraph, Stats)

    def test_final_stats_failure_does_not_replace_the_primary_worker_error(self):
        class GraphConfigurationError(RuntimeError):
            pass

        class FailingGraph:
            def __init__(self, **kwargs):
                raise GraphConfigurationError("primary graph configuration failure")

        class FailingStats:
            def get_stats(self):
                raise RuntimeError("secondary stats failure")

        with tempfile.TemporaryDirectory() as tmp:
            request = {
                "config": {
                    "ticker": "AAPL",
                    "date": "2026-09-01",
                    "provider": "ollama",
                    "quick_model": "offline-test",
                    "deep_model": "offline-test",
                    "backend_url": "http://localhost:11434/v1",
                    "analysts": ["market"],
                    "rounds": 1,
                    "language": "Chinese",
                },
                "directory": tmp,
            }
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                GraphConfigurationError, "primary graph configuration failure"
            ):
                execute(request, FailingGraph, FailingStats)

    def test_run_budget_stops_calls_and_tokens_at_next_call_boundary(self):
        budget = RunBudgetCallback(max_calls=2, max_tokens=5)
        budget.on_chat_model_start({}, [])
        budget.on_llm_end(
            SimpleNamespace(
                generations=[
                    [
                        SimpleNamespace(
                            message=SimpleNamespace(
                                usage_metadata={"input_tokens": 3, "output_tokens": 2}
                            )
                        )
                    ]
                ]
            )
        )
        with self.assertRaises(RunBudgetExceeded):
            budget.on_chat_model_start({}, [])
        self.assertEqual(budget.usage_known_calls, 1)
        self.assertEqual((budget.tokens_in, budget.tokens_out), (3, 2))

    def test_run_budget_does_not_invent_token_usage_when_provider_omits_it(self):
        budget = RunBudgetCallback(max_calls=2, max_tokens=5)
        budget.on_chat_model_start({}, [])
        budget.on_llm_end(
            SimpleNamespace(
                generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata=None))]]
            )
        )
        self.assertEqual(budget.llm_calls, 1)
        self.assertEqual(budget.usage_known_calls, 0)
        self.assertEqual((budget.tokens_in, budget.tokens_out, budget.tokens), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
