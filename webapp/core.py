from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from dotenv import dotenv_values, set_key
from markdown_it import MarkdownIt

from webapp import __version__ as WORKSPACE_VERSION

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_IS_CHECKOUT = (_SOURCE_ROOT / "pyproject.toml").is_file() and (
    _SOURCE_ROOT / "tradingagents"
).is_dir()
_WORKSPACE_OVERRIDE = os.environ.get("TRADINGAGENTS_WORKSPACE_DIR")
if _WORKSPACE_OVERRIDE:
    ROOT = Path(_WORKSPACE_OVERRIDE).expanduser().resolve()
elif _IS_CHECKOUT:
    ROOT = _SOURCE_ROOT
else:
    # Installed packages must keep writable state out of site-packages.
    ROOT = Path.home() / ".tradingagents" / "workspace"
DATA = ROOT / ".local" / "web" if _IS_CHECKOUT else ROOT
PREFIX = "@@TA_EVENT@@"
LABELS = {
    "market_report": "技术面分析",
    "fundamentals_report": "基本面分析",
    "news_report": "新闻分析",
    "sentiment_report": "市场情绪",
    "bull": "多头观点",
    "bear": "空头观点",
    "investment_plan": "研究经理结论",
    "trader_investment_plan": "交易方案",
    "aggressive": "积极风控",
    "conservative": "保守风控",
    "neutral": "中性风控",
    "final_trade_decision": "最终决策",
}
ANALYSTS = {
    "market": "market_report",
    "fundamentals": "fundamentals_report",
    "news": "news_report",
    "social": "sentiment_report",
}
PROVIDERS = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "qwen-cn": "通义千问 · 中国",
    "qwen": "通义千问 · 国际",
    "glm-cn": "智谱 · 中国",
    "glm": "Z.AI · 国际",
    "kimi": "Kimi",
    "minimax-cn": "MiniMax · 中国",
    "minimax": "MiniMax · 国际",
    "openrouter": "OpenRouter",
    "xai": "xAI",
    "mistral": "Mistral",
    "groq": "Groq",
    "nvidia": "NVIDIA",
    "chatgpt": "ChatGPT 订阅",
    "ollama": "Ollama 本地模型",
    "openai_compatible": "自定义兼容接口",
}
ACTIVE = {"queued", "running", "cancelling"}
MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
SETTINGS_LOCK = threading.RLock()
SAVED_CREDENTIAL_OVERRIDES = {}
INSTANCE_LOCK_GUARD = threading.Lock()
INSTANCE_LOCK_HELD = set()
DATA_API_KEYS = {"ALPHA_VANTAGE_API_KEY", "FRED_API_KEY"}
OPTIONAL_SERVICE_KEYS = {"TYPESAFE_API_KEY"}
OTHER_SECRET_KEYS = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GITHUB_TOKEN",
}


class InstanceLock:
    """Exclusive process lock for the web service sharing this workspace."""

    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        self.key = os.path.normcase(str(self.path.resolve()))
        with INSTANCE_LOCK_GUARD:
            if self.key in INSTANCE_LOCK_HELD:
                return False
            INSTANCE_LOCK_HELD.add(self.key)
        stream = None
        try:
            self.path.touch(exist_ok=True)
            os.chmod(self.path, 0o600)
            if os.name == "nt":
                stream = self.path.open("r+b")
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    stream.close()
                    with INSTANCE_LOCK_GUARD:
                        INSTANCE_LOCK_HELD.discard(self.key)
                    return False
                stream.seek(0)
                stream.write(str(os.getpid()).encode())
                stream.truncate()
                stream.flush()
            else:
                stream = self.path.open("a+", encoding="utf-8")
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    with INSTANCE_LOCK_GUARD:
                        INSTANCE_LOCK_HELD.discard(self.key)
                    return False
                stream.seek(0)
                stream.truncate()
                stream.write(str(os.getpid()))
                stream.flush()
        except Exception:
            if stream is not None:
                with suppress(OSError):
                    stream.close()
            with INSTANCE_LOCK_GUARD:
                INSTANCE_LOCK_HELD.discard(self.key)
            raise
        self.stream = stream
        return True

    def release(self):
        if self.stream is not None:
            try:
                if os.name == "nt":
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None
                with INSTANCE_LOCK_GUARD:
                    INSTANCE_LOCK_HELD.discard(self.key)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def credentials():
    from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

    with SETTINGS_LOCK:
        file_values = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}
        values = {**file_values, **os.environ}
        values.update(SAVED_CREDENTIAL_OVERRIDES)
        return values, PROVIDER_API_KEY_ENV


