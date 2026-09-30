"""EasyOCR backend — OCRBackend implementation using EasyOCR + PyTorch CPU.

EasyOCR downloads models on first use to ~/.EasyOCR/model/:
  - craft_mlt_25k.pth  (detection, ~41 MB)
  - latin_g2.pth       (recognition for Portuguese, ~666 MB)

No GPU is used (gpu=False). Models are cached locally after first download.
Override model directory via environment variable:
    EASYOCR_MODULE_PATH=/path/to/easyocr/model/dir
"""
from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig


_LANG_MAP: dict[str, list[str]] = {
    "pt": ["pt"],
    "por": ["pt"],
    "en": ["en"],
    "eng": ["en"],
}


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _import_easyocr():
    try:
        import easyocr  # type: ignore
        return easyocr
    except ImportError as exc:
        raise ImportError(
            "easyocr is not installed. "
            'Install with: pip install "structured-pdf-text[ocr-easyocr]"'
        ) from exc


def _to_numpy(image: object):
    import numpy as np
    if isinstance(image, np.ndarray):
        return image
    arr = np.array(image)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    return arr


def _result_to_ocr_tokens(raw: list[Any], source_engine: str) -> list[OCRToken]:
    """Convert EasyOCR result list to canonical OCRToken list.

    Each item: (bbox_points, text, confidence)
      bbox_points: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (4 corners)
    """
    if not raw:
        return []
    tokens = []
    for item in raw:
        if len(item) < 2:
            continue
        bbox_pts, text = item[0], item[1]
        confidence = float(item[2]) if len(item) > 2 and item[2] is not None else None

        import numpy as np
        if isinstance(bbox_pts, np.ndarray):
            bbox_pts = bbox_pts.tolist()

        xs = [float(p[0]) for p in bbox_pts]
        ys = [float(p[1]) for p in bbox_pts]
        polygon = tuple((float(p[0]), float(p[1])) for p in bbox_pts)
        tokens.append(OCRToken(
            text=str(text),
            polygon_px=polygon,
            bbox_px=(min(xs), min(ys), max(xs), max(ys)),
            confidence_native=confidence,
            confidence_scale="0..1",
            level="line",
            source_engine=source_engine,
        ))
    return tokens


def _result_to_pipeline_tokens(
    raw: list[Any], page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
) -> list[OcrToken]:
    if not raw:
        return []
    tokens = []
    for item in raw:
        if len(item) < 2:
            continue
        bbox_pts, text = item[0], item[1]
        confidence = float(item[2]) if len(item) > 2 and item[2] is not None else 1.0

        import numpy as np
        if isinstance(bbox_pts, np.ndarray):
            bbox_pts = bbox_pts.tolist()

        xs = [float(p[0]) + offset_x for p in bbox_pts]
        ys = [float(p[1]) + offset_y for p in bbox_pts]
        try:
            bbox = BBox(min(xs), min(ys), max(xs), max(ys))
        except Exception:
            continue
        tokens.append(OcrToken(
            text=str(text),
            bbox=bbox,
            confidence=max(0.0, min(1.0, confidence)),
            language=language,
            source=SourceKind.OCR_PAGE,
        ))
    return tokens


class EasyOCRBackend:
    """OCRBackend using EasyOCR with PyTorch CPU inference.

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
    Models are downloaded to ~/.EasyOCR/model/ on first use (~700 MB total).
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        self._language = config.language
        self._langs = _LANG_MAP.get(config.language, ["pt"])

        easyocr_mod = _import_easyocr()
        module_path = os.environ.get("EASYOCR_MODULE_PATH")
        kwargs: dict[str, Any] = {"gpu": False, "verbose": False}
        if module_path:
            kwargs["model_storage_directory"] = module_path

        self._reader = easyocr_mod.Reader(self._langs, **kwargs)

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        return OCRBackendIdentity(
            engine="easyocr",
            runtime="torch-cpu",
            profile="+".join(self._langs),
            language=self._language,
            device="cpu",
            package_versions={
                "easyocr": _package_version("easyocr"),
                "torch": _package_version("torch"),
            },
            artifact_hashes={},
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=True,
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
            raw = self._reader.readtext(img)
            tokens = tuple(_result_to_ocr_tokens(raw, "easyocr"))
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
    ) -> list[OcrToken]:
        try:
            img = _to_numpy(page_image)
            raw = self._reader.readtext(img)
            return _result_to_pipeline_tokens(raw, page_index, self._language)
        except Exception:
            return []

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
    ) -> list[OcrToken]:
        try:
            import numpy as np
            img = _to_numpy(page_image)
            x0, y0 = int(region_bbox.x0), int(region_bbox.y0)
            x1, y1 = int(region_bbox.x1), int(region_bbox.y1)
            crop = img[y0:y1, x0:x1]
            raw = self._reader.readtext(crop)
            return _result_to_pipeline_tokens(
                raw, page_index, self._language,
                offset_x=float(x0), offset_y=float(y0),
            )
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        try:
            _import_easyocr()
            return "ready"
        except ImportError:
            return "missing"
        except Exception:
            return "unknown"

    def close(self) -> None:
        pass
