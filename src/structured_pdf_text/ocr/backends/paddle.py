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

import dataclasses
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from structured_pdf_text.config import ExtractorConfig, effective_ocr_quality_policy
from structured_pdf_text.document import OcrToken
from structured_pdf_text.errors import PaddleOcrUnavailable
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.runtime_policy import (
    apply_paddle_runtime_policy,
    resolve_paddle_runtime_policy,
)
from structured_pdf_text.ocr.pipelines.paddle.wire import PROTOCOL_VERSION, deserialize_tokens
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


def _raise_worker_failure(response: dict[str, Any], context: str) -> None:
    """Rebuild the worker's stable fatal-error contract in the parent process."""
    import errno
    from structured_pdf_text.errors import (
        FatalExtractionError,
        RequiredRuntimeUnavailableError,
        ResourceExhaustedExtractionError,
    )

    error_type = str(response.get("error_type") or "RuntimeError")
    message = f"{context}: {response.get('error') or error_type}"
    stage = response.get("stage")
    failure_context = {
        "stage": stage,
        "page_index": response.get("page_index"),
        "details": response.get("details") if isinstance(response.get("details"), dict) else {},
    }
    fatal_code = response.get("fatal_code")
    if fatal_code == "resource_exhausted" or error_type == "MemoryError" or (
        error_type == "OSError" and response.get("errno") == errno.ENOMEM
    ):
        raise ResourceExhaustedExtractionError(message, **failure_context)
    if fatal_code == "paddle_ocr_unavailable":
        raise PaddleOcrUnavailable(message, **failure_context)
    if fatal_code == "required_runtime_unavailable":
        raise RequiredRuntimeUnavailableError(message, **failure_context)
    if response.get("fatal"):
        raise FatalExtractionError(message, **failure_context)
    raise RuntimeError(message)


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

    Delegates ``recognize_page`` and ``recognize_region`` to the underlying
    PaddleOcrEngine after applying CF-4 subprocess routing when needed (see
    module docstring).  ``recognize_region`` adds page-bbox crop logic to map
    region coordinates correctly before forwarding.  The ``recognize`` method
    provides the canonical OCRResult contract for the benchmarking layer.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        from structured_pdf_text.ocr.models import get_profile

        self._config = config
        self._closed = False
        get_profile(config.paddle_model_profile)
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
        self._worker_diagnostics: dict[str, Any] = {}
        self._worker_proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._cancel_requested = threading.Event()
        # Serialise all subprocess request/response cycles so two callers
        # on different threads can never interleave their writes and reads.
        self._worker_lock: threading.Lock = threading.Lock()
        # Monotonic counter used as request_id for response matching.
        self._worker_req_seq: int = 0

        # Resolve cache_home once at construction time, mirroring PaddleOcrEngine's
        # own resolution, so healthcheck() checks the same directory the engine uses.
        self._cache_home: str = str(getattr(config, "ocr_cache_home", None) or os.environ.get(
            "PADDLE_PDX_CACHE_HOME",
            str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
        ))
        self._artifact_hashes = self._resolve_artifact_hashes()

        if _has_torch_conflict():
            # Subprocess mode (CF-4): torch DLLs would crash paddle at import time.
            # Store config and defer all OCR work to a clean subprocess.
            self._subprocess_config = {
                "language": config.language,
                "model_profile": config.paddle_model_profile,
                "num_threads": _resolve_num_threads(config.num_threads),
                "ocr_batch_size": config.ocr_batch_size,
                "quality_variants": config.ocr_quality_variants,
                "quality_policy": effective_ocr_quality_policy(config).value,
                "quality_thresholds": dataclasses.asdict(config.ocr_quality_thresholds),
                "mkldnn": _enable_mkldnn,
                "disable_pir_api": self._runtime_policy.disable_pir_api,
                "cache_home": self._cache_home,
            }
            self._engine = None
        else:
            # Direct mode: no DLL conflict — import and use paddle in-process.
            from structured_pdf_text.ocr.paddle import PaddleOcrEngine

            self._engine = PaddleOcrEngine(
                language=config.language,
                num_threads=_resolve_num_threads(config.num_threads),
                model_profile=config.paddle_model_profile,
                ocr_batch_size=config.ocr_batch_size,
                quality_variants=config.ocr_quality_variants,
                quality_policy=effective_ocr_quality_policy(config).value,
                quality_thresholds=config.ocr_quality_thresholds,
                enable_mkldnn=_enable_mkldnn,
                cache_home=self._cache_home,
            )

    # ------------------------------------------------------------------
    # Subprocess worker management (CF-4)
    # ------------------------------------------------------------------

    def _discard_worker(self) -> None:
        """Stop and forget a worker after any protocol framing failure."""
        proc = self._worker_proc
        self._worker_proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=1)
        except Exception:
            pass
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _ensure_worker(self) -> None:
        """Start the subprocess worker if not already running.

        Must be called while ``_worker_lock`` is held (done by ``_worker_send``).
        Uses ``_raw_send`` directly to avoid re-acquiring the lock for the
        init handshake.
        """
        if self._cancel_requested.is_set():
            raise RuntimeError("Paddle worker startup cancelled")
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            return  # still alive

        self._worker_proc = subprocess.Popen(
            [sys.executable, str(_WORKER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,  # unbuffered binary I/O — flush() is explicit in _raw_send
        )
        if self._cancel_requested.is_set():
            self._discard_worker()
            raise RuntimeError("Paddle worker startup cancelled")
        # Init handshake: use _raw_send (no lock, no seq) — we're already locked.
        init_req = {"protocol_version": PROTOCOL_VERSION, "method": "init", **self._subprocess_config}  # type: ignore[arg-type]
        try:
            response = self._raw_send(init_req)
        except Exception:
            self._discard_worker()
            raise
        if response.get("status") != "ok":
            self._discard_worker()
            _raise_worker_failure(response, "Paddle worker init failed")
        if response.get("protocol_version") != PROTOCOL_VERSION:
            self._discard_worker()
            raise RuntimeError("Paddle worker metadata protocol mismatch")

    def _raw_send(self, request: dict) -> dict:
        """Write one request and read one response on the raw pipe.

        No lock or request ID injection. A live worker may wait indefinitely;
        explicit cancellation kills it and wakes the pipe reader.
        """
        assert self._worker_proc is not None
        assert self._worker_proc.stdin is not None
        assert self._worker_proc.stdout is not None
        line = json.dumps(request, ensure_ascii=False) + "\n"
        try:
            self._worker_proc.stdin.write(line.encode())
            self._worker_proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("Paddle worker closed unexpectedly") from exc

        stdout = self._worker_proc.stdout
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(stdout.readline)
        try:
            response_line = fut.result()
        finally:
            pool.shutdown(wait=True)

        if not response_line:
            raise RuntimeError("Paddle worker closed unexpectedly")

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Paddle worker sent malformed JSON: {response_line[:200]!r}"
            ) from exc
        if not isinstance(response, dict) or response.get("status") not in ("ok", "error"):
            raise RuntimeError("Paddle worker sent an invalid response object")
        return response

    def _worker_send(self, request: dict) -> dict:
        """Send one JSONL request to the worker and return the parsed response.

        The full write → read cycle is serialised by ``_worker_lock`` so that
        concurrent callers on different threads cannot interleave their writes
        and reads and receive mismatched responses.

        A monotonic ``request_id`` is injected into every request and verified
        against the response, making protocol violations immediately visible
        rather than silently returning a stale or misrouted result.

        There is no inference deadline. Cancellation kills the child process
        outside this lock so a pending read wakes and releases serialization.
        """
        with self._worker_lock:
            # Ensure the worker is alive inside the lock so concurrent callers
            # cannot race to start two workers simultaneously.
            self._ensure_worker()

            self._worker_req_seq += 1
            req_id = self._worker_req_seq
            request = {**request, "protocol_version": PROTOCOL_VERSION, "request_id": req_id}
            try:
                response = self._raw_send(request)
            except Exception:
                self._discard_worker()
                raise

            resp_id = response.get("request_id")
            if resp_id != req_id:
                self._discard_worker()
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
        page_bbox: BBox | None = None,
        quality_variants: bool | None = None,
        page_rotation: int = 0,
    ) -> list[OcrToken]:
        """Write a temporary PNG path, send it to the worker, and decode tokens."""
        from PIL import Image

        if not isinstance(image, Image.Image):
            image = Image.fromarray(image)

        fd, image_path = tempfile.mkstemp(prefix="pdfextractor-paddle-", suffix=".png")
        os.close(fd)
        try:
            image.save(image_path, format="PNG")
            req: dict[str, Any] = {
                "method": method,
                "image_path": image_path,
                "page_index": page_index,
            }
            if quality_policy is not None:
                req["quality_policy"] = quality_policy
            if page_bbox is not None:
                req["page_bbox"] = [page_bbox.x0, page_bbox.y0, page_bbox.x1, page_bbox.y1]
            if quality_variants is not None:
                req["quality_variants"] = quality_variants
            if method == "recognize_page":
                req["page_rotation"] = page_rotation
            if region_bbox is not None:
                req["region_bbox"] = [region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1]

            response = self._worker_send(req)
        finally:
            try:
                os.unlink(image_path)
            except FileNotFoundError:
                pass

        self._worker_diagnostics = dict(response.get("diagnostics") or {})
        if response.get("status") != "ok":
            _raise_worker_failure(response, "Paddle worker request failed")
        return deserialize_tokens(response.get("tokens", []), language=self._config.language)

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        """Return engine identity including package versions, artifact hashes, and runtime mode."""
        return OCRBackendIdentity(
            engine="paddle",
            runtime="paddle_subprocess" if self._subprocess_config else "paddle_static",
            profile=self._config.paddle_model_profile,
            language=self._config.language,
            device="cpu",
            package_versions={
                "paddlepaddle": _package_version("paddlepaddle"),
                "paddleocr": _package_version("paddleocr"),
                "paddlex": _package_version("paddlex"),
            },
            artifact_hashes=dict(self._artifact_hashes),
            extra={
                "render_scale": self._config.effective_ocr_render_scale(),
                "quality_policy": effective_ocr_quality_policy(self._config).value,
                "quality_variants": self._config.ocr_quality_variants,
                "ocr_batch_size": self._config.ocr_batch_size,
                "num_threads": _resolve_num_threads(self._config.num_threads),
                "one_dnn": self._runtime_policy.enable_mkldnn,
                "orientation_classifier": True,
                "unwarping": True,
            },
        )

    def _resolve_artifact_hashes(self) -> dict[str, str]:
        from structured_pdf_text.ocr.models import UV_DOC_MODEL, get_profile
        from structured_pdf_text.ocr.paddle import _local_model_root
        from structured_pdf_text.ocr.backends._parser_utils import sha256_file

        root = _local_model_root(self._cache_home)
        profile = get_profile(self._config.paddle_model_profile)
        names = set(profile.model_names.values()) | {UV_DOC_MODEL}
        hashes: dict[str, str] = {}
        for name in sorted(names):
            directory = root / name
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(directory)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                if path.suffix in {".lock", ".metadata"}:
                    continue
                digest = sha256_file(path)
                if digest is not None:
                    hashes[path.relative_to(root).as_posix()] = digest
        return hashes

    @property
    def capabilities(self) -> OCRCapabilities:
        """Return static capability flags for this backend."""
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=True,
            page_orientation=True,
            quadrilateral_boxes=True,
            per_token_confidence=True,
            polygons=True,
            native_confidence=True,
        )

    # ------------------------------------------------------------------
    # OCRBackend — canonical recognize method (for benchmarking layer)
    # ------------------------------------------------------------------

    def recognize(self, request: OCRRequest) -> OCRResult:
        """Run PaddleOCR inference (in-process or via subprocess) and return a canonical OCRResult."""
        t0 = time.perf_counter()

        if self._closed:
            return OCRResult(
                status="runtime_error", tokens=(), text="", engine_identity=self.identity,
                elapsed_total_s=0.0, warnings=("PaddleOCR backend is closed",),
            )

        if request.input_kind == "region" and request.region_bbox is None:
            return OCRResult(
                status="invalid_input",
                tokens=(),
                text="",
                engine_identity=self.identity,
                elapsed_total_s=time.perf_counter() - t0,
                warnings=("region input requires region_bbox",),
            )

        try:
            is_region = request.input_kind == "region"
            region_box: BBox | None = None
            if is_region:
                region_box = BBox(*request.region_bbox)

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
        page_rotation: int = 0,
    ) -> list[OcrToken]:
        """Run OCR on a full page image, routing through the subprocess worker when needed."""
        if self._closed:
            raise RuntimeError("PaddleOCR backend is closed")
        if self._subprocess_config is not None:
            return self._call_subprocess(
                "recognize_page", page_image, page_index, page_bbox=page_bbox,
                quality_variants=quality_variants, quality_policy=quality_policy,
                page_rotation=page_rotation
            )
        return self._engine.recognize_page(  # type: ignore[union-attr]
            page_image,
            page_index,
            page_bbox,
            quality_variants=quality_variants,
            quality_policy=quality_policy,
            page_rotation=page_rotation,
        )

    def recognize_region(
        self, page_image: object, page_index: int, region_bbox: BBox,
        *, page_bbox: BBox | None = None,
    ) -> list[OcrToken]:
        """Crop and recognize a region; applies raster-crop logic when page_bbox is provided."""
        if self._closed:
            raise RuntimeError("PaddleOCR backend is closed")
        if page_bbox is not None:
            import numpy as np
            from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
            from structured_pdf_text.ocr.coordinates import map_tokens_to_page, offset_tokens

            crop, (cx0, cy0, _cx1, _cy1), (width, height) = crop_region_in_raster(
                np.asarray(page_image), region_bbox, page_bbox
            )
            if crop.size == 0:
                return []
            local = self.recognize_page(crop, page_index)
            return map_tokens_to_page(
                offset_tokens(local, float(cx0), float(cy0)), page_bbox, width, height
            )
        if self._subprocess_config is not None:
            return self._call_subprocess("recognize_region", page_image, page_index, region_bbox)
        return self._engine.recognize_region(page_image, page_index, region_bbox)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        """Return 'ready', 'missing', or 'unknown' after validating local model files and the subprocess."""
        from structured_pdf_text.ocr.paddle import validate_local_ocr_models

        try:
            validate_local_ocr_models(language=self._config.paddle_model_profile, cache_home=self._cache_home)
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
        """Gracefully shut down the subprocess worker (if active) and release the engine."""
        if self._closed:
            return
        self.cancel()
        with self._worker_lock:
            if self._worker_proc is not None:
                process = self._worker_proc
                try:
                    if process.stdin is not None:
                        process.stdin.write(b"QUIT\n")
                        process.stdin.flush()
                    process.wait(timeout=10)
                except Exception:
                    try:
                        process.kill()
                    finally:
                        try:
                            process.wait(timeout=5)
                        except Exception:
                            pass
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except Exception:
                                pass
                    self._worker_proc = None
        engine = self._engine
        close = getattr(engine, "close", None)
        if callable(close):
            close()
        self._engine = None
        self._closed = True

    def cancel(self) -> None:
        """Interrupt an active worker request without acquiring its request lock."""
        self._cancel_requested.set()
        process = self._worker_proc
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def reset_cancellation(self) -> None:
        """Start a new extraction cancellation scope after the prior one closed."""
        if self._worker_lock.locked():
            raise RuntimeError("Cannot reset Paddle cancellation during an active request")
        self._cancel_requested.clear()

    # ------------------------------------------------------------------
    # Forward diagnostic attributes accessed by api.py
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if self._engine is None:
            if name in self._worker_diagnostics:
                return self._worker_diagnostics[name]
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


def build_backend(config):
    """Lazy registry constructor for the Paddle raw backend."""
    return PaddleOCRBackend(config)
