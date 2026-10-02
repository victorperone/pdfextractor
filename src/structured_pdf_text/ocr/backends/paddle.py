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
import importlib.util
import io
import json
import os
import logging
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

_WORKER_READ_TIMEOUT_S: float = float(os.environ.get("PADDLE_WORKER_TIMEOUT", "120"))

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
                "quality_thresholds": config.ocr_quality_thresholds,
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
        """Start the subprocess worker if not already running."""
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            return  # still alive

        self._worker_proc = subprocess.Popen(
            [sys.executable, str(_WORKER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=1,  # line-buffered
        )
        # Send init request
        init_req = {"method": "init", **self._subprocess_config}  # type: ignore[arg-type]
        response = self._worker_send(init_req)
        if response.get("status") != "ok":
            raise RuntimeError(
                f"Paddle worker init failed: {response.get('error')}"
            )

    def _worker_send(self, request: dict) -> dict:
        """Send one JSONL request to the worker and return the parsed response.

        Enforces a read timeout (PADDLE_WORKER_TIMEOUT env var, default 120 s)
        so that a hung worker never blocks the parent process indefinitely.
        readline() runs in a background thread; TimeoutError is raised and the
        worker process is killed if the deadline expires.
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
                response_line = fut.result(timeout=_WORKER_READ_TIMEOUT_S)
            except TimeoutError:
                self._worker_proc.kill()
                self._worker_proc = None
                raise RuntimeError(
                    f"Paddle worker timed out after {_WORKER_READ_TIMEOUT_S}s"
                )

        if not response_line:
            raise RuntimeError("Paddle worker closed unexpectedly")

        try:
            return json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Paddle worker sent malformed JSON: {response_line[:200]!r}"
            ) from exc

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

        self._ensure_worker()
        response = self._worker_send(req)

        if response.get("status") != "ok":
            raise RuntimeError(response.get("error", "unknown subprocess error"))

        tokens: list[OcrToken] = []
        for item in response.get("tokens", []):
            x0, y0, x1, y1 = item["bbox"]
            tokens.append(
                OcrToken(
                    text=item["text"],
                    confidence=float(item["confidence"]),
                    bbox=BBox(x0, y0, x1, y1),
                    source=SourceKind.OCR_PAGE,
                    language=self._config.language,
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
            artifact_hashes={},
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
        if self._subprocess_config is not None:
            # In subprocess mode: send a lightweight ping to the worker
            try:
                self._ensure_worker()
                resp = self._worker_send({"method": "healthcheck"})
                return "ready" if resp.get("status") == "ok" else "unknown"
            except Exception:
                return "unknown"

        from structured_pdf_text.ocr.paddle import validate_local_ocr_models

        try:
            validate_local_ocr_models(language=self._config.language, cache_home=self._cache_home)
            return "ready"
        except PaddleOcrUnavailable:
            return "missing"
        except ValueError:
            return "unknown"
        except Exception:
            return "unknown"

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