def redact(text):
    text = str(text)
    values, mapping = credentials()
    names = (
        {name for name in mapping.values() if name}
        | DATA_API_KEYS
        | OPTIONAL_SERVICE_KEYS
        | OTHER_SECRET_KEYS
    )
    names |= {
        name for name in values if re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD)$", name, re.I)
    }
    secrets = sorted(
        {
            str(values.get(name))
            for name in names
            if isinstance(values.get(name), str)
            and len(values[name]) >= 4
            and not values[name].isspace()
        },
        key=len,
        reverse=True,
    )
    for secret in secrets:
        text = text.replace(secret, "[已隐藏密钥]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;\"\']+", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[已隐藏]@", text)
    text = re.sub(r"(?i)([?&](?:api[_-]?key|token|secret|password)=)[^&#\s]+", r"\1[已隐藏]", text)
    return text


def chatgpt_account():
    from tradingagents.llm_clients.chatgpt_auth import signed_in_as

    return signed_in_as()


def metadata():
    from tradingagents import __version__
    from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS

    values, keys = credentials()
    saved = DATA / "preferences.json"
    try:
        loaded = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
    except (OSError, ValueError):
        loaded = {}
    preferences = (
        {
            key: loaded[key]
            for key in ("provider", "quick_model", "deep_model", "backend_url")
            if isinstance(loaded, dict) and isinstance(loaded.get(key), str)
        }
        if isinstance(loaded, dict)
        else {}
    )
    providers = []
    for key, label in PROVIDERS.items():
        models = MODEL_OPTIONS.get(key, MODEL_OPTIONS.get("openai", {}) if key == "chatgpt" else {})
        providers.append(
            {
                "id": key,
                "name": label,
                "configured": bool(values.get(keys.get(key, ""))),
                "key_required": bool(keys.get(key)) and key != "openai_compatible",
                "models": {
                    tier: [{"label": a, "id": b} for a, b in options if b != "custom"]
                    for tier, options in models.items()
                },
            }
        )
    return {
        "version": __version__,
        "workspace_version": WORKSPACE_VERSION,
        "providers": providers,
        "preferences": preferences,
        "chatgpt_account": chatgpt_account(),
        "labels": LABELS,
        "app": "tradingagents-workspace",
    }


def save_settings(body):
    if not isinstance(body, dict):
        raise ValueError("请求格式不正确。")
    provider = body.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("请选择有效的模型服务。")
    if not isinstance(body.get("api_key", ""), str):
        raise ValueError("密钥格式不正确。")
    key = body.get("api_key", "").strip()
    if len(key) > 4096 or any(ord(ch) < 32 for ch in key):
        raise ValueError("密钥格式不正确。")
    fields = ("provider", "quick_model", "deep_model", "backend_url")
    if any(not isinstance(body.get(field, ""), str) for field in fields):
        raise ValueError("设置格式不正确。")
    prefs = {field: body.get(field, "").strip() for field in fields}
    if any(
        not value or len(value) > 180 or re.search(r"[\x00-\x1f]", value)
        for value in (prefs["quick_model"], prefs["deep_model"])
    ):
        raise ValueError("模型 ID 格式不正确。")
    if prefs["backend_url"]:
        validate_url(prefs["backend_url"])
    if provider == "openai_compatible" and not prefs["backend_url"]:
        raise ValueError("自定义模型服务需要填写接口地址。")
    _, mapping = credentials()
    variable = mapping.get(provider)
    with SETTINGS_LOCK:
        if key and variable:
            ROOT.mkdir(parents=True, exist_ok=True)
            set_key(str(ROOT / ".env"), variable, key)
            (ROOT / ".env").chmod(0o600)
            SAVED_CREDENTIAL_OVERRIDES[variable] = key
        DATA.mkdir(parents=True, exist_ok=True)
        DATA.chmod(0o700)
        target = DATA / "preferences.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(target)
    return metadata()


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(ch) <= 32 for ch in url):
        raise ValueError("接口地址需为不含密钥或查询参数的 http(s) URL。")
    try:
        parsed = urlparse(url)
        # Accessing .port validates its range and syntax.
        port = parsed.port
        if port == 0:
            raise ValueError("invalid port")
    except ValueError:
        raise ValueError("接口地址格式不正确。") from None
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("接口地址需为不含密钥或查询参数的 http(s) URL。")


