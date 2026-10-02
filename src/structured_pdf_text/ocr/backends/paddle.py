"""PaddleOCR backend — wraps the existing PaddleOcrEngine.

Delegates all pipeline calls to the original implementation so that the
output is byte-for-byte identical to the pre-refactor baseline.  The new
``recognize`` / ``identity`` / ``capabilities`` / ``healthcheck`` methods
add the benchmarking contract without touching any existing code paths.

``recovery.py`` and the rest of the pipeline continue to see exactly the same
``OcrEngine``-compatible interface they always have.

CF-4 (Windows DLL isolation):
  When ``torch`` (installed by EasyOCR) is present in the same venv,
  PaddlePaddle DLLs collide with torch's DLLs at load time (0xC0000139).
  To fix this without requiring separate venvs, the backend detects the
  conflict at construction time and transparently routes all OCR calls
  through a long-lived subprocess (_paddle_subprocess_worker.py) that runs
  in a clean process with no torch DLLs loaded.  One subprocess is spawned
  per PaddleOCRBackend instance; it keeps the model in memory for the
  entire extraction, so model load cost is paid once.
"""
from __future__ import annotations

import base64
import dataclasses
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Inference timeout: per-request deadline (one page of OCR).
# Default 2400 s = ~25× the observed ~94 s/page average — exists only to kill
# a truly frozen process, not to race against normal inference.
# Override with PADDLE_WORKER_REQUEST_TIMEOUT (or legacy PADDLE_WORKER_TIMEOUT).
_WORKER_REQUEST_TIMEOUT_S: float = float(
    os.environ.get("PADDLE_WORKER_REQUEST_TIMEOUT")
    or os.environ.get("PADDLE_WORKER_TIMEOUT")
    or "2400"
)

# Init timeout: model loading can be slower than inference on a cold cache.
# Default 2400 s (same as request) — override with PADDLE_WORKER_INIT_TIMEOUT.
_WORKER_INIT_TIMEOUT_S: float = float(
    os.environ.get("PADDLE_WORKER_INIT_TIMEOUT") or "2400"
)

from structured_pdf_text.config import ExtractorConfig, effective_ocr_quality_policy
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.errors import PaddleOcrUnavailable
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.runtime_policy import (
    apply_paddle_runtime_policy,
    resolve_paddle_runtime_policy,
)
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)

_WORKER_SCRIPT = Path(__file__).parent.parent / "_paddle_subprocess_worker.py"


def _resolve_num_threads(num_threads: int) -> int:
    """Translate ExtractorConfig.num_threads to a concrete value for PaddleOcrEngine.

    -1 means "leave Paddle's own default unchanged" (not passed to the engine).
     0 means "auto-detect" → max(2, cpu_count).
    Any positive value is clamped to at least 1.
    """
    if num_threads == -1:
        return -1
    if num_threads == 0:
        return max(2, os.cpu_count() or 2)
    return max(1, num_threads)


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _has_torch_conflict() -> bool:
    """True when torch is findable in this venv on Windows (DLL conflict with paddle)."""
    if sys.platform != "win32":
        return False
    try:
        return importlib.util.find_spec("torch") is not None
    except Exception:
        return False


