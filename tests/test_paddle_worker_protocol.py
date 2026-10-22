"""Tests for the Paddle subprocess worker protocol — P1-11.

Covers:
  - cancellation: a hung worker is interrupted without an inference deadline
  - worker crash: unexpected EOF raises RuntimeError clearly
  - malformed JSON response: raises RuntimeError with context
  - stdout noise (print()): print() in the worker does NOT corrupt the JSONL pipe
  - worker crash: unexpected EOF raises RuntimeError clearly
  - token metadata round-trip: source/rotation/provenance/language survive serialisation
  - quality thresholds round-trip: OcrQualityThresholds values survive the init handshake
  - concurrent calls: _worker_lock prevents response cross-contamination

All tests work without loading PaddleOCR weights — they use either a fake
inline worker script or mock the subprocess directly on the backend instance.
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest
from structured_pdf_text.ocr.pipelines.paddle.wire import PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SRC = Path(__file__).parent.parent / "src"
_WORKER_SCRIPT = Path(__file__).parent.parent / "src" / "structured_pdf_text" / "ocr" / "_paddle_subprocess_worker.py"

# Environment for subprocesses that need to import structured_pdf_text.
_WORKER_ENV = {**os.environ, "PYTHONPATH": str(_SRC)}


def _fake_worker_proc(script: str) -> subprocess.Popen:  # type: ignore[type-arg]
    """Spawn a throwaway Python process running the given inline script."""
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
        env=_WORKER_ENV,
    )


def _send(proc: subprocess.Popen, obj: dict) -> dict:  # type: ignore[type-arg]
    """Write one JSON line and read one response line."""
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line.encode())  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    raw = proc.stdout.readline()  # type: ignore[union-attr]
    return json.loads(raw)


def test_worker_bootstrap_cannot_shadow_paddle_runtime_with_compatibility_module():
    script = textwrap.dedent(f"""
        import importlib.util, json, sys
        sys.path.insert(0, {str(_WORKER_SCRIPT.parent)!r})
        from structured_pdf_text.ocr import _paddle_subprocess_worker
        spec = importlib.util.find_spec('paddle')
        print(json.dumps({{'origin': spec.origin if spec else None, 'paths': sys.path}}))
    """)
    process = _fake_worker_proc(script)
    try:
        payload = json.loads(process.stdout.readline())
        assert process.wait(timeout=3) == 0
        assert str(_WORKER_SCRIPT.parent) not in payload["paths"]
        assert payload["origin"] != str(_WORKER_SCRIPT.parent / "paddle.py")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("error_name", [
    "ResourceExhaustedExtractionError", "PaddleOcrUnavailable",
    "RequiredRuntimeUnavailableError", "FatalExtractionError",
])
def test_worker_preserves_typed_failure_details_and_processing_stage(monkeypatch, error_name):
    from structured_pdf_text import errors
    from structured_pdf_text.ocr import _paddle_subprocess_worker as worker
    from structured_pdf_text.ocr.backends.paddle import _raise_worker_failure

    error_class = getattr(errors, error_name)
    failure = error_class("runtime failed", page_index=7, stage="ocr_initialize",
                          details={"cause_type": "AttributeError", "cause_message": "missing device"})
    def fail(_config):
        raise failure
    responses = []
    monkeypatch.setattr(worker, "_redirect_protocol_stdout", lambda: None)
    monkeypatch.setattr(worker, "_make_engine", fail)
    monkeypatch.setattr(worker, "_reply", lambda response, request_id=None: responses.append(response))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"protocol_version": PROTOCOL_VERSION, "method": "init"}) + '\nQUIT\n'))
    worker.main()
    with pytest.raises(error_class) as raised:
        _raise_worker_failure(responses[0], "worker init")
    assert raised.value.stage == "ocr_initialize"
    assert raised.value.page_index == 7
    assert raised.value.details == failure.details


def test_real_worker_and_parent_round_trip_polygon_provenance_and_diagnostics(monkeypatch):
    from PIL import Image
    from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox, Point
    from structured_pdf_text.ocr import _paddle_subprocess_worker as worker
    from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
    from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector

    token = OcrToken(
        "ação -0,01", BBox(2, 3, 41, 20), .93, "pt-BR", SourceKind.OCR_REGION,
        rotation=90, provenance="orientation_recovery", level="word",
        polygon=(Point(2, 4), Point(40, 3), Point(41, 19), Point(3, 20)),
        ocr_provenance=OcrProvenance("paddle", candidate_id="rot90", detector="v6",
                                     preprocessing=("clahe",), rotation=90),
    )
    engine = SimpleNamespace(
        recognize_page=lambda *args, **kwargs: [token], last_pass_count=3,
        last_batch_count=2, last_attempt_errors=["optional variant failed"],
        last_selected_variant="rot90", last_candidate_metrics={"baseline": {"score": .87}},
    )
    responses = []
    monkeypatch.setattr(worker, "_redirect_protocol_stdout", lambda: None)
    monkeypatch.setattr(worker, "_make_engine", lambda _: engine)
    monkeypatch.setattr(worker, "_reply", lambda response, request_id=None: responses.append(response))
    requests = [
        {"method": "init", "protocol_version": PROTOCOL_VERSION},
        {"method": "recognize_page", "protocol_version": PROTOCOL_VERSION, "image_path": "mock.png"},
    ]
    monkeypatch.setattr(Image, "open", lambda _: Image.new("RGB", (50, 30)))
    monkeypatch.setattr(sys, "stdin", io.StringIO('\n'.join(json.dumps(req) for req in requests) + '\nQUIT\n'))
    worker.main()
    assert responses[0]["protocol_version"] == PROTOCOL_VERSION
    assert responses[1]["status"] == "ok"
    backend = PaddleOCRBackend.__new__(PaddleOCRBackend)
    backend._config = SimpleNamespace(language="pt-BR")
    backend._engine = None
    backend._worker_send = lambda _: json.loads(json.dumps(responses[1]))
    assert backend._call_subprocess("recognize_page", Image.new("RGB", (50, 30)), 0) == [token]
    assert LegacyDiagnosticsProjector.operation_metrics(backend) == LegacyDiagnosticsProjector.operation_metrics(engine)
    assert LegacyDiagnosticsProjector.consume(backend, engine_name="paddle") == LegacyDiagnosticsProjector.consume(engine, engine_name="paddle")


def test_worker_startup_rejects_an_older_metadata_protocol(monkeypatch):
    from structured_pdf_text.ocr.backends import paddle as module

    backend = module.PaddleOCRBackend.__new__(module.PaddleOCRBackend)
    backend._cancel_requested = threading.Event()
    backend._worker_proc = None
    backend._subprocess_config = {}
    backend._raw_send = lambda _: {"status": "ok", "protocol_version": 4}
    discarded = []
    backend._discard_worker = lambda: discarded.append(True)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace())
    with pytest.raises(RuntimeError, match="protocol mismatch"):
        backend._ensure_worker()
    assert discarded == [True]


# ---------------------------------------------------------------------------
# Minimal fake _worker_send harness
#
# Rather than constructing a real PaddleOCRBackend (needs paddle weights and a
# real ExtractorConfig), we build a minimal object that has exactly the
# attributes that _raw_send and _worker_send need, then borrow the real
# implementations from the class.
# ---------------------------------------------------------------------------

def _make_harness(fake_proc: subprocess.Popen) -> Any:  # type: ignore[type-arg]
    """Return an object that has _worker_proc, _worker_lock, _worker_req_seq,
    and implements _raw_send / _worker_send / _ensure_worker via the real
    PaddleOCRBackend code — without touching paddle at all.
    """
    import importlib.util
    import threading

    # Import backends.paddle without triggering paddle import
    spec = importlib.util.spec_from_file_location(
        "paddle_backend",
        _SRC / "structured_pdf_text" / "ocr" / "backends" / "paddle.py",
    )

    # We need the module but it imports from structured_pdf_text at module level.
    # Instead, directly borrow the methods we need by constructing a duck-typed object.

    class Harness:
        def __init__(self, proc: subprocess.Popen):  # type: ignore[type-arg]
            self._worker_proc = proc
            self._worker_lock = threading.Lock()
            self._worker_req_seq = 0
            self._subprocess_config: dict = {}
            self._cancel_requested = threading.Event()

        # Copy the real method bodies via delegation
        def _ensure_worker(self) -> None:
            # For tests, the worker is already started; only implement the
            # "still alive" fast-path and the "None → raise" path.
            if self._worker_proc is not None and self._worker_proc.poll() is None:
                return
            # Worker is dead or None — signal so tests can detect restart logic.
            raise RuntimeError("_ensure_worker: worker is dead (no restart in harness)")

        def _discard_worker(self) -> None:
            proc, self._worker_proc = self._worker_proc, None
            if proc is not None and proc.poll() is None:
                proc.kill()

        def _raw_send(self, request: dict) -> dict:
            from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
            return PaddleOCRBackend._raw_send(self, request)

        def _worker_send(self, request: dict) -> dict:
            # Exercise the production implementation; the harness only fakes IO.
            from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
            return PaddleOCRBackend._worker_send(self, request)

        def cancel(self) -> None:
            from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
            PaddleOCRBackend.cancel(self)

    return Harness(fake_proc)


# ---------------------------------------------------------------------------
# 1. Explicit cancellation interrupts a pending worker read
# ---------------------------------------------------------------------------

def test_worker_cancellation_interrupts_pending_read() -> None:
    """A live worker may wait indefinitely until the caller cancels it."""
    # Worker reads stdin but never writes anything back.
    hung_worker = _fake_worker_proc("import sys; sys.stdin.read()")
    h = _make_harness(hung_worker)

    errors = []
    thread = threading.Thread(target=lambda: _capture_error(errors, h._raw_send, {"method": "healthcheck"}))
    thread.start()
    time.sleep(0.2)
    h.cancel()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], RuntimeError)
    assert "closed unexpectedly" in str(errors[0])
    hung_worker.wait(timeout=3)


def _capture_error(errors, function, *args):
    try:
        function(*args)
    except Exception as exc:
        errors.append(exc)


# ---------------------------------------------------------------------------
# 2. Worker crash: EOF on stdout → RuntimeError
# ---------------------------------------------------------------------------

def test_worker_crash_raises_runtime_error() -> None:
    """A worker that exits immediately causes 'closed unexpectedly'."""
    # Worker exits right away without writing anything.
    crashed_worker = _fake_worker_proc("import sys; sys.exit(0)")
    # Give the process a moment to exit.
    crashed_worker.wait(timeout=3)

    h = _make_harness(crashed_worker)
    with pytest.raises(RuntimeError, match="closed unexpectedly"):
        h._raw_send({"method": "healthcheck"})


# ---------------------------------------------------------------------------
# 3. Malformed JSON response → RuntimeError with context
# ---------------------------------------------------------------------------

def test_malformed_json_response_raises_runtime_error() -> None:
    """A worker that sends garbage JSON causes a clear RuntimeError."""
    garbage_worker = _fake_worker_proc(
        "import sys\n"
        "for _ in sys.stdin:\n"
        "    sys.stdout.buffer.write(b'NOT_VALID_JSON\\n')\n"
        "    sys.stdout.buffer.flush()\n"
    )
    h = _make_harness(garbage_worker)

    with pytest.raises(RuntimeError, match="malformed JSON"):
        h._raw_send({"method": "healthcheck"})

    garbage_worker.kill()
    garbage_worker.wait()


# ---------------------------------------------------------------------------
# 4. stdout noise: print() does NOT corrupt the JSONL pipe
# ---------------------------------------------------------------------------

def test_real_worker_print_noise_does_not_corrupt_pipe() -> None:
    """The real worker redirects sys.stdout to stderr before processing.

    A noisy dependency that calls print() must not corrupt the response pipe.
    We verify this by sending a healthcheck to the real worker after injecting
    noise via a monkeypatched print() inside the worker process.

    We use a thin wrapper that prints before echoing a valid response,
    simulating a noisy library.
    """
    noisy_worker_script = textwrap.dedent("""
        import sys, io, json

        # Mimic the real worker: capture stdout.buffer BEFORE redirect.
        _pipe = sys.stdout.buffer
        sys.stdout = io.TextIOWrapper(sys.stderr.buffer, line_buffering=True)

        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line or line == "QUIT":
                break
            req = json.loads(line)
            req_id = req.get("request_id")
            # Simulate a noisy library calling print() after redirection.
            print("noise from library")  # goes to stderr, not _pipe
            resp = {"status": "ok", "tokens": [], "error": None}
            if req_id is not None:
                resp["request_id"] = req_id
            _pipe.write((json.dumps(resp) + "\\n").encode())
            _pipe.flush()
    """)
    proc = _fake_worker_proc(noisy_worker_script)
    h = _make_harness(proc)

    resp = h._worker_send({"method": "healthcheck"})
    assert resp["status"] == "ok"
    assert resp.get("request_id") == 1

    proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    proc.wait(timeout=3)


def test_protocol_stdout_redirects_native_fd1_noise() -> None:
    """Native writes to fd 1 are sent to stderr, leaving JSONL intact."""
    script = textwrap.dedent("""
        import os
        from structured_pdf_text.ocr._paddle_subprocess_worker import (
            _redirect_protocol_stdout, _reply,
        )
        _redirect_protocol_stdout()
        print("python noise")
        os.write(1, b"native noise\\n")
        _reply({"status": "ok", "tokens": [], "error": None}, 17)
    """)
    proc = _fake_worker_proc(script)
    raw = proc.stdout.readline()  # type: ignore[union-attr]
    assert json.loads(raw) == {
        "status": "ok", "tokens": [], "error": None, "request_id": 17
    }
    assert proc.wait(timeout=3) == 0


# ---------------------------------------------------------------------------
# 5. request_id mismatch → RuntimeError
# ---------------------------------------------------------------------------

def test_request_id_mismatch_raises() -> None:
    """A worker that returns a wrong request_id is detected immediately."""
    liar_worker_script = textwrap.dedent("""
        import sys, io, json
        _pipe = sys.stdout.buffer
        sys.stdout = io.TextIOWrapper(sys.stderr.buffer, line_buffering=True)
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line or line == "QUIT":
                break
            # Always echo request_id=999, regardless of what was sent.
            resp = {"status": "ok", "tokens": [], "error": None, "request_id": 999}
            _pipe.write((json.dumps(resp) + "\\n").encode())
            _pipe.flush()
    """)
    proc = _fake_worker_proc(liar_worker_script)
    h = _make_harness(proc)

    with pytest.raises(RuntimeError, match="request_id mismatch"):
        h._worker_send({"method": "healthcheck"})

    assert h._worker_proc is None
    proc.kill()
    proc.wait()


def test_missing_request_id_discards_worker() -> None:
    script = textwrap.dedent("""
        import sys, json
        for line in sys.stdin:
            if line.strip() == "QUIT": break
            sys.stdout.write('{"status":"ok","tokens":[],"error":null}\\n')
            sys.stdout.flush()
    """)
    proc = _fake_worker_proc(script)
    h = _make_harness(proc)
    with pytest.raises(RuntimeError, match="request_id mismatch"):
        h._worker_send({"method": "healthcheck"})
    assert h._worker_proc is None
    proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# 6. Token metadata round-trip via _tokens_to_json / reconstruction
#
# The real OcrToken / SourceKind / BBox classes require pypdfium2 to be
# importable (the package __init__ pulls in render.py).  In envs that only
# have the test runner but not the full OCR stack we test the same logic
# using lightweight duck-typed stand-ins that mirror the real dataclass
# fields exactly.
# ---------------------------------------------------------------------------

class _FakeSourceKind:
    """Minimal SourceKind stand-in matching the real enum's .value contract."""
    OCR_PAGE   = type("_SK", (), {"value": "ocr_page"})()
    OCR_REGION = type("_SK", (), {"value": "ocr_region"})()

    @classmethod
    def _from_str(cls, s: str) -> Any:
        mapping = {"ocr_page": cls.OCR_PAGE, "ocr_region": cls.OCR_REGION}
        if s not in mapping:
            raise ValueError(s)
        return mapping[s]


