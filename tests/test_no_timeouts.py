"""No OCR engine, page or document may carry a deadline; slow work only warns."""
from __future__ import annotations

import ast
import logging
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

from structured_pdf_text.ocr import readiness
from structured_pdf_text.ocr.slow_work import warn_if_slow

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "src", ROOT / "scripts")
EVENT_RECEIVERS = {"self._stop", "stop"}


def _production_files():
    for base in PRODUCTION_ROOTS:
        for path in base.rglob("*.py"):
            if "archive" not in path.parts:
                yield path


def test_production_code_has_no_timeout_keyword_argument():
    offenders = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg and "timeout" in node.arg.lower():
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            # Popen.wait(timeout) and Popen.communicate(timeout) take the timeout first.
            # str.join and Thread.join are not checked positionally: join is ambiguous.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"wait", "communicate"}:
                # threading.Event.wait(interval) is a periodic sleep, not a process deadline.
                if node.args and ast.unparse(node.func.value) not in EVENT_RECEIVERS:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} positional {node.func.attr}")
    assert offenders == []


def test_warn_if_slow_logs_without_interrupting(caplog):
    caplog.set_level(logging.WARNING, logger="structured_pdf_text.ocr.slow_work")
    with warn_if_slow("fixture engine page 7", interval_s=0.02):
        time.sleep(0.15)
        finished = True
    assert finished
    messages = [record.getMessage() for record in caplog.records]
    assert any("fixture engine page 7 still running" in message and "no timeout is applied" in message for message in messages)


def test_warn_if_slow_is_silent_for_fast_work(caplog):
    caplog.set_level(logging.WARNING, logger="structured_pdf_text.ocr.slow_work")
    with warn_if_slow("fast", interval_s=5):
        pass
    assert not [record for record in caplog.records if "still running" in record.getMessage()]


def test_warn_if_slow_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        with warn_if_slow("bad", interval_s=0):
            pass


def test_tesseract_readiness_probe_runs_without_any_timeout(monkeypatch):
    from structured_pdf_text.config import ExtractorConfig

    calls = []

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="por\neng\n", stderr="")

    monkeypatch.setattr(readiness.subprocess, "run", fake_run)
    monkeypatch.setenv("TESSERACT_CMD", "/usr/bin/tesseract")
    monkeypatch.delenv("TESSERACT_LANG", raising=False)
    result = readiness.probe_static_tesseract(ExtractorConfig(ocr_engine="tesseract", language="pt-BR"))
    assert result.status.value == "ready"
    assert calls and all("timeout" not in kwargs for kwargs in calls)
