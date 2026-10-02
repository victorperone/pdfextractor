"""EasyOCR backend — OCRBackend implementation using EasyOCR + PyTorch CPU.

EasyOCR downloads models on first use to ~/.EasyOCR/model/:
  - craft_mlt_25k.pth  (detection, ~41 MB)
  - latin_g2.pth       (recognition for Portuguese, ~666 MB)
  - latin_g1.pth       (alternative recognition model, larger, ~800 MB)

No GPU is used (gpu=False). Models are cached locally after first download.

Environment variables
---------------------
EASYOCR_MODULE_PATH        Path to model cache directory (default: ~/.EasyOCR/model/)
EASYOCR_RECOG_NETWORK      Recognition model name (default: '' → EasyOCR default = latin_g2)
                           Use 'latin_g1' for the older, larger model.
EASYOCR_ALLOW_DOWNLOAD     Set to '1' to allow model downloads during Reader init.
                           Default: '0' (download disabled). Use only during setup,
                           never during a measured benchmark run.
EASYOCR_DECODER            CTC decoder: 'greedy' or 'beamsearch' (default: 'greedy').
                           beamsearch reduces substitution errors but adds ~20-40%
                           inference time and can produce overflow warnings on some inputs.
EASYOCR_BEAMWIDTH          Beam width for beamsearch decoder (default: 5, min: 1).
                           Only used when EASYOCR_DECODER=beamsearch.
EASYOCR_WORKERS            DataLoader workers for recognition.
                           Priority: env var > config.num_threads > platform auto.
                           Platform auto: 0 on Windows (spawn safety), half of
                           cpu_count() capped at 4 on Linux/macOS.
                           When config.num_threads > 0, workers = min(4, threads // 2).
                           Set to 0 explicitly for the controlled benchmark track.
EASYOCR_ADJUST_CONTRAST    EasyOCR internal contrast multiplier for recognition crops.
                           Default: 0.5 (EasyOCR default).  Range: 0.0–1.0.
                           Higher values help very low-contrast scans but degrade
                           already-good pages — do not raise above 0.7.
EASYOCR_MAG_RATIO          Input magnification factor before CRAFT detection.
                           Default: 1.2.  Higher values improve small-text recall
                           but generate more false positives on dense pages.
EASYOCR_ALLOWLIST          Character allowlist applied to all recognition calls.
                           Example: '0123456789.,R$%()-/ '  for financial documents.
                           Default: unset (no restriction).
EASYOCR_BLOCKLIST          Character blocklist applied to all recognition calls.
                           Default: unset (no restriction).
                           Note: do NOT set 'OoIl' globally — 'o' and 'O' are common
                           Portuguese letters.  Use EASYOCR_ALLOWLIST instead for
                           digit-only deployments.
EASYOCR_ROTATION_INFO      Comma-separated rotation angles to try for line orientation
                           correction, e.g. '90,180,270'.  EasyOCR will recognise each
                           detected line at the original angle and at each listed angle,
                           keeping the result with highest confidence.  Default: unset
                           (None — no rotation, fastest).  Use '90,180,270' only when
                           the corpus contains lines rotated at arbitrary angles.
                           Adds roughly N× inference time per line where N = len(angles).

Optimization notes
------------------
- Detect/recognize split: mirrors EasyOCR's own readtext() implementation.
  reader.detect() returns aggregate lists (one entry per image); the first
  element [0] is extracted before passing to reader.recognize(), exactly as
  the upstream readtext() does.  reader.recognize() receives the grayscale
  image, matching the upstream contract.
  Falls back to readtext() on any failure, but marks the result as degraded
  so the benchmark can flag the run as partial.
- canvas_size: set to max(h, w) of the reformatted image so CRAFT never
  downscales the input.  This fixes detection loss on pages rendered at higher DPI.
- decoder: defaults to 'greedy' (upstream default). Use EASYOCR_DECODER=beamsearch
  to enable beam search after validating there is a measurable quality gain.
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


def _resolve_workers(config_num_threads: int) -> int:
    """Resolve effective DataLoader worker count.

    Priority (highest to lowest):
      1. EASYOCR_WORKERS env var — explicit override, any value.
      2. config.num_threads > 0 — derive workers proportionally
         (half of threads, capped at 4; 0 on Windows always).
      3. Platform auto-detect via _default_workers().

    Windows always returns 0 regardless of config or env var, because
    PyTorch 'spawn' requires the __main__ guard which is absent in
    subprocess / library contexts.
    """
    import platform
    is_windows = platform.system() == "Windows"

    env_val = os.environ.get("EASYOCR_WORKERS")
    if env_val is not None:
        return 0 if is_windows else max(0, int(env_val))

    if is_windows:
        return 0

    if config_num_threads > 0:
        return min(4, max(0, config_num_threads // 2))

    return _default_workers()


def _apply_torch_threads(num_threads: int) -> int:
    """Apply PyTorch intra/inter-op thread limits and return the effective count.

    Only sets torch threads when num_threads > 0 and torch is importable.
    Does not override values already set by the caller via torch directly.
    Returns the effective intra-op thread count (0 = unchanged/torch default).
    """
    if num_threads <= 0:
        return 0
    try:
        import torch
        torch.set_num_threads(num_threads)
        torch.set_num_interop_threads(max(1, num_threads // 2))
        return num_threads
    except Exception:
        return 0


def _model_cache_dir() -> "Path":
    """Resolve the effective EasyOCR model cache directory.

    Mirrors EasyOCR's own resolution: EASYOCR_MODULE_PATH env var if set,
    otherwise ~/.EasyOCR/model/.
    """
    from pathlib import Path
    module_path = os.environ.get("EASYOCR_MODULE_PATH")
    if module_path:
        return Path(module_path)
    return Path.home() / ".EasyOCR" / "model"


def _check_model_files(cache_dir: "Path", recog_network: str) -> list[str]:
    """Return a list of missing model file paths.

    EasyOCR requires two .pth files: the CRAFT detector and the recogniser.
    File names match EasyOCR's own naming convention.
    """
    from pathlib import Path
    detector = cache_dir / "craft_mlt_25k.pth"
    recogniser = cache_dir / f"{recog_network}.pth"
    missing = []
    if not detector.exists():
        missing.append(str(detector))
    if not recogniser.exists():
        missing.append(str(recogniser))
    return missing


_LANG_MAP: dict[str, list[str]] = {
    "pt": ["pt"],
    "por": ["pt"],
    "en": ["en"],
    "eng": ["en"],
}


def _parse_rotation_info(env_val: str) -> "list[int] | None":
    """Parse EASYOCR_ROTATION_INFO env var into a list of int angles or None.

    '90,180,270' → [90, 180, 270]
    '' or unset  → None (no rotation correction)
    """
    val = env_val.strip()
    if not val:
        return None
    try:
        return [int(a.strip()) for a in val.split(",") if a.strip()]
    except ValueError:
        return None


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



def _run_easyocr(
    reader: "Any",
    img: "Any",
    *,
    decoder: str,
    beamwidth: int,
    adjust_contrast: float,
    allowlist: "str | None",
    blocklist: "str | None",
    workers: int,
    rotation_info: "list[int] | None" = None,
) -> "tuple[list[Any], dict[str, Any] | None]":
    """Run EasyOCR using detect/recognize split mirroring upstream readtext().

    Returns (raw_results, fallback_info).  fallback_info is None on the nominal
    path; on the recovery path it contains diagnostic metadata so callers can
    mark the result as degraded.

    The detect/recognize split follows exactly what EasyOCR's own readtext()
    does internally:
      1. reformat_input() prepares colour + grayscale arrays.
      2. detect() is called with reformat=False; its aggregate output [0] is
         extracted for the single input image.
      3. recognize() receives the grayscale image, the unwrapped lists, and
         rotation_info (EasyOCR 1.7.2 supports this on the nominal path).

    Falls back to unified readtext() only on exception, with explicit logging.
    The fallback receives the same rotation_info so behaviour is consistent.
    """
    import numpy as np
    mag_ratio = float(os.environ.get("EASYOCR_MAG_RATIO", "1.2"))
    arr = np.asarray(img)

    # --- Prepare colour + grayscale arrays (mirrors upstream reformat_input) ---
    try:
        from easyocr.utils import reformat_input  # type: ignore
        img_color, img_gray = reformat_input(arr)
    except Exception:
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            reason="reformat_input_unavailable",
        )

    h, w = img_color.shape[:2]
    canvas_size = max(h, w)

    # --- Stage 1: text detection (CRAFT) ---
    try:
        horizontal_agg, free_agg = reader.detect(
            img_color,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
            reformat=False,
        )
        # detect() returns one entry per image in the batch; unwrap for our
        # single image — this is what EasyOCR's own readtext() does.
        horizontal_list = horizontal_agg[0]
        free_list = free_agg[0]
    except Exception as exc:
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            reason=f"detect_failed: {type(exc).__name__}: {exc}",
        )

    # --- Stage 2: recognition (CRNN + CTC) ---
    try:
        result = reader.recognize(
            img_gray,
            horizontal_list,
            free_list,
            decoder=decoder,
            beamWidth=beamwidth,
            workers=workers,
            allowlist=allowlist,
            blocklist=blocklist,
            detail=1,
            paragraph=False,
            adjust_contrast=adjust_contrast,
            rotation_info=rotation_info,
            reformat=False,
        )
        return result, None  # nominal path — no fallback
    except Exception as exc:
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            reason=f"recognize_failed: {type(exc).__name__}: {exc}",
        )


def _fallback_readtext(
    reader: "Any",
    arr: "Any",
    *,
    decoder: str,
    beamwidth: int,
    adjust_contrast: float,
    allowlist: "str | None",
    blocklist: "str | None",
    workers: int,
    rotation_info: "list[int] | None",
    reason: str,
) -> "tuple[list[Any], dict[str, Any]]":
    """Recover via unified readtext() and return explicit fallback metadata.

    readtext() re-runs its own detect() internally, so cost and path differ
    from the nominal split.  Callers must surface this as a degraded result.
    The same rotation_info as the nominal path is forwarded so behaviour is
    consistent between the two paths.
    """
    import warnings as _warnings
    mag_ratio = float(os.environ.get("EASYOCR_MAG_RATIO", "1.2"))
    h, w = arr.shape[:2]
    canvas_size = max(h, w)

    fallback_info: "dict[str, Any]" = {
        "fallback_used": True,
        "fallback_backend_path": "easyocr.readtext",
        "primary_error": reason,
        "ocr_outcome": "recovered",
    }

    _warnings.warn(
        f"EasyOCR: falling back to readtext() — {reason}",
        RuntimeWarning,
        stacklevel=4,
    )

    try:
        result = reader.readtext(
            arr,
            decoder=decoder,
            beamWidth=beamwidth,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
            rotation_info=rotation_info,
        )
        return result, fallback_info
    except Exception as exc2:
        raise RuntimeError(
            f"EasyOCR: readtext() also failed after primary failure ({reason}): {exc2}"
        ) from exc2


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

        # --- env-var + config-derived configuration ---
        raw_decoder = os.environ.get("EASYOCR_DECODER", "greedy").strip().lower()
        self._decoder: str = raw_decoder if raw_decoder in ("greedy", "beamsearch") else "greedy"
        self._beamwidth = max(1, int(os.environ.get("EASYOCR_BEAMWIDTH", "5")))
        self._workers = _resolve_workers(config.num_threads)
        self._adjust_contrast = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
        allowlist_env = os.environ.get("EASYOCR_ALLOWLIST", "")
        self._allowlist: str | None = allowlist_env if allowlist_env else None
        blocklist_env = os.environ.get("EASYOCR_BLOCKLIST", "")
        self._blocklist: str | None = blocklist_env if blocklist_env else None
        rotation_info_env = os.environ.get("EASYOCR_ROTATION_INFO", "")
        self._rotation_info: list[int] | None = _parse_rotation_info(rotation_info_env)

        # Apply PyTorch thread limits before the Reader (and its model loading)
        # initialises, so all inference calls inherit the constrained thread pool.
        self._torch_num_threads = _apply_torch_threads(config.num_threads)

        # --- reader init ---
        easyocr_mod = _import_easyocr()
        module_path = os.environ.get("EASYOCR_MODULE_PATH")
        recog_network = os.environ.get("EASYOCR_RECOG_NETWORK", "")
        self._recog_network = recog_network or "latin_g2"
        self._model_cache_dir = _model_cache_dir()

        # Download is disabled by default so a benchmark run never touches the
        # network. Set EASYOCR_ALLOW_DOWNLOAD=1 only during the setup phase.
        allow_download = os.environ.get("EASYOCR_ALLOW_DOWNLOAD", "0") == "1"

        kwargs: dict[str, Any] = {
            "gpu": False,
            "verbose": False,
            "download_enabled": allow_download,
        }
        if module_path:
            kwargs["model_storage_directory"] = module_path
        if recog_network:
            kwargs["recog_network"] = recog_network

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
            extra={
                "decoder": self._decoder,
                "beamwidth": self._beamwidth,
                "workers": self._workers,
                "torch_num_threads": self._torch_num_threads,
                "adjust_contrast": self._adjust_contrast,
                "rotation_info": self._rotation_info,
            },
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
            raw, fallback_info = _run_easyocr(
                self._reader,
                img,
                decoder=self._decoder,
                beamwidth=self._beamwidth,
                adjust_contrast=self._adjust_contrast,
                allowlist=self._allowlist,
                blocklist=self._blocklist,
                workers=self._workers,
                rotation_info=self._rotation_info,
            )
            tokens = tuple(_result_to_ocr_tokens(raw, "easyocr"))
            text = " ".join(t.text for t in tokens)
            if fallback_info is not None:
                status = "recovered"
                extra_warnings: tuple[str, ...] = (
                    f"easyocr_fallback: {fallback_info.get('primary_error', 'unknown')}",
                )
            else:
                status = "ok" if tokens else "no_text"
                extra_warnings = ()
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
            warnings=extra_warnings,
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
        raw, _fallback = _run_easyocr(
            self._reader,
            img,
            decoder=self._decoder,
            beamwidth=self._beamwidth,
            adjust_contrast=self._adjust_contrast,
            allowlist=self._allowlist,
            blocklist=self._blocklist,
            workers=self._workers,
            rotation_info=self._rotation_info,
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
        raw, _fallback = _run_easyocr(
            self._reader,
            crop,
            decoder=self._decoder,
            beamwidth=self._beamwidth,
            adjust_contrast=self._adjust_contrast,
            allowlist=self._allowlist,
            blocklist=self._blocklist,
            workers=self._workers,
            rotation_info=self._rotation_info,
        )
        return _result_to_pipeline_tokens(
            raw, page_index, self._language,
            offset_x=float(x0), offset_y=float(y0),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        """Check that easyocr is importable and required model files exist on disk.

        Returns one of: "ready", "missing" (package not installed),
        "model_missing" (package OK but .pth files absent), "unknown" (error).
        """
        try:
            _import_easyocr()
        except ImportError:
            return "missing"
        except Exception:
            return "unknown"

        cache_dir = getattr(self, "_model_cache_dir", None) or _model_cache_dir()
        recog = getattr(self, "_recog_network", "latin_g2")
        missing_files = _check_model_files(cache_dir, recog)
        if missing_files:
            return "model_missing"
        return "ready"

    def close(self) -> None:
        pass
