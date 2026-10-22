"""Loopback-only HTTP service. Run with .venv/bin/python -m webapp.server --open."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import mimetypes
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from webapp import __version__ as WORKSPACE_VERSION
from webapp.core import (
    DATA,
    MD,
    RUN_PAGE_SIZE,
    InstanceLock,
    Manager,
    Store,
    display_report,
    document,
    metadata,
    redact,
    refresh_models,
    report_html,
    save_settings,
)
from webapp.market import get_market_history

STATIC = Path(__file__).parent / "static"


def workspace_identity(directory):
    return hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:24]


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, manager):
        self.manager = manager
        self.workspace_id = workspace_identity(manager.store.root if manager else DATA)
        self.token = secrets.token_urlsafe(32)
        self.settings_lock = threading.Lock()
        self.login = {"status": "idle", "url": None, "error": None}
        self.login_lock = threading.Lock()
        super().__init__(address, Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def start_login(self):
        with self.login_lock:
            if self.login["status"] == "pending":
                return self.login.copy()
            self.login = {"status": "pending", "url": None, "error": None}

        def work():
            try:
                from tradingagents.llm_clients.chatgpt_auth import sign_in

                def say(message):
                    if message.startswith("https://auth.openai.com/"):
                        with self.login_lock:
                            self.login["url"] = message

                def no_paste():
                    raise EOFError

                account = sign_in(say=say, read_line=no_paste, timeout=180)
                with self.login_lock:
                    self.login.update(status="completed", account=account, url=None)
            except Exception as exc:
                with self.login_lock:
                    self.login.update(status="failed", error=redact(str(exc)), url=None)

        threading.Thread(target=work, daemon=True).start()
        return self.login.copy()


class Handler(BaseHTTPRequestHandler):
    server_version = f"TradingAgentsWorkspace/{WORKSPACE_VERSION}"

    def log_message(self, *_):
        pass

    def send(
        self, data, status=200, content_type="application/json; charset=utf-8", disposition=None
    ):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.host_ok():
            return self.send({"error": "请通过本机 127.0.0.1 地址访问。"}, 403)
        try:
            self.route_get()
        except KeyError:
            self.send({"error": "未找到记录。"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.send({"error": redact(str(exc))}, 500)

    def route_get(self):
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path == "/api/health":
            return self.send(
                {
                    "app": "tradingagents-workspace",
                    "version": 1,
                    "workspace_version": WORKSPACE_VERSION,
                    "workspace_id": self.server.workspace_id,
                }
            )
        if path == "/api/bootstrap":
            return self.send(
                {
                    **metadata(),
                    "token": self.server.token,
                    "runs": self.server.manager.store.summaries(limit=5),
                }
            )
        if path == "/api/models":
            providers = parse_qs(parsed_url.query).get("provider", [])
            if len(providers) != 1 or not providers[0]:
                return self.send({"error": "请指定要刷新的模型服务。"}, 400)
            try:
                return self.send(refresh_models(providers[0]))
            except PermissionError as exc:
                return self.send({"error": str(exc)}, 409)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self.send({"error": str(exc)}, 502)
        if path == "/api/runs":
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            try:
                limit_values = query.get("limit", [str(RUN_PAGE_SIZE)])
                offset_values = query.get("offset", ["0"])
                search_values = query.get("q", [""])
                if any(len(values) != 1 for values in (limit_values, offset_values, search_values)):
                    raise ValueError("分页参数不正确。")
                limit = int(limit_values[0])
                offset = int(offset_values[0])
                search = search_values[0]
                if len(search) > 100:
                    raise ValueError("搜索内容过长。")
                if "cursor" in query:
                    cursor_values = query["cursor"]
                    if len(cursor_values) != 1:
                        raise ValueError("分页参数不正确。")
                    cursor = cursor_values[0]
                    if cursor in ("", "first"):
                        cursor = None
                    return self.send(
                        self.server.manager.store.summaries(
                            limit=limit,
                            cursor=cursor,
                            query=search,
                            cursor_page=True,
                        )
                    )
                return self.send(
                    self.server.manager.store.summaries(
                        limit=limit,
                        offset=offset,
                        query=search,
                    )
                )
            except (ValueError, TypeError) as exc:
                return self.send({"error": redact(str(exc))}, 400)
        if path == "/api/login":
            with self.server.login_lock:
                return self.send(self.server.login.copy())
        parts = path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[:2] == ["api", "runs"]:
            run = self.server.manager.store.get(parts[2])
            if len(parts) == 3:
                return self.send(
                    {
                        **run,
                        "rendered": {
                            k: MD.render(display_report(k, v)) for k, v in run["reports"].items()
                        },
                    }
                )
            ext = parts[3]
            if ext in {"market", "evidence.json"}:
                run_root = (self.server.manager.store.root / "runs").resolve()
                candidate_directory = run_root / parts[2]
                if candidate_directory.is_symlink():
                    raise KeyError("未找到证据文件。")
                run_directory = candidate_directory.resolve()
                try:
                    run_directory.relative_to(run_root)
                except ValueError:
                    raise KeyError("未找到证据文件。") from None
                if run_directory.parent != run_root:
                    raise KeyError("未找到证据文件。")
            if ext == "market":
                try:
                    cache_dir = run_directory / "cache"
                    return self.send(get_market_history({
                        **run["config"], "data_cache_dir": str(cache_dir),
                        "market_snapshot_dir": str(cache_dir / "market"),
                    }))
                except ValueError as exc:
                    return self.send({"error": str(exc)}, 400)
                except RuntimeError as exc:
                    return self.send({"error": str(exc)}, 502)
            if ext == "report.md":
                return self.send(
                    document(run),
                    content_type="text/markdown; charset=utf-8",
                    disposition=f'attachment; filename="{run["config"]["ticker"]}-{run["config"]["date"]}.md"',
                )
            if ext == "report.html":
                return self.send(
                    report_html(run),
                    content_type="text/html; charset=utf-8",
                    disposition=f'attachment; filename="{run["config"]["ticker"]}-{run["config"]["date"]}.html"',
                )
            if ext == "evidence.json":
                evidence_path = run_directory / "evidence.json"
                if not evidence_path.is_file() or evidence_path.is_symlink():
                    raise KeyError("未找到证据文件。")
                return self.send(
                    evidence_path.read_bytes(),
                    content_type="application/json; charset=utf-8",
                    disposition=f'attachment; filename="{parts[2]}-evidence.json"',
                )
        files = {
            "/": "index.html",
            "/app.js": "app.js",
            "/visuals.js": "visuals.js",
            "/style.css": "style.css",
            "/favicon.svg": "favicon.svg",
        }
        if path in files:
            file = STATIC / files[path]
            return self.send(
                file.read_bytes(),
                content_type=(mimetypes.guess_type(file.name)[0] or "text/plain")
                + "; charset=utf-8",
            )
        self.send({"error": "页面不存在。"}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        token = self.headers.get("X-Workspace-Token", "")
        if (
            not self.host_ok()
            or origin != self.server.origin
            or not hmac.compare_digest(token, self.server.token)
        ):
            return self.send({"error": "请求校验失败，请刷新页面后重试。"}, 403)
        try:
            if self.headers.get_content_type() != "application/json":
                return self.send({"error": "需要 JSON 格式。"}, 415)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                return self.send({"error": "请求大小不正确。"}, 400)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求格式不正确。")
            path = urlsplit(self.path).path
            if path == "/api/settings":
                with self.server.settings_lock:
                    result = save_settings(body)
                return self.send(result)
            if path == "/api/runs":
                return self.send(self.server.manager.start(body), 201)
            if path == "/api/login":
                return self.send(self.server.start_login())
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                if parts[3] == "cancel":
                    return self.send(self.server.manager.cancel(parts[2]))
                if parts[3] == "resume":
                    return self.send(self.server.manager.resume(parts[2]), 202)
            self.send({"error": "接口不存在。"}, 404)
        except (ValueError, TypeError) as exc:
            self.send({"error": redact(str(exc))}, 400)
        except KeyError:
            self.send({"error": "未找到记录。"}, 404)
        except Exception as exc:
            self.send({"error": redact(str(exc))}, 500)


def open_browser(url):
    if sys.platform == "darwin":
        subprocess.Popen(
            ["/usr/bin/open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    else:
        webbrowser.open(url)


def existing_service(start_port, port_count=20):
    for port in range(start_port, min(start_port + port_count, 65536)):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=0.2) as response:
                health = json.load(response)
                if health.get("app") == "tradingagents-workspace" and health.get(
                    "workspace_id"
                ) == workspace_identity(DATA):
                    return url
        except (OSError, ValueError):
            continue
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65516:
        parser.error("--port must be between 1 and 65516")

    lock = InstanceLock(DATA / "server.lock")
    if not lock.acquire():
        # Another launch may be between acquiring the lock and binding its port.
        for _ in range(20):
            url = existing_service(args.port)
            if url:
                if args.open:
                    open_browser(url)
                print(url, flush=True)
                return
            time.sleep(0.1)
        raise SystemExit("TradingAgents 投研台正在启动，请稍后重试。")

    try:
        # Reuse a service left by an older version before touching the store.
        url = existing_service(args.port)
        if url:
            lock.release()
            if args.open:
                open_browser(url)
            print(url, flush=True)
            return

        for port in range(args.port, args.port + 20):
            url = f"http://127.0.0.1:{port}"
            try:
                # Bind first. If a port race occurs, no Manager has changed the
                # persisted run states yet.
                server = Server(("127.0.0.1", port), None)
            except OSError:
                continue
            manager = None
            try:
                manager = Manager(Store())
                server.manager = manager
                print(url, flush=True)
                if args.open:
                    threading.Timer(0.5, lambda target=url: open_browser(target)).start()
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                if manager:
                    manager.shutdown()
                server.server_close()
            return
        raise SystemExit(f"{args.port}–{args.port + 19} 端口均被占用。")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