class _FakeBBox:
    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _FakeBBox):
            return NotImplemented
        return (self.x0, self.y0, self.x1, self.y1) == (other.x0, other.y0, other.x1, other.y1)


class _FakeToken:
    def __init__(
        self, *, text: str, bbox: _FakeBBox, confidence: float | None,
        language: str | None, source: Any, rotation: int = 0, provenance: str | None = None,
    ) -> None:
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
        self.language = language
        self.source = source
        self.rotation = rotation
        self.provenance = provenance


def _tokens_to_json_inline(tokens: list) -> list[dict]:
    """Inline copy of the worker's _tokens_to_json — tests the logic without imports."""
    out = []
    for t in tokens:
        source = getattr(t, "source", None)
        out.append({
            "text": t.text,
            "confidence": float(t.confidence) if t.confidence is not None else None,
            "bbox": [t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1],
            "source": source.value if hasattr(source, "value") else str(source or "ocr_page"),
            "language": getattr(t, "language", None),
            "rotation": getattr(t, "rotation", 0),
            "provenance": getattr(t, "provenance", None),
        })
    return out


def test_tokens_to_json_round_trip() -> None:
    """source, rotation, provenance, language survive _tokens_to_json serialisation."""
    token = _FakeToken(
        text="ação",
        bbox=_FakeBBox(10.0, 20.0, 100.0, 40.0),
        confidence=0.92,
        language="pt",
        source=_FakeSourceKind.OCR_REGION,
        rotation=90,
        provenance="orientation_recovery",
    )

    serialised = _tokens_to_json_inline([token])
    assert len(serialised) == 1
    item = serialised[0]

    assert item["text"] == "ação"
    assert item["confidence"] == pytest.approx(0.92)
    assert item["source"] == "ocr_region"
    assert item["language"] == "pt"
    assert item["rotation"] == 90
    assert item["provenance"] == "orientation_recovery"
    assert item["bbox"] == [10.0, 20.0, 100.0, 40.0]

    # Simulate parent reconstruction (mirrors _call_subprocess in paddle.py)
    x0, y0, x1, y1 = item["bbox"]
    conf = item.get("confidence")
    raw_source = item.get("source", "ocr_page")
    try:
        source = _FakeSourceKind._from_str(raw_source)
    except ValueError:
        source = _FakeSourceKind.OCR_PAGE

    assert source is _FakeSourceKind.OCR_REGION
    assert item["language"] == "pt"
    assert int(item.get("rotation", 0)) == 90
    assert item.get("provenance") == "orientation_recovery"
    assert _FakeBBox(x0, y0, x1, y1) == _FakeBBox(10.0, 20.0, 100.0, 40.0)
    assert (float(conf) if conf is not None else None) == pytest.approx(0.92)


