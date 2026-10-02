"""RapidOCR backends — OCRBackend implementations for ONNX Runtime and OpenVINO.

Both runtimes use the same Python API (RapidOCR class) and bundled models.
The only difference is the underlying inference package:
  - rapidocr-onnxruntime 1.x  → runtime="onnxruntime"
  - rapidocr-openvino 1.x     → runtime="openvino"

CF-2: Direct PP-OCRv6 ONNX export from Paddle is blocked on Windows by a DLL
incompatibility (paddle2onnx 2.x + PaddlePaddle 3.3.1). Both backends therefore
use PP-OCRv4 models.  PP-OCRv6 ONNX files must be exported on Linux and then
pointed to via the env vars below.

**Default bundled model is PP-OCRv4 ch (Chinese + basic ASCII), which does NOT
cover Portuguese diacritics (ã ç ê õ).**  For correct Portuguese recognition,
set the Latin/English PP-OCRv4 (or PP-OCRv6 if exported) recognition model:

    # PP-OCRv4 Latin (recommended for Portuguese, available pre-built):
    RAPIDOCR_REC_MODEL=/path/to/en_PP-OCRv4_rec_infer.onnx
    RAPIDOCR_REC_KEYS=/path/to/en_dict.txt   # required when switching rec model

    # PP-OCRv6 medium (better quality; export on Linux first — CF-2):
    RAPIDOCR_REC_MODEL=/path/to/PP-OCRv6_medium_rec_infer.onnx
    RAPIDOCR_DET_MODEL=/path/to/PP-OCRv6_medium_det_infer.onnx
    RAPIDOCR_REC_KEYS=/path/to/ppocr_keys_v1.txt

A UserWarning is emitted at init time when neither RAPIDOCR_REC_MODEL nor
RAPIDOCR_DET_MODEL is set, because the bundled ch model will silently drop
diacritics and produce incorrect output for Portuguese documents.

Detection/quality tuning:
    RAPIDOCR_DET_MODEL      — override detection model path
    RAPIDOCR_UNCLIP_RATIO   — DB box expansion (default 1.8, was 1.6)
    RAPIDOCR_BOX_THRESH     — per-box score threshold (default 0.45, was 0.5)
    RAPIDOCR_DET_THRESH     — pixel-level binarisation threshold (default 0.25, was 0.3)
    RAPIDOCR_TEXT_SCORE     — minimum line confidence (default 0.5)
    RAPIDOCR_ANGLE_CLS      — enable angle classifier 0/1 (default 0)

Preprocessing:
    RAPIDOCR_CLAHE          — enable CLAHE LAB preprocessing 0/1 (default 1)
                              Set to 0 for the controlled/raw benchmark track so
                              all engines receive identical unmodified pixels.
"""
from __future__ import annotations

import inspect
import os
import time
import warnings
from typing import TYPE_CHECKING, Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import finite_confidence, quadrilateral_geometry, safe_crop_array, sha256_file
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


def _clahe_preprocess(img: Any) -> Any:
    """CLAHE on LAB L-channel, returning BGR 3-channel uint8 array.

    Preserves colour information (stamps, coloured headers, table backgrounds)
    by enhancing only the luminance channel.  RapidOCR 1.4.x expects a
    3-channel uint8 BGR array.  Falls back to the original array if cv2 is
    unavailable or on any error.
    """
    try:
        import cv2
        import numpy as np
        arr = np.asarray(img)
        if arr.ndim == 2:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        elif arr.ndim != 3:
            return img
        try:
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        except Exception:
            bgr = arr
        bgr = np.clip(bgr, 0, 255).astype(np.uint8)
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l_eq = clahe.apply(l_ch)
        merged = cv2.merge([l_eq, a_ch, b_ch])
        return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    except Exception:
        return img


def _rapidocr_accepted_params(cls: type) -> frozenset[str]:
    """Return the set of parameter names accepted by RapidOCR.__init__.

    Used to filter tuning kwargs so that an older package sub-version that
    lacks a particular knob is handled without swallowing unrelated TypeErrors.
    """
    try:
        sig = inspect.signature(cls.__init__)
        return frozenset(sig.parameters.keys()) - {"self"}
    except (ValueError, TypeError):
        return frozenset()


def _run_rapidocr(engine: Any, img: Any, *, clahe: bool = True) -> Any:
    """Run RapidOCR, optionally applying CLAHE (LAB L-channel) preprocessing.

    Pass clahe=False on the controlled/raw benchmark track so all engines
    receive identical unmodified pixels.
    """
    import numpy as np
    arr = np.asarray(img)
    enhanced = _clahe_preprocess(arr) if clahe else arr
    return engine(enhanced)


def _extract_raw(out: Any) -> Any:
    if isinstance(out, tuple) and len(out) >= 1:
        return out[0]
    if hasattr(out, "__iter__") and not isinstance(out, (str, bytes)):
        return out
    return None