def validate_run(body, check_credentials=True):
    if not isinstance(body, dict):
        raise ValueError("请求格式不正确。")
    if not isinstance(body.get("ticker"), str):
        raise ValueError("请输入有效的股票代码，例如 AAPL、0700.HK 或 600519.SS。")
    ticker = body["ticker"].strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,30}", ticker) or ".." in ticker:
        raise ValueError("请输入有效的股票代码，例如 AAPL、0700.HK 或 600519.SS。")
    date = body.get("date")
    if not isinstance(date, str):
        raise ValueError("请选择有效的分析日期。")
    try:
        parsed = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("请选择有效的分析日期。") from None
    if parsed.strftime("%Y-%m-%d") != date or parsed.date() > datetime.now().date():
        raise ValueError("分析日期不能晚于今天。")
    provider = body.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError("请选择模型服务。")
    analysts = body.get("analysts")
    if (
        not isinstance(analysts, list)
        or not analysts
        or len(analysts) > len(ANALYSTS)
        or any(not isinstance(a, str) or a not in ANALYSTS for a in analysts)
    ):
        raise ValueError("请至少选择一位分析师。")
    asset_type = body.get("asset_type", "stock")
    if not isinstance(asset_type, str) or asset_type not in ("stock", "crypto"):
        raise ValueError("资产类型不正确。")
    if asset_type == "crypto" and "fundamentals" in analysts:
        raise ValueError("加密资产不支持公司基本面分析，请取消基本面分析师。")
    models_raw = {k: body.get(k, "") for k in ("quick_model", "deep_model")}
    if any(not isinstance(v, str) for v in models_raw.values()):
        raise ValueError("请填写快速模型和深度模型的 ID。")
    models = {key: value.strip() for key, value in models_raw.items()}
    if any(not v or len(v) > 180 or re.search(r"[\x00-\x1f]", v) for v in models.values()):
        raise ValueError("请填写快速模型和深度模型的 ID。")
    rounds = body.get("rounds", 1)
    if type(rounds) is not int or not 1 <= rounds <= 3:
        raise ValueError("研究深度需为 1–3 轮。")
    endpoint_raw = body.get("backend_url", "")
    if not isinstance(endpoint_raw, str):
        raise ValueError("接口地址格式不正确。")
    endpoint = endpoint_raw.strip()
    if endpoint:
        validate_url(endpoint)
    if provider == "openai_compatible" and not endpoint:
        raise ValueError("自定义模型服务需要填写接口地址。")
    if check_credentials:
        values, keys = credentials()
        keyvar = keys.get(provider)
        if keyvar and provider != "openai_compatible" and not values.get(keyvar):
            raise ValueError(f"{PROVIDERS[provider]} 尚未配置 API Key，请先打开模型设置。")
        if provider == "chatgpt" and not chatgpt_account():
            raise ValueError("请先在模型设置中登录 ChatGPT。")
    language = body.get("language", "Chinese")
    if language not in ("Chinese", "English"):
        raise ValueError("报告语言不正确。")
    return {
        "ticker": ticker,
        "date": date,
        "provider": provider,
        "analysts": list(dict.fromkeys(analysts)),
        "asset_type": asset_type,
        "rounds": rounds,
        "backend_url": endpoint,
        "language": language,
        **models,
    }


