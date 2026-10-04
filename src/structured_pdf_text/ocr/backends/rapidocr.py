"""RapidOCR backend with ONNX Runtime and OpenVINO inference providers.

Both providers use the unified rapidocr package. Imports from the retired
provider packages are retained only as a runtime compatibility fallback.

CF-2: Direct PP-OCRv6 ONNX export from Paddle is blocked on Windows by a DLL
incompatibility (paddle2onnx 2.x + PaddlePaddle 3.3.1). Both backends therefore
use PP-OCRv4 models.  PP-OCRv6 ONNX files must be exported on Linux and then
pointed to via the env vars below.

**Default bundled model is PP-OCRv4 ch (Chinese + basic ASCII), which does NOT
cover Portuguese diacritics (ã ç ê õ).** For Portuguese, configure the paired
PP-OCRv4 Latin recognizer and its matching dictionary:

    RAPIDOCR_REC_MODEL=/path/to/latin_PP-OCRv3_rec_mobile.onnx
    RAPIDOCR_REC_KEYS=/path/to/latin_dict.txt

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

"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
import time
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


PORTUGUESE_REQUIRED_CHARS = frozenset("ãõçêáéíóú")


def portuguese_dictionary_profile(path: str | os.PathLike[str] | None) -> tuple[str, tuple[str, ...]]:
    """Classify a recognizer dictionary for Portuguese sanity-check purposes.

    This only verifies that the dictionary can represent a small set of
    Portuguese letters. It does not claim full linguistic/model coverage.
    """
    if not path:
        return "missing-dictionary", tuple(sorted(PORTUGUESE_REQUIRED_CHARS))
    try:
        with open(path, "r", encoding="utf-8-sig") as dictionary_file:
            text = dictionary_file.read()
    except (OSError, UnicodeError):
        return "unreadable-dictionary", tuple(sorted(PORTUGUESE_REQUIRED_CHARS))
    missing = tuple(sorted(PORTUGUESE_REQUIRED_CHARS - set(text)))
    if not missing:
        return "latin/pt-compatible", ()
    return "latin-unverified", missing


if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _import_rapidocr(runtime: str) -> type:
    """Import only the maintained unified RapidOCR package."""
    try:
        from rapidocr import RapidOCR  # type: ignore
        return RapidOCR
    except ImportError as exc:
        provider_hint = "ocr-rapidocr-openvino" if runtime == "openvino" else "ocr-rapidocr-onnx"
        raise ImportError(
            "The unified rapidocr package is not installed. "
            f'Install with: pip install "structured-pdf-text[{provider_hint}]"'
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


def _run_rapidocr(engine: Any, img: Any) -> Any:
    """Run RapidOCR with CLAHE (LAB L-channel) preprocessing."""
    import numpy as np
    arr = np.asarray(img)
    enhanced = _clahe_preprocess(arr)
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
    """OCRBackend using the unified RapidOCR package with a selected provider.

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
    """

    def __init__(self, config: "ExtractorConfig", runtime: str = "onnxruntime") -> None:
        self._config = config
        self._closed = False
        from structured_pdf_text.ocr.env import env_float
        self._language = config.language
        self._runtime = runtime
        self._engine_key = "rapidocr"

        RapidOCR = _import_rapidocr(runtime)
        self._unified = getattr(RapidOCR, "__module__", "").split(".")[0] == "rapidocr"
        kwargs: dict[str, Any] = {}

        det_path = os.environ.get("RAPIDOCR_DET_MODEL")
        rec_path = os.environ.get("RAPIDOCR_REC_MODEL")
        rec_keys = os.environ.get("RAPIDOCR_REC_KEYS")
        if not rec_path and not rec_keys:
            cache = Path.home() / ".cache" / "pdfextractor" / "rapidocr"
            cached_rec = cache / "latin_PP-OCRv3_rec_mobile.onnx"
            cached_keys = cache / "latin_dict.txt"
            if cached_rec.is_file() or cached_keys.is_file():
                rec_path = str(cached_rec)
                rec_keys = str(cached_keys)
        if det_path and not self._unified:
            kwargs["det_model_path"] = det_path
        if bool(rec_path) != bool(rec_keys):
            raise ValueError(
                "RapidOCR recognition model and dictionary must be configured "
                "together via RAPIDOCR_REC_MODEL and RAPIDOCR_REC_KEYS."
            )
        if rec_path and not self._unified:
            kwargs["rec_model_path"] = rec_path
        if rec_keys and not self._unified:
            kwargs["rec_keys_path"] = rec_keys
        for artifact in (det_path, rec_path, rec_keys):
            if artifact:
                candidate = Path(artifact).expanduser()
                if not candidate.is_file() or candidate.stat().st_size <= 0:
                    raise ValueError(f"RapidOCR artifact is missing or empty: {artifact}")

        if not rec_path and self._language.casefold() in {"pt", "pt-br", "por"}:
            self._profile = "builtin-ch"
            raise ValueError(
                "RapidOCR's bundled Chinese recognizer is not compatible with pt-BR; "
                "configure a paired Latin recognizer and dictionary"
            )
        elif rec_path:
            profile, missing = portuguese_dictionary_profile(rec_keys)
            if self._language.casefold() in {"pt", "pt-br", "por"} and missing:
                raise ValueError(f"RapidOCR dictionary cannot represent Portuguese: missing {''.join(missing)}")
            self._profile = profile if self._language.lower().startswith("pt") else "custom-rec"
        else:
            self._profile = "custom-det"

        unclip    = env_float("RAPIDOCR_UNCLIP_RATIO", 1.8, minimum=0.0)
        box_thresh = env_float("RAPIDOCR_BOX_THRESH", 0.45, minimum=0.0, maximum=1.0)
        det_thresh = env_float("RAPIDOCR_DET_THRESH", 0.25, minimum=0.0, maximum=1.0)
        text_score = env_float("RAPIDOCR_TEXT_SCORE", 0.5, minimum=0.0, maximum=1.0)
        angle_cls  = os.environ.get("RAPIDOCR_ANGLE_CLS", "0").lower() in ("1", "true", "yes")
        self._effective_tuning = {
            "det_db_unclip_ratio": unclip,
            "det_db_box_thresh": box_thresh,
            "det_db_thresh": det_thresh,
            "text_score": text_score,
            "with_angle_cls": angle_cls,
        }

        tuning_kwargs: dict[str, Any] = {
            "det_db_unclip_ratio": unclip,
            "det_db_box_thresh":   box_thresh,
            "det_db_thresh":       det_thresh,
            "text_score":          text_score,
            "with_angle_cls":      angle_cls,
        }
        if self._unified:
            provider = {"Det.engine_type": runtime, "Cls.engine_type": runtime, "Rec.engine_type": runtime}
            requested_threads = config.num_threads
            effective_threads = max(1, os.cpu_count() or 1) if requested_threads == 0 else requested_threads
            if effective_threads > 0 and runtime == "onnxruntime":
                provider["EngineConfig.onnxruntime.intra_op_num_threads"] = effective_threads
            elif effective_threads > 0 and runtime == "openvino":
                provider["EngineConfig.openvino.inference_num_threads"] = effective_threads
            if det_path:
                provider["Det.model_path"] = det_path
            if rec_path:
                provider["Rec.model_path"] = rec_path
                provider["Rec.rec_keys_path"] = rec_keys
            provider.update({
                "Det.unclip_ratio": unclip,
                "Det.box_thresh": box_thresh,
                "Det.thresh": det_thresh,
            })
            self._engine = RapidOCR(params=provider)
        else:
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
        pkg_name = "rapidocr" if self._unified else (
            "rapidocr-onnxruntime" if self._runtime == "onnxruntime" else "rapidocr-openvino"
        )
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
            extra={
                "render_scale": self._config.effective_ocr_render_scale(),
                "clahe": True,
                **self._effective_tuning,
                "requested_threads": self._config.num_threads,
                "effective_threads": (
                    max(1, os.cpu_count() or 1) if self._config.num_threads == 0
                    else self._config.num_threads
                ),
                "rec_model": self._rec_model,
                "rec_keys":  self._rec_keys,
                "language_profile": self._profile,
                "det_model": self._det_model,
            },
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
        if self._closed:
            return OCRResult(
                status="runtime_error", tokens=(), text="", engine_identity=self.identity,
                elapsed_total_s=0.0, warnings=("RapidOCR backend is closed",),
            )
        try:
            img = _to_numpy(request.image)
            out = _run_rapidocr(self._engine, img)
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
        if self._closed:
            raise RuntimeError("RapidOCR backend is closed")
        img = _to_numpy(page_image)
        out = _run_rapidocr(self._engine, img)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        tokens = _result_to_pipeline_tokens(_extract_raw(out), page_index, self._language)
        return map_tokens_to_page(tokens, page_bbox, img.shape[1], img.shape[0])

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
        *,
        page_bbox: "BBox | None" = None,
    ) -> list[OcrToken]:
        if self._closed:
            raise RuntimeError("RapidOCR backend is closed")
        img = _to_numpy(page_image)
        from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
        crop, (cx0, cy0, _cx1, _cy1), (width, height) = crop_region_in_raster(img, region_bbox, page_bbox)
        if crop.size == 0:
            return []
        out = _run_rapidocr(self._engine, crop)
        tokens = _result_to_pipeline_tokens(
            _extract_raw(out), page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            source=SourceKind.OCR_REGION,
        )
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, width, height)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        try:
            _import_rapidocr(self._runtime)
        except ImportError:
            return "missing"
        except Exception:
            return "unknown"

        # Probe the engine with a minimal white image to verify the model
        # weights (bundled or custom) are loadable and inference runs.
        try:
            import numpy as np
            probe = np.full((32, 32, 3), 255, dtype=np.uint8)
            _run_rapidocr(self._engine, probe)
            return "ready"
        except Exception:
            return "unknown"

    def close(self) -> None:
        if self._closed:
            return
        engine = getattr(self, "_engine", None)
        close = getattr(engine, "close", None)
        if callable(close):
            close()
        self._engine = None
        self._closed = True


# Convenience aliases used by factory.py
RapidOCROnnxBackend = RapidOCRBackend
