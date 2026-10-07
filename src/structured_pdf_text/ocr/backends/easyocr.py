"""EasyOCR backend — OCRBackend implementation using EasyOCR + PyTorch CPU.

EasyOCR downloads models on first use to ~/.cache/pdfextractor/easyocr/:
  - craft_mlt_25k.pth  (detection, ~83 MB)
  - latin_g2.pth       (recognition for Portuguese, ~15 MB)

No GPU is used (gpu=False). Models are cached locally after first download.
The Reader always receives model_storage_directory pointing to the project
cache directory so the readiness probe and the inference runtime always check
the same location, regardless of whether EASYOCR_MODULE_PATH is set.

Environment variables
---------------------
EASYOCR_MODULE_PATH        Override path to model cache directory.
                           Default: ~/.cache/pdfextractor/easyocr/
EASYOCR_RECOG_NETWORK      Recognition model name (default: '' → EasyOCR default = latin_g2)
                           Use 'latin_g1' for the older, larger model.
EASYOCR_ALLOW_DOWNLOAD     Set to '1' to allow model downloads during Reader init.
                           Default: '0' (download disabled). Use only during setup,
                           never during a measured benchmark run.
EASYOCR_DECODER            CTC decoder: 'greedy', 'beamsearch', or 'wordbeamsearch'
                           (default: 'greedy').  beamsearch reduces substitution errors
                           but adds ~20-40% inference time and can produce overflow
                           warnings on some inputs.  wordbeamsearch adds vocabulary-level
                           beam search, best for pt-BR prose; slightly slower than
                           beamsearch.
EASYOCR_BEAMWIDTH          Beam width for beamsearch decoder (default: 5, min: 1).
                           Only used when EASYOCR_DECODER=beamsearch.
EASYOCR_WORKERS            DataLoader workers for recognition (default: 0).
                           CPU recognition creates a loader for each detected box;
                           process startup costs more than loading that one crop.
                           num_threads controls Torch inference, not crop loading.
                           Explicit env overrides are honoured outside Windows.
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

Detector parameters (CRAFT text region proposal)
-------------------------------------------------
EASYOCR_TEXT_THRESHOLD     CRAFT character region score threshold (default: 0.7).
                           Lower values increase recall on faint/small text at the
                           cost of more false positives.  Range: 0.01–1.0.
EASYOCR_LOW_TEXT           CRAFT link/affinity threshold for character grouping
                           (default: 0.4).  Lower values join characters more
                           aggressively into longer words/lines.
EASYOCR_LINK_THRESHOLD     CRAFT character-link score threshold (default: 0.4).
                           Controls how adjacent characters are connected into words.
EASYOCR_MIN_SIZE           Minimum character region size in pixels (default: 20).
                           Increase to filter tiny noise boxes; decrease for very
                           small fonts (e.g. footnotes, table headers).

Box grouping and merging parameters
------------------------------------
EASYOCR_SLOPE_THS          Maximum slope difference (radians) to merge boxes into
                           one text line (default: 0.1).
EASYOCR_YCENTER_THS        Maximum vertical-centre distance (fraction of box height)
                           to merge boxes into one line (default: 0.5).
EASYOCR_HEIGHT_THS         Maximum height difference (fraction of taller box) to
                           merge boxes into one line (default: 0.5).
EASYOCR_WIDTH_THS          Maximum horizontal gap (fraction of box width) to merge
                           horizontally adjacent boxes (default: 0.5).
                           Reduce to ~0.2–0.35 for multi-column layouts and tables
                           to prevent merging across gutters.
EASYOCR_ADD_MARGIN         Extra margin added around detected boxes before
                           recognition (fraction of box size, default: 0.1).

Recognizer parameters (CRNN + CTC)
------------------------------------
EASYOCR_CONTRAST_THS       Minimum contrast threshold for recognition crops.
                           Crops below this threshold receive a second pass with
                           contrast adjustment applied (default: 0.1).
                           Range: 0.0–1.0.
EASYOCR_FILTER_THS         Minimum pixel-value filter threshold inside crops
                           (default: 0.003).  Very small values retain almost all
                           pixels; raise slightly to suppress low-level noise.
EASYOCR_QUANTIZE           Set to '0' to disable PyTorch model quantization.
                           Default: '1' (quantization enabled by EasyOCR).
                           Disabling may improve accuracy on borderline characters
                           at the cost of higher CPU usage.  A/B test before
                           changing in production.
                           In exhaustive mode a no_quantize candidate is also run
                           even when this is '1' so the effect can be observed.
EASYOCR_MAX_QUALITY_THREADS
                           Set to '1' to enable maximum-quality thread allocation.
                           When active, the backend probes available CPUs at
                           startup and allocates all logical cores to PyTorch
                           inference (intra-op threads) and up to 16 DataLoader
                           workers for recognition.  Intended for dedicated
                           benchmark machines where the process has exclusive
                           access to the CPU. Default: '0' (zero loader workers).
                           When enabling this legacy opt-in, set EASYOCR_WORKERS=0
                           to avoid per-box multiprocessing overhead on CPU.

Optimization notes
------------------
- Detect/recognize split: mirrors EasyOCR's own readtext() implementation.
  reader.detect() returns aggregate lists (one entry per image); the first
  element [0] is extracted before passing to reader.recognize(), exactly as
  the upstream readtext() does.  reader.recognize() receives the grayscale
  image, matching the upstream contract.
  Falls back to readtext() on any failure, but marks the result as degraded
  so the benchmark can flag the run as partial.
- canvas_size: CRAFT uses int(mag_ratio * max(h, w)) so the canvas is always large
  enough for the magnified image.  Using max(h, w) would clamp target_size back
  to max(h, w) inside resize_aspect_ratio(), neutralising mag_ratio entirely.
  DBNet18 instead sets its short side to canvas_size, so it uses min(h, w).
- decoder: defaults to 'greedy' (upstream default). Use EASYOCR_DECODER=beamsearch
  to enable beam search after validating there is a measurable quality gain.
"""
from __future__ import annotations

import os
import time
from copy import deepcopy
from hashlib import blake2b
from typing import TYPE_CHECKING, Any

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import finite_confidence, quadrilateral_geometry, sha256_file
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from structured_pdf_text.config import ExtractorConfig


def _probe_environment() -> "dict[str, Any]":
    """Detect available CPU resources at runtime for adaptive thread allocation.

    Collects:
      cpu_count_logical  — total logical CPUs (os.cpu_count())
      cpu_count_physical — physical cores when psutil is available; else None
      cpu_load_1m        — 1-minute load average (Linux/macOS); else None
      platform           — 'linux', 'darwin', or 'windows'
      is_windows         — True on Windows (affects multiprocessing safety)
      max_quality_env    — True when EASYOCR_MAX_QUALITY_THREADS=1

    Used by _resolve_workers() and _apply_torch_threads() to decide whether
    to use all available cores or keep conservative defaults.
    """
    import platform as _platform
    system = _platform.system().lower()
    is_win = system == "windows"

    logical = os.cpu_count() or 1
    physical: "int | None" = None
    try:
        import psutil  # type: ignore
        physical = psutil.cpu_count(logical=False) or logical
    except Exception:
        physical = None

    load_1m: "float | None" = None
    if not is_win:
        try:
            load_1m = os.getloadavg()[0]
        except (AttributeError, OSError):
            pass

    max_quality_env = os.environ.get("EASYOCR_MAX_QUALITY_THREADS", "0").strip() == "1"

    return {
        "cpu_count_logical": logical,
        "cpu_count_physical": physical,
        "cpu_load_1m": load_1m,
        "platform": system,
        "is_windows": is_win,
        "max_quality_env": max_quality_env,
    }


def _default_workers(env_probe: "dict[str, Any] | None" = None) -> int:
    """Return a safe default for EasyOCR DataLoader worker count.

    On Windows, PyTorch uses 'spawn' for multiprocessing, which requires
    the __main__ guard and causes deadlocks in subprocess contexts like
    our paddle_subprocess worker.  Zero is the only safe default there.
    CPU EasyOCR recognizes one box at a time, constructing a DataLoader for
    each box (and another for low-contrast retries). Multiple workers spawn
    processes repeatedly for datasets of length one. Keep the default at zero;
    the legacy max-quality environment opt-in remains an explicit override.
    """
    probe = env_probe or _probe_environment()
    if probe["is_windows"]:
        return 0
    logical = probe["cpu_count_logical"]
    if probe["max_quality_env"]:
        # All logical CPUs available, capped only by a generous safety ceiling
        # so a 128-core server does not spawn 128 DataLoader processes.
        return min(logical, 16)
    return 0


def _resolve_workers(config_num_threads: int, env_probe: "dict[str, Any] | None" = None) -> int:
    """Resolve effective DataLoader worker count.

    Priority (highest to lowest):
      1. EASYOCR_WORKERS env var — explicit override, any value.
      2. config.num_threads > 0 — inference threads are separate from loading.
         Conservative mode: zero loader workers.
         Max-quality mode (EASYOCR_MAX_QUALITY_THREADS=1): uses full count.
      3. Platform auto-detect via _default_workers().

    Windows always returns 0 regardless of config or env var, because
    PyTorch 'spawn' requires the __main__ guard which is absent in
    subprocess / library contexts.
    """
    probe = env_probe or _probe_environment()
    is_windows = probe["is_windows"]

    env_val = os.environ.get("EASYOCR_WORKERS")
    if env_val is not None:
        from structured_pdf_text.ocr.env import env_int
        return 0 if is_windows else env_int("EASYOCR_WORKERS", 0, minimum=0)

    if is_windows:
        return 0

    if config_num_threads > 0:
        if probe["max_quality_env"]:
            return min(config_num_threads, 16)
        return 0

    return _default_workers(probe)