class Store:
    def __init__(self, root=DATA):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.path = self.root / "workspace.sqlite3"
        self.lock = threading.RLock()
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, status TEXT NOT NULL, document TEXT NOT NULL)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
            if "status" not in columns:
                # Migrate the original two-column workspace file in place.
                db.execute("ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT ''")
                rows = db.execute("SELECT id, document FROM runs").fetchall()
                for run_id, raw in rows:
                    try:
                        status = json.loads(raw).get("status", "")
                    except (ValueError, AttributeError):
                        status = ""
                    db.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
            db.execute("CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status)")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def all(self):
        with self.lock, self.db() as db:
            items = [json.loads(row[0]) for row in db.execute("SELECT document FROM runs")]
        return sorted(items, key=lambda r: r["created_at"], reverse=True)

    def get(self, run_id):
        with self.lock, self.db() as db:
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError("未找到这次分析。")
        return json.loads(row[0])

    def put(self, run):
        with self.lock, self.db() as db:
            db.execute(
                "INSERT INTO runs(id, status, document) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, document=excluded.document",
                (run["id"], run.get("status", ""), json.dumps(run, ensure_ascii=False)),
            )

    def claim_if_idle(self, run):
        """Atomically insert a run only if no other process owns an active run."""
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM runs WHERE status IN (?, ?, ?) LIMIT 1", tuple(ACTIVE)
            ).fetchone():
                return False
            db.execute(
                "INSERT INTO runs(id, status, document) VALUES (?, ?, ?)",
                (run["id"], run["status"], json.dumps(run, ensure_ascii=False)),
            )
            return True

    def mutate(self, run_id, fn):
        with self.lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError("未找到这次分析。")
            run = json.loads(row[0])
            fn(run)
            db.execute(
                "UPDATE runs SET status=?, document=? WHERE id=?",
                (run.get("status", ""), json.dumps(run, ensure_ascii=False), run_id),
            )
            return run


def summary(run):
    return {k: v for k, v in run.items() if k not in ("reports", "events")}


def document(run):
    config = run["config"]
    text = (
        f"# {config['ticker']} 投研报告\n\n分析日期：{config['date']}  \n"
        f"模型服务：{config['provider']}  \n快速模型：{config['quick_model']}  \n"
        f"深度模型：{config['deep_model']}  \n状态：{run['status']}  \n"
        f"最终评级：{run.get('rating') or '尚未生成'}\n\n"
    )
    for key, label in LABELS.items():
        if run["reports"].get(key):
            text += f"## {label}\n\n{run['reports'][key]}\n\n"
    return text


def report_html(run):
    """Self-contained, printable report; model text remains escaped Markdown."""
    content = MD.render(document(run))
    ticker = escape(run["config"]["ticker"])
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{ticker} · TradingAgents 投研报告</title>"
        "<style>"
        ":root{color-scheme:light}*{box-sizing:border-box}"
        "body{margin:0;background:#f2f1e9;color:#243c30;font:15px/1.9 system-ui,sans-serif}"
        "header,main,footer{max-width:940px;margin:auto;padding:32px 42px}"
        "header{display:flex;justify-content:space-between;align-items:center;gap:20px;"
        "border-bottom:1px solid #dce2d3;color:#59765d;font-size:11px;letter-spacing:2px}"
        "header b{font-size:14px;color:#214b36;letter-spacing:.5px}"
        "main{margin:30px auto 0;padding:42px 48px;background:#fffef9;"
        "border:1px solid #e1e4d7;border-radius:12px}"
        "h1,h2,h3{line-height:1.45;color:#203b2b}h1{font:36px/1.4 Georgia,serif;margin-top:0}"
        "h2{font-size:23px;margin:48px 0 18px;padding-top:22px;border-top:1px solid #e5e7dd}"
        "h3{font-size:18px;margin-top:30px}p{margin:14px 0}a{color:#316c49;overflow-wrap:anywhere}"
        "table{border-collapse:collapse;display:block;overflow:auto;max-width:100%;font-size:13px}"
        "td,th{padding:10px 14px;border:1px solid #dfe5d7;vertical-align:top}"
        "th{background:#f1f5e9;text-align:left}pre{overflow:auto;padding:18px;background:#f3f5ed;"
        "border-radius:6px;white-space:pre-wrap}code{font-size:13px;background:#f0f3e9;padding:2px 4px}"
        "blockquote{margin:20px 0;padding:1px 20px;border-left:3px solid #b9cca6;color:#6c7f63}"
        "footer{font-size:11px;color:#839176;display:flex;justify-content:space-between;gap:20px}"
        "@media(max-width:640px){header,footer{padding:22px}main{margin:12px;padding:24px;"
        "border-radius:8px}h1{font-size:28px}header span{display:none}}"
        "@media print{body,main{background:white}header,main,footer{max-width:none;padding:12px 0}"
        "main{border:0;margin:0}h2,h3{break-after:avoid}tr{break-inside:avoid}"
        "a{color:inherit}pre{white-space:pre-wrap}}"
        "</style></head><body>"
        "<header><b>TradingAgents Workspace</b><span>RESEARCH NOTE · 本地研究档案</span></header>"
        f"<main>{content}</main>"
        "<footer><span>由 TradingAgents Workspace 导出</span><span>AI 辅助研究 · 请独立评估结论</span></footer>"
        "</body></html>"
    )


