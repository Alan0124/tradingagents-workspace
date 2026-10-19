import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from webapp.core import (
    MD,
    PREFIX,
    InstanceLock,
    Manager,
    Store,
    document,
    redact,
    save_settings,
    validate_run,
    worker_environment,
)
from webapp.server import Server
from webapp.worker import execute


def config(**extra):
    return dict(
        ticker="AAPL",
        date="2026-09-01",
        provider="ollama",
        quick_model="test-model",
        deep_model="test-model",
        backend_url="http://localhost:11434/v1",
        analysts=["market", "news"],
        asset_type="stock",
        language="Chinese",
        rounds=1,
        **extra,
    )


def wait_finished(store, run_id):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        run = store.get(run_id)
        if run["status"] not in ("queued", "running", "cancelling"):
            return run
        time.sleep(0.03)
    raise AssertionError("Worker did not finish")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.manager = None

    def tearDown(self):
        if self.manager:
            self.manager.shutdown()
        self.tmp.cleanup()

    def test_validation_blocks_future_path_traversal_and_missing_credentials(self):
        for value in ("../../secrets", "..", "AAPL/../../x", ""):
            c = config()
            c["ticker"] = value
            with self.assertRaises(ValueError):
                validate_run(c, False)
        c = config()
        c["date"] = "2999-01-01"
        with self.assertRaises(ValueError):
            validate_run(c, False)
        c = config()
        c["asset_type"] = "crypto"
        c["analysts"] = ["fundamentals"]
        with self.assertRaises(ValueError):
            validate_run(c, False)
        c = config()
        c["provider"] = "openai"
        with (
            patch("webapp.core.credentials", return_value=({}, {"openai": "OPENAI_API_KEY"})),
            self.assertRaisesRegex(ValueError, "API Key"),
        ):
            validate_run(c)
        c = config()
        c["backend_url"] = "https://secret@example.com/v1"
        with self.assertRaises(ValueError):
            validate_run(c, False)
        for field, value in (
            ("ticker", None),
            ("analysts", [{}]),
            ("quick_model", {"id": "bad"}),
            ("backend_url", "https://example.com:99999/v1"),
        ):
            c = config()
            c[field] = value
            with self.assertRaises(ValueError):
                validate_run(c, False)

    def test_worker_environment_only_has_the_selected_model_key(self):
        values = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "openai-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "OPENAI_COMPATIBLE_API_KEY": "custom-secret",
            "ALPHA_VANTAGE_API_KEY": "alpha-secret",
            "FRED_API_KEY": "fred-secret",
            "TYPESAFE_API_KEY": "social-secret",
            "GITHUB_TOKEN": "git-secret",
        }
        mapping = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
        }
        openai_config = config()
        openai_config["provider"] = "openai"
        social_config = config()
        social_config.update(provider="anthropic", analysts=["social"])
        with patch("webapp.core.credentials", return_value=(values, mapping)):
            env = worker_environment(openai_config)
            social_env = worker_environment(social_config)
        self.assertEqual(env["OPENAI_API_KEY"], "openai-secret")
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("OPENAI_COMPATIBLE_API_KEY", env)
        self.assertNotIn("TYPESAFE_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["FRED_API_KEY"], "fred-secret")
        self.assertEqual(social_env["ANTHROPIC_API_KEY"], "anthropic-secret")
        self.assertEqual(social_env["TYPESAFE_API_KEY"], "social-secret")

    def test_worker_dotenv_disable_prevents_reloading_other_keys(self):
        import webapp.core as core

        root = Path(self.tmp.name) / "dotenv-root"
        root.mkdir()
        (root / ".env").write_text("ANTHROPIC_API_KEY=unrelated-file-key\n")
        (root / ".env.enterprise").write_text("GOOGLE_API_KEY=unrelated-enterprise-key\n")
        with patch(
            "webapp.core.credentials",
            return_value=(
                {"PATH": os.environ.get("PATH", ""), "OPENAI_API_KEY": "chosen-key"},
                {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"},
            ),
        ):
            selected_config = config()
            selected_config["provider"] = "openai"
            env = worker_environment(selected_config)
        env["PYTHONPATH"] = str(Path(core.__file__).resolve().parents[1])
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os, tradingagents; print(os.getenv('ANTHROPIC_API_KEY', ''))",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "")

    def test_settings_save_secrets_only_to_private_env_file(self):
        import webapp.core as core

        root = Path(self.tmp.name) / "settings-root"
        data = root / ".local" / "web"
        secret = "test-openai-secret-value"
        settings = {
            "provider": "openai",
            "api_key": secret,
            "quick_model": "fast",
            "deep_model": "deep",
            "backend_url": "",
        }
        with (
            patch.object(core, "ROOT", root),
            patch.object(core, "DATA", data),
            patch("webapp.core.credentials", return_value=({}, {"openai": "OPENAI_API_KEY"})),
            patch("webapp.core.metadata", return_value={"preferences": {}}),
        ):
            save_settings(settings)
        self.assertIn(secret, (root / ".env").read_text())
        self.assertNotIn(secret, (data / "preferences.json").read_text())
        if os.name != "nt":
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            self.assertEqual((data / "preferences.json").stat().st_mode & 0o777, 0o600)

    def test_saved_key_overrides_an_inherited_key_for_this_workspace(self):
        import webapp.core as core

        root = Path(self.tmp.name) / "credential-root"
        data = root / ".local" / "web"
        saved = "saved-key-from-settings"
        settings = {
            "provider": "openai",
            "api_key": saved,
            "quick_model": "fast",
            "deep_model": "deep",
            "backend_url": "",
        }
        with (
            patch.object(core, "ROOT", root),
            patch.object(core, "DATA", data),
            patch.dict(core.SAVED_CREDENTIAL_OVERRIDES, {}, clear=True),
            patch.dict(os.environ, {"OPENAI_API_KEY": "inherited-old-key"}),
            patch("webapp.core.metadata", return_value={}),
        ):
            save_settings(settings)
            values, _ = core.credentials()
        self.assertEqual(values["OPENAI_API_KEY"], saved)

    def test_redaction_covers_short_provider_keys_and_url_credentials(self):
        values = {"OPENAI_API_KEY": "short-key", "FRED_API_KEY": "fred-secret"}
        with patch("webapp.core.credentials", return_value=(values, {"openai": "OPENAI_API_KEY"})):
            result = redact(
                "Bearer short-key https://credentials@example.com/?api_key=query-secret"
            )
        self.assertNotIn("short-key", result)
        self.assertNotIn("credentials@", result)
        self.assertNotIn("query-secret", result)

    def test_instance_lock_is_exclusive_and_released(self):
        path = Path(self.tmp.name) / "server.lock"
        first, second = InstanceLock(path), InstanceLock(path)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_legacy_store_schema_migrates_run_status(self):
        directory = Path(self.tmp.name) / "legacy"
        directory.mkdir()
        database = directory / "workspace.sqlite3"
        run = {"id": "old", "status": "completed", "created_at": 1, "config": config()}
        with contextlib.closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            connection.execute("INSERT INTO runs VALUES (?, ?)", ("old", json.dumps(run)))
        migrated = Store(directory)
        self.assertEqual(migrated.get("old"), run)
        with contextlib.closing(sqlite3.connect(database)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            status = connection.execute("SELECT status FROM runs WHERE id=?", ("old",)).fetchone()[
                0
            ]
        self.assertIn("status", columns)
        self.assertEqual(status, "completed")

    def test_subprocess_completion_reports_and_persistent_history(self):
        script = (
            "import sys,json;json.loads(sys.stdin.readline());"
            f'print({PREFIX!r}+"[]",flush=True);'
            f'print({PREFIX!r}+json.dumps({{"type":"report","key":"invalid","text":"ignore"}}),flush=True);'
            f'print({PREFIX!r}+json.dumps({{"type":"report","key":"final_trade_decision","text":"**Rating**: Hold\\nKeep watching."}}),flush=True);'
            f'print({PREFIX!r}+json.dumps({{"type":"result","rating":"Hold"}}),flush=True)'
        )
        self.manager = Manager(self.store, [sys.executable, "-u", "-c", script])
        run = self.manager.start(config())
        finished = wait_finished(self.store, run["id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["rating"], "Hold")
        self.assertIn("Keep watching", document(finished))
        reloaded = Store(self.tmp.name).get(run["id"])
        self.assertEqual(reloaded, finished)
        self.assertNotIn("api_key", json.dumps(reloaded))

    def test_cancellation_and_single_run_lock(self):
        self.manager = Manager(
            self.store,
            [sys.executable, "-u", "-c", "import sys,time;sys.stdin.readline();time.sleep(20)"],
        )
        run = self.manager.start(config())
        with self.assertRaisesRegex(ValueError, "已有分析"):
            self.manager.start(config())
        self.manager.cancel(run["id"])
        self.assertEqual(wait_finished(self.store, run["id"])["status"], "cancelled")

    def test_run_claim_is_shared_across_manager_instances(self):
        worker = [sys.executable, "-u", "-c", "import sys,time;sys.stdin.readline();time.sleep(20)"]
        self.manager = Manager(self.store, worker)
        other = Manager(Store(self.tmp.name), worker)
        try:
            run = self.manager.start(config())
            with self.assertRaisesRegex(ValueError, "已有分析"):
                other.start(config())
            self.manager.cancel(run["id"])
            self.assertEqual(wait_finished(self.store, run["id"])["status"], "cancelled")
        finally:
            other.shutdown()

    def test_failed_process_redacts_credentials(self):
        secret = "private-test-key-12345"
        self.manager = Manager(
            self.store,
            [
                sys.executable,
                "-u",
                "-c",
                'import sys;sys.stdin.readline();print("private-test-key-12345");sys.exit(1)',
            ],
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            run = self.manager.start(config())
            finished = wait_finished(self.store, run["id"])
        self.assertEqual(finished["status"], "failed")
        self.assertNotIn(secret, finished["error"])
        self.assertIn("已隐藏", finished["error"])

    def test_restart_marks_unfinished_runs(self):
        self.store.put({"id": "old", "status": "running", "created_at": 1, "config": config()})
        self.manager = Manager(self.store)
        self.assertEqual(self.store.get("old")["status"], "interrupted")

    def test_markdown_does_not_execute_model_html(self):
        rendered = MD.render("<script>alert(1)</script>\n\n[x](javascript:alert(1))")
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_http_origin_token_host_and_export(self):
        self.manager = Manager(self.store)
        server = Server(("127.0.0.1", 0), self.manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = server.origin
        try:
            with urllib.request.urlopen(base + "/api/bootstrap") as response:
                result = json.load(response)
            self.assertIn("token", result)
            self.assertNotIn("api_key", result)
            request = urllib.request.Request(
                base + "/api/runs",
                data=json.dumps(config()).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(request)
            self.assertEqual(exc.exception.code, 403)
            request = urllib.request.Request(
                base + "/api/runs",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Origin": base,
                    "X-Workspace-Token": result["token"],
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(request)
            self.assertEqual(exc.exception.code, 400)
            request = urllib.request.Request(
                base + "/api/bootstrap", headers={"Host": "attacker.example"}
            )
            with self.assertRaises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(request)
            self.assertEqual(exc.exception.code, 403)
            run = {
                "id": "saved",
                "config": config(),
                "status": "completed",
                "created_at": 1,
                "reports": {"final_trade_decision": "**Rating**: Hold\n\n<script>bad()</script>"},
                "rating": "Hold",
                "events": [],
            }
            self.store.put(run)
            with urllib.request.urlopen(base + "/api/runs/saved/report.html") as response:
                body = response.read().decode()
                self.assertIn("attachment;", response.headers["Content-Disposition"])
                self.assertNotIn("<script>", body)
                self.assertIn("AAPL", body)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/.env")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_worker_streams_real_graph_contract_and_clears_inherited_tiers(self):
        captured = {}

        class Stats:
            def get_stats(self):
                return {"llm_calls": 3, "tool_calls": 2, "tokens_in": 200, "tokens_out": 100}

        class FakeGraph:
            def __init__(self, selected_analysts, config, callbacks):
                captured.update(config)
                self.propagator = self

            def create_run_state(self, ticker, date, asset):
                return {"company_of_interest": ticker, "trade_date": date}

            def get_graph_args(self, **kwargs):
                return {}

            def stream_run(self, initial, **kwargs):
                yield [], {"market_report": "Technical report"}
                yield (
                    [],
                    {
                        "news_report": "News report",
                        "investment_debate_state": {"bull_history": "Bull", "bear_history": "Bear"},
                    },
                )
                yield (
                    [],
                    {
                        "investment_plan": "Research decision",
                        "trader_investment_plan": "Transaction",
                    },
                )
                yield (
                    [],
                    {
                        "risk_debate_state": {
                            "aggressive_history": "A",
                            "conservative_history": "C",
                            "neutral_history": "N",
                        }
                    },
                )
                yield [], {"final_trade_decision": "**Rating**: Hold", "final_rating": "Hold"}

            def record_decision(self, *args):
                captured["recorded"] = True

            def save_reports(self, *args):
                captured["saved"] = True

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            execute(
                {"config": config(), "directory": self.tmp.name},
                graph_class=FakeGraph,
                stats_class=Stats,
            )
        events = [json.loads(s[len(PREFIX) :]) for s in output.getvalue().splitlines()]
        self.assertTrue(captured["recorded"] and captured["saved"])
        self.assertIsNone(captured["deep_think_provider"])
        self.assertEqual(captured["llm_provider"], "ollama")
        self.assertEqual(
            {e["key"] for e in events if e["type"] == "report"},
            {
                "market_report",
                "news_report",
                "bull",
                "bear",
                "investment_plan",
                "trader_investment_plan",
                "aggressive",
                "conservative",
                "neutral",
                "final_trade_decision",
            },
        )
        self.assertEqual(next(e["rating"] for e in events if e["type"] == "result"), "Hold")


if __name__ == "__main__":
    unittest.main()
