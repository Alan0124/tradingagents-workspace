"""Each analysis runs in a separate process, keeping provider settings isolated."""

import copy
import json
import sys
import threading
from contextlib import nullcontext, suppress

from langchain_core.callbacks import BaseCallbackHandler

from webapp.core import (
    ANALYSTS,
    LABELS,
    PREFIX,
    RUN_LLM_CALL_LIMIT,
    RUN_TOKEN_LIMIT,
)

_lock = threading.Lock()


class RunBudgetExceeded(RuntimeError):
    """Raised before a model call once this run reaches its call or token cap."""


class RunBudgetCallback(BaseCallbackHandler):
    """Bound model spend and elapsed work across providers through callbacks."""

    raise_error = True
    run_inline = True

    def __init__(self, max_calls, max_tokens):
        super().__init__()
        self.max_calls = max(0, int(max_calls))
        self.max_tokens = max(0, int(max_tokens))
        self.llm_calls = 0
        self.tokens = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.usage_known_calls = 0
        self.lock = threading.Lock()

    def _begin_call(self):
        with self.lock:
            if self.llm_calls >= self.max_calls or self.tokens >= self.max_tokens:
                raise RunBudgetExceeded("本次研究达到资源上限，已保留已生成的报告。")
            self.llm_calls += 1

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._begin_call()

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self._begin_call()

    def on_llm_end(self, response, **kwargs):
        try:
            generation = response.generations[0][0]
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None) or {}
        except (IndexError, TypeError, AttributeError):
            return
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            return
        with self.lock:
            self.tokens_in += input_tokens
            self.tokens_out += output_tokens
            self.tokens += input_tokens + output_tokens
            self.usage_known_calls += 1


def emit(kind, **data):
    with _lock:
        print(PREFIX + json.dumps({"type": kind, **data}, ensure_ascii=False), flush=True)


def _bounded_int(value, default, *, minimum=0, maximum=10**9):
    if type(value) is not int:
        return default
    return min(max(value, minimum), maximum)


def _cumulative_stats(stats, budget, base):
    current = stats.get_stats()
    llm_calls = _bounded_int(current.get("llm_calls"), 0) + _bounded_int(
        base.get("llm_calls"), 0
    )
    tool_calls = _bounded_int(current.get("tool_calls"), 0) + _bounded_int(
        base.get("tool_calls"), 0
    )
    known_calls = budget.usage_known_calls + _bounded_int(base.get("usage_known_calls"), 0)
    usage_reported = known_calls > 0 or base.get("usage_reported") is True
    usage_complete = llm_calls > 0 and known_calls == llm_calls
    if usage_reported:
        tokens_in = budget.tokens_in + _bounded_int(base.get("tokens_in"), 0)
        tokens_out = budget.tokens_out + _bounded_int(base.get("tokens_out"), 0)
    else:
        tokens_in = tokens_out = None
    return {
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "usage_known_calls": known_calls,
        "usage_reported": usage_reported,
        "usage_complete": usage_complete,
    }