def worker_environment(config):
    """Keep the selected model credential out of unrelated provider clients."""
    values, mapping = credentials()
    env = dict(values)
    # tradingagents imports dotenv at package import; prevent it from adding
    # all unrelated keys back into this process after the allowlist is applied.
    env["PYTHON_DOTENV_DISABLED"] = "1"
    provider_keys = {name for name in mapping.values() if name}
    secret_names = provider_keys | DATA_API_KEYS | OPTIONAL_SERVICE_KEYS | OTHER_SECRET_KEYS
    secret_names |= {
        name for name in env if re.search(r"(?:API_KEY|TOKEN|SECRET|PASSWORD)$", name, re.I)
    }
    for name in secret_names:
        env.pop(name, None)

    selected_key = mapping.get(config["provider"])
    if selected_key and values.get(selected_key):
        env[selected_key] = values[selected_key]
    # These are separate data services used by the existing analysis engine.
    for name in DATA_API_KEYS:
        if values.get(name):
            env[name] = values[name]
    # The social analyst can call TypeSafe; other runs need no access to it.
    if "social" in config["analysts"] and values.get("TYPESAFE_API_KEY"):
        env["TYPESAFE_API_KEY"] = values["TYPESAFE_API_KEY"]
    return env


def process_group_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop_process(process, *, force=False):
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError, PermissionError):
        if os.name == "nt":
            (process.kill if force else process.terminate)()
        else:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)