class PaddleOCRBackend:
    """OCRBackend implementation backed by PaddleOcrEngine.

    Thin wrapper: all ``recognize_page`` / ``recognize_region`` calls are
    forwarded unchanged so the existing pipeline output is preserved exactly.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        from structured_pdf_text.ocr.models import get_profile

        self._config = config
        get_profile(config.language)
        self._runtime_policy = resolve_paddle_runtime_policy()
        apply_paddle_runtime_policy(self._runtime_policy)
        logging.getLogger(__name__).info(
            "Paddle runtime policy: device=cpu, paddle=%s, enable_mkldnn=%s (%s), disable_pir_api=%s",
            _package_version("paddlepaddle"),
            self._runtime_policy.enable_mkldnn,
            self._runtime_policy.reason,
            self._runtime_policy.disable_pir_api,
        )
        _enable_mkldnn = self._runtime_policy.enable_mkldnn

        self._subprocess_config: dict | None = None
        self._worker_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        # Serialise all subprocess request/response cycles so two callers
        # on different threads can never interleave their writes and reads.
        self._worker_lock: threading.Lock = threading.Lock()
        # Monotonic counter used as request_id for response matching.
        self._worker_req_seq: int = 0

        # Resolve cache_home once at construction time, mirroring PaddleOcrEngine's
        # own resolution, so healthcheck() checks the same directory the engine uses.
        self._cache_home: str = os.environ.get(
            "PADDLE_PDX_CACHE_HOME",
            str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
        )

        if _has_torch_conflict():
            # Subprocess mode (CF-4): torch DLLs would crash paddle at import time.
            # Store config and defer all OCR work to a clean subprocess.
            self._subprocess_config = {
                "language": config.language,
                "num_threads": _resolve_num_threads(config.num_threads),
                "ocr_batch_size": config.ocr_batch_size,
                "quality_variants": config.ocr_quality_variants,
                "quality_policy": effective_ocr_quality_policy(config).value,
                "quality_thresholds": dataclasses.asdict(config.ocr_quality_thresholds),
                "mkldnn": _enable_mkldnn,
                "disable_pir_api": self._runtime_policy.disable_pir_api,
            }
            self._engine = None
        else:
            # Direct mode: no DLL conflict — import and use paddle in-process.
            from structured_pdf_text.ocr.paddle import PaddleOcrEngine

            self._engine = PaddleOcrEngine(
                language=config.language,
                num_threads=_resolve_num_threads(config.num_threads),
                ocr_batch_size=config.ocr_batch_size,
                quality_variants=config.ocr_quality_variants,
                quality_policy=effective_ocr_quality_policy(config).value,
                quality_thresholds=config.ocr_quality_thresholds,
                enable_mkldnn=_enable_mkldnn,
            )

    # ------------------------------------------------------------------
    # Subprocess worker management (CF-4)
    # ------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        """Start the subprocess worker if not already running.

        Must be called while ``_worker_lock`` is held (done by ``_worker_send``).
        Uses ``_raw_send`` directly to avoid re-acquiring the lock for the
        init handshake.
        """
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            return  # still alive

        self._worker_proc = subprocess.Popen(
            [sys.executable, str(_WORKER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,  # unbuffered binary I/O — flush() is explicit in _raw_send
        )
        # Init handshake: use _raw_send (no lock, no seq) — we're already locked.
        init_req = {"method": "init", **self._subprocess_config}  # type: ignore[arg-type]
        response = self._raw_send(init_req, timeout=_WORKER_INIT_TIMEOUT_S)
        if response.get("status") != "ok":
            raise RuntimeError(
                f"Paddle worker init failed: {response.get('error')}"
            )

    def _raw_send(self, request: dict, timeout: float = _WORKER_REQUEST_TIMEOUT_S) -> dict:
        """Write one request and read one response on the raw pipe.

        No lock, no request_id injection — callers must hold ``_worker_lock``
        before calling this.  Used by ``_ensure_worker`` (init handshake, passes
        ``_WORKER_INIT_TIMEOUT_S``) and ``_worker_send`` (normal requests, passes
        ``_WORKER_REQUEST_TIMEOUT_S``).
        """
        assert self._worker_proc is not None
        assert self._worker_proc.stdin is not None
        assert self._worker_proc.stdout is not None

        line = json.dumps(request, ensure_ascii=False) + "\n"
        self._worker_proc.stdin.write(line.encode())
        self._worker_proc.stdin.flush()

        stdout = self._worker_proc.stdout
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut: Future[bytes] = pool.submit(stdout.readline)
            try:
                response_line = fut.result(timeout=timeout)
            except TimeoutError:
                self._worker_proc.kill()
                self._worker_proc = None
                raise RuntimeError(
                    f"Paddle worker timed out after {timeout}s"
                )

        if not response_line:
            raise RuntimeError("Paddle worker closed unexpectedly")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Paddle worker sent malformed JSON: {response_line[:200]!r}"
            ) from exc

    def _worker_send(self, request: dict) -> dict:
        """Send one JSONL request to the worker and return the parsed response.

        The full write → read cycle is serialised by ``_worker_lock`` so that
        concurrent callers on different threads cannot interleave their writes
        and reads and receive mismatched responses.

        A monotonic ``request_id`` is injected into every request and verified
        against the response, making protocol violations immediately visible
        rather than silently returning a stale or misrouted result.

        Enforces a per-request read timeout (PADDLE_WORKER_REQUEST_TIMEOUT env
        var, or legacy PADDLE_WORKER_TIMEOUT; default 2400 s) so that a truly
        frozen worker never blocks the
        parent process indefinitely. readline() runs in a background thread;
        TimeoutError kills the worker and raises RuntimeError.
        """
        with self._worker_lock:
            # Ensure the worker is alive inside the lock so concurrent callers
            # cannot race to start two workers simultaneously.
            self._ensure_worker()

            self._worker_req_seq += 1
            req_id = self._worker_req_seq
            request = {**request, "request_id": req_id}

            response = self._raw_send(request)

            # Verify the response belongs to this request.
            resp_id = response.get("request_id")
            if resp_id is not None and resp_id != req_id:
                raise RuntimeError(
                    f"Paddle worker request_id mismatch: sent {req_id}, got {resp_id}"
                )

            return response

    def _call_subprocess(
        self,
        method: str,
        image: Any,
        page_index: int,
        region_bbox: BBox | None = None,
        *,
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        """Serialize image, send to worker, deserialize OcrToken list."""
        from PIL import Image

        if not isinstance(image, Image.Image):
            image = Image.fromarray(image)

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        image_b64 = base64.b64encode(buf.getvalue()).decode()

        req: dict[str, Any] = {
            "method": method,
            "image_b64": image_b64,
            "page_index": page_index,
        }
        if quality_policy is not None:
            req["quality_policy"] = quality_policy
        if region_bbox is not None:
            req["region_bbox"] = [region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1]

        response = self._worker_send(req)

        if response.get("status") != "ok":
            raise RuntimeError(response.get("error", "unknown subprocess error"))

        tokens: list[OcrToken] = []
        for item in response.get("tokens", []):
            x0, y0, x1, y1 = item["bbox"]
            conf = item.get("confidence")
            # Reconstruct source from the serialized string value; fall back to
            # OCR_PAGE for responses from older workers that omit the field.
            raw_source = item.get("source", "ocr_page")
            try:
                source = SourceKind(raw_source)
            except ValueError:
                source = SourceKind.OCR_PAGE
            tokens.append(
                OcrToken(
                    text=item["text"],
                    confidence=float(conf) if conf is not None else None,
                    bbox=BBox(x0, y0, x1, y1),
                    source=source,
                    language=item.get("language") or self._config.language,
                    rotation=int(item.get("rotation", 0)),
                    provenance=item.get("provenance"),
                )
            )
        return tokens

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        return OCRBackendIdentity(
            engine="paddle",
            runtime="paddle_subprocess" if self._subprocess_config else "paddle_static",
            profile=self._config.language,
            language=self._config.language,
            device="cpu",
            package_versions={
                "paddlepaddle": _package_version("paddlepaddle"),
                "paddleocr": _package_version("paddleocr"),
                "paddlex": _package_version("paddlex"),
            },
            artifact_hashes={},  # Paddle model dirs contain many files — hashing deferred
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=True,
            page_orientation=True,
            quadrilateral_boxes=True,
            per_token_confidence=True,
        )

    # ------------------------------------------------------------------
    # OCRBackend — canonical recognize method (for benchmarking layer)
    # ------------------------------------------------------------------

    def recognize(self, request: OCRRequest) -> OCRResult:
        t0 = time.perf_counter()

        try:
            is_region = request.input_kind == "region" and request.region_id is not None
            region_box: BBox | None = None
            if is_region:
                if request.region_bbox is not None:
                    region_box = BBox(*request.region_bbox)
                else:
                    region_box = BBox(0.0, 0.0, 1.0, 1.0)

            if self._subprocess_config is not None:
                method = "recognize_region" if is_region else "recognize_page"
                tokens = self._call_subprocess(method, request.image, request.page_index, region_box)
            elif is_region:
                tokens = self._engine.recognize_region(  # type: ignore[union-attr]
                    request.image, request.page_index, region_box  # type: ignore[arg-type]
                )
            else:
                tokens = self._engine.recognize_page(  # type: ignore[union-attr]
                    request.image, request.page_index
                )
        except Exception as exc:
            return OCRResult(
                status="runtime_error",
                tokens=(),
                text="",
                engine_identity=self.identity,
                elapsed_total_s=time.perf_counter() - t0,
                warnings=(str(exc),),
            )

        elapsed = time.perf_counter() - t0
        canonical = tuple(_pipeline_token_to_canonical(t, "paddle") for t in tokens)
        text = " ".join(t.text for t in canonical)
        status = "ok" if canonical else "no_text"
        return OCRResult(
            status=status,
            tokens=canonical,
            text=text,
            engine_identity=self.identity,
            elapsed_total_s=elapsed,
        )

    # ------------------------------------------------------------------
    # OcrEngine protocol — consumed by recovery.py / pipeline (unchanged)
    # ------------------------------------------------------------------

    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        if self._subprocess_config is not None:
            return self._call_subprocess(
                "recognize_page", page_image, page_index, quality_policy=quality_policy
            )
        return self._engine.recognize_page(  # type: ignore[union-attr]
            page_image,
            page_index,
            page_bbox,
            quality_variants=quality_variants,
            quality_policy=quality_policy,
        )

    def recognize_region(
        self, page_image: object, page_index: int, region_bbox: BBox
    ) -> list[OcrToken]:
        if self._subprocess_config is not None:
            return self._call_subprocess("recognize_region", page_image, page_index, region_bbox)
        return self._engine.recognize_region(page_image, page_index, region_bbox)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        from structured_pdf_text.ocr.paddle import validate_local_ocr_models

        try:
            validate_local_ocr_models(language=self._config.language, cache_home=self._cache_home)
        except PaddleOcrUnavailable:
            return "missing"
        except ValueError:
            return "unknown"
        except Exception:
            return "unknown"

        if self._subprocess_config is not None:
            # In subprocess mode: also ping the worker to verify the subprocess runs.
            # _worker_send handles _ensure_worker internally under the lock.
            try:
                resp = self._worker_send({"method": "healthcheck"})
                return "ready" if resp.get("status") == "ok" else "unknown"
            except Exception:
                return "unknown"

        return "ready"

    def close(self) -> None:
        if self._worker_proc is not None:
            try:
                self._worker_proc.stdin.write(b"QUIT\n")  # type: ignore[union-attr]
                self._worker_proc.stdin.flush()  # type: ignore[union-attr]
                self._worker_proc.wait(timeout=10)
            except Exception:
                self._worker_proc.kill()
            finally:
                self._worker_proc = None

    # ------------------------------------------------------------------
    # Forward diagnostic attributes accessed by api.py
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if self._engine is None:
            raise AttributeError(
                f"'{type(self).__name__}' in subprocess mode has no attribute '{name}'"
            )
        return getattr(self._engine, name)


def _pipeline_token_to_canonical(token: OcrToken, source_engine: str) -> OCRToken:
    """Convert a pipeline OcrToken to the canonical benchmark OCRToken."""
    b = token.bbox
    return OCRToken(
        text=token.text,
        polygon_px=(
            (b.x0, b.y0),
            (b.x1, b.y0),
            (b.x1, b.y1),
            (b.x0, b.y1),
        ),
        bbox_px=(b.x0, b.y0, b.x1, b.y1),
        confidence_native=token.confidence,
        confidence_scale="0..1",
        level="line",
        source_engine=source_engine,
    )