def execute(request, graph_class=None, stats_class=None):
    from webapp.core import validate_run

    if not isinstance(request, dict) or not isinstance(request.get("directory"), str):
        raise ValueError("工作进程请求格式不正确。")
    chosen = validate_run(request.get("config"), check_credentials=False)
    if len(request["directory"]) > 4096 or "\x00" in request["directory"]:
        raise ValueError("工作目录格式不正确。")

    from pathlib import Path

    from tradingagents.agents.rating import run_rating
    from tradingagents.dataflows.evidence import EvidenceLedger
    from tradingagents.default_config import DEFAULT_CONFIG

    if graph_class is None:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph_class = TradingAgentsGraph
    if stats_class is None:
        from cli.stats_handler import StatsCallbackHandler

        stats_class = StatsCallbackHandler

    directory = Path(request["directory"])
    if not directory.is_absolute():
        raise ValueError("工作目录必须是绝对路径。")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    cache_directory = directory / "cache"
    cache_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_directory.chmod(0o700)

    raw_limits = request.get("limits") if isinstance(request.get("limits"), dict) else {}
    call_limit = _bounded_int(raw_limits.get("llm_calls"), RUN_LLM_CALL_LIMIT)
    token_limit = _bounded_int(raw_limits.get("tokens"), RUN_TOKEN_LIMIT)
    raw_stats_base = (
        request.get("stats_base") if isinstance(request.get("stats_base"), dict) else {}
    )
    base_stats = {
        key: _bounded_int(raw_stats_base.get(key), 0)
        for key in ("llm_calls", "tool_calls", "tokens_in", "tokens_out")
    }
    base_stats["usage_known_calls"] = _bounded_int(
        raw_stats_base.get("usage_known_calls"), 0
    )
    base_stats["usage_reported"] = (
        raw_stats_base.get("usage_reported") is True or base_stats["usage_known_calls"] > 0
    )
    stats = stats_class()
    budget = RunBudgetCallback(call_limit, token_limit)
    ledger = EvidenceLedger(directory, chosen["date"], chosen["analysts"])
    primary_error = None

    def run_analysis():
        config = copy.deepcopy(DEFAULT_CONFIG)
        config.update(
            llm_provider=chosen["provider"],
            quick_think_llm=chosen["quick_model"],
            deep_think_llm=chosen["deep_model"],
            backend_url=chosen["backend_url"] or None,
            quick_think_provider=None,
            deep_think_provider=None,
            quick_think_backend_url=None,
            deep_think_backend_url=None,
            max_debate_rounds=chosen["rounds"],
            max_risk_discuss_rounds=chosen["rounds"],
            output_language=chosen["language"],
            results_dir=str(directory),
            data_cache_dir=str(cache_directory),
            market_snapshot_dir=str(cache_directory / "market"),
            checkpoint_enabled=True,
            llm_max_retries=0,
            max_tokens=(
                DEFAULT_CONFIG.get("max_tokens")
                if DEFAULT_CONFIG.get("max_tokens") not in (None, "")
                else 16384
            ),
        )
        from tradingagents.dataflows.vendors.eastmoney import is_china_equity

        if is_china_equity(chosen["ticker"]):
            # Keep every tool on the canonical China providers unless an
            # existing config explicitly selected another source.
            tools = config.setdefault("tool_vendors", {})
            for method in (
                "get_news",
                "get_fundamentals",
                "get_balance_sheet",
                "get_income_statement",
                "get_cashflow",
            ):
                tools.setdefault(method, "eastmoney")

        emit("stage", text="正在连接模型与数据源")
        graph = graph_class(
            selected_analysts=chosen["analysts"],
            config=config,
            callbacks=[stats, budget],
        )
        checkpoint_scope = getattr(graph, "checkpoint_scope", None)
        scope = (
            checkpoint_scope(chosen["ticker"], chosen["date"], chosen["asset_type"])
            if callable(checkpoint_scope)
            else nullcontext(None)
        )
        with scope as checkpoint_thread_id:
            emit("stage", text="正在识别标的、准备历史数据")
            initial = graph.create_run_state(
                chosen["ticker"], chosen["date"], chosen["asset_type"]
            )
            checkpoint_input = getattr(graph, "checkpoint_input", None)
            graph_input = checkpoint_input(initial) if callable(checkpoint_input) else initial
            if request.get("resume") and graph_input is not None:
                raise RuntimeError(
                    "未找到与本次研究配置匹配的检查点，未重新发起模型请求。"
                )
            args = graph.propagator.get_graph_args(callbacks=[stats])
            if checkpoint_thread_id:
                args.setdefault("config", {}).setdefault("configurable", {})[
                    "thread_id"
                ] = checkpoint_thread_id
            seen, sent = set(), {}
            state = dict(initial)
            stage = ""
            for messages, chunk in graph.stream_run(graph_input, **args):
                for message in messages:
                    for call in getattr(message, "tool_calls", None) or []:
                        ident = call.get("id") or json.dumps(call, sort_keys=True)
                        if ident not in seen:
                            seen.add(ident)
                            emit("log", text="调用数据工具 · " + call.get("name", "data"))
                if chunk:
                    state.update(chunk)
                reports = {
                    key: state.get(key)
                    for key in LABELS
                    if key not in ("bull", "bear", "aggressive", "conservative", "neutral")
                }
                debate = state.get("investment_debate_state") or {}
                risk = state.get("risk_debate_state") or {}
                reports.update(
                    bull=debate.get("bull_history"),
                    bear=debate.get("bear_history"),
                    aggressive=risk.get("aggressive_history"),
                    conservative=risk.get("conservative_history"),
                    neutral=risk.get("neutral_history"),
                )
                for key, text in reports.items():
                    if text and text != sent.get(key):
                        sent[key] = text
                        emit("report", key=key, text=text)
                if state.get("final_trade_decision"):
                    next_stage = "正在保存最终报告"
                elif any(
                    risk.get(k)
                    for k in ("aggressive_history", "conservative_history", "neutral_history")
                ):
                    next_stage = "风控团队正在评估交易方案"
                elif state.get("trader_investment_plan"):
                    next_stage = "正在进入风险评估"
                elif state.get("investment_plan"):
                    next_stage = "交易员正在制定交易方案"
                elif debate.get("bull_history") or debate.get("bear_history"):
                    next_stage = "多空研究员正在辩论"
                elif all(state.get(ANALYSTS[a]) for a in chosen["analysts"]):
                    next_stage = "分析师报告已完成，正在进入多空研究"
                else:
                    next_stage = "分析师正在并行研究"
                if stage != next_stage:
                    stage = next_stage
                    emit("stage", text=stage)
                emit("stats", stats=_cumulative_stats(stats, budget, base_stats))
                emit("quality", quality=ledger.quality())
            if not state.get("final_trade_decision"):
                raise RuntimeError("模型流程结束，但没有产生最终决策。已保留收到的分析师报告。")
            graph.record_decision(chosen["ticker"], chosen["date"], state)
            graph.save_reports(state, chosen["ticker"])
            clear_checkpoint = getattr(graph, "clear_checkpoint_on_success", None)
            if callable(clear_checkpoint):
                clear_checkpoint(chosen["ticker"], chosen["date"], chosen["asset_type"])
        emit("result", rating=run_rating(state))
        emit("stats", stats=_cumulative_stats(stats, budget, base_stats))

    try:
        with ledger.activate():
            run_analysis()
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_error = None
        try:
            emit("stats", stats=_cumulative_stats(stats, budget, base_stats))
        except Exception as exc:
            cleanup_error = exc
            if primary_error is not None:
                with suppress(Exception):
                    emit("log", text="用量统计未能保存。")
        try:
            ledger.flush()
        except Exception:
            if cleanup_error is None:
                cleanup_error = RuntimeError("证据副本未能保存。")
            with suppress(Exception):
                emit("log", text="证据副本未能保存。")
            if primary_error is not None:
                with suppress(Exception):
                    emit("log", text="分析本身也未完成，已保留可读取的部分结果。")
        try:
            emit("quality", quality=ledger.quality())
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def main():
    try:
        raw = sys.stdin.read(32769)
        if len(raw) > 32768 or not raw.endswith("\n"):
            raise ValueError("工作进程请求大小不正确。")
        execute(json.loads(raw))
    except Exception as exc:
        from webapp.core import redact

        emit("error", text=redact(f"{type(exc).__name__}: {exc}"))
        sys.exit(1)


if __name__ == "__main__":
    main()
