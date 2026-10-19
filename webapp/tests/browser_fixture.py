"""Disposable, offline browser QA server. Never used by the desktop launcher."""

import copy
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from webapp.tests.test_engine import OfflineModel


class SlowOfflineModel(OfflineModel):
    def _generate(self, *args, **kwargs):
        time.sleep(0.6)
        return super()._generate(*args, **kwargs)


def worker():
    from tradingagents.default_config import DEFAULT_CONFIG
    from webapp.worker import execute

    request = json.loads(sys.stdin.readline())
    tmp = Path(request["directory"])
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(memory_log_path=str(tmp / "memory.md"), data_cache_dir=str(tmp / "cache"))
    client = type("Client", (), {"get_llm": lambda _: SlowOfflineModel()})()
    with (
        patch("tradingagents.default_config.DEFAULT_CONFIG", cfg),
        patch("tradingagents.graph.trading_graph.create_tier_client", return_value=client),
        patch(
            "tradingagents.graph.trading_graph.TradingAgentsGraph.resolve_instrument_context",
            return_value="AAPL · OFFLINE UI TEST",
        ),
    ):
        execute(request)


def serve():
    import webapp.core as core
    from webapp.server import Server

    with tempfile.TemporaryDirectory(prefix="tradingagents-browser-qa-") as directory:
        core.DATA = Path(directory)
        manager = core.Manager(
            core.Store(directory),
            [sys.executable, "-u", "-m", "webapp.tests.browser_fixture", "--worker"],
        )
        server = Server(("127.0.0.1", 18785), manager)
        print(server.origin, flush=True)
        try:
            server.serve_forever()
        finally:
            manager.shutdown()
            server.server_close()


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else serve()
