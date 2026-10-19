"""Each analysis runs in a separate process, keeping provider settings isolated."""

import copy
import json
import sys
import threading

from webapp.core import ANALYSTS, LABELS, PREFIX

_lock = threading.Lock()


def emit(kind, **data):
    with _lock:
        print(PREFIX + json.dumps({"type": kind, **data}, ensure_ascii=False), flush=True)


def execute(request, graph_class=None, stats_class=None):
    from webapp.core import validate_run

    if not isinstance(request, dict) or not isinstance(request.get("directory"), str):
        raise ValueError("工作进程请求格式不正确。")
    chosen = validate_run(request.get("config"), check_credentials=False)
    if len(request["directory"]) > 4096 or "\x00" in request["directory"]:
        raise ValueError("工作目录格式不正确。")

    from tradingagents.default_config import DEFAULT_CONFIG

    if graph_class is None:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph_class = TradingAgentsGraph
    if stats_class is None:
        from cli.stats_handler import StatsCallbackHandler

        stats_class = StatsCallbackHandler
    from pathlib import Path

    from tradingagents.agents.rating import run_rating

    directory = Path(request["directory"])
    if not directory.is_absolute():
        raise ValueError("工作目录必须是绝对路径。")
    directory.mkdir(parents=True, exist_ok=True)
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
        checkpoint_enabled=False,
        llm_max_retries=2,
    )
    stats = stats_class()
    emit("stage", text="正在连接模型与数据源")
    graph = graph_class(selected_analysts=chosen["analysts"], config=config, callbacks=[stats])
    emit("stage", text="正在识别标的、准备历史数据")
    initial = graph.create_run_state(chosen["ticker"], chosen["date"], chosen["asset_type"])
    args = graph.propagator.get_graph_args(callbacks=[stats])
    seen, sent = set(), {}
    state = dict(initial)
    stage = ""
    for messages, chunk in graph.stream_run(initial, **args):
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
            risk.get(k) for k in ("aggressive_history", "conservative_history", "neutral_history")
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
        emit("stats", stats=stats.get_stats())
    if not state.get("final_trade_decision"):
        raise RuntimeError("模型流程结束，但没有产生最终决策。已保留收到的分析师报告。")
    graph.record_decision(chosen["ticker"], chosen["date"], state)
    graph.save_reports(state, chosen["ticker"])
    emit("result", rating=run_rating(state))
    emit("stats", stats=stats.get_stats())


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