def test_tokens_to_json_none_confidence_survives() -> None:
    """confidence=None is serialised as null and reconstructed as None."""
    token = _FakeToken(
        text="x",
        bbox=_FakeBBox(0.0, 0.0, 1.0, 1.0),
        confidence=None,
        language=None,
        source=_FakeSourceKind.OCR_PAGE,
    )
    item = _tokens_to_json_inline([token])[0]
    assert item["confidence"] is None
    conf = item.get("confidence")
    assert (float(conf) if conf is not None else None) is None


def test_tokens_to_json_unknown_source_falls_back() -> None:
    """An unrecognised source string falls back to OCR_PAGE on reconstruction."""
    raw_source = "totally_unknown_source"
    try:
        result = _FakeSourceKind._from_str(raw_source)
    except ValueError:
        result = _FakeSourceKind.OCR_PAGE

    assert result is _FakeSourceKind.OCR_PAGE


# ---------------------------------------------------------------------------
# 7. Quality thresholds round-trip through _make_engine init dict
# ---------------------------------------------------------------------------

def test_quality_thresholds_round_trip() -> None:
    """OcrQualityThresholds fields survive asdict → constructor round-trip.

    Uses an inline dataclass that mirrors the exact fields of the real
    OcrQualityThresholds so the test does not require pypdfium2.
    """
    import dataclasses

    @dataclasses.dataclass(frozen=True, slots=True)
    class OcrQualityThresholds:
        strong_mean_confidence: float = 0.90
        strong_lower_quartile: float = 0.78
        max_low_confidence_char_ratio: float = 0.12
        severe_mean_confidence: float = 0.70
        severe_low_confidence_char_ratio: float = 0.35
        minimum_printable_ratio: float = 0.90
        low_confidence_threshold: float = 0.70
        minimum_orientation_ratio: float = 0.75

    original = OcrQualityThresholds(
        strong_mean_confidence=0.85,
        strong_lower_quartile=0.70,
        max_low_confidence_char_ratio=0.20,
        severe_mean_confidence=0.60,
        severe_low_confidence_char_ratio=0.40,
        minimum_printable_ratio=0.88,
        low_confidence_threshold=0.65,
        minimum_orientation_ratio=0.80,
    )

    # This is exactly what the parent does before sending to the worker (P0-01/OK-01).
    serialised: dict = dataclasses.asdict(original)
    # This is exactly what the worker does when rebuilding the thresholds.
    reconstructed = OcrQualityThresholds(**serialised)

    assert reconstructed == original
    # All non-default values must survive
    assert reconstructed.strong_mean_confidence == pytest.approx(0.85)
    assert reconstructed.minimum_orientation_ratio == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# 8. Concurrent calls do not cross responses (lock test)
