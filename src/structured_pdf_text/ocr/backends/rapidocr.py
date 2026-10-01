"""RapidOCR backends — OCRBackend implementations for ONNX Runtime and OpenVINO.

Both runtimes use the same Python API (RapidOCR class) and bundled models.
The only difference is the underlying inference package:
  - rapidocr-onnxruntime 1.x  → runtime="onnxruntime"
  - rapidocr-openvino 1.x     → runtime="openvino"

CF-2: Direct PP-OCRv6 ONNX export from Paddle is blocked on Windows by a DLL
incompatibility (paddle2onnx 2.x + PaddlePaddle 3.3.1). Both backends use the
PP-OCRv4 models bundled in their respective packages. Override with env vars:
    RAPIDOCR_DET_MODEL=/path/to/det.onnx (or .xml for OpenVINO)
    RAPIDOCR_REC_MODEL=/path/to/rec.onnx (or .xml for OpenVINO)
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import finite_confidence, quadrilateral_geometry
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _import_rapidocr(runtime: str) -> type:
    """Import RapidOCR from the appropriate runtime package."""
    if runtime == "openvino":
        try:
            from rapidocr_openvino import RapidOCR  # type: ignore
            return RapidOCR
        except ImportError as exc:
            raise ImportError(
                "rapidocr-openvino is not installed. "
                "Install with: pip install openvino==2024.4.0 && "
                'pip install "rapidocr-openvino==1.4.4" --no-deps'
            ) from exc
    else:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            return RapidOCR
        except ImportError as exc:
            raise ImportError(
                "rapidocr-onnxruntime is not installed. "
                'Install with: pip install "structured-pdf-text[ocr-rapidocr-onnx]"'
            ) from exc


def _to_numpy(image: object):
    import numpy as np
    if isinstance(image, np.ndarray):
        return image
    arr = np.array(image)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    return arr


def _extract_raw(out: Any) -> Any:
    if isinstance(out, tuple) and len(out) >= 1:
        return out[0]
    if hasattr(out, "__iter__") and not isinstance(out, (str, bytes)):
        return out
    return None


def _result_to_ocr_tokens(raw: Any, source_engine: str) -> list[OCRToken]:
    if not raw:
        return []
    tokens = []
    for item in raw:
        try:
            if len(item) < 2:
                continue
            bbox_pts, text = item[0], item[1]
            if not str(text).strip():
                continue
            confidence = finite_confidence(item[2]) if len(item) > 2 else None
            geometry = quadrilateral_geometry(bbox_pts)
        except (TypeError, ValueError, IndexError):
            continue
        if geometry is None:
            continue
        polygon, bounds = geometry
        tokens.append(OCRToken(
            text=str(text), polygon_px=polygon, bbox_px=bounds,
            confidence_native=confidence, confidence_scale="0..1",
            level="line", source_engine=source_engine,
        ))
    return tokens


def _result_to_pipeline_tokens(
    raw: Any, page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
) -> list[OcrToken]:
    if not raw:
        return []
    tokens = []
    for item in raw:
        try:
            if len(item) < 2:
                continue
            bbox_pts, text = item[0], item[1]
            if not str(text).strip():
                continue
            confidence = finite_confidence(item[2]) if len(item) > 2 else 1.0
            geometry = quadrilateral_geometry(bbox_pts, offset_x, offset_y)
        except (TypeError, ValueError, IndexError):
            continue
        if geometry is None:
            continue
        _, (x0, y0, x1, y1) = geometry
        try:
            bbox = BBox(x0, y0, x1, y1)
        except (TypeError, ValueError):
            continue
        tokens.append(OcrToken(
            text=str(text), bbox=bbox,
            confidence=max(0.0, min(1.0, confidence if confidence is not None else 0.0)),
            language=language, source=SourceKind.OCR_PAGE,
        ))
    return tokens


class RapidOCRBackend:
    """OCRBackend using RapidOCR with either onnxruntime or OpenVINO.

    runtime="onnxruntime" → rapidocr-onnxruntime (engine key: "rapidocr-onnx")
    runtime="openvino"    → rapidocr-openvino    (engine key: "rapidocr-openvino")

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
    """

    def __init__(self, config: "ExtractorConfig", runtime: str = "onnxruntime") -> None:
        self._config = config
        self._language = config.language
        self._runtime = runtime
        self._engine_key = "rapidocr-onnx" if runtime == "onnxruntime" else "rapidocr-openvino"

        RapidOCR = _import_rapidocr(runtime)
        kwargs: dict[str, Any] = {}
        det_path = os.environ.get("RAPIDOCR_DET_MODEL")
        rec_path = os.environ.get("RAPIDOCR_REC_MODEL")
        if det_path:
            kwargs["det_model_path"] = det_path
        if rec_path:
            kwargs["rec_model_path"] = rec_path

        self._engine = RapidOCR(**kwargs)
        self._det_model = det_path
        self._rec_model = rec_path

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        profile = "custom-onnx" if self._det_model else "builtin"
        pkg_name = "rapidocr-onnxruntime" if self._runtime == "onnxruntime" else "rapidocr-openvino"
        runtime_pkg = "onnxruntime" if self._runtime == "onnxruntime" else "openvino"
        return OCRBackendIdentity(
            engine=self._engine_key,
            runtime=self._runtime,
            profile=profile,
            language=self._language,
            device="cpu",
            package_versions={
                pkg_name: _package_version(pkg_name),
                runtime_pkg: _package_version(runtime_pkg),
            },
            artifact_hashes={},
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=False,
            page_orientation=False,
            quadrilateral_boxes=True,
            per_token_confidence=True,
        )

    # ------------------------------------------------------------------
    # OCRBackend — canonical recognize method (for benchmarking)
    # ------------------------------------------------------------------

    def recognize(self, request: OCRRequest) -> OCRResult:
        t0 = time.perf_counter()
        try:
            img = _to_numpy(request.image)
            out = self._engine(img)
            raw = _extract_raw(out)
            tokens = tuple(_result_to_ocr_tokens(raw, self._engine_key))
            text = " ".join(t.text for t in tokens)
            status = "ok" if tokens else "no_text"
        except Exception as exc:
            return OCRResult(
                status="runtime_error",
                tokens=(),
                text="",
                engine_identity=self.identity,
                elapsed_total_s=time.perf_counter() - t0,
                warnings=(str(exc),),
            )
        return OCRResult(
            status=status,
            tokens=tokens,
            text=text,
            engine_identity=self.identity,
            elapsed_total_s=time.perf_counter() - t0,
        )

    # ------------------------------------------------------------------
    # OcrEngine protocol — consumed by recovery.py / pipeline
    # ------------------------------------------------------------------

    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: "BBox | None" = None,
        *,
        quality_variants: bool | None = None,
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        img = _to_numpy(page_image)
        out = self._engine(img)
        return _result_to_pipeline_tokens(_extract_raw(out), page_index, self._language)

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
    ) -> list[OcrToken]:
        img = _to_numpy(page_image)
        x0, y0 = int(region_bbox.x0), int(region_bbox.y0)
        x1, y1 = int(region_bbox.x1), int(region_bbox.y1)
        crop = img[y0:y1, x0:x1]
        out = self._engine(crop)
        return _result_to_pipeline_tokens(
            _extract_raw(out), page_index, self._language,
            offset_x=float(x0), offset_y=float(y0),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        try:
            _import_rapidocr(self._runtime)
            return "ready"
        except ImportError:
            return "missing"
        except Exception:
            return "unknown"

    def close(self) -> None:
        pass


# Convenience aliases used by factory.py
RapidOCROnnxBackend = RapidOCRBackend
