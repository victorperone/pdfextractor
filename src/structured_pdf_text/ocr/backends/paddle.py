"""PaddleOCR backend — wraps the existing PaddleOcrEngine.

Delegates all pipeline calls to the original implementation so that the
output is byte-for-byte identical to the pre-refactor baseline.  The new
``recognize`` / ``identity`` / ``capabilities`` / ``healthcheck`` methods
add the benchmarking contract without touching any existing code paths.

``recovery.py`` and the rest of the pipeline continue to see exactly the same
``OcrEngine``-compatible interface they always have.
"""
from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any

from structured_pdf_text.config import ExtractorConfig, effective_ocr_quality_policy
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.errors import PaddleOcrUnavailable
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)


def _resolve_num_threads(num_threads: int) -> int:
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


class PaddleOCRBackend:
    """OCRBackend implementation backed by PaddleOcrEngine.

    Thin wrapper: all ``recognize_page`` / ``recognize_region`` calls are
    forwarded unchanged so the existing pipeline output is preserved exactly.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        from structured_pdf_text.ocr.models import get_profile
        from structured_pdf_text.ocr.paddle import PaddleOcrEngine

        self._config = config
        get_profile(config.language)
        # PaddlePaddle's oneDNN (MKL-DNN) backend has known compatibility issues
        # on Windows with certain PIR attribute types (ArrayAttribute<DoubleAttribute>)
        # in PaddlePaddle 3.x. Disable it on Windows to avoid runtime_error on all
        # OCR pages. Can be overridden by setting PADDLE_ENABLE_MKLDNN=1.
        _default_mkldnn = platform.system() != "Windows"
        _enable_mkldnn = os.environ.get("PADDLE_ENABLE_MKLDNN", "1" if _default_mkldnn else "0") == "1"
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
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        return OCRBackendIdentity(
            engine="paddle",
            runtime="paddle_static",
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
        warnings: list[str] = []

        try:
            if request.input_kind == "region" and request.region_id is not None:
                page_bbox = BBox(0.0, 0.0, 1.0, 1.0)
                tokens = self._engine.recognize_region(
                    request.image, request.page_index, page_bbox
                )
            else:
                tokens = self._engine.recognize_page(
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
    ) -> list[OcrToken]:
        return self._engine.recognize_page(
            page_image,
            page_index,
            page_bbox,
            quality_variants=quality_variants,
        )

    def recognize_region(
        self, page_image: object, page_index: int, region_bbox: BBox
    ) -> list[OcrToken]:
        return self._engine.recognize_region(page_image, page_index, region_bbox)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        from structured_pdf_text.ocr.paddle import validate_local_ocr_models

        try:
            validate_local_ocr_models(language=self._config.language)
            return "ready"
        except PaddleOcrUnavailable:
            return "missing"
        except ValueError:
            return "unknown"
        except Exception:
            return "unknown"

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Forward diagnostic attributes accessed by api.py
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
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