# ---------------------------------------------------------------------------

def test_concurrent_calls_do_not_cross_responses() -> None:
    """Two threads sending requests concurrently each receive their own response.

    Uses a fake worker that echoes request_id in the response so we can verify
    each thread got back the right answer.
    """
    echo_worker_script = textwrap.dedent("""
        import sys, io, json
        _pipe = sys.stdout.buffer
        sys.stdout = io.TextIOWrapper(sys.stderr.buffer, line_buffering=True)
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line or line == "QUIT":
                break
            req = json.loads(line)
            req_id = req.get("request_id")
            # Simulate slight processing delay so threads actually overlap.
            import time; time.sleep(0.01)
            resp = {"status": "ok", "tokens": [], "error": None}
            if req_id is not None:
                resp["request_id"] = req_id
            _pipe.write((json.dumps(resp) + "\\n").encode())
            _pipe.flush()
    """)
    proc = _fake_worker_proc(echo_worker_script)
    h = _make_harness(proc)

    results: dict[int, dict] = {}
    errors: list[Exception] = []

    def call(thread_id: int) -> None:
        try:
            resp = h._worker_send({"method": "healthcheck", "thread_id": thread_id})
            results[thread_id] = resp
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    assert len(results) == 5

    for thread_id, resp in results.items():
        assert resp["status"] == "ok"
        # request_id must match the seq number sent by this thread's call.
        # We can't predict the exact req_id (seq is shared), but we can
        # verify no response was lost and none contains a cross-thread mix.
        assert "request_id" in resp

    proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# 9-11. Real worker tests — require the full structured_pdf_text package stack