def _apply_torch_threads(
    num_threads: int,
    env_probe: "dict[str, Any] | None" = None,
) -> tuple[int | None, int | None]:
    """Apply PyTorch thread limits and return both effective values.

    When num_threads == 0 and EASYOCR_MAX_QUALITY_THREADS=1, all logical
    CPUs are handed to PyTorch so inference uses the full machine.
    When num_threads > 0, that value is applied directly (no change).
    When num_threads == 0 without max-quality mode, PyTorch keeps its own
    default (unchanged, as before).
    """
    try:
        import torch
    except Exception:
        return None, None

    probe = env_probe or _probe_environment()

    if num_threads > 0:
        target_intra = num_threads
    elif probe["max_quality_env"]:
        # Use all logical CPUs for intra-op parallelism (inference threads).
        target_intra = probe["cpu_count_logical"]
    else:
        target_intra = 0  # leave PyTorch default unchanged

    if target_intra > 0:
        try:
            torch.set_num_threads(target_intra)
        except Exception:
            pass
        try:
            torch.set_num_interop_threads(max(1, target_intra // 2))
        except Exception:
            pass

    try:
        intra = int(torch.get_num_threads())
    except Exception:
        intra = None
    try:
        inter = int(torch.get_num_interop_threads())
    except Exception:
        inter = None
    return intra, inter


def _model_cache_dir() -> "Path":
    """Resolve the effective EasyOCR model cache directory.

    Uses the project cache convention unless EasyOCR's explicit override is set.
    """
    from pathlib import Path
    module_path = os.environ.get("EASYOCR_MODULE_PATH")
    if module_path:
        return Path(module_path)
    return Path.home() / ".cache" / "pdfextractor" / "easyocr"


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


def _dbnet18_weights_available(cache_dir: "Path | None" = None) -> bool:
    """Return True when the EasyOCR-configured DBNet18 weights exist on disk.

    Resolve the filename from EasyOCR's own detection model registry instead
    of hardcoding it so the check remains compatible with the installed
    EasyOCR version.
    """
    from pathlib import Path

    try:
        import easyocr.config as easy_config  # type: ignore

        model = easy_config.detection_models.get("dbnet18", {})
        filename = model.get("filename")
        if not filename:
            return False

        root = Path(cache_dir) if cache_dir is not None else _model_cache_dir()
        return (root / str(filename)).is_file()
    except Exception:
        return False


def _probe_dbnet18_runtime_uncached(reader: "Any", cache_dir: "Path | None" = None) -> "tuple[bool, str | None]":
    """Execute a DBNet18 runtime probe without any caching.

    Builds a DBNet18 Reader from ``reader``'s parameters and runs a minimal
    detection call on a blank image to confirm the detector can actually execute.

    Returns ``(available, failure_reason)`` where ``failure_reason`` is None
    when available is True, or a short descriptive string on failure.

    Separates two distinct failure modes:
      - weights_missing: the .pth file is not on disk → ``False, "weights_missing"``
      - runtime_unavailable: weights present but inference failed (e.g. missing
        MSVC Build Tools on Windows) → ``False, "runtime_probe_failed: <type>: <msg>"``
      - reader_construction_failed: lang_list missing or None returned → similar

    This function never caches.  Callers that want per-instance caching should
    use :meth:`EasyOCRBackend._ensure_dbnet18_runtime` instead.
    """
    if not _dbnet18_weights_available(cache_dir):
        return False, "weights_missing"

    try:
        import numpy as _np
        dbnet_reader = _build_dbnet18_reader_strict(reader)
        if dbnet_reader is None:
            return False, "reader_construction_failed"
        probe_img = _np.full((96, 320, 3), 255, dtype=_np.uint8)
        # DBNet interprets canvas_size as the SHORT side. The Reader default
        # (2560) expands this tiny probe to 2560 x 8544 pixels on CPU.
        dbnet_reader.detect(probe_img, canvas_size=96)
    except Exception as exc:
        _propagate_fatal_error(exc)
        return False, f"runtime_probe_failed: {type(exc).__name__}: {exc}"

    return True, None


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
        angles = [int(a.strip()) for a in val.split(",") if a.strip()]
    except ValueError:
        from structured_pdf_text.errors import ConfigurationError
        raise ConfigurationError(f"Environment variable EASYOCR_ROTATION_INFO={env_val!r} must be a comma-separated list of angles") from None
    if any(angle not in {90, 180, 270} for angle in angles):
        from structured_pdf_text.errors import ConfigurationError
        raise ConfigurationError(f"Environment variable EASYOCR_ROTATION_INFO={env_val!r} allows only 90,180,270")
    return angles


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



def _propagate_fatal_error(exc: BaseException) -> None:
    """Optional OCR variants may degrade, but resource failures must abort."""
    if isinstance(exc, FatalExtractionError):
        raise exc
    raise_if_resource_exhausted(exc, stage="easyocr")


class _DetectionCachingReader:
    """Reuse detection only for identical pixels and detector parameters.

    One proxy lives for one exhaustive invocation, bounding the cache to that
    image's variants. Decoders and contrast retries still run independently.
    Copies prevent recognition from mutating another candidate's geometry.
    """

    def __init__(self, reader: Any, stats: dict[str, int]) -> None:
        self._wrapped = reader
        self._stats = stats
        self._detections: dict[tuple[Any, ...], Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def detect(self, image: Any, **kwargs: Any) -> Any:
        import numpy as np
        arr = np.ascontiguousarray(image)
        key = (
            arr.shape, arr.dtype.str, blake2b(memoryview(arr), digest_size=32).digest(),
            tuple(sorted(kwargs.items())),
        )
        if key in self._detections:
            self._stats["easyocr_exhaustive_detection_cache_hits"] += 1
            return deepcopy(self._detections[key])
        self._stats["easyocr_exhaustive_detection_calls"] += 1
        result = self._wrapped.detect(image, **kwargs)
        self._detections[key] = deepcopy(result)
        return result


def _detector_canvas_size(reader: Any, height: int, width: int, mag_ratio: float) -> int:
    """CRAFT caps the long side; DBNet sets the short side to canvas_size."""
    side = min(height, width) if getattr(reader, "detect_network", None) == "dbnet18" else max(height, width)
    return max(1, int(mag_ratio * side))


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
    mag_ratio: "float | None" = None,
    # Detector parameters
    text_threshold: float = 0.7,
    low_text: float = 0.4,
    link_threshold: float = 0.4,
    min_size: int = 20,
    slope_ths: float = 0.1,
    ycenter_ths: float = 0.5,
    height_ths: float = 0.5,
    width_ths: float = 0.5,
    add_margin: float = 0.1,
    # Recognizer parameters
    contrast_ths: float = 0.1,
    filter_ths: float = 0.003,
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
    The fallback receives the same parameters so behaviour is consistent.
    """
    import numpy as np
    arr = np.asarray(img)
    if mag_ratio is None:
        from structured_pdf_text.ocr.env import env_float
        mag_ratio = env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)

    # --- Prepare colour + grayscale arrays (mirrors upstream reformat_input) ---
    try:
        from easyocr.utils import reformat_input  # type: ignore
        img_color, img_gray = reformat_input(arr)
    except Exception as exc:
        _propagate_fatal_error(exc)
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            mag_ratio=mag_ratio,
            text_threshold=text_threshold, low_text=low_text,
            link_threshold=link_threshold, min_size=min_size,
            slope_ths=slope_ths, ycenter_ths=ycenter_ths,
            height_ths=height_ths, width_ths=width_ths, add_margin=add_margin,
            contrast_ths=contrast_ths, filter_ths=filter_ths,
            reason="reformat_input_unavailable",
        )

    h, w = img_color.shape[:2]
    # CRAFT canvas_size must be at least mag_ratio * max(h, w); otherwise
    # resize_aspect_ratio() clamps target_size back to max(h, w) and
    # the magnification has no effect. DBNet uses the short-side contract.
    canvas_size = _detector_canvas_size(reader, h, w, mag_ratio)

    # --- Stage 1: text detection (CRAFT) ---
    try:
        horizontal_agg, free_agg = reader.detect(
            img_color,
            canvas_size=canvas_size,
            mag_ratio=mag_ratio,
            reformat=False,
            text_threshold=text_threshold,
            low_text=low_text,
            link_threshold=link_threshold,
            min_size=min_size,
            slope_ths=slope_ths,
            ycenter_ths=ycenter_ths,
            height_ths=height_ths,
            width_ths=width_ths,
            add_margin=add_margin,
        )
        # detect() returns one entry per image in the batch; unwrap for our
        # single image — this is what EasyOCR's own readtext() does.
        horizontal_list = horizontal_agg[0]
        free_list = free_agg[0]
    except Exception as exc:
        _propagate_fatal_error(exc)
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            mag_ratio=mag_ratio,
            text_threshold=text_threshold, low_text=low_text,
            link_threshold=link_threshold, min_size=min_size,
            slope_ths=slope_ths, ycenter_ths=ycenter_ths,
            height_ths=height_ths, width_ths=width_ths, add_margin=add_margin,
            contrast_ths=contrast_ths, filter_ths=filter_ths,
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
            contrast_ths=contrast_ths,
            filter_ths=filter_ths,
            rotation_info=rotation_info,
            reformat=False,
        )
        return result, None  # nominal path — no fallback
    except Exception as exc:
        _propagate_fatal_error(exc)
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
            mag_ratio=mag_ratio,
            text_threshold=text_threshold, low_text=low_text,
            link_threshold=link_threshold, min_size=min_size,
            slope_ths=slope_ths, ycenter_ths=ycenter_ths,
            height_ths=height_ths, width_ths=width_ths, add_margin=add_margin,
            contrast_ths=contrast_ths, filter_ths=filter_ths,
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
    mag_ratio: "float | None" = None,
    # Detector parameters — forwarded for parity with nominal path
    text_threshold: float = 0.7,
    low_text: float = 0.4,
    link_threshold: float = 0.4,
    min_size: int = 20,
    slope_ths: float = 0.1,
    ycenter_ths: float = 0.5,
    height_ths: float = 0.5,
    width_ths: float = 0.5,
    add_margin: float = 0.1,
    # Recognizer parameters — forwarded for parity
    contrast_ths: float = 0.1,
    filter_ths: float = 0.003,
) -> "tuple[list[Any], dict[str, Any]]":
    """Recover via unified readtext() and return explicit fallback metadata.

    readtext() re-runs its own detect() internally, so cost and path differ
    from the nominal split.  Callers must surface this as a degraded result.
    All parameters match the nominal path so the fallback result is comparable.
    """
    import warnings as _warnings
    if mag_ratio is None:
        from structured_pdf_text.ocr.env import env_float
        mag_ratio = env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)
    h, w = arr.shape[:2]
    canvas_size = _detector_canvas_size(reader, h, w, mag_ratio)

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
            contrast_ths=contrast_ths,
            filter_ths=filter_ths,
            text_threshold=text_threshold,
            low_text=low_text,
            link_threshold=link_threshold,
            min_size=min_size,
            slope_ths=slope_ths,
            ycenter_ths=ycenter_ths,
            height_ths=height_ths,
            width_ths=width_ths,
            add_margin=add_margin,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
            rotation_info=rotation_info,
        )
        return result, fallback_info
    except Exception as exc2:
        _propagate_fatal_error(exc2)
        raise RuntimeError(
            f"EasyOCR: readtext() also failed after primary failure ({reason}): {exc2}"
        ) from exc2


def _result_to_ocr_tokens(
    raw: list[Any],
    source_engine: str,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[OCRToken]:
    """Convert EasyOCR result list to canonical OCRToken list.

    Each item: (bbox_points, text, confidence)
      bbox_points: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] (4 corners)

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


def _token_rotation_from_applied_correction(angle: int) -> int:
    """Convert the clockwise raster correction into token orientation.

    ``angle`` is the clockwise rotation applied to the raster so EasyOCR can
    read it upright.  After OCR polygons are remapped back to the original
    page coordinate system, the token orientation is the inverse rotation.

    Examples:
        applied correction   token orientation
        0                    0
        90                   270
        180                  180
        270                  90
    """
    return (-int(angle)) % 360

def _result_to_pipeline_tokens(
    raw: list[Any], page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
    source: "SourceKind" = SourceKind.OCR_PAGE,
    token_rotation: int = 0,
) -> list[OcrToken]:
    """Convert EasyOCR raw result tuples to pipeline OcrToken instances.

    Each raw item is ``(bbox_pts, text, confidence)`` as returned by
    ``reader.detect()`` + ``reader.recognize()`` (or the readtext() fallback).
    Invalid items (missing text, degenerate geometry) are silently skipped.

    ``offset_x``/``offset_y`` shift all token coordinates into the parent
    coordinate space when the result comes from a region crop rather than the
    full page.  ``source`` is forwarded to every token for provenance tracking.
    """
    if not raw:
        return []
    from structured_pdf_text.geometry import Point
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
        polygon_raw, (x0, y0, x1, y1) = geometry
        try:
            bbox = BBox(x0, y0, x1, y1)
        except (TypeError, ValueError):
            continue
        # Preserve the quadrilateral from the detector so downstream passes
        # (table cell assignment, reading order, native/OCR fusion) have the
        # exact box shape rather than the axis-aligned envelope.
        pipeline_polygon: "tuple[Point, ...] | None" = None
        if polygon_raw and len(polygon_raw) >= 3:
            try:
                pipeline_polygon = tuple(Point(p[0], p[1]) for p in polygon_raw)
            except (TypeError, ValueError, IndexError):
                pipeline_polygon = None
        tokens.append(OcrToken(
            text=str(text), bbox=bbox,
            confidence=max(0.0, min(1.0, confidence if confidence is not None else 0.0)),
            language=language, source=source,
            rotation=token_rotation,
            polygon=pipeline_polygon,
        ))
    return tokens


def _candidate_metrics(tokens: "list[OcrToken]") -> "dict[str, float]":
    """Compute quality metrics for a candidate token list.

    Returns a dict with keys used both for scoring and for diagnostics:
      mean_confidence              — average OCR confidence (0..1)
      lower_quartile_confidence    — 25th-percentile confidence; guards against
                                     outlier-driven mean inflation
      low_conf_ratio               — fraction of tokens below 0.60 confidence
      replacement_char_ratio       — fraction of characters that are U+FFFD or control chars
      duplicate_ratio              — fraction of token texts that are exact duplicates
      horizontal_ratio             — fraction of tokens with width >= height
      invalid_geometry_ratio       — fraction of tokens with degenerate/zero-area bboxes
      suspicious_insertion_ratio   — fraction of tokens whose text looks like
                                     hallucinated insertions (very short, high-conf duplicates)
      char_count                   — total non-whitespace characters
      token_count                  — number of tokens
    """
    import math as _math
    import unicodedata as _ud

    n = len(tokens)
    if not n:
        return {
            "mean_confidence": 0.0, "low_conf_ratio": 1.0,
            "replacement_char_ratio": 1.0, "duplicate_ratio": 1.0,
            "horizontal_ratio": 0.0, "char_count": 0, "token_count": 0,
            "lower_quartile_confidence": 0.0, "invalid_geometry_ratio": 1.0,
            "suspicious_insertion_ratio": 1.0,
        }

    # Exclude tokens with degenerate bboxes from confidence statistics so a
    # 0×0 high-confidence detection cannot inflate the candidate score (B4).
    confidences = [
        t.confidence for t in tokens
        if t.confidence is not None
        and t.bbox.area > 0
        and all(_math.isfinite(v) for v in (t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1))
    ]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    low_conf = sum(1 for c in confidences if c < 0.60) / max(len(confidences), 1)
    sorted_confidences = sorted(confidences)
    lower_quartile = sorted_confidences[max(0, (len(sorted_confidences) - 1) // 4)] if sorted_confidences else 0.0
    invalid_geometry = sum(
        1 for token in tokens
        if token.bbox.area <= 0 or any(not _math.isfinite(value) for value in (
            token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1
        ))
    ) / n

    all_chars = "".join(t.text for t in tokens)
    bad_chars = sum(
        1 for ch in all_chars
        if ch == "�" or (_ud.category(ch).startswith("C") and ch not in " \t\n")
    )
    repl_ratio = bad_chars / max(len(all_chars), 1)
    suspicious_ratio = sum(
        1 for token in tokens
        if not token.text.strip() or "�" in token.text or len(token.text.strip()) <= 1
    ) / n

    texts = [t.text.strip() for t in tokens if t.text.strip()]
    seen: set[str] = set()
    dups = 0
    for txt in texts:
        if txt in seen:
            dups += 1
        seen.add(txt)
    dup_ratio = dups / max(len(texts), 1)

    horiz = sum(t.bbox.width >= t.bbox.height for t in tokens) / n
    chars = sum(len(t.text.strip()) for t in tokens)

    return {
        "mean_confidence": mean_conf,
        "low_conf_ratio": low_conf,
        "replacement_char_ratio": repl_ratio,
        "duplicate_ratio": dup_ratio,
        "horizontal_ratio": horiz,
        "char_count": chars,
        "token_count": n,
        "lower_quartile_confidence": lower_quartile,
        "invalid_geometry_ratio": invalid_geometry,
        "suspicious_insertion_ratio": suspicious_ratio,
    }


def _lexical_plausibility(tokens: "list[OcrToken]") -> float:
    """Return a [0, 1] plausibility score for the token list as pt-BR prose (§38).

    A token is considered lexically plausible when it:
      - contains at least 2 characters of predominantly alphabetic content
      - contains at least one alphabetic Unicode character (i.e. is not
        purely numeric, symbolic or punctuation-only)
      - has no more than one consecutive unrecognised glyph (U+FFFD)

    The score is the fraction of tokens that pass this test.  It is deliberately
    weak — it only distinguishes coherent alphabetic text from garbled output;
    it is NOT a spell-checker.  Numeric-only tokens (R$, dates, CPF) score
    neutrally (0.5) so they do not degrade the score of an otherwise good
    candidate.

    Used as a small tiebreaker bonus in _score_candidate.  Maximum contribution
    is 0.03 so it cannot reverse a clear quality difference.
    """
    if not tokens:
        return 0.0
    import unicodedata as _ud

    plausible = 0
    neutral = 0
    for tok in tokens:
        text = tok.text.strip()
        if not text:
            neutral += 1
            continue
        alpha_count = sum(1 for ch in text if _ud.category(ch).startswith("L"))
        digit_count = sum(1 for ch in text if ch.isdigit())
        total = len(text)
        # Purely numeric or currency — neutral
        if digit_count > 0 and alpha_count == 0:
            neutral += 1
            continue
        # Contains more than one replacement character U+FFFD (garbled glyph)
        if text.count("�") > 1:
            continue
        # Plausible: majority alphabetic, at least 2 chars
        if alpha_count >= 2 and alpha_count / total >= 0.5:
            plausible += 1

    denominator = len(tokens) - neutral
    if denominator <= 0:
        return 0.5  # all tokens are numeric/neutral → no information
    return plausible / denominator


def _score_candidate(tokens: "list[OcrToken]") -> float:
    """Score a candidate token list for selection. Higher is better.

    Scoring components:
      + mean_confidence                  — primary quality signal
      + lower_quartile_confidence * 0.05 — guards against outlier-driven mean inflation
      + char_count bonus                 — logarithmic, capped at 0.08, so content
                                           coverage matters but never overrides confidence
      + horizontal_ratio * 0.02          — small bonus for coherent horizontal text
      + lexical_plausibility * 0.03      — small bonus for pt-BR alphabetic plausibility
                                           (§38); max 0.03 — only acts as tiebreaker
      - low_conf_ratio * 0.15            — penalise high fraction of uncertain tokens
      - replacement_char_ratio * 0.30    — penalise garbled / control characters
      - duplicate_ratio * 0.10           — penalise repeated token texts (hallucination)
      - invalid_geometry_ratio * 0.40    — largest single penalty; degenerate bboxes
                                           indicate a broken detection pass
      - suspicious_insertion_ratio * 0.08 — penalise hallucinated short insertions

    A candidate may not win solely by having more characters if those characters
    are low-confidence, garbled, or duplicated (§22 rollback rule).
    """
    import math as _math
    if not tokens:
        return -_math.inf
    m = _candidate_metrics(tokens)
    lex = _lexical_plausibility(tokens)
    score = (
        m["mean_confidence"]
        + min(0.08, _math.log1p(m["char_count"]) * 0.012)
        + m["horizontal_ratio"] * 0.02
        + m["lower_quartile_confidence"] * 0.05
        + lex * 0.03
        - m["low_conf_ratio"] * 0.15
        - m["replacement_char_ratio"] * 0.30
        - m["duplicate_ratio"] * 0.10
        - m["invalid_geometry_ratio"] * 0.40
        - m["suspicious_insertion_ratio"] * 0.08
    )
    return score


def _best_candidate(
    candidates: "list[tuple[str, list[OcrToken]]]",
) -> "tuple[list[OcrToken], list[dict[str, Any]]]":
    """Fuse spatially distinct candidate evidence and report candidate scores.

    Each diagnostics entry contains the candidate_id, all quality metrics from
    _candidate_metrics, the composite score, and a ``selected`` flag.
    """
    import math as _math
    if not candidates:
        return [], []

    scored = []
    for label, tokens in candidates:
        metrics = _candidate_metrics(tokens)
        score = _score_candidate(tokens)
        scored.append((score, label, tokens, metrics))
    baseline = next((tokens for label, tokens in candidates if label == "default"), candidates[0][1])
    baseline_trusted = [token for token in baseline if (token.confidence or 0.0) >= 0.70]
    adjusted = []
    for score, label, tokens, metrics in scored:
        preserved = _baseline_preservation_ratio(baseline_trusted, tokens)
        metrics["baseline_preservation_ratio"] = preserved
        # A hypothesis that drops reliable baseline spans receives a material
        # penalty even when its average confidence is higher.
        score -= max(0.0, 0.80 - preserved) * 0.45
        adjusted.append((score, label, tokens, metrics))
    scored = adjusted
    scored.sort(key=lambda x: x[0], reverse=True)

    _best_score, best_label, _best_tokens, _best_metrics = scored[0]
    from structured_pdf_text.ocr.candidate_fusion import (
        OcrCandidateFusionEngine,
        OcrCandidateResult,
    )
    candidate_results = [
        OcrCandidateResult(label, _candidate_family(label), tuple(tokens), score, "easyocr")
        for score, label, tokens, _ in scored
    ]
    fused = OcrCandidateFusionEngine().fuse(candidate_results)
    diagnostics = [
        {
            "candidate_id": label,
            "token_count": metrics["token_count"],
            "char_count": metrics["char_count"],
            "mean_confidence": round(metrics["mean_confidence"], 4),
            "low_conf_ratio": round(metrics["low_conf_ratio"], 4),
            "replacement_char_ratio": round(metrics["replacement_char_ratio"], 4),
            "duplicate_ratio": round(metrics["duplicate_ratio"], 4),
            "horizontal_ratio": round(metrics["horizontal_ratio"], 4),
            "lower_quartile_confidence": round(metrics["lower_quartile_confidence"], 4),
            "invalid_geometry_ratio": round(metrics["invalid_geometry_ratio"], 4),
            "suspicious_insertion_ratio": round(metrics["suspicious_insertion_ratio"], 4),
            "baseline_preservation_ratio": round(metrics["baseline_preservation_ratio"], 4),
            "score": round(score, 4) if _math.isfinite(score) else None,
            # D9: "selected" historically meant "this candidate won the ranking".
            # It does NOT mean this candidate's tokens are the only ones in the
            # fusion output. Use primary_candidate + contributed_token_count for
            # accurate attribution of what actually appeared in the result.
            "selected": label == best_label,
            "primary_candidate": label == best_label,
            "contributed_token_count": fused.contribution_counts.get(label, 0),
            "fused_evidence_count": len(fused.tokens),
            "fusion_consensus_count": fused.consensus_count,
            "fusion_conflict_count": fused.conflict_count,
        }
        for score, label, tokens, metrics in scored
    ]
    return list(fused.tokens), diagnostics


def _baseline_preservation_ratio(baseline: list[OcrToken], candidate: list[OcrToken]) -> float:
    """Return the fraction of high-confidence baseline tokens preserved in the candidate.

    A token is "preserved" when the candidate contains a text-and-position match:
    same normalized text (case-folded, whitespace-collapsed) AND IoU ≥ 0.25.
    Returns 1.0 when the baseline is empty (no baseline to compare against).

    Used as a guard in :func:`_best_candidate` to penalise candidates that drop
    reliable baseline detections even when their average confidence is higher.
    """
    if not baseline:
        return 1.0
    preserved = 0
    for token in baseline:
        key = " ".join(token.text.casefold().split())
        if any(
            other.bbox.iou(token.bbox) >= 0.25
            and " ".join(other.text.casefold().split()) == key
            for other in candidate
        ):
            preserved += 1
    return preserved / len(baseline)


def _candidate_family(label: str) -> str:
    """Map correlated variants to a shared independent-evidence family."""
    value = label.lower()
    if "dbnet" in value:
        return "dbnet"
    if "direct" in value:
        return "direct_recognition"
    if "wordbeam" in value or "beamsearch" in value:
        return "craft_beam"
    return "craft_greedy"


_ORIENTATION_MIN_TOKENS = 3
_ORIENTATION_MIN_CHARS = 8
_ORIENTATION_MIN_MEAN_CONFIDENCE = 0.40
_ORIENTATION_MAX_LOW_CONF_RATIO = 0.75


def _adaptive_candidates(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
    *,
    record_call: "Callable[[dict[str, Any] | None], None] | None" = None,
) -> "list[tuple[str, list[tuple[Any, Any, Any]]]]":
    """Run EasyOCR upright candidates for the adaptive quality policy.

    Always runs at least two candidates: default (A) and high-recall (B).
    When the image is detected as low-contrast or dark-background,
    additional preprocessing variants are appended by
    :func:`_image_preprocessing_candidates` with ``adaptive=True``.

    Returns only upright (0°) candidates. Orientation selection — including
    conditional 90/180/270° probes when upright quality is insufficient — is
    handled by :func:`_adaptive_with_orientation_selection`, which calls this
    function internally and decides which orientation's candidates enter fusion.

    The total number of candidates is therefore dynamic (image-dependent).
    Every call to :func:`_run_easyocr` is reported to ``record_call`` when
    provided, so the caller can track per-call fallback statistics.
    """
    # Candidate A: default profile
    raw_a, _fb_a = _run_easyocr(reader, img, **base_kwargs)
    if record_call is not None:
        record_call(_fb_a)

    # Candidate B: high-recall — lower detection thresholds
    hr_kwargs = dict(base_kwargs)
    hr_kwargs["text_threshold"] = min(base_kwargs["text_threshold"], 0.55)
    hr_kwargs["low_text"] = min(base_kwargs["low_text"], 0.30)
    hr_kwargs["link_threshold"] = min(base_kwargs["link_threshold"], 0.35)
    hr_kwargs["min_size"] = max(1, base_kwargs["min_size"] // 2)
    raw_b, _fb_b = _run_easyocr(reader, img, **hr_kwargs)
    if record_call is not None:
        record_call(_fb_b)
    results = [("default", raw_a), ("high_recall", raw_b)]
    # Adaptive mode activates image transforms only when the raster signals
    # low contrast or a dark background. The original remains the baseline.
    for label, variant in _image_preprocessing_candidates(img, adaptive=True):
        raw, _fb = _run_easyocr(reader, variant, **base_kwargs)
        if record_call is not None:
            record_call(_fb)
        results.append((label, raw))
    return results


def _orientation_quality_score(tokens: "list[OcrToken]") -> float:
    """Scalar quality score for orientation comparison (higher = better, max ~1.1).

    Combines confidence, content volume, and hallucination penalties so that
    a page with many low-confidence tokens scores worse than one with few
    high-confidence tokens — preventing token-count bias in orientation selection.
    """
    if not tokens:
        return 0.0
    m = _candidate_metrics(tokens)
    return (
        m["mean_confidence"] * 0.40
        + m["lower_quartile_confidence"] * 0.25
        + (1.0 - m["low_conf_ratio"]) * 0.15
        + (1.0 - min(m["invalid_geometry_ratio"], 1.0)) * 0.10
        + min(m["char_count"], 300) / 3000
        - m["replacement_char_ratio"] * 0.20
        - m["suspicious_insertion_ratio"] * 0.15
    )


def _orientation_quality_sufficient(tokens: "list[OcrToken]") -> bool:
    """Return True when upright orientation quality is good enough to skip rotation probes.

    Uses multi-dimensional quality metrics rather than token count alone, so pages
    that produce many low-confidence tokens (false detections at wrong orientation)
    still trigger rotation search even when the raw token count exceeds a naive
    threshold.
    """
    if not tokens:
        return False
    m = _candidate_metrics(tokens)
    return (
        m["token_count"] >= _ORIENTATION_MIN_TOKENS
        and m["char_count"] >= _ORIENTATION_MIN_CHARS
        and m["mean_confidence"] >= _ORIENTATION_MIN_MEAN_CONFIDENCE
        and m["low_conf_ratio"] <= _ORIENTATION_MAX_LOW_CONF_RATIO
    )


def _adaptive_with_orientation_selection(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
    page_index: int,
    language: str,
    *,
    record_call: "Callable[[dict[str, Any] | None], None] | None" = None,
) -> "tuple[list[tuple[str, list[OcrToken]]], dict[str, Any]]":
    """Adaptive candidates with global orientation selection (B1/R73).

    Runs upright candidates first via :func:`_adaptive_candidates`, evaluates
    their combined quality using confidence and content metrics (not token count
    alone), then probes 90/180/270° only when upright quality is insufficient.
    Exactly one orientation is selected; tokens from losing orientations never
    enter :func:`_best_candidate` / candidate fusion.

    Returns:
        pipeline_candidates: OcrToken-level candidates for :func:`_best_candidate`.
        orientation_diag:    dict with ``selected_angle`` (int, clockwise degrees)
                             and ``attempts`` (list of per-angle quality snapshots).
    """
    # 1. Upright candidates (default + high_recall + preprocessing variants)
    upright_raw = _adaptive_candidates(reader, img, base_kwargs, record_call=record_call)

    # 2. Convert to pipeline tokens (all upright → rotation=0)
    upright_pipeline: list[tuple[str, list[OcrToken]]] = [
        (label, _result_to_pipeline_tokens(raw, page_index, language, token_rotation=0))
        for label, raw in upright_raw
    ]

    # 3. Assess upright quality across all upright candidates combined
    upright_tokens = [t for _, tokens in upright_pipeline for t in tokens]
    upright_score = _orientation_quality_score(upright_tokens)
    attempts: list[dict[str, Any]] = [
        {
            "angle": 0,
            "score": round(upright_score, 4),
            "token_count": len(upright_tokens),
            "sufficient": _orientation_quality_sufficient(upright_tokens),
        }
    ]
    selected_pipeline = upright_pipeline
    selected_angle = 0

    if not _orientation_quality_sufficient(upright_tokens):
        # 4. Probe rotations — one pass per angle with base_kwargs
        try:
            import numpy as _np
            _arr_base = _np.asarray(img)
            best_rot_score = upright_score
            best_rot_result: "tuple[str, list[OcrToken]] | None" = None

            for _angle, _rot_label in ((90, "rot90"), (180, "rot180"), (270, "rot270")):
                try:
                    _rot_img = _rotate_image(_arr_base, _angle)
                    _rot_h, _rot_w = _rot_img.shape[:2]
                    _raw_rot, _fb_rot = _run_easyocr(reader, _rot_img, **base_kwargs)
                    if record_call is not None:
                        record_call(_fb_rot)
                    if _raw_rot:
                        _remapped = _remap_raw_for_rotation(_raw_rot, _angle, _rot_h, _rot_w)
                        _rot_tokens = _result_to_pipeline_tokens(
                            _remapped,
                            page_index,
                            language,
                            token_rotation=_token_rotation_from_applied_correction(_angle),
                        )
                        _rot_score = _orientation_quality_score(_rot_tokens)
                        attempts.append({
                            "angle": _angle,
                            "score": round(_rot_score, 4),
                            "token_count": len(_rot_tokens),
                        })
                        if _rot_score > best_rot_score:
                            best_rot_score = _rot_score
                            best_rot_result = (_rot_label, _rot_tokens)
                    else:
                        attempts.append({"angle": _angle, "score": 0.0, "token_count": 0})
                except Exception as _exc:
                    _propagate_fatal_error(_exc)
        except Exception as _exc:
            _propagate_fatal_error(_exc)

        # 5. If a rotation beats upright quality, use ONLY that rotation's tokens
        if best_rot_result is not None:
            _rot_label, _rot_tokens_list = best_rot_result
            selected_pipeline = [(_rot_label, _rot_tokens_list)]
            selected_angle = {"rot90": 90, "rot180": 180, "rot270": 270}[_rot_label]

    orientation_diag: dict[str, Any] = {
        "selected_angle": selected_angle,
        "attempts": attempts,
    }
    return selected_pipeline, orientation_diag


def _image_preprocessing_candidates(img: "Any", *, adaptive: bool) -> list[tuple[str, Any]]:
    """Build optional grayscale/contrast/denoise/threshold image hypotheses.

    P1: visually identical images are deduplicated by content fingerprint so
    clean pages do not trigger redundant OCR inference passes.
    """
    try:
        import hashlib

        import cv2
        import numpy as np
        from PIL import Image, ImageOps
        arr = np.asarray(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr.copy()
        if not gray.size:
            return []
        mean, std = float(gray.mean()), float(gray.std())
        dark_background = mean < 112 and float((gray < 96).mean()) > 0.55
        low_contrast = std < 58
        if adaptive and not dark_background and not low_contrast:
            return []
        candidates: list[tuple[str, Any]] = [("grayscale", gray)]
        pil = Image.fromarray(gray)
        candidates.append(("autocontrast", np.asarray(ImageOps.autocontrast(pil))))
        if dark_background:
            candidates.append(("inverted_grayscale", cv2.bitwise_not(gray)))
        sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        candidates.append(("sharpen", cv2.filter2D(gray, -1, sharpen_kernel)))
        candidates.append(("median_denoise", cv2.medianBlur(gray, 3)))
        candidates.append(("bilateral_denoise", cv2.bilateralFilter(gray, 5, 45, 45)))
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(("otsu", otsu))
        adaptive_threshold = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
        )
        candidates.append(("adaptive_threshold", adaptive_threshold))
        if arr.ndim == 3:
            candidates = [
                (label, cv2.cvtColor(variant, cv2.COLOR_GRAY2RGB) if variant.ndim == 2 else variant)
                for label, variant in candidates
            ]
        # P1: deduplicate identical image arrays before returning to avoid
        # running separate OCR inference passes on the same pixel content.
        def _fp(variant: Any) -> str:
            a = np.asarray(variant)
            return f"{a.shape}:{a.dtype}:{hashlib.md5(a.tobytes()).hexdigest()}"

        seen: set[str] = {_fp(arr)}  # skip variants identical to the input image (P1)
        deduped: list[tuple[str, Any]] = []
        for label, variant in candidates:
            fp = _fp(variant)
            if fp not in seen:
                seen.add(fp)
                deduped.append((label, variant))
        return deduped
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        return []


def _apply_clahe(img: "Any") -> "Any":
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalization) to an image.

    Operates on the luminance channel of the image to enhance local contrast
    without destroying colour information.  Returns the enhanced image as a
    numpy array in the same shape/dtype as the input (RGB or grayscale).

    Requires OpenCV (cv2).  If unavailable, returns the original image unchanged
    so the CLAHE candidate degrades gracefully to the same result as the default.
    """
    try:
        import cv2
        import numpy as np
        arr = np.asarray(img)
        if arr.ndim == 3 and arr.shape[2] == 3:
            # Convert to LAB, apply CLAHE to L channel, convert back to RGB
            lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
            l_channel, a_channel, b_channel = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            l_enhanced = clahe.apply(l_channel)
            lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
            return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2RGB)
        elif arr.ndim == 2:
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            return clahe.apply(arr)
        return arr
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        return img


def _apply_deskew(img: "Any") -> "Any":
    """Compatibility wrapper returning only the deskewed image.

    Production candidates use :func:`_apply_deskew_with_inverse` so token
    polygons can be mapped back to the original raster. The image-only helper
    remains for callers that used the former private test utility.

    Estimate and correct small rotation angles in a page image.

    Uses a combination of Hough-line angle estimation and the projection-profile
    method on a binarised copy of the image.  Only corrects angles in the range
    [-15°, +15°] — larger rotations indicate intentional orientation (portrait/
    landscape) rather than scan skew and should be handled by an orientation
    candidate instead.

    Returns the deskewed image (same dtype/shape).  Falls back silently to the
    original image when cv2 is unavailable or angle estimation fails so the
    deskew candidate degrades gracefully.
    """
    return _apply_deskew_with_inverse(img)[0]


def _apply_deskew_with_inverse(img: "Any") -> tuple["Any", "Any"]:
    """Deskew an image and return the affine inverse for OCR polygons."""
    import numpy as np
    identity = np.eye(3, dtype=float)
    try:
        import cv2
        import numpy as np
        arr = np.asarray(img)
        if arr.ndim == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        else:
            gray = arr.copy()

        # Binarise for contour / line analysis
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find connected components and estimate angle via minAreaRect
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        angles: list[float] = []
        for cnt in contours:
            if cv2.contourArea(cnt) < 50:
                continue
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            # minAreaRect returns angles in [-90, 0]; normalise to [-45, 45]
            if angle < -45:
                angle += 90
            if abs(angle) <= 15:
                angles.append(angle)

        if not angles:
            return img, identity

        # Use median to be robust against outlier components
        skew_angle = float(np.median(angles))

        # Only correct meaningful skew (ignore < 0.5° — rounding noise)
        if abs(skew_angle) < 0.5:
            return img, identity

        h, w = gray.shape[:2]
        center = (w / 2.0, h / 2.0)
        M = cv2.getRotationMatrix2D(center, skew_angle, 1.0)
        if arr.ndim == 3:
            rotated = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        else:
            rotated = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_REPLICATE)
        affine = np.vstack((M, [0.0, 0.0, 1.0]))
        return rotated, np.linalg.inv(affine)
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        return img, identity


def _remap_raw_affine(raw: "list[Any]", inverse: "Any") -> "list[Any]":
    """Map polygon coordinates from a transformed OCR image to its source."""
    import numpy as np
    if not raw:
        return raw
    remapped = []
    for item in raw:
        try:
            points, text, confidence = item[0], item[1], item[2] if len(item) > 2 else None
            mapped = []
            for point in points:
                value = inverse @ np.array([float(point[0]), float(point[1]), 1.0])
                mapped.append([float(value[0]), float(value[1])])
            remapped.append((mapped, text, confidence))
        except Exception as exc:
            _propagate_fatal_error(exc)
            remapped.append(item)
    return remapped


def _rotate_image(img: "Any", angle: int) -> "Any":
    """Rotate an image by 90, 180, or 270 degrees clockwise.

    Uses numpy array operations (no cv2 dependency).  Returns the rotated array.
    Only handles multiples of 90°; other angles are returned as-is.
    """
    import numpy as np
    arr = np.asarray(img)
    if angle == 90:
        return np.rot90(arr, k=3)   # 90° clockwise = 3 CCW
    if angle == 180:
        return np.rot90(arr, k=2)
    if angle == 270:
        return np.rot90(arr, k=1)   # 270° clockwise = 1 CCW
    return arr


def _remap_raw_for_rotation(
    raw: "list[Any]",
    angle: int,
    rotated_h: int,
    rotated_w: int,
) -> "list[Any]":
    """Map OCR bbox points from rotated-image coordinates back to original-image space.

    EasyOCR returns bbox_points as [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] in the
    coordinate system of the image it was given.  When we rotated the image by
    ``angle`` degrees clockwise before passing it to OCR, we need to apply the
    inverse rotation to map detected polygons back to the original image space.

    Args:
        raw:        EasyOCR raw result list — each item is (bbox_pts, text, conf).
        angle:      The clockwise rotation that was applied to the image (90/180/270).
        rotated_h:  Height of the rotated image (= width of original for 90°/270°).
        rotated_w:  Width  of the rotated image (= height of original for 90°/270°).

    Returns the same list structure with bbox_points remapped.
    """
    if not raw or angle not in (90, 180, 270):
        return raw

    remapped = []
    for item in raw:
        try:
            bbox_pts, text, conf = item[0], item[1], item[2] if len(item) > 2 else None
            new_pts = []
            for pt in bbox_pts:
                x, y = float(pt[0]), float(pt[1])
                if angle == 90:
                    # Clockwise 90°: (x, y) in rotated → (y, W_rot - 1 - x) in original
                    nx, ny = y, rotated_w - 1 - x
                elif angle == 180:
                    # 180°: (x, y) → (W_rot-1-x, H_rot-1-y)
                    nx, ny = rotated_w - 1 - x, rotated_h - 1 - y
                else:  # 270
                    # Clockwise 270° = CCW 90°: (x, y) → (H_rot-1-y, x)
                    nx, ny = rotated_h - 1 - y, x
                new_pts.append([nx, ny])
            if conf is not None:
                remapped.append((new_pts, text, conf))
            else:
                remapped.append((new_pts, text, None))
        except (IndexError, TypeError, ValueError):
            remapped.append(item)
    return remapped


def _exhaustive_candidates(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
    *,
    quantize: bool = True,
    _dbnet_diag: "list[dict[str, Any]] | None" = None,
    _dbnet_precomputed: "tuple[bool, str | None] | None" = None,
    record_call: "Callable[[dict[str, Any] | None], None] | None" = None,
    _reader_cache: "dict[str, Any] | None" = None,
    _detection_stats: "dict[str, int] | None" = None,
    _include_rotations: bool = True,
) -> "list[tuple[str, list[tuple[Any, Any, Any]]]]":
    """Run the exhaustive candidate set for maximum-quality OCR.

    The candidate set is dynamic: its size depends on the image content,
    installed dependencies, and runtime state of optional components
    (DBNet18, cv2, wordbeamsearch, quantize).  Candidate categories:

      Conditional variants (depend on configuration or runtime state):
        default, high_recall, layout_sensitive, low_contrast, clahe,
        beamsearch (if not base decoder), wordbeamsearch (if not default
        decoder), no_quantize (if quantize=True and rebuild succeeds),
        deskew (if image changed), high_mag, DBNet18 (if runtime probe passed).

      Preprocessing variants (conditional on image content):
        autocontrast, inverted_grayscale (dark background only), sharpen,
        median_denoise, bilateral_denoise, otsu, adaptive_threshold.
        Each preprocessing label is executed at most once per page — labels
        already added by the adaptive baseline are not repeated.

      Orientation variants (conditional on result count):
        rot90, rot180, rot270 — added when they produce any result tokens;
        remapped back to original-image coordinates before scoring.

    Every :func:`_run_easyocr` call is reported via ``record_call`` when
    provided, so the caller can track per-call fallback statistics.

    ``_dbnet_diag`` is an optional mutable list.  When provided, a single dict
    is appended with keys ``weights_available``, ``runtime_available``, and
    ``failure_reason`` so the caller can surface the DBNet18 runtime state in
    page diagnostics without changing this function's return type.
    """
    stats = _detection_stats if _detection_stats is not None else {}
    stats.setdefault("easyocr_exhaustive_detection_calls", 0)
    stats.setdefault("easyocr_exhaustive_detection_cache_hits", 0)
    original_reader = reader
    reader = _DetectionCachingReader(reader, stats)

    def auxiliary_reader(label: str, factory: Callable[[Any], Any]) -> Any:
        if _reader_cache is None:
            built = factory(original_reader)
        else:
            if label not in _reader_cache:
                _reader_cache[label] = factory(original_reader)
            built = _reader_cache[label]
        return _DetectionCachingReader(built, stats) if built is not None else None

    results = _adaptive_candidates(reader, img, base_kwargs, record_call=record_call)

    # Candidate C: beamsearch decoder (if not already)
    if base_kwargs.get("decoder") != "beamsearch":
        bs_kwargs = dict(base_kwargs)
        bs_kwargs["decoder"] = "beamsearch"
        raw_c, _fb_c = _run_easyocr(reader, img, **bs_kwargs)
        if record_call is not None:
            record_call(_fb_c)
        results.append(("beamsearch", raw_c))

    # Candidate D: layout-sensitive — conservative merging to avoid cross-gutter joins
    ls_kwargs = dict(base_kwargs)
    ls_kwargs["width_ths"] = min(base_kwargs["width_ths"], 0.25)
    ls_kwargs["add_margin"] = min(base_kwargs["add_margin"], 0.05)
    raw_d, _fb_d = _run_easyocr(reader, img, **ls_kwargs)
    if record_call is not None:
        record_call(_fb_d)
    results.append(("layout_sensitive", raw_d))

    # Candidate E: low-contrast recovery — raise contrast_ths so more crops
    # receive the second-pass contrast adjustment, and boost adjust_contrast
    # to recover faint text missed by the default recognizer parameters (§13).
    lc_kwargs = dict(base_kwargs)
    lc_kwargs["contrast_ths"] = max(base_kwargs.get("contrast_ths", 0.1), 0.20)
    lc_kwargs["adjust_contrast"] = min(base_kwargs.get("adjust_contrast", 0.5) + 0.15, 0.70)
    raw_e, _fb_e = _run_easyocr(reader, img, **lc_kwargs)
    if record_call is not None:
        record_call(_fb_e)
    results.append(("low_contrast", raw_e))

    # Candidate F: CLAHE preprocessing (§14) — apply adaptive histogram equalization
    # to the full page image before detection.  This is a true image-level preprocess
    # rather than a parameter change, so it can recover text that EasyOCR's internal
    # contrast adjustment misses (grey-on-white, fax degraded, uneven illumination).
    # Degrades gracefully if cv2 is unavailable (returns same result as default).
    clahe_img = _apply_clahe(img)
    raw_f, _fb_f = _run_easyocr(reader, clahe_img, **base_kwargs)
    if record_call is not None:
        record_call(_fb_f)
    results.append(("clahe", raw_f))

    # Preprocessing variants: run only labels not already produced by _adaptive_candidates
    # to avoid executing the same preprocessing profile twice on low-contrast or dark pages.
    existing_labels = {label for label, _ in results}
    for label, variant in _image_preprocessing_candidates(img, adaptive=False):
        if label == "grayscale" or label in existing_labels:
            continue
        raw, _fb = _run_easyocr(reader, variant, **base_kwargs)
        if record_call is not None:
            record_call(_fb)
        results.append((label, raw))
        existing_labels.add(label)

    # Candidate G: wordbeamsearch decoder (§9) — CTC with vocabulary-constrained
    # beam search.  Best for pt-BR prose where word-level context resolves ambiguous
    # characters (e.g. 'rn' vs 'm').  Skipped when the base decoder is already
    # wordbeamsearch to avoid running the same candidate twice.
    if base_kwargs.get("decoder") != "wordbeamsearch":
        wbs_kwargs = dict(base_kwargs)
        wbs_kwargs["decoder"] = "wordbeamsearch"
        try:
            raw_g, _fb_g = _run_easyocr(reader, img, **wbs_kwargs)
            if record_call is not None:
                record_call(_fb_g)
            results.append(("wordbeamsearch", raw_g))
        except Exception as _optional_exc:
            _propagate_fatal_error(_optional_exc)
            pass

    # Candidate H: no_quantize (§10) — run with quantize=False via a separate
    # Reader instance only when possible.  This avoids the weight-rounding that
    # quantize=True applies, which can push borderline characters across the wrong
    # decision boundary.  We attempt to rebuild the Reader without quantize; if
    # the Reader lacks a quantize attribute or the rebuild fails, the candidate is
    # silently skipped rather than raising.
    if quantize:
        try:
            nq_reader = auxiliary_reader("no_quantize", _rebuild_reader_no_quantize)
            if nq_reader is not None:
                raw_h, _fb_h = _run_easyocr(nq_reader, img, **base_kwargs)
                if record_call is not None:
                    record_call(_fb_h)
                results.append(("no_quantize", raw_h))
        except Exception as _optional_exc:
            _propagate_fatal_error(_optional_exc)
            pass

    # Candidate I: deskew (§15) — estimate and correct small scan rotation before
    # CRAFT detection.  Only corrects angles ≤ 15°; larger rotations imply
    # intentional layout orientation rather than scan skew.
    deskew_img, deskew_inverse = _apply_deskew_with_inverse(img)
    # Only add if deskew actually changed the image (saves time on already-straight pages)
    try:
        import numpy as np
        _arr_orig = np.asarray(img)
        _arr_deskew = np.asarray(deskew_img)
        _changed = (_arr_orig.shape == _arr_deskew.shape and
                    not bool((_arr_orig == _arr_deskew).all()))
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        _changed = True
    if _changed:
        raw_i, _fb_i = _run_easyocr(reader, deskew_img, **base_kwargs)
        if record_call is not None:
            record_call(_fb_i)
        raw_i = _remap_raw_affine(raw_i, deskew_inverse)
        results.append(("deskew", raw_i))

    # Candidate J: high_mag (§17) — higher magnification ratio so CRAFT sees the
    # page at a larger effective resolution.  Particularly helps pages with very
    # small fonts (footnotes, table captions, dense tables).  The base mag_ratio
    # is read from EASYOCR_MAG_RATIO (default 1.2); this candidate uses base × 1.5
    # (capped at 2.5), which is typically 1.8 for the default mag_ratio.
    try:
        from structured_pdf_text.ocr.env import env_float as _env_float
        _base_mag = _env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)
        _high_mag = min(_base_mag * 1.5, 2.5)
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        _base_mag = 1.2
        _high_mag = 1.8
    if _high_mag > _base_mag + 0.1:
        try:
            raw_j, _fb_j = _run_easyocr(reader, img, **base_kwargs, mag_ratio=_high_mag)
            if record_call is not None:
                record_call(_fb_j)
            results.append(("high_mag", raw_j))
        except Exception as _optional_exc:
            _propagate_fatal_error(_optional_exc)
            pass

    # Candidate N: DBNet18 detector ensemble (§11) — runs the second EasyOCR detector
    # on the same image.  DBNet18 uses differentiable binarisation (DB) and can detect
    # text boxes that CRAFT misses in dense or irregular layouts.  Both detector outputs
    # enter the candidate set independently; the scorer picks the better result or a
    # later merge pass can combine non-overlapping boxes from both.
    # Two distinct failure modes are distinguished and recorded:
    #   - weights_missing: .pth file not on disk → skip, record in diag
    #   - runtime_unavailable: weights present but inference failed (e.g. missing
    #     Build Tools on Windows) → skip candidate, record cause in diag
    # CRAFT continues as the primary detector in both cases.
    # The runtime state is supplied by the caller via _dbnet_precomputed so that
    # the probe runs at most once per instance (cached in EasyOCRBackend); when
    # called without a precomputed state (e.g. in tests), the uncached probe runs.
    if _dbnet_precomputed is not None:
        _dbnet_runtime_ok, _dbnet_failure = _dbnet_precomputed
        # Derive weights availability from the same probe snapshot to keep the
        # diagnostic internally consistent. A fresh _dbnet18_weights_available()
        # call here could disagree with the precomputed state if weights moved
        # on disk between the probe and this execution.
        if _dbnet_runtime_ok:
            _dbnet_weights_ok = True
        elif _dbnet_failure == "weights_missing":
            _dbnet_weights_ok = False
        else:
            # Probe reached the runtime/construction stage — weights were present
            _dbnet_weights_ok = True
    else:
        _model_dir = getattr(reader, "model_storage_directory", None)
        _cache_for_probe = None
        if _model_dir:
            from pathlib import Path as _Path
            _cache_for_probe = _Path(_model_dir)
        _dbnet_runtime_ok, _dbnet_failure = _probe_dbnet18_runtime_uncached(reader, _cache_for_probe)
        _dbnet_weights_ok = _dbnet18_weights_available(_cache_for_probe)
    if _dbnet_diag is not None:
        _dbnet_diag.append({
            "weights_available": _dbnet_weights_ok,
            "runtime_available": _dbnet_runtime_ok,
            "failure_reason": _dbnet_failure,
        })
    if _dbnet_runtime_ok:
        try:
            _dbnet_reader = auxiliary_reader("dbnet18", _build_dbnet18_reader)
            if _dbnet_reader is not None:
                raw_n, _fb_n = _run_easyocr(_dbnet_reader, img, **base_kwargs)
                if record_call is not None:
                    record_call(_fb_n)
                results.append(("dbnet18", raw_n))
            else:
                # _build_dbnet18_reader absorbed an exception and returned None.
                # The probe passed but construction failed — mark runtime unavailable
                # so the cache invalidation in recognize_page() fires correctly.
                if _dbnet_diag is not None and _dbnet_diag:
                    _dbnet_diag[-1]["runtime_available"] = False
                    _dbnet_diag[-1]["failure_reason"] = "reader_construction_failed"
        except Exception as _dbnet_exc:
            _propagate_fatal_error(_dbnet_exc)
            if _dbnet_diag is not None and _dbnet_diag:
                _dbnet_diag[-1]["runtime_available"] = False
                _dbnet_diag[-1]["failure_reason"] = (
                    f"runtime_probe_failed: {type(_dbnet_exc).__name__}: {_dbnet_exc}"
                )

    # Candidates K/L/M: page orientation variants (§16) — 90°/180°/270° clockwise.
    # Only included when _include_rotations=True (the legacy default).
    # When called from _exhaustive_with_orientation_selection(), orientation has
    # already been decided and the caller passes the winning-orientation image, so
    # injecting rotation candidates here would re-mix competing orientations.
    if _include_rotations:
        _existing_labels = {label for label, _ in results}
        try:
            import numpy as _np
            _arr_base = _np.asarray(img)

            for _angle, _label in ((90, "rot90"), (180, "rot180"), (270, "rot270")):
                if _label in _existing_labels:
                    continue
                try:
                    _rot_img = _rotate_image(_arr_base, _angle)
                    _rot_h, _rot_w = _rot_img.shape[:2]
                    _raw_rot, _fb_rot = _run_easyocr(reader, _rot_img, **base_kwargs)
                    if record_call is not None:
                        record_call(_fb_rot)
                    if _raw_rot:
                        _raw_remapped = _remap_raw_for_rotation(
                            _raw_rot, _angle, _rot_h, _rot_w
                        )
                        results.append((_label, _raw_remapped))
                        _existing_labels.add(_label)
                except Exception as _optional_exc:
                    _propagate_fatal_error(_optional_exc)
                    pass
        except Exception as _optional_exc:
            _propagate_fatal_error(_optional_exc)
            pass

    return results


def _exhaustive_with_orientation_selection(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
    *,
    quantize: bool = True,
    _dbnet_diag: "list[dict[str, Any]] | None" = None,
    _dbnet_precomputed: "tuple[bool, str | None] | None" = None,
    record_call: "Callable[[dict[str, Any] | None], None] | None" = None,
    _reader_cache: "dict[str, Any] | None" = None,
    _detection_stats: "dict[str, int] | None" = None,
    page_index: int = 0,
    language: str = "pt",
) -> "tuple[list[tuple[str, list[OcrToken]]], dict[str, Any]]":
    """Run exhaustive candidates with upfront orientation selection (B1/R73).

    Orientation is chosen BEFORE quality variant fusion, so tokens from losing
    orientations never enter candidate fusion.

    Flow:
      1. Quick single-pass probe per orientation (0°/90°/180°/270°) using the
         same logic as _adaptive_with_orientation_selection.
      2. Select winning orientation by _orientation_quality_score.
         If upright is sufficient, skip rotation probes entirely.
      3. Run full exhaustive variants on the winning orientation's image
         with _include_rotations=False — the orientation decision is final.
      4. Remap all candidate coordinates back to the original image space
         when the winning orientation is non-zero.
      5. Return (pipeline_candidates, orientation_diag).

    Both adaptive and exhaustive paths now share the same orientation selection
    logic (_orientation_quality_sufficient / _orientation_quality_score).
    """
    import numpy as _np_orient

    arr = _np_orient.asarray(img)

    # ── Step 1: orientation probe ──────────────────────────────────────────
    raw_upright, _fb_up = _run_easyocr(reader, img, **base_kwargs)
    if record_call is not None:
        record_call(_fb_up)
    upright_tokens = _result_to_pipeline_tokens(raw_upright, page_index, language, token_rotation=0)
    upright_score = _orientation_quality_score(upright_tokens)

    attempts: "list[dict[str, Any]]" = [{
        "angle": 0,
        "score": round(upright_score, 4),
        "token_count": len(upright_tokens),
        "sufficient": _orientation_quality_sufficient(upright_tokens),
    }]

    selected_angle = 0
    selected_img: "Any" = img  # original image — replaced if a rotation wins

    if not _orientation_quality_sufficient(upright_tokens):
        best_score = upright_score

        for _angle, _rot_label in ((90, "rot90"), (180, "rot180"), (270, "rot270")):
            try:
                _rot_img = _rotate_image(arr, _angle)
                _rot_h, _rot_w = _rot_img.shape[:2]
                _raw_rot, _fb_rot = _run_easyocr(reader, _rot_img, **base_kwargs)
                if record_call is not None:
                    record_call(_fb_rot)
                if _raw_rot:
                    _remapped = _remap_raw_for_rotation(_raw_rot, _angle, _rot_h, _rot_w)
                    _rot_tokens = _result_to_pipeline_tokens(
                        _remapped,
                        page_index,
                        language,
                        token_rotation=_token_rotation_from_applied_correction(_angle),
                    )
                    _rot_score = _orientation_quality_score(_rot_tokens)
                    attempts.append({
                        "angle": _angle,
                        "score": round(_rot_score, 4),
                        "token_count": len(_rot_tokens),
                    })
                    if _rot_score > best_score:
                        best_score = _rot_score
                        selected_angle = _angle
                        selected_img = _rot_img
                else:
                    attempts.append({"angle": _angle, "score": 0.0, "token_count": 0})
            except Exception as _exc:
                _propagate_fatal_error(_exc)

    orientation_diag: "dict[str, Any]" = {
        "selected_angle": selected_angle,
        "attempts": attempts,
    }

    # ── Step 2: exhaustive quality variants on winning orientation ─────────
    raw_candidates = _exhaustive_candidates(
        reader, selected_img, base_kwargs,
        quantize=quantize,
        _dbnet_diag=_dbnet_diag,
        _dbnet_precomputed=_dbnet_precomputed,
        record_call=record_call,
        _reader_cache=_reader_cache,
        _detection_stats=_detection_stats,
        _include_rotations=False,  # orientation already decided above
    )

    # ── Step 3: convert to pipeline tokens in original image space ────────
    sel_arr = _np_orient.asarray(selected_img)
    sel_h, sel_w = sel_arr.shape[:2]

    pipeline_candidates: "list[tuple[str, list[OcrToken]]]" = []
    for label, raw in raw_candidates:
        # For selected_angle==0, _remap_raw_for_rotation returns raw unchanged.
        remapped = _remap_raw_for_rotation(raw, selected_angle, sel_h, sel_w)
        pl_tokens = _result_to_pipeline_tokens(
            remapped,
            page_index,
            language,
            token_rotation=_token_rotation_from_applied_correction(selected_angle),
        )
        pipeline_candidates.append((label, pl_tokens))

    return pipeline_candidates, orientation_diag


def _reader_init_options(reader: Any) -> dict[str, Any] | None:
    """Recover constructor inputs; upstream Reader does not retain languages.

    Backend-created Readers carry an explicit snapshot. Attribute fallback is
    retained for integrations that supply their own annotated Reader.
    """
    captured = getattr(reader, "_pdfextractor_init_options", None)
    if isinstance(captured, dict):
        return deepcopy(captured)
    languages = getattr(reader, "lang_list", None)
    if not languages:
        return None
    device = getattr(reader, "device", "cpu")
    quantize = getattr(reader, "quantize", True)
    # EasyOCR 1.7.2 stores this as a one-element tuple (including False).
    if isinstance(quantize, tuple) and len(quantize) == 1:
        quantize = quantize[0]
    options = {
        "lang_list": list(languages), "gpu": False if device == "cpu" else device,
        "quantize": bool(quantize),
    }
    for name in ("model_storage_directory", "user_network_directory", "recog_network"):
        value = getattr(reader, name, None)
        if value:
            options[name] = value
    return options


def _build_dbnet18_reader_strict(reader: "Any") -> "Any":
    """Build a DBNet18 EasyOCR Reader, propagating all exceptions.

    Used by the runtime probe so the caller can distinguish:
      - ``lang_list`` missing → returns None (not a real failure)
      - import error, model file missing, deformable-conv compile failure → raises

    The normal pipeline helper :func:`_build_dbnet18_reader` wraps this with a
    ``try/except`` so that a runtime failure never aborts the CRAFT-based extraction.
    """
    options = _reader_init_options(reader)
    if options is None:
        return None
    lang = options.pop("lang_list")
    import easyocr as _easyocr  # type: ignore
    options.update(detect_network="dbnet18", download_enabled=False, verbose=False)
    return _easyocr.Reader(lang, **options)


def _build_dbnet18_reader(reader: "Any") -> "Any":
    """Build a new EasyOCR Reader using DBNet18 detector instead of CRAFT (§11).

    CRAFT and DBNet18 use fundamentally different text region proposal strategies:
      - CRAFT: character-region affinity map, excellent for curved/complex layouts
      - DBNet18: differentiable binarisation, faster and better for straight text

    Running both provides an ensemble of independent detections.  The two readers
    produce different box sets; boxes present in only one are retained as candidates
    and evaluated by the candidate scoring system, so a genuine detection is not
    discarded even if the other detector misses it.

    Returns None when ``reader`` does not expose ``lang_list`` or when any
    instantiation error occurs (import failure, missing weights, compile error).
    The pipeline should always check for None before using the result.

    For a version that propagates exceptions (needed by the runtime probe), see
    :func:`_build_dbnet18_reader_strict`.
    """
    try:
        return _build_dbnet18_reader_strict(reader)
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        return None


def _rebuild_reader_no_quantize(reader: "Any") -> "Any":
    """Return a new EasyOCR Reader with quantize=False, sharing the same weights.

    EasyOCR's Reader stores its model networks on ``reader.detector`` and
    ``reader.recognizer``.  We cannot safely share the same model objects because
    quantize=True and quantize=False use different dtypes on the same weights.
    Instead, we build a fresh Reader from the same init parameters but with
    ``quantize=False``.

    Returns None when the reader does not expose the required attributes so the
    caller can skip the no_quantize candidate gracefully.
    """
    try:
        options = _reader_init_options(reader)
        if options is None:
            return None
        lang = options.pop("lang_list")
        import easyocr as _easyocr  # type: ignore
        options.update(quantize=False, download_enabled=False, verbose=False)
        return _easyocr.Reader(lang, **options)
    except Exception as _optional_exc:
        _propagate_fatal_error(_optional_exc)
        return None


class EasyOCRBackend:
    """OCRBackend using EasyOCR with PyTorch CPU inference.

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
    Models are stored in ~/.cache/pdfextractor/easyocr/ by default.

    All CRAFT detector thresholds, box-grouping parameters, and CRNN recognizer
    parameters are configurable via environment variables (see module docstring).
    The full parameter set is forwarded to both the nominal detect/recognize path
    and the readtext() fallback path for consistent behaviour.

    Quality policies (quality_policy arg to recognize_page):
      None / 'default': single EasyOCR call with configured parameters.
      'adaptive': at least 2 candidates (default + high-recall, plus adaptive
                  image preprocessing variants when image quality signals warrant
                  it); best wins.
      'exhaustive': evaluates a dynamic candidate set combining baseline,
                    detector, decoder, preprocessing, deskew, magnification,
                    orientation, and DBNet18 variants.  The total size varies
                    with image content and available dependencies.
                    Best candidate wins.

    See module docstring for all tunable environment variables.
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        self._closed = False
        from structured_pdf_text.ocr.env import env_bool, env_float, env_int
        from structured_pdf_text.ocr.languages import backend_language, canonical_language, easyocr_recognition_model
        self._language = canonical_language(config.language)
        self._langs = [backend_language(config.language, "easyocr")]

        # --- env-var + config-derived configuration ---
        raw_decoder = os.environ.get("EASYOCR_DECODER", "greedy").strip().lower()
        self._decoder: str = (
            raw_decoder if raw_decoder in ("greedy", "beamsearch", "wordbeamsearch") else "greedy"
        )
        self._beamwidth = env_int("EASYOCR_BEAMWIDTH", 5, minimum=1)
        # _workers is set later, after _env_probe is built, so the probe-aware
        # _resolve_workers() can use all available cores in max-quality mode.
        self._workers: int = 0
        self._adjust_contrast = env_float("EASYOCR_ADJUST_CONTRAST", 0.5, minimum=0.0, maximum=1.0)
        allowlist_env = os.environ.get("EASYOCR_ALLOWLIST", "")
        self._allowlist: str | None = allowlist_env if allowlist_env else None
        blocklist_env = os.environ.get("EASYOCR_BLOCKLIST", "")
        self._blocklist: str | None = blocklist_env if blocklist_env else None
        rotation_info_env = os.environ.get("EASYOCR_ROTATION_INFO", "")
        self._rotation_info: list[int] | None = _parse_rotation_info(rotation_info_env)
        self._mag_ratio = env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)

        # Detector parameters — all match EasyOCR upstream defaults
        self._text_threshold = env_float("EASYOCR_TEXT_THRESHOLD", 0.7, minimum=0.01, maximum=1.0)
        self._low_text = env_float("EASYOCR_LOW_TEXT", 0.4, minimum=0.01, maximum=1.0)
        self._link_threshold = env_float("EASYOCR_LINK_THRESHOLD", 0.4, minimum=0.01, maximum=1.0)
        self._min_size = env_int("EASYOCR_MIN_SIZE", 20, minimum=1)

        # Box grouping/merging parameters
        self._slope_ths = env_float("EASYOCR_SLOPE_THS", 0.1, minimum=0.0)
        self._ycenter_ths = env_float("EASYOCR_YCENTER_THS", 0.5, minimum=0.0)
        self._height_ths = env_float("EASYOCR_HEIGHT_THS", 0.5, minimum=0.0)
        self._width_ths = env_float("EASYOCR_WIDTH_THS", 0.5, minimum=0.0)
        self._add_margin = env_float("EASYOCR_ADD_MARGIN", 0.1, minimum=0.0)

        # Recognizer parameters
        self._contrast_ths = env_float("EASYOCR_CONTRAST_THS", 0.1, minimum=0.0, maximum=1.0)
        self._filter_ths = env_float("EASYOCR_FILTER_THS", 0.003, minimum=0.0)
        self._quantize = env_bool("EASYOCR_QUANTIZE", default=True)

        # Probe the execution environment once at init time so all resource
        # decisions (workers, torch threads) use a consistent snapshot.
        self._env_probe = _probe_environment()

        # Re-resolve workers now that the probe is available.
        self._workers = _resolve_workers(config.num_threads, self._env_probe)

        # Apply PyTorch thread limits before the Reader (and its model loading)
        # initialises, so all inference calls inherit the constrained thread pool.
        self._torch_num_threads, self._torch_num_interop_threads = _apply_torch_threads(
            config.num_threads, self._env_probe
        )

        # --- reader init ---
        easyocr_mod = _import_easyocr()
        recog_network = os.environ.get("EASYOCR_RECOG_NETWORK", "")
        self._recog_network = easyocr_recognition_model(config.language, recog_network)
        # Always resolve from _model_cache_dir() so the Reader and the readiness
        # probe check the same directory regardless of EASYOCR_MODULE_PATH being set.
        configured_cache = getattr(config, "ocr_cache_home", None)
        if configured_cache and not os.environ.get("EASYOCR_MODULE_PATH"):
            from pathlib import Path
            self._model_cache_dir = Path(configured_cache).expanduser() / "easyocr"
        else:
            self._model_cache_dir = _model_cache_dir()

        # Download is disabled by default so a benchmark run never touches the
        # network. Set EASYOCR_ALLOW_DOWNLOAD=1 only during the setup phase.
        allow_download = os.environ.get("EASYOCR_ALLOW_DOWNLOAD", "0") == "1"

        kwargs: dict[str, Any] = {
            "gpu": False,
            "verbose": False,
            "download_enabled": allow_download,
            # Always pass the resolved cache dir so Reader and readiness probe
            # check the same location, even when EASYOCR_MODULE_PATH is unset.
            "model_storage_directory": str(self._model_cache_dir),
            "quantize": self._quantize,
        }
        if recog_network:
            kwargs["recog_network"] = recog_network

        self._reader = easyocr_mod.Reader(self._langs, **kwargs)
        self._reader._pdfextractor_init_options = {
            "lang_list": list(self._langs), **kwargs,
        }

        # DBNet18 runtime state cached per instance.  None = not yet probed.
        # Populated lazily by _ensure_dbnet18_runtime(); never reset between pages.
        self._dbnet_runtime_state: bool | None = None
        self._dbnet_failure_reason: str | None = None
        # Alternate networks are loaded once, including unsuccessful builds.
        self._auxiliary_readers: dict[str, Any] = {}

        self.reset_page_diagnostics()

    # ------------------------------------------------------------------
    # DBNet18 per-instance runtime probe
    # ------------------------------------------------------------------

    def _ensure_dbnet18_runtime(self, *, force: bool = False) -> "tuple[bool, str | None]":
        """Return (available, failure_reason), running the probe at most once.

        The result is cached in ``_dbnet_runtime_state`` / ``_dbnet_failure_reason``
        for the lifetime of this backend instance.  Pass ``force=True`` to re-run
        (e.g. after provisioning new weights).

        This method must NOT be called from ``capabilities``; it is only called
        by code paths that actually need to execute DBNet18 (exhaustive planner,
        preflight, setup command).
        """
        if self._dbnet_runtime_state is None or force:
            if force:
                getattr(self, "_auxiliary_readers", {}).pop("dbnet18", None)
            ok, reason = _probe_dbnet18_runtime_uncached(
                self._reader, self._model_cache_dir
            )
            self._dbnet_runtime_state = ok
            self._dbnet_failure_reason = reason
        return self._dbnet_runtime_state, self._dbnet_failure_reason

    def reset_page_diagnostics(self) -> None:
        """Start a fresh per-page diagnostic accumulator for the extraction pipeline."""
        self._easyocr_calls = 0
        self.last_pass_count = 0
        self.last_batch_count = 0
        self._easyocr_fallback_count = 0
        self._easyocr_fallback_reasons: list[str] = []
        self.last_easyocr_fallback_used = False
        self.last_easyocr_fallback_reason: str | None = None
        self._last_candidate_diagnostics: list[dict[str, Any]] = []
        self._last_orientation_decision: dict[str, Any] = {}
        self._last_dbnet_diag: list[dict[str, Any]] = []
        self._detection_stats = {
            "easyocr_exhaustive_detection_calls": 0,
            "easyocr_exhaustive_detection_cache_hits": 0,
        }

    def consume_page_diagnostics(self) -> dict[str, Any]:
        """Return and reset accumulated diagnostics for one page.

        Keys include per-call fallback counters and, when multi-candidate
        policies were used, a ``candidate_diagnostics`` list with one entry per
        candidate carrying token_count, char_count, mean_confidence, score and
        a ``selected`` flag indicating which candidate was chosen.

        ``env_probe`` is included once per backend lifetime (not reset each page)
        and contains the runtime environment snapshot used for thread allocation:
        cpu_count_logical, cpu_count_physical, cpu_load_1m, max_quality_env.
        """
        dbnet_diag = list(getattr(self, "_last_dbnet_diag", []))
        dbnet_entry = dbnet_diag[0] if dbnet_diag else {}
        orient_decision = dict(getattr(self, "_last_orientation_decision", {}))
        result = {
            "easyocr_calls": self._easyocr_calls,
            **self._detection_stats,
            "easyocr_fallback_count": self._easyocr_fallback_count,
            "easyocr_fallback_rate": (
                self._easyocr_fallback_count / self._easyocr_calls
                if self._easyocr_calls else 0.0
            ),
            "easyocr_fallback_reasons": list(self._easyocr_fallback_reasons),
            "candidate_diagnostics": list(self._last_candidate_diagnostics),
            "env_probe": dict(getattr(self, "_env_probe", {})),
            "effective_workers": getattr(self, "_workers", None),
            "effective_torch_intra_threads": getattr(self, "_torch_num_threads", None),
            "dbnet_weights_available": dbnet_entry.get("weights_available"),
            "dbnet_runtime_available": dbnet_entry.get("runtime_available"),
            "dbnet_failure_reason": dbnet_entry.get("failure_reason"),
            "orientation_selected": orient_decision.get("selected_angle"),
            "orientation_attempts": list(orient_decision.get("attempts", [])),
        }
        self.reset_page_diagnostics()
        return result

    def _record_call(self, fallback: dict[str, Any] | None) -> None:
        self._easyocr_calls += 1
        # One completed image call per variant; upstream's per-box CRNN
        # batches are separate. Regional orchestration reads these counters.
        self.last_pass_count = getattr(self, "last_pass_count", 0) + 1
        self.last_batch_count = self.last_pass_count
        if fallback is not None:
            self._easyocr_fallback_count += 1
            reason = str(fallback.get("primary_error", "unknown"))
            self._easyocr_fallback_reasons.append(reason)
            self.last_easyocr_fallback_used = True
            self.last_easyocr_fallback_reason = reason
        else:
            self.last_easyocr_fallback_used = False
            self.last_easyocr_fallback_reason = None

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
            artifact_hashes={
                name: digest
                for name, path in (
                    ("craft_mlt_25k.pth", self._model_cache_dir / "craft_mlt_25k.pth"),
                    (f"{self._recog_network}.pth", self._model_cache_dir / f"{self._recog_network}.pth"),
                )
                if (digest := sha256_file(path)) is not None
            },
            extra={
                "render_scale": self._config.effective_ocr_render_scale(),
                "recognition_network": self._recog_network,
                "mag_ratio": self._mag_ratio,
                "canvas_size_policy": "int(mag_ratio * max(h, w))",
                "decoder": self._decoder,
                "beamwidth": self._beamwidth if self._decoder in ("beamsearch", "wordbeamsearch") else None,
                "workers": self._workers,
                "torch_num_threads": self._torch_num_threads,
                "torch_num_interop_threads": self._torch_num_interop_threads,
                "torch_thread_settings_process_global": True,
                "quantize": self._quantize,
                # Recognizer parameters
                "adjust_contrast": self._adjust_contrast,
                "contrast_ths": self._contrast_ths,
                "filter_ths": self._filter_ths,
                "allowlist": self._allowlist,
                "blocklist": self._blocklist,
                "rotation_info": self._rotation_info,
                # Detector parameters
                "text_threshold": self._text_threshold,
                "low_text": self._low_text,
                "link_threshold": self._link_threshold,
                "min_size": self._min_size,
                # Box grouping/merging
                "slope_ths": self._slope_ths,
                "ycenter_ths": self._ycenter_ths,
                "height_ths": self._height_ths,
                "width_ths": self._width_ths,
                "add_margin": self._add_margin,
                # Environment probe — recorded at init time
                "env_cpu_logical": self._env_probe.get("cpu_count_logical"),
                "env_cpu_physical": self._env_probe.get("cpu_count_physical"),
                "env_cpu_load_1m": self._env_probe.get("cpu_load_1m"),
                "env_max_quality_threads": self._env_probe.get("max_quality_env"),
            },
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        """Return static capability flags for this backend instance.

        ``multiple_detectors`` reflects the *cached* DBNet18 runtime probe result:
        - False (default): probe has not run yet, or DBNet18 is unavailable.
        - True: set only after a successful exhaustive page pass confirms that
          DBNet18 weights are present *and* inference is functional on this runtime.

        This property never triggers the DBNet18 probe; it only reads the cached
        state set by :meth:`_ensure_dbnet18_runtime` (called by ``recognize_page``
        with ``quality_policy="exhaustive"``).
        """
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=True,
            page_orientation=False,
            quadrilateral_boxes=True,
            per_token_confidence=True,
            polygons=True,
            direct_recognition=True,
            detector_profiles=True,
            decoder_profiles=True,
            orientation_search=True,
            multiple_detectors=self._dbnet_runtime_state is True,
            word_beam_search=True,
            native_confidence=True,
        )

    def _run_kwargs(self) -> "dict[str, Any]":
        """Return the full set of keyword arguments for _run_easyocr calls."""
        return {
            "decoder": self._decoder,
            "beamwidth": self._beamwidth,
            "adjust_contrast": self._adjust_contrast,
            "allowlist": self._allowlist,
            "blocklist": self._blocklist,
            "workers": self._workers,
            "rotation_info": self._rotation_info,
            "text_threshold": self._text_threshold,
            "low_text": self._low_text,
            "link_threshold": self._link_threshold,
            "min_size": self._min_size,
            "slope_ths": self._slope_ths,
            "ycenter_ths": self._ycenter_ths,
            "height_ths": self._height_ths,
            "width_ths": self._width_ths,
            "add_margin": self._add_margin,
            "contrast_ths": self._contrast_ths,
            "filter_ths": self._filter_ths,
        }

    # ------------------------------------------------------------------
    # OCRBackend — canonical recognize method (for benchmarking)
    # ------------------------------------------------------------------

    def recognize(self, request: OCRRequest) -> OCRResult:
        """Run OCR on an image crop and return a canonical OCRResult for the benchmarking layer.

        Falls back to EasyOCR's readtext() when the detect/recognize split fails;
        the result status is "recovered" in that case.  Returns "runtime_error"
        when the backend has been closed.
        """
        t0 = time.perf_counter()
        self.last_pass_count = self.last_batch_count = 0
        if self._closed:
            return OCRResult(
                status="runtime_error", tokens=(), text="", engine_identity=self.identity,
                elapsed_total_s=0.0, warnings=("EasyOCR backend is closed",),
            )
        try:
            img = _to_numpy(request.image)
            raw, fallback_info = _run_easyocr(self._reader, img, **self._run_kwargs())
            self._record_call(fallback_info)
            rx0, ry0 = (request.region_bbox[0], request.region_bbox[1]) if request.region_bbox else (0.0, 0.0)
            tokens = tuple(_result_to_ocr_tokens(raw, "easyocr", offset_x=rx0, offset_y=ry0))
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
        page_rotation: int = 0,
    ) -> list[OcrToken]:
        """OCR a full page image and return pipeline OcrToken instances.

        ``quality_policy`` controls the candidate strategy:
          - ``None`` / ``'default'``: single EasyOCR call.
          - ``'adaptive'``: default + high-recall + conditional preprocessing variants.
          - ``'exhaustive'``: runs :func:`_exhaustive_candidates`, collects DBNet18
            diagnostics via ``_last_dbnet_diag``, and selects the best candidate.

        When ``quality_policy='exhaustive'``, the DBNet18 runtime state is probed
        at most once per instance (via :meth:`_ensure_dbnet18_runtime`) and cached.
        If the real execution fails after a positive probe, the cache is invalidated
        so subsequent calls do not attempt DBNet18 again.
        """
        if self._closed:
            raise RuntimeError("EasyOCR backend is closed")
        self.last_pass_count = self.last_batch_count = 0
        img = _to_numpy(page_image)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page

        policy = (quality_policy or "").strip().lower()
        use_variants = quality_variants or policy in ("adaptive", "exhaustive")

        if use_variants:
            base_kwargs = self._run_kwargs()
            if policy == "exhaustive":
                if not hasattr(self, "_auxiliary_readers"):
                    self._auxiliary_readers = {}
                self._last_dbnet_diag = []
                dbnet_state = self._ensure_dbnet18_runtime()
                # B1/R73: orientation selection before quality fusion.
                # _exhaustive_with_orientation_selection probes 0°/90°/180°/270°,
                # selects the winning orientation, then runs full exhaustive variants
                # ONLY within that orientation.  Tokens from losing orientations
                # never enter _best_candidate / candidate fusion.
                pipeline_candidates, _orient_diag = _exhaustive_with_orientation_selection(
                    self._reader, img, base_kwargs,
                    quantize=self._quantize,
                    _dbnet_diag=self._last_dbnet_diag,
                    _dbnet_precomputed=dbnet_state,
                    record_call=self._record_call,
                    _reader_cache=self._auxiliary_readers,
                    _detection_stats=self._detection_stats,
                    page_index=page_index,
                    language=self._language,
                )
                self._last_orientation_decision = _orient_diag
                if (
                    self._last_dbnet_diag
                    and not self._last_dbnet_diag[-1].get("runtime_available", True)
                    and self._dbnet_runtime_state is True
                ):
                    self._dbnet_runtime_state = False
                    self._dbnet_failure_reason = self._last_dbnet_diag[-1].get("failure_reason")
                tokens, cand_diag = _best_candidate(pipeline_candidates)
                self._last_candidate_diagnostics = cand_diag
            else:
                # B1/R73: adaptive with global orientation selection before fusion.
                # Upright quality (confidence + content) drives the orientation probe;
                # only the winning orientation's candidates enter _best_candidate —
                # losing orientation tokens are excluded entirely.
                pipeline_candidates, _orient_diag = _adaptive_with_orientation_selection(
                    self._reader, img, base_kwargs, page_index, self._language,
                    record_call=self._record_call,
                )
                self._last_orientation_decision = _orient_diag
                tokens, cand_diag = _best_candidate(pipeline_candidates)
                self._last_candidate_diagnostics = cand_diag
            # _easyocr_calls is incremented per-call inside _record_call; no aggregate here
        else:
            raw, fallback = _run_easyocr(self._reader, img, **self._run_kwargs())
            self._record_call(fallback)
            tokens = _result_to_pipeline_tokens(raw, page_index, self._language)
            self._last_candidate_diagnostics = []

        return map_tokens_to_page(tokens, page_bbox, img.shape[1], img.shape[0], page_rotation)

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
        *,
        page_bbox: "BBox | None" = None,
    ) -> list[OcrToken]:
        """OCR a sub-region of a page and return tokens in page coordinate space.

        Crops ``region_bbox`` from the full page raster, runs a single EasyOCR pass,
        and maps token coordinates back to the page frame via the crop offset.
        """
        if self._closed:
            raise RuntimeError("EasyOCR backend is closed")
        self.last_pass_count = self.last_batch_count = 0
        img = _to_numpy(page_image)
        from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
        crop, (cx0, cy0, _cx1, _cy1), (width, height) = crop_region_in_raster(img, region_bbox, page_bbox)
        if crop.size == 0:
            return []
        raw, fallback = _run_easyocr(self._reader, crop, **self._run_kwargs())
        self._record_call(fallback)
        tokens = _result_to_pipeline_tokens(
            raw, page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            source=SourceKind.OCR_REGION,
        )
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, width, height)

    def recognize_direct(
        self, image: object, page_index: int, page_bbox: "BBox | None" = None,
        *, quality_policy: str | None = None,
    ) -> list[OcrToken]:
        """Recognize a known crop without text detection.

        quality_policy is accepted for compatibility with the common regional
        recognition interface. Direct recognition is a single recognizer pass,
        so candidate quality policies do not alter this path.
        """
        self.last_pass_count = self.last_batch_count = 0
        if self._closed:
            return []
        import numpy as np
        import cv2  # type: ignore
        image_array = _to_numpy(image)
        gray = cv2.cvtColor(image_array, cv2.COLOR_RGB2GRAY) if image_array.ndim == 3 else image_array
        height, width = gray.shape[:2]
        if not width or not height:
            return []
        result = self._reader.recognize(
            gray,
            [[0, width, 0, height]],
            [],
            decoder=self._decoder,
            beamWidth=self._beamwidth,
            allowlist=self._allowlist,
            blocklist=self._blocklist,
            workers=self._workers,
        )
        self._record_call(None)
        tokens = _result_to_pipeline_tokens(result or [], page_index, self._language)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, width, height)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        """Check that easyocr is importable and required model files exist on disk.

        Returns one of: "ready", "missing" (package not installed),
        "incomplete" (package OK but .pth files absent), "unknown" (error).
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
            return "incomplete"
        return "ready"

    def close(self) -> None:
        """Release the EasyOCR Reader and mark this backend as closed.

        Subsequent calls to ``recognize_page`` will raise RuntimeError.
        Idempotent — calling close() more than once is safe.
        """
        if self._closed:
            return
        self._reader = None
        getattr(self, "_auxiliary_readers", {}).clear()
        self._closed = True