def _result_to_ocr_tokens(
    raw: Any,
    source_engine: str,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[OCRToken]:
    """Convert RapidOCR result list to canonical OCRToken list.

    offset_x/offset_y shift all coordinates into page-pixel space when the
    image passed to OCR was a pre-cropped region.
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
            geometry = quadrilateral_geometry(bbox_pts, offset_x, offset_y)
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
    source: "SourceKind" = SourceKind.OCR_PAGE,
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
            language=language, source=source,
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
        rec_keys = os.environ.get("RAPIDOCR_REC_KEYS")
        if det_path:
            kwargs["det_model_path"] = det_path
        if rec_path:
            kwargs["rec_model_path"] = rec_path
        if rec_keys:
            kwargs["rec_char_dict_path"] = rec_keys

        if not rec_path and not det_path:
            warnings.warn(
                "RapidOCR is using the bundled PP-OCRv4-ch model, which does not "
                "cover Portuguese diacritics (ã ç ê õ). "
                "Set RAPIDOCR_REC_MODEL and RAPIDOCR_REC_KEYS to a Latin/en PP-OCRv4 "
                "or PP-OCRv6 ONNX model for correct Portuguese recognition. "
                "See the module docstring for instructions.",
                UserWarning,
                stacklevel=2,
            )
            self._profile = "builtin-ch"
        elif rec_path:
            self._profile = "custom-rec"
        else:
            self._profile = "custom-det"

        self._clahe = os.environ.get("RAPIDOCR_CLAHE", "1").lower() not in ("0", "false", "no")

        unclip    = float(os.environ.get("RAPIDOCR_UNCLIP_RATIO", "1.8"))
        box_thresh = float(os.environ.get("RAPIDOCR_BOX_THRESH",   "0.45"))
        det_thresh = float(os.environ.get("RAPIDOCR_DET_THRESH",   "0.25"))
        text_score = float(os.environ.get("RAPIDOCR_TEXT_SCORE",   "0.5"))
        angle_cls  = os.environ.get("RAPIDOCR_ANGLE_CLS", "0").lower() in ("1", "true", "yes")

        tuning_kwargs: dict[str, Any] = {
            "det_db_unclip_ratio": unclip,
            "det_db_box_thresh":   box_thresh,
            "det_db_thresh":       det_thresh,
            "text_score":          text_score,
            "with_angle_cls":      angle_cls,
        }
        accepted = _rapidocr_accepted_params(RapidOCR)
        merged = {**kwargs, **{k: v for k, v in tuning_kwargs.items() if k in accepted}}
        self._engine = RapidOCR(**merged)

        self._det_model = det_path
        self._rec_model = rec_path
        self._rec_keys  = rec_keys

        # Hash every custom artefact provided via env vars individually so the
        # manifest can prove exactly which files were used.  Bundled models live
        # inside the package wheel and are not addressable as ordinary paths.
        hashes: dict[str, str] = {}
        if det_path:
            d = sha256_file(det_path)
            if d:
                hashes["det_model"] = d
        if rec_path:
            r = sha256_file(rec_path)
            if r:
                hashes["rec_model"] = r
        if rec_keys:
            k = sha256_file(rec_keys)
            if k:
                hashes["rec_keys"] = k
        self._artifact_hashes = hashes

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        pkg_name = "rapidocr-onnxruntime" if self._runtime == "onnxruntime" else "rapidocr-openvino"
        runtime_pkg = "onnxruntime" if self._runtime == "onnxruntime" else "openvino"
        return OCRBackendIdentity(
            engine=self._engine_key,
            runtime=self._runtime,
            profile=self._profile,
            language=self._language,
            device="cpu",
            package_versions={
                pkg_name: _package_version(pkg_name),
                runtime_pkg: _package_version(runtime_pkg),
            },
            artifact_hashes=self._artifact_hashes,
            extra={"clahe": self._clahe},
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
            out = _run_rapidocr(self._engine, img, clahe=self._clahe)
            raw = _extract_raw(out)
            rx0, ry0 = (request.region_bbox[0], request.region_bbox[1]) if request.region_bbox else (0.0, 0.0)
            tokens = tuple(_result_to_ocr_tokens(raw, self._engine_key, offset_x=rx0, offset_y=ry0))
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
        out = _run_rapidocr(self._engine, img, clahe=self._clahe)
        return _result_to_pipeline_tokens(_extract_raw(out), page_index, self._language)

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
    ) -> list[OcrToken]:
        img = _to_numpy(page_image)
        crop, (cx0, cy0, _cx1, _cy1) = safe_crop_array(
            img, region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1,
        )
        if crop.size == 0:
            return []
        out = _run_rapidocr(self._engine, crop, clahe=self._clahe)
        return _result_to_pipeline_tokens(
            _extract_raw(out), page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            source=SourceKind.OCR_REGION,
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