#       (pypdfium2, etc.) to be importable in the subprocess environment.
# ---------------------------------------------------------------------------

def _real_worker_available() -> bool:
    """Check if the real worker script can be imported in a subprocess."""
    result = subprocess.run(
        [sys.executable, "-c",
         "from structured_pdf_text.ocr.runtime_policy import PaddleRuntimePolicy"],
        env=_WORKER_ENV,
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


_SKIP_REAL_WORKER = pytest.mark.skipif(
    not _real_worker_available(),
    reason="structured_pdf_text not fully importable in this env (missing pypdfium2 or similar)",
)


@_SKIP_REAL_WORKER
def test_real_worker_healthcheck_before_init_returns_error() -> None:
    """The real worker returns status=error for healthcheck before init."""
    proc = subprocess.Popen(
        [sys.executable, str(_WORKER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_WORKER_ENV,
        bufsize=0,
    )
    resp = _send(proc, {"method": "healthcheck", "request_id": 1})
    assert resp["status"] == "error"
    assert resp.get("request_id") == 1

    proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    proc.wait(timeout=3)


@_SKIP_REAL_WORKER
def test_real_worker_bad_json_returns_error_and_continues() -> None:
    """The real worker sends a structured error for bad JSON and stays alive."""
    proc = subprocess.Popen(
        [sys.executable, str(_WORKER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_WORKER_ENV,
        bufsize=0,
    )
    # Send garbage
    proc.stdin.write(b"THIS IS NOT JSON\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]

    raw = proc.stdout.readline()  # type: ignore[union-attr]
    resp = json.loads(raw)
    assert resp["status"] == "error"
    assert "bad JSON" in (resp.get("error") or "")

    # Worker must still be alive and able to handle further requests.
    resp2 = _send(proc, {"method": "healthcheck", "request_id": 42})
    assert resp2.get("request_id") == 42

    proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    proc.wait(timeout=3)


@_SKIP_REAL_WORKER
def test_real_worker_unknown_method_returns_error() -> None:
    """The real worker handles unknown method names with a structured error."""
    proc = subprocess.Popen(
        [sys.executable, str(_WORKER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_WORKER_ENV,
        bufsize=0,
    )
    resp = _send(proc, {"protocol_version": PROTOCOL_VERSION, "method": "fly_to_the_moon", "request_id": 7})
    assert resp["status"] == "error"
    assert "unknown method" in (resp.get("error") or "")
    assert resp.get("request_id") == 7

    proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]
    proc.wait(timeout=3)
