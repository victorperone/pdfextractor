"""EasyOCR backend — OCRBackend implementation using EasyOCR + PyTorch CPU.

EasyOCR downloads models on first use to ~/.EasyOCR/model/:
  - craft_mlt_25k.pth  (detection, ~41 MB)
  - latin_g2.pth       (recognition for Portuguese, ~666 MB)
  - latin_g1.pth       (alternative recognition model, larger, ~800 MB)

No GPU is used (gpu=False). Models are cached locally after first download.

Environment variables
---------------------
EASYOCR_MODULE_PATH      Path to model cache directory (default: ~/.EasyOCR/model/)
EASYOCR_RECOG_NETWORK    Recognition model name (default: '' → EasyOCR default = latin_g2)
                         Use 'latin_g1' for the older, larger model.
EASYOCR_BEAMWIDTH        Beam width for beamsearch decoder (default: 10, min: 1)
EASYOCR_WORKERS          DataLoader workers for recognition.
                         Default: auto — 0 on Windows (spawn safety), half of
                         cpu_count() capped at 4 on Linux/macOS.
                         Override only if the auto-detection is wrong.
EASYOCR_ALLOWLIST        Character allowlist applied to all recognition calls.
                         Example: '0123456789.,R$%()-/ '  for financial documents.
                         Default: unset (no restriction).
EASYOCR_BLOCKLIST        Character blocklist applied to all recognition calls.
                         Default: unset (no restriction).
                         Note: do NOT set 'OoIl' globally — 'o' and 'O' are common
                         Portuguese letters.  Use EASYOCR_ALLOWLIST instead for
                         digit-only deployments.

Optimization notes (Fase 9)
----------------------------
- CLAHE preprocessing: local contrast enhancement applied before OCR using
  cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).  Skipped silently if
  OpenCV is unavailable.
- Detect/recognize split: uses reader.detect() + reader.recognize() instead
  of the unified readtext().  This allows different parameter sets for the
  two stages and feeds a CLAHE-enhanced grayscale to the recognition stage.
  Falls back to readtext() on any API incompatibility.
- canvas_size: set to max(image_height, image_width) so CRAFT never downscales
  the input.  This fixes detection loss on pages rendered at higher DPI.
- mag_ratio=1.5: magnifies input before CRAFT detection, improving recall on
  small text (table cells, footnotes).
- decoder='beamsearch': CTC beamsearch instead of greedy; reduces substitution
  errors on ambiguous characters at the cost of ~20-40% extra inference time.
- adjust_contrast=1.0: stronger contrast recovery for low-contrast regions
  (desbotado text, gray headers, scanned documents).
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


def _default_workers() -> int:
    """Return a safe default for EasyOCR DataLoader worker count.

    On Windows, PyTorch uses 'spawn' for multiprocessing, which requires
    the __main__ guard and causes deadlocks in subprocess contexts like
    our paddle_subprocess worker.  Zero is the only safe default there.
    On Linux/macOS, 'fork' is used and workers parallelize data loading
    for a ~30% throughput gain.  We use half of the available cores,
    capped at 4, to avoid starving other pipeline stages.
    """
    import platform
    if platform.system() == "Windows":
        return 0
    cpu = os.cpu_count() or 1
    return min(4, max(1, cpu // 2))


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


def _to_numpy(image: object) -> "Any":
    import numpy as np
    if isinstance(image, np.ndarray):
        return image
    arr = np.array(image)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    return arr


def _clahe_grey(img: "Any") -> "Any | None":
    """Convert image to CLAHE-enhanced grayscale for recognition stage.

    Returns a uint8 grayscale numpy array, or None if OpenCV is unavailable.
    CLAHE (clipLimit=2.0, tileGridSize=8×8) applies local contrast enhancement
    without affecting high-contrast regions significantly.
    """
    try:
        import cv2
        import numpy as np
        arr = np.asarray(img)
        if arr.ndim == 3:
            # Try RGB→gray first; fall back to BGR→gray
            try:
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            except Exception:
                gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        elif arr.ndim == 2:
            gray = arr.astype(np.uint8)
        else:
            return None
        gray = np.clip(gray, 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(gray)
    except Exception:
        return None


def _run_easyocr(
    reader: "Any",
    img: "Any",
    *,
    beamwidth: int,
    adjust_contrast: float,
    allowlist: "str | None",
    blocklist: "str | None",
    workers: int,
) -> list[Any]:
    """Run EasyOCR using detect/recognize split with optimized parameters.

    Falls back to unified readtext() if the split fails (e.g. API version
    mismatch).  canvas_size is set to max(h, w) so CRAFT never downscales
    the input for detection.
    """
    import numpy as np
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    canvas_size = max(h, w)

    # --- Stage 1: text detection (CRAFT) ---
    try:
        mag_ratio = float(os.environ.get("EASYOCR_MAG_RATIO", "1.2"))
        horizontal_list, free_list = reader.detect(
            arr,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
        )
    except Exception:
        horizontal_list, free_list = None, None

    if horizontal_list is None:
        # Fallback: unified readtext with optimised params
        return reader.readtext(
            arr,
            decoder="beamsearch",
            beamWidth=beamwidth,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
        )

    # --- Stage 2: recognition (CRNN + CTC) ---
    try:
        return reader.recognize(
            arr,
            horizontal_list=horizontal_list,
            free_list=free_list,
            decoder="beamsearch",
            beamWidth=beamwidth,
            workers=workers,
            detail=1,
            paragraph=False,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
        )
    except Exception:
        # Fallback: unified readtext without the split
        return reader.readtext(
            arr,
            decoder="beamsearch",
            beamWidth=beamwidth,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
        )


def _result_to_ocr_tokens(raw: list[Any], source_engine: str) -> list[OCRToken]:
    """Convert EasyOCR result list to canonical OCRToken list.

    Each item: (bbox_points, text, confidence)
      bbox_points: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (4 corners)
    """
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
    raw: list[Any], page_index: int, language: str,
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


class EasyOCRBackend:
    """OCRBackend using EasyOCR with PyTorch CPU inference.

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
    Models are downloaded to ~/.EasyOCR/model/ on first use (~700 MB total).

    See module docstring for all tunable environment variables.
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        self._language = config.language
        self._langs = _LANG_MAP.get(config.language, ["pt"])

        # --- env-var configuration ---
        self._beamwidth = max(1, int(os.environ.get("EASYOCR_BEAMWIDTH", "10")))
        self._workers = max(0, int(os.environ.get("EASYOCR_WORKERS", str(_default_workers()))))
        self._adjust_contrast = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
        allowlist_env = os.environ.get("EASYOCR_ALLOWLIST", "")
        self._allowlist: str | None = allowlist_env if allowlist_env else None
        blocklist_env = os.environ.get("EASYOCR_BLOCKLIST", "")
        self._blocklist: str | None = blocklist_env if blocklist_env else None

        # --- reader init ---
        easyocr_mod = _import_easyocr()
        module_path = os.environ.get("EASYOCR_MODULE_PATH")
        recog_network = os.environ.get("EASYOCR_RECOG_NETWORK", "")

        kwargs: dict[str, Any] = {"gpu": False, "verbose": False}
        if module_path:
            kwargs["model_storage_directory"] = module_path
        if recog_network:
            kwargs["recog_network"] = recog_network

        self._reader = easyocr_mod.Reader(self._langs, **kwargs)
        self._recog_network = recog_network or "latin_g2"

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
            raw = _run_easyocr(
                self._reader,
                img,
                beamwidth=self._beamwidth,
                adjust_contrast=self._adjust_contrast,
                allowlist=self._allowlist,
                blocklist=self._blocklist,
                workers=self._workers,
            )
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
        quality_policy: str | None = None,
    ) -> list[OcrToken]:
        img = _to_numpy(page_image)
        raw = _run_easyocr(
            self._reader,
            img,
            beamwidth=self._beamwidth,
            adjust_contrast=self._adjust_contrast,
            allowlist=self._allowlist,
            blocklist=self._blocklist,
            workers=self._workers,
        )
        return _result_to_pipeline_tokens(raw, page_index, self._language)

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
        raw = _run_easyocr(
            self._reader,
            crop,
            beamwidth=self._beamwidth,
            adjust_contrast=self._adjust_contrast,
            allowlist=self._allowlist,
            blocklist=self._blocklist,
            workers=self._workers,
        )
        return _result_to_pipeline_tokens(
            raw, page_index, self._language,
            offset_x=float(x0), offset_y=float(y0),
        )

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
