"""Run-scoped, deterministic receipts for data actually shown to the agents.

Receipts describe acquisition, never the truth of a model's conclusion. Raw
responses live beside the run, outside the public list API. Repeated requests
share a receipt and an in-flight fetch, so two analysts cannot manufacture two
independent sources by asking the same provider the same question.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from tradingagents.dataflows.files import replace_file

_ACTIVE: ContextVar[EvidenceLedger | None] = ContextVar("evidence_ledger", default=None)
_REPORTS = {"market": "market_report", "social": "sentiment_report",
            "news": "news_report", "fundamentals": "fundamentals_report"}
_METHOD_REPORTS = {
    "get_stock_data": ("market_report",), "get_indicators": ("market_report",),
    "get_verified_market_snapshot": ("market_report",),
    "get_fundamentals": ("fundamentals_report",),
    "get_balance_sheet": ("fundamentals_report",),
    "get_cashflow": ("fundamentals_report",),
    "get_income_statement": ("fundamentals_report",),
    "get_insider_transactions": ("fundamentals_report", "news_report"),
    "get_news": ("news_report", "sentiment_report"),
    "get_global_news": ("news_report",),
    "get_macro_indicators": ("news_report",),
    "get_prediction_markets": ("news_report",),
}
_STATUSES = {"available", "sparse", "unavailable", "unknown"}
_MAX_PAYLOAD_BYTES = 1_000_000
logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def _acquisition_status(payload):
    if payload is None or payload == "":
        return "unavailable"
    if not isinstance(payload, str):
        return "unknown"
    text = payload.strip()
    if not text:
        return "unavailable"
    if text.startswith(("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:")):
        return "unavailable"
    if text.startswith("<"):
        _, _, remainder = text.partition("\n")
        return "sparse" if remainder.strip() else "unavailable"
    if re.search(r"\b(?:are|is) withheld for this date\b", text, re.I):
        return "unavailable"
    if re.match(r"^(?:No (?:news|posts|messages|data|insider)|Error\b|HTTPError\b)", text, re.I):
        return "unavailable"
    # A returned payload is not a verified sample count, full coverage or an
    # independent source. Those need explicit provider metadata.
    return "available"


class EvidenceLedger:
    def __init__(self, directory, as_of, analysts):
        self.path = Path(directory) / "evidence.json"
        self.as_of = str(as_of)
        self.reports = [_REPORTS[a] for a in analysts if a in _REPORTS]
        self._entries = {}
        self._cache = {}
        self._lock = threading.RLock()
        if self.path.exists():
            if self.path.stat().st_size > 64_000_000:
                raise ValueError("Evidence archive exceeds the supported size")
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("version") != 1 or saved.get("as_of") != self.as_of:
                raise ValueError("Evidence archive does not match this run")
            for entry in saved.get("evidence", []):
                digest = hashlib.sha256(_json(entry["payload"]).encode()).hexdigest()
                expected = entry.get("stored_sha256") if entry.get("truncated") else entry.get("sha256")
                if digest != expected:
                    raise ValueError("Evidence archive checksum mismatch")
                self._entries[entry["id"]] = entry
                request = entry.get("request")
                if request and not entry.get("truncated") and entry["status"] in {"available", "sparse"}:
                    key = _json([request["method"], request.get("vendor_chain", entry["source"]),
                                 request["args"], request["kwargs"]])
                    future = Future()
                    raw = entry["payload"]
                    if isinstance(raw, str):
                        raw = f"[Evidence {entry['id']}; source={entry['source']}; as_of={self.as_of}]\n{raw}"
                    future.set_result(raw)
                    self._cache[key] = future

    @contextmanager
    def activate(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        except BaseException:
            try:
                self.flush()
            except OSError:
                logger.exception("Could not save evidence while handling an earlier failure")
            raise
        else:
            self.flush()
        finally:
            _ACTIVE.reset(token)

    def memoize(self, key, fetch):
        """Single-flight only within this run; failed calls remain retryable."""
        encoded = _json(key)
        with self._lock:
            future = self._cache.get(encoded)
            owner = future is None
            if owner:
                future = self._cache[encoded] = Future()
        if not owner:
            return future.result()
        try:
            result = fetch()
        except BaseException as exc:
            with self._lock:
                self._cache.pop(encoded, None)
            future.set_exception(exc)
            raise
        future.set_result(result)
        return result

    def record(self, report_keys, source, payload, *, status, sample_count=None,
               observed_dates=None, coverage_caveat="", request=None):
        if isinstance(report_keys, str):
            report_keys = [report_keys]
        raw = _json(payload)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        evidence_id = "E-" + hashlib.sha256(_json([source, request, digest]).encode()).hexdigest()[:16]
        raw_bytes = raw.encode()
        truncated = len(raw_bytes) > _MAX_PAYLOAD_BYTES
        if truncated:
            stored = raw_bytes[:_MAX_PAYLOAD_BYTES].decode("utf-8", errors="ignore")
        else:
            stored = json.loads(raw)
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
            sample_count = None
        status = status if status in _STATUSES else "unknown"
        if sample_count == 0 and status in {"available", "sparse"}:
            status = "unavailable"
        if truncated and status == "available":
            status = "sparse"
        if truncated:
            coverage_caveat += " 归档超过长度限制，仅保留前 1 MB；原始全文未完整留存。"
        with self._lock:
            old = self._entries.get(evidence_id, {})
            self._entries[evidence_id] = {
                "id": evidence_id, "source": str(source),
                "report_keys": sorted(set(old.get("report_keys", [])) | set(report_keys)),
                "status": status, "sample_count": sample_count,
                "observed_dates": json.loads(_json(observed_dates or {"start": None, "end": None})),
                "coverage_caveat": str(coverage_caveat), "as_of": self.as_of,
                "retrieved_at": old.get("retrieved_at") or datetime.now(UTC).isoformat(),
                "request": json.loads(_json(request)), "sha256": digest,
                "stored_sha256": hashlib.sha256(_json(stored).encode()).hexdigest(),
                "payload": stored, "truncated": truncated,
                "urls": sorted(set(re.findall(r"https?://[^\s<>\"\]\)]+", raw)))[:100],
            }
        # Persist as each tool finishes, not only at the end: a watchdog or
        # process interruption must not discard completed acquisition receipts.
        self.flush()
        return evidence_id

    def quality(self):
        with self._lock:
            entries = list(self._entries.values())
        reports = {}
        for key in self.reports:
            receipts = [r for r in entries if key in r["report_keys"]]
            statuses = [r["status"] for r in receipts]
            if not statuses:
                status = "unknown"
            elif all(s == "unavailable" for s in statuses):
                status = "unavailable"
            elif all(s == "available" for s in statuses):
                status = "available"
            elif any(s in {"available", "sparse"} for s in statuses):
                status = "sparse"
            else:
                status = "unknown"
            reports[key] = {"status": status, "sources": [
                {k: r[k] for k in ("id", "source", "sample_count", "observed_dates", "coverage_caveat")}
                for r in receipts
            ]}
        statuses = [r["status"] for r in reports.values()]
        if statuses and all(s == "available" for s in statuses):
            status = "available"
        elif statuses and all(s == "unavailable" for s in statuses):
            status = "unavailable"
        elif any(s in {"available", "sparse"} for s in statuses):
            status = "sparse"
        else:
            status = "unknown"
        summary = {"available": "已获取所选维度的数据；获取成功不代表证据充分或结论正确。",
                   "sparse": "部分证据或来源覆盖有限，请结合各维度的数据缺口阅读结论。",
                   "unavailable": "未获得所选维度的有效证据，不能据此判断市场方向。",
                   "unknown": "尚无可核验的完整采集记录；不能由报告篇幅推断证据质量。"}[status]
        return {"status": status, "summary": summary, "reports": reports,
                "evidence_count": len(entries), "as_of": self.as_of}

    def flush(self):
        with self._lock:
            if not self._entries:
                return
            payload = {"version": 1, "as_of": self.as_of,
                       "quality": self.quality(), "evidence": list(self._entries.values())}
            self.path.parent.mkdir(parents=True, exist_ok=True)

            def write(path):
                Path(path).write_text(_json(payload), encoding="utf-8")
                os.chmod(path, 0o600)

            replace_file(str(self.path), write)


def current_ledger():
    return _ACTIVE.get()


def record_source(report_key, source, payload, *, status, sample_count=None,
                  observed_dates=None, coverage_caveat=""):
    ledger = current_ledger()
    if ledger is None:
        return None
    return ledger.record(report_key, source, payload, status=status,
                         sample_count=sample_count, observed_dates=observed_dates,
                         coverage_caveat=coverage_caveat)


def capture_result(method, source, args, kwargs, payload, *, vendor_chain=None):
    ledger = current_ledger()
    if ledger is None:
        return payload
    evidence_id = ledger.record(
        _METHOD_REPORTS.get(method, ()), source, payload,
        status=_acquisition_status(payload),
        request={"method": method, "args": args, "kwargs": kwargs, "vendor_chain": vendor_chain or source},
        coverage_caveat="工具返回快照；未单独核验完整性、样本代表性或独立性。",
    )
    if isinstance(payload, str):
        return f"[Evidence {evidence_id}; source={source}; as_of={ledger.as_of}]\n{payload}"
    return payload