class Manager:
    def __init__(self, store, worker_cmd=None):
        self.store = store
        self.lock = threading.RLock()
        self.processes = {}
        self.worker_cmd = worker_cmd or [sys.executable, "-u", "-m", "webapp.worker"]
        for run in store.all():
            if run["status"] in ACTIVE:
                run.update(
                    status="interrupted",
                    finished_at=time.time(),
                    error="服务已重启，分析中断。可以使用相同配置重新运行。",
                )
                store.put(run)

    def start(self, config, check_credentials=True):
        config = validate_run(config, check_credentials=check_credentials)
        with self.lock:
            run_id = uuid.uuid4().hex
            run = {
                "id": run_id,
                "config": config,
                "status": "queued",
                "created_at": time.time(),
                "finished_at": None,
                "stage": "准备数据",
                "rating": None,
                "reports": {},
                "events": [],
                "stats": {"llm_calls": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0},
                "error": None,
            }
            if not self.store.claim_if_idle(run):
                raise ValueError("已有分析正在运行，请等待完成或先停止当前分析。")
            process = None
            try:
                workdir = self.store.root / "runs" / run_id
                workdir.mkdir(parents=True, mode=0o700)
                workdir.chmod(0o700)
                process = subprocess.Popen(
                    self.worker_cmd,
                    cwd=ROOT,
                    env=worker_environment(config),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    **process_group_options(),
                )
                self.processes[run_id] = process
                process.stdin.write(
                    json.dumps({"config": config, "directory": str(workdir)}) + "\n"
                )
                process.stdin.close()
            except Exception:
                if process is not None:
                    stop_process(process, force=True)
                    for stream in (process.stdin, process.stdout):
                        if stream:
                            with suppress(OSError):
                                stream.close()
                    self.processes.pop(run_id, None)
                self.store.mutate(
                    run_id,
                    lambda r: r.update(
                        status="failed", finished_at=time.time(), error="分析进程启动失败。"
                    ),
                )
                raise
            threading.Thread(target=self._consume, args=(run_id, process), daemon=True).start()
            return self.store.get(run_id)

    def _consume(self, run_id, process):
        tail = []
        protocol_error = False
        try:
            while True:
                line = process.stdout.readline(8 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 8 * 1024 * 1024:
                    protocol_error = True
                    if not line.endswith("\n"):
                        while line and not line.endswith("\n"):
                            line = process.stdout.readline(65536)
                    continue
                line = line.rstrip()
                if not line.startswith(PREFIX):
                    if line:
                        tail = (tail + [redact(line)])[-8:]
                    continue
                try:
                    event = json.loads(line[len(PREFIX) :])
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get("type")
                if kind not in {"report", "stage", "stats", "result", "error", "log"}:
                    continue
                if kind == "report" and (
                    event.get("key") not in LABELS or not isinstance(event.get("text"), str)
                ):
                    continue
                if kind in {"stage", "error", "log"} and not isinstance(event.get("text"), str):
                    continue
                if kind == "stats":
                    stats = event.get("stats")
                    if not isinstance(stats, dict):
                        continue
                    event["stats"] = {
                        key: value
                        for key in ("llm_calls", "tool_calls", "tokens_in", "tokens_out")
                        if type(value := stats.get(key)) is int and 0 <= value <= 10**12
                    }
                if (
                    kind == "result"
                    and event.get("rating") is not None
                    and not isinstance(event.get("rating"), str)
                ):
                    continue

                def update(run, *, event=event, kind=kind):
                    if run["status"] not in ("queued", "running"):
                        return
                    run["status"] = "running"
                    if kind == "report" and event.get("key") in LABELS:
                        run["reports"][event["key"]] = redact(event["text"][: 8 * 1024 * 1024])
                    elif kind == "stage":
                        run["stage"] = redact(event["text"][:150])
                    elif kind == "stats":
                        run["stats"] = event["stats"]
                    elif kind == "result":
                        rating = event.get("rating")
                        run["rating"] = redact(rating[:250]) if isinstance(rating, str) else None
                    elif kind == "error":
                        run["error"] = redact(event["text"][:3000])
                    if kind in ("log", "stage", "error"):
                        run["events"] = (
                            run["events"]
                            + [
                                {
                                    "time": time.time(),
                                    "type": kind,
                                    "text": redact(event["text"][:600]),
                                }
                            ]
                        )[-200:]

                self.store.mutate(run_id, update)
            code = process.wait()

            def finish(run):
                if run["status"] in ("cancelling", "cancelled"):
                    run.update(status="cancelled", stage="已停止")
                elif code == 0 and run["reports"].get("final_trade_decision"):
                    run.update(status="completed", stage="分析完成")
                else:
                    run.update(
                        status="failed",
                        stage="分析失败",
                        error=run["error"]
                        or (
                            "Worker 输出过大的消息，无法解析。"
                            if protocol_error
                            else "\n".join(tail) or "未生成最终报告，请检查模型和数据源后重试。"
                        ),
                    )
                run["finished_at"] = time.time()

            self.store.mutate(run_id, finish)
        except Exception:
            # Keep an unexpected pipe/storage failure from leaving a permanent
            # queued record. The detailed exception can contain provider data.
            if process.poll() is None:
                stop_process(process, force=True)
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=2)
            with suppress(Exception):
                self.store.mutate(
                    run_id,
                    lambda run: run.update(
                        status="failed",
                        finished_at=time.time(),
                        stage="分析失败",
                        error="读取分析进程输出失败。",
                    ),
                )
        finally:
            if process.stdout:
                process.stdout.close()
            with self.lock:
                self.processes.pop(run_id, None)

    def cancel(self, run_id):
        with self.lock:
            run = self.store.get(run_id)
            if run["status"] not in ACTIVE:
                return run
            self.store.mutate(run_id, lambda r: r.update(status="cancelling", stage="正在停止"))
            process = self.processes.get(run_id)
            if process:
                stop_process(process)

                def ensure_exit():
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        stop_process(process, force=True)

                threading.Thread(target=ensure_exit, daemon=True).start()
            return self.store.get(run_id)

    def shutdown(self):
        with self.lock:
            run_ids = list(self.processes)
        for run_id in run_ids:
            self.cancel(run_id)
        deadline = time.monotonic() + 6
        for run_id in run_ids:
            with self.lock:
                process = self.processes.get(run_id)
            if process:
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    stop_process(process, force=True)
