import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from webapp.core import MD, Manager, Store, display_report, document, report_html
from webapp.report_sources import readable_report, render_report, source_page
from webapp.server import Server

RUN_ID = "a" * 32
RECEIPT = "E-" + "1" * 16
MISSING = "E-" + "0" * 16


def saved_run():
    return {
        "id": RUN_ID, "status": "completed", "rating": "Hold", "created_at": 1,
        "config": {"ticker": "600519.SS", "date": "2026-10-23", "provider": "ollama",
                   "quick_model": "offline", "deep_model": "offline", "analysts": ["fundamentals"]},
        "reports": {"final_trade_decision": (
            f"**Rating**: Hold\n\n**Executive Summary**: 盈利保持稳定（Evidence {RECEIPT}）。\n\n"
            f"[公告](https://example.com/filing)\n\n### 结论与证据引用\n\n"
            f"- **引用已核对** · 盈利保持稳定。\n\n  `{RECEIPT}` (eastmoney)\n\n"
            f"#### 对应采集记录\n\n- `{RECEIPT}` · eastmoney · 未知 — 未知 · 已获取"
        )},
        "quality": {"status": "sparse", "summary": "财务覆盖有限。", "evidence_count": 1,
                    "reports": {"fundamentals_report": {"status": "sparse", "sources": [
                        {"id": RECEIPT, "source": "eastmoney", "status": "available"},
                    ]}}},
    }


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []

    def handle_data(self, data):
        self.text.append(data)


class ReadableSourceTests(unittest.TestCase):
    def test_empty_fields_hidden_in_rendered_reports_and_exports_only(self):
        run = saved_run()
        empty = ["**Time Horizon**: not provided", "- **Investment Horizon:** N/A",
                 "Holding Period: unknown", "**持有期限**：未设定", "Entry Price:",
                 "Confidence: `null`", "Position Size: not specified"]
        substantive = ["Time Horizon: long term", "目标价：1500 元（模型估计）",
                       "Position Size: 0", "财务异常：无", "未提供目标价，需要继续核验。"]
        run["reports"]["final_trade_decision"] = "\n\n".join(empty + substantive)
        original = copy.deepcopy(run)
        for result in [render_report(run, "final_trade_decision",
                                    run["reports"]["final_trade_decision"], MD),
                       document(run), report_html(run)]:
            for placeholder in ["not provided", "N/A", "unknown", "未设定", "Entry Price", "null",
                                "not specified"]:
                self.assertNotIn(placeholder, result)
            for value in substantive:
                self.assertIn(value, result)
        self.assertEqual(run, original)

    def test_missing_field_filter_preserves_fenced_examples_and_nonempty_values(self):
        text = "```text\nTime Horizon: not provided\n```\n\nStop Loss: 1200\n风险提示：未知事件可能影响市场。"
        self.assertEqual(readable_report(text), text)

    def test_existing_report_becomes_readable_without_mutating_research(self):
        run = saved_run()
        original = copy.deepcopy(run)
        html = render_report(run, "final_trade_decision", run["reports"]["final_trade_decision"], MD)
        visible = VisibleText()
        visible.feed(html)
        self.assertNotIn(RECEIPT, "".join(visible.text))
        self.assertNotIn("对应采集记录", html)
        self.assertNotIn("结论与证据引用", html)
        self.assertIn("东方财富·财务数据", html)
        self.assertIn(f'/api/runs/{RUN_ID}/evidence.html?source={RECEIPT}', html)
        self.assertIn('class="report-source-link"', html)
        self.assertIn('target="_self"', html)
        self.assertIn('href="https://example.com/filing"', html)
        self.assertEqual(run, original)

    def test_absent_archive_or_unknown_receipt_does_not_create_dead_links(self):
        run = saved_run()
        text = f"结论（Evidence {MISSING}）。\n\n数据 [Evidence {RECEIPT}]。"
        html = render_report(run, "market_report", text, MD, sources_available=False)
        self.assertNotIn("evidence.html", html)
        self.assertNotIn("E-", html)
        self.assertIn("结论。", html)
        self.assertIn("数据 。", html)

    def test_plain_exports_keep_source_names_and_target_guard_without_id_dump(self):
        run = saved_run()
        for text in (document(run), report_html(run)):
            self.assertNotIn(RECEIPT, text)
            self.assertNotIn("对应采集记录", text)
            self.assertIn("盈利保持稳定", text)
            self.assertIn("东方财富·财务数据", text)
        draft = "**Price Target**: None\n\n观点。\n\n### 结论与证据引用\n\n引用待核对。\n\n目标价已省略：缺少估值说明。"
        result = display_report("final_trade_decision", draft)
        self.assertNotIn("Price Target", result)
        self.assertIn("部分结论的来源尚未逐项核对", result)
        self.assertIn("目标价已省略", result)

    def test_citation_styles_and_existing_external_urls_survive_projection(self):
        run = saved_run()
        original = f"[原文](https://example.com/{RECEIPT})"
        for text in [f"[Evidence {RECEIPT}]", f"(Evidence {RECEIPT}; Evidence {RECEIPT})",
                     f"`{RECEIPT}` (eastmoney)", f"Evidence {RECEIPT}"]:
            readable = readable_report(text + "\n\n" + original, run)
            self.assertIn("东方财富·财务数据", readable)
            self.assertIn(original, readable)
            self.assertNotIn("(eastmoney)", readable)

    def test_snapshot_renders_saved_payload_safely_without_internal_identifiers(self):
        entry = {"source": "eastmoney", "report_keys": ["fundamentals_report"],
                 "payload": "# 利润表\n\n| 项目 | 数值 |\n|---|---|\n| 收入 | 10 |\n\n"
                            "<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n![x](https://example.com/image)"}
        html = source_page(entry, saved_run())
        self.assertIn("利润表", html)
        self.assertIn("<table>", html)
        self.assertIn("来源未提供完整的覆盖日期", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn("<img", html)
        self.assertNotIn(RECEIPT, html)

    def test_http_snapshot_is_bound_to_the_selected_run_and_rejects_invalid_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            run = saved_run()
            store.put(run)
            directory = Path(tmp) / "runs" / RUN_ID
            directory.mkdir(parents=True)
            archive = directory / "evidence.json"
            archive.write_text(json.dumps({"evidence": [{
                "id": RECEIPT, "source": "eastmoney", "report_keys": ["fundamentals_report"],
                "payload": "本次保存的财务资料。",
            }]}))
            original = archive.read_bytes()
            manager = Manager(store)
            server = Server(("127.0.0.1", 0), manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = server.origin + "/api/runs/" + RUN_ID
            try:
                with patch("webapp.server.instrument_name", return_value="贵州茅台"), \
                        urllib.request.urlopen(base) as response:
                    data = json.load(response)
                self.assertEqual(data["display_name"], "贵州茅台")
                self.assertNotIn("display_name", store.get(RUN_ID))
                self.assertIn("evidence.html", data["rendered"]["final_trade_decision"])
                self.assertEqual(data["reports"], run["reports"])
                with urllib.request.urlopen(base + "/evidence.html?source=" + RECEIPT) as response:
                    self.assertIn("本次保存的财务资料", response.read().decode())
                    self.assertIn("text/html", response.headers["Content-Type"])
                for query in ["source=../../.env", "source=" + MISSING,
                              "source=" + RECEIPT + "&source=" + MISSING]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base + "/evidence.html?" + query)
                    self.assertEqual(error.exception.code, 404)
                self.assertEqual(archive.read_bytes(), original)
            finally:
                server.shutdown()
                server.server_close()
                manager.shutdown()
                thread.join()
