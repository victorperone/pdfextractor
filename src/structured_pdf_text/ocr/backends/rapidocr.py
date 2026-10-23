"""RapidOCR backend with ONNX Runtime and OpenVINO inference providers.

Both providers use the unified ``rapidocr`` package (a single import, no
provider-specific packages).  ONNX Runtime and OpenVINO are selected at
instantiation time via the ``runtime`` parameter.

CF-2: Direct PP-OCRv6 ONNX export from Paddle is blocked on Windows by a DLL
incompatibility (paddle2onnx 2.x + PaddlePaddle 3.3.1).  PP-OCRv6 ONNX files
must be exported on Linux and then pointed to via the env vars below.

The project selects PP-OCRv6 medium by default and small explicitly. Detector,
recognizer, dictionary and the disabled PP-OCRv4 classifier are pinned and
verified before use. Extraction never downloads missing models. Legacy v5
environment overrides are rejected instead of being presented as v6.

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
import logging
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import finite_confidence, quadrilateral_geometry, sha256_file
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)


PORTUGUESE_REQUIRED_CHARS = frozenset("ãõçêáéíóú")

# cuDNN convolution algorithm selection for the CUDA provider. "DEFAULT" searched
# again for every new input shape: the 001 campaign logged 52.712 "Fallback mode"
# warnings on V4 and detection ran about 5x slower in isolation (3,0 s against
# 0,6 s for six shapes). HEURISTIC emits no warnings and is repeatable, but its
# kernels round differently: on 18 sampled pages 15 produced identical text and
# the other 3 differed only at detection borders (one punctuation mark and one
# spurious fragment on noise pages). Captures under the old value stay comparable
# only through their recorded identity.
_CUDNN_CONV_ALGO_SEARCH = "HEURISTIC"


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


def _rapidocr_v6_language(language: str) -> str:
    """Normalize a requested language to the PP-OCRv6 route vocabulary."""
    normalized = language.casefold().replace("_", "-").split("-", 1)[0]
    aliases = {"por": "pt", "pt": "pt"}
    normalized = aliases.get(normalized, normalized)
    supported = {
        "af", "az", "bs", "ca", "cs", "cy", "da", "de", "en", "es", "et", "eu",
        "fi", "fr", "ga", "gl", "hr", "hu", "id", "is", "it", "ku", "la", "lb",
        "lt", "lv", "mi", "ms", "mt", "nl", "no", "oc", "pl", "pt", "qu", "rm",
        "ro", "rs_latin", "sk", "sl", "sq", "sv", "sw", "tl", "tr", "uz", "vi",
        "french", "german",
    }
    if normalized not in supported:
        raise ValueError(f"RapidOCR PP-OCRv6 does not support language {language!r}")
    return normalized


def _rapidocr_config_types():
    """Load API enums only with the selected RapidOCR runtime."""
    from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
    return EngineType, ModelType, OCRVersion


def _run_rapidocr(engine: Any, img: Any) -> Any:
    """Run RapidOCR with CLAHE (LAB L-channel) preprocessing."""
    import numpy as np
    arr = np.asarray(img)
    enhanced = _clahe_preprocess(arr)
    return engine(enhanced)


def _extract_raw(out: Any) -> Any:
    if isinstance(out, tuple) and len(out) >= 1:
        return out[0]
    # RapidOCR 3.x returns a RapidOCROutput dataclass rather than the legacy
    # list of ``(box, text, score)`` rows. Normalize its parallel fields to the
    # representation consumed by the coordinate/token parsers below.
    boxes = getattr(out, "boxes", None)
    texts = getattr(out, "txts", None)
    scores = getattr(out, "scores", None)
    if boxes is not None and texts is not None:
        score_values = scores if scores is not None else [None] * len(texts)
        return list(zip(boxes, texts, score_values))
    if hasattr(out, "__iter__") and not isinstance(out, (str, bytes)):
        return out
    return None


def _quarter_turn_to_source(points, quarter_turns: int, height: int, width: int) -> list[list[float]]:
    """Map polygon points from ``np.rot90(image, quarter_turns)`` to the source image.

    ``height`` and ``width`` are the dimensions of the unrotated source image.
    """
    mapped = []
    for x, y in points:
        if quarter_turns == 1:
            x, y = width - y, x
        elif quarter_turns == 2:
            x, y = width - x, height - y
        elif quarter_turns == 3:
            x, y = y, height - x
        mapped.append([float(x), float(y)])
    return mapped


def _rows_to_source(rows, quarter_turns: int, height: int, width: int) -> list:
    mapped = []
    for box, text, score in rows:
        points = [(float(point[0]), float(point[1])) for point in box]
        mapped.append((_quarter_turn_to_source(points, quarter_turns, height, width), text, score))
    return mapped


def _reading_evidence(rows) -> tuple[float, float]:
    """Return (mean confidence, confidence-weighted characters) for non-empty rows."""
    confidences = []
    weighted = 0.0
    for row in rows or ():
        text = str(row[1]).strip()
        if not text:
            continue
        confidence = finite_confidence(row[2]) if len(row) > 2 else None
        confidence = 0.0 if confidence is None else confidence
        confidences.append(confidence)
        weighted += len(text) * confidence
    if not confidences:
        return 0.0, 0.0
    return sum(confidences) / len(confidences), weighted


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
        self._runtime = runtime
        self._engine_key = "rapidocr"
        self._device = "cpu"
        self._gpu_fallback_reason = None
        self._active_providers = {}
        self._cuda_enabled = False

        from structured_pdf_text.ocr.pipelines.rapidocr.models import (
            PROFILES,
            RAPIDOCR_MANIFEST_REVISION,
            selected_paths,
            verify_artifacts,
        )

        settings = getattr(config, "ocr_settings", None)
        if settings is None:
            from structured_pdf_text.ocr.pipelines.rapidocr.settings import resolve_settings
            from structured_pdf_text.ocr.registry import get_entry
            settings = resolve_settings(config, get_entry(config.ocr_engine))
        if settings.selection.engine != "rapidocr" or settings.selection.provider != runtime:
            raise ValueError("RapidOCR settings do not match the selected engine/provider")
        self._language = settings.language
        size = settings.model_size
        paths = (settings.detector_path, settings.recognizer_path, settings.dictionary_path, settings.classifier_path)
        if any(path is None for path in paths):
            raise ValueError("RapidOCR requires detector, recognizer, dictionary and classifier paths")
        det_path, rec_path, rec_keys, cls_path = (str(Path(value).expanduser()) for value in paths)
        hashes = verify_artifacts(size, paths)
        # Validate the exact offline artifact set before importing the runtime.
        RapidOCR = _import_rapidocr(runtime)
        self._unified = getattr(RapidOCR, "__module__", "").split(".")[0] == "rapidocr"
        profile = PROFILES[size]
        self._profile = profile.profile
        self._model_size = size
        self._manifest_revision = RAPIDOCR_MANIFEST_REVISION
        self._effective_models = {
            "detector": profile.detector.filename,
            "recognizer": profile.recognizer.filename,
            "dictionary": profile.dictionary.filename,
            "classifier": f"{profile.classifier.filename} (disabled during inference; PP-OCRv4)",
            "classifier_inference": False,
        }

        unclip = settings.unclip_ratio
        box_thresh = settings.box_threshold
        det_thresh = settings.detector_threshold
        text_score = settings.text_score
        from structured_pdf_text.ocr.pipelines.rapidocr.policy import ORIENTATION_POLICY
        self._orientation_policy = ORIENTATION_POLICY
        self._orientation_selected = {}
        self._effective_tuning = {
            "det_db_unclip_ratio": unclip,
            "det_db_box_thresh": box_thresh,
            "det_db_thresh": det_thresh,
            "text_score": text_score,
            "orientation_classifier_enabled": False,
            "orientation_search": self._orientation_policy.identity(),
            "cudnn_conv_algo_search": _CUDNN_CONV_ALGO_SEARCH,
        }
        if self._unified:
            # RapidOCR 3.x validates enum-typed parameters before applying
            # overrides. Passing the provider name as a plain string raises
            # TypeError during backend construction, so use the package's
            # EngineType enum for all three stages.
            EngineType, ModelType, OCRVersion = _rapidocr_config_types()
            engine_type = EngineType(runtime)
            model_type = ModelType(size)
            ocr_version = OCRVersion.PPOCRV6
            language = _rapidocr_v6_language(self._language)
            provider = {
                "Det.engine_type": engine_type,
                "Rec.engine_type": engine_type,
                "Global.use_cls": False,
                "Global.model_root_dir": str(Path(det_path).parent),
                "Det.model_type": model_type,
                "Det.ocr_version": ocr_version,
                "Det.lang_type": "multi",
                "Det.model_path": det_path,
                "Global.text_score": text_score,
                "Cls.engine_type": engine_type,
                "Cls.model_path": cls_path,
                "Cls.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": model_type,
                "Rec.ocr_version": ocr_version,
                "Rec.lang_type": language,
                "Rec.model_path": rec_path,
                "Rec.rec_keys_path": rec_keys,
            }
            requested_threads = settings.num_threads
            effective_threads = max(1, os.cpu_count() or 1) if requested_threads == 0 else requested_threads
            if effective_threads > 0 and runtime == "onnxruntime":
                provider["EngineConfig.onnxruntime.intra_op_num_threads"] = effective_threads
            elif effective_threads > 0 and runtime == "openvino":
                provider["EngineConfig.openvino.inference_num_threads"] = effective_threads
            cuda = False
            if settings.use_gpu and runtime == "onnxruntime":
                try:
                    import onnxruntime as ort
                    # CUDA/cuDNN come from this environment's NVIDIA wheels.
                    preload = getattr(ort, "preload_dlls", None)
                    if callable(preload):
                        preload(directory="")
                    cuda = "CUDAExecutionProvider" in ort.get_available_providers()
                    if not cuda:
                        self._gpu_fallback_reason = "cuda_provider_unavailable"
                except Exception as exc:
                    from structured_pdf_text.errors import raise_if_resource_exhausted
                    raise_if_resource_exhausted(exc, stage="rapidocr_device_probe")
                    self._gpu_fallback_reason = f"cuda_probe_failed:{type(exc).__name__}"
            elif settings.use_gpu:
                self._gpu_fallback_reason = "openvino_profile_cpu_only"
            if runtime == "onnxruntime":
                provider["EngineConfig.onnxruntime.use_cuda"] = cuda
                if cuda:
                    provider["EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search"] = _CUDNN_CONV_ALGO_SEARCH
            provider.update({
                "Det.unclip_ratio": unclip,
                "Det.box_thresh": box_thresh,
                "Det.thresh": det_thresh,
            })
            self._cuda_enabled = cuda
            self._provider_options = dict(provider)
            self._runtime_constructor = RapidOCR
            try:
                self._engine = RapidOCR(params=provider)
            except Exception as exc:
                from structured_pdf_text.ocr.device import gpu_configuration_failure
                if not cuda or not gpu_configuration_failure(exc):
                    raise
                self._gpu_fallback_reason = f"cuda_initialization_failed:{type(exc).__name__}:{exc}"
                logging.getLogger(__name__).warning("RapidOCR: CUDA initialization failed; using CPU (%s)", exc)
                cuda = self._cuda_enabled = False
                self._provider_options["EngineConfig.onnxruntime.use_cuda"] = False
                self._engine = RapidOCR(params=self._provider_options)
            if runtime == "onnxruntime":
                self._refresh_execution_device()
                if cuda and self._device != "cuda":
                    logging.getLogger(__name__).warning("RapidOCR: CUDA not active in all stages; using CPU")
                    cuda = self._cuda_enabled = False
                    self._provider_options["EngineConfig.onnxruntime.use_cuda"] = False
                    self._engine = RapidOCR(params=self._provider_options)
                    self._refresh_execution_device()
                for attribute in ("text_det", "text_rec"):
                    session = getattr(getattr(getattr(self._engine, attribute, None), "session", None), "session", None)
                    # ORT's run() can otherwise retry EP failures on CPU,
                    # hiding a CUDA failure (including memory exhaustion).
                    disable_fallback = getattr(session, "disable_fallback", None)
                    if cuda and callable(disable_fallback):
                        disable_fallback()
                self._refresh_execution_device()
            if settings.use_gpu and self._device != "cuda":
                logging.getLogger(__name__).warning(
                    "RapidOCR: CUDA requested; effective device=%s (%s)", self._device, self._gpu_fallback_reason)
        else:
            raise RuntimeError(
                "RapidOCR PP-OCRv6 profiles require the unified rapidocr 3.9.2 API; "
                "the legacy provider package cannot represent the selected model identity."
            )

        self._det_model = det_path
        self._rec_model = rec_path
        self._rec_keys  = rec_keys
        self._cls_model = cls_path

        # Hash every custom artefact provided via env vars individually so the
        # manifest can prove exactly which files were used.  Bundled models live
        # inside the package wheel and are not addressable as ordinary paths.
        self._artifact_hashes = {
            "det_model": hashes["detector"],
            "rec_model": hashes["recognizer"],
            "rec_keys": hashes["dictionary"],
            "cls_model": hashes["classifier"],
        }

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    def _infer(self, image):
        try:
            return _run_rapidocr(self._engine, image)
        except Exception as exc:
            from structured_pdf_text.ocr.device import gpu_configuration_failure
            if not self._cuda_enabled or not gpu_configuration_failure(exc):
                raise
            logging.getLogger(__name__).warning("RapidOCR: CUDA unavailable during inference; retrying on CPU (%s)", exc)
            self._provider_options["EngineConfig.onnxruntime.use_cuda"] = False
            self._engine = self._runtime_constructor(params=self._provider_options)
            self._cuda_enabled = False
            self._gpu_fallback_reason = f"cuda_runtime_failed:{type(exc).__name__}:{exc}"
            self._refresh_execution_device()
            return _run_rapidocr(self._engine, image)

    def _recognize_rows(self, image) -> list:
        """Return raw rows in the source image frame, searching turns only when needed.

        The upright pass runs first. A weak reading (mean confidence below the
        policy floor) is compared with the policy's quarter turns; an alternative
        replaces the upright reading only if it is confident and clearly reads
        more text. Alternative rows are mapped back to the source frame before
        they reach the token parsers, so callers still receive crop coordinates.
        """
        import numpy as np
        rows = _extract_raw(self._infer(image)) or []
        policy = self._orientation_policy
        upright_mean, upright_score = _reading_evidence(rows)
        if not rows or upright_mean >= policy.confidence_floor:
            return rows
        height, width = image.shape[0], image.shape[1]
        best_rows, best_turn, best_score = rows, 0, upright_score
        for quarter_turns in policy.alternatives:
            turned = np.ascontiguousarray(np.rot90(image, quarter_turns))
            candidate = _extract_raw(self._infer(turned)) or []
            mean, score = _reading_evidence(candidate)
            if mean < policy.confidence_floor or score < policy.minimum_gain * upright_score:
                continue
            if score > best_score:
                best_rows = _rows_to_source(candidate, quarter_turns, height, width)
                best_turn, best_score = quarter_turns, score
        if best_turn:
            self._orientation_selected[best_turn] = self._orientation_selected.get(best_turn, 0) + 1
            logging.getLogger(__name__).info(
                "RapidOCR orientation search selected %d quarter turn(s): upright_mean=%.3f upright_weighted=%.0f selected_weighted=%.0f",
                best_turn, upright_mean, upright_score, best_score,
            )
        return best_rows

    def _refresh_execution_device(self) -> None:
        """Inspect live sessions; a construction-time provider list can go stale."""
        if self._runtime != "onnxruntime" or self._closed or self._engine is None:
            return
        providers = {}
        for stage, attribute in (("detector", "text_det"), ("recognizer", "text_rec")):
            session = getattr(getattr(getattr(self._engine, attribute, None), "session", None), "session", None)
            if session is not None and callable(getattr(session, "get_providers", None)):
                providers[stage] = list(session.get_providers())
        self._active_providers = providers
        count = sum("CUDAExecutionProvider" in active for active in providers.values())
        self._device = "cuda" if count == 2 else "mixed" if count else "cpu"
        if self._cuda_enabled and self._device != "cuda":
            self._gpu_fallback_reason = "cuda_provider_not_active_in_all_stages"
        elif self._device == "cuda" and self._gpu_fallback_reason == "cuda_provider_not_active_in_all_stages":
            self._gpu_fallback_reason = None

    @property
    def identity(self) -> OCRBackendIdentity:
        """Return live device identity, package/model hashes and tuning parameters."""
        previous = self._device
        self._refresh_execution_device()
        if self._config.use_gpu and self._device != previous and self._device != "cuda":
            logging.getLogger(__name__).warning(
                "RapidOCR: runtime device changed to %s (%s)", self._device, self._gpu_fallback_reason)
        pkg_name = "rapidocr" if self._unified else (
            "rapidocr-onnxruntime" if self._runtime == "onnxruntime" else "rapidocr-openvino"
        )
        runtime_pkg = "onnxruntime" if self._runtime == "onnxruntime" else "openvino"
        if runtime_pkg == "onnxruntime" and _package_version("onnxruntime-gpu") != "unknown":
            runtime_pkg = "onnxruntime-gpu"
        return OCRBackendIdentity(
            engine=self._engine_key,
            runtime=self._runtime,
            profile=self._profile,
            language=self._language,
            device=self._device,
            package_versions={
                pkg_name: _package_version(pkg_name),
                runtime_pkg: _package_version(runtime_pkg),
            },
            artifact_hashes=self._artifact_hashes,
            extra={
                "render_scale": self._config.effective_ocr_render_scale(),
                "gpu_requested": self._config.use_gpu,
                "gpu_fallback_reason": self._gpu_fallback_reason,
                "active_providers": self._active_providers,
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
                "model_size": self._model_size,
                "model_profile": self._profile,
                "model_manifest": self._manifest_revision,
                "models": self._effective_models,
                "classifier_model": self._cls_model,
                "classifier_inference": False,
                "effective_tuning": self._effective_tuning,
            },
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        """Return static capability flags for this backend."""
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
        """Run CLAHE-enhanced RapidOCR inference and return a canonical OCRResult."""
        t0 = time.perf_counter()
        if self._closed:
            return OCRResult(
                status="runtime_error", tokens=(), text="", engine_identity=self.identity,
                elapsed_total_s=0.0, warnings=("RapidOCR backend is closed",),
            )
        try:
            img = _to_numpy(request.image)
            raw = self._recognize_rows(img)
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
        page_rotation: int = 0,
    ) -> list[OcrToken]:
        """Run OCR on a full page image and return pipeline-format tokens."""
        if self._closed:
            raise RuntimeError("RapidOCR backend is closed")
        img = _to_numpy(page_image)
        rows = self._recognize_rows(img)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        tokens = _result_to_pipeline_tokens(rows, page_index, self._language)
        return map_tokens_to_page(tokens, page_bbox, img.shape[1], img.shape[0], page_rotation)

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
        *,
        page_bbox: "BBox | None" = None,
    ) -> list[OcrToken]:
        """Crop region_bbox from page_image, run OCR, and return pipeline-format tokens."""
        if self._closed:
            raise RuntimeError("RapidOCR backend is closed")
        img = _to_numpy(page_image)
        from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
        crop, (cx0, cy0, _cx1, _cy1), (width, height) = crop_region_in_raster(img, region_bbox, page_bbox)
        if crop.size == 0:
            return []
        rows = self._recognize_rows(crop)
        tokens = _result_to_pipeline_tokens(
            rows, page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            source=SourceKind.OCR_REGION,
        )
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, width, height)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        """Return 'ready', 'missing', or 'unknown' after probing the engine with a blank image."""
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
            self._infer(probe)
            return "ready"
        except Exception:
            return "unknown"

    def close(self) -> None:
        """Release the RapidOCR engine and mark this backend as closed."""
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


def build_backend(config):
    """Lazy registry constructor for the selected RapidOCR provider."""
    return RapidOCRBackend(config, runtime=config.ocr_provider or "onnxruntime")
