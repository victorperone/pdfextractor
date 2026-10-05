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

Optimization notes
------------------
- Detect/recognize split: mirrors EasyOCR's own readtext() implementation.
  reader.detect() returns aggregate lists (one entry per image); the first
  element [0] is extracted before passing to reader.recognize(), exactly as
  the upstream readtext() does.  reader.recognize() receives the grayscale
  image, matching the upstream contract.
  Falls back to readtext() on any failure, but marks the result as degraded
  so the benchmark can flag the run as partial.
- canvas_size: set to int(mag_ratio * max(h, w)) so the canvas is always large
  enough for the magnified image.  Using max(h, w) would clamp target_size back
  to max(h, w) inside resize_aspect_ratio(), neutralising mag_ratio entirely.
- decoder: defaults to 'greedy' (upstream default). Use EASYOCR_DECODER=beamsearch
  to enable beam search after validating there is a measurable quality gain.
"""
from __future__ import annotations

import os
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
        from structured_pdf_text.ocr.env import env_int
        return 0 if is_windows else env_int("EASYOCR_WORKERS", 0, minimum=0)

    if is_windows:
        return 0

    if config_num_threads > 0:
        return min(4, max(0, config_num_threads // 2))

    return _default_workers()


def _apply_torch_threads(num_threads: int) -> tuple[int | None, int | None]:
    """Apply global PyTorch thread limits and return both effective values."""
    try:
        import torch
    except Exception:
        return None, None
    if num_threads > 0:
        try:
            torch.set_num_threads(num_threads)
        except Exception:
            pass
        try:
            torch.set_num_interop_threads(max(1, num_threads // 2))
        except Exception:
            # Torch can reject inter-op changes after work starts. Report its
            # actual value and the separately effective intra-op value.
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
    from structured_pdf_text.ocr.env import env_float
    mag_ratio = env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)
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
            text_threshold=text_threshold, low_text=low_text,
            link_threshold=link_threshold, min_size=min_size,
            slope_ths=slope_ths, ycenter_ths=ycenter_ths,
            height_ths=height_ths, width_ths=width_ths, add_margin=add_margin,
            contrast_ths=contrast_ths, filter_ths=filter_ths,
            reason="reformat_input_unavailable",
        )

    h, w = img_color.shape[:2]
    # canvas_size must be at least mag_ratio * max(h, w); otherwise
    # resize_aspect_ratio() clamps target_size back to max(h, w) and
    # the magnification has no effect.
    canvas_size = int(mag_ratio * max(h, w))

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
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
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
        return _fallback_readtext(
            reader, arr,
            decoder=decoder, beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist, blocklist=blocklist, workers=workers,
            rotation_info=rotation_info,
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
    from structured_pdf_text.ocr.env import env_float
    mag_ratio = env_float("EASYOCR_MAG_RATIO", 1.2, minimum=0.01)
    h, w = arr.shape[:2]
    canvas_size = int(mag_ratio * max(h, w))

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


def _result_to_pipeline_tokens(
    raw: list[Any], page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
    source: "SourceKind" = SourceKind.OCR_PAGE,
) -> list[OcrToken]:
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
            polygon=pipeline_polygon,
        ))
    return tokens


def _candidate_metrics(tokens: "list[OcrToken]") -> "dict[str, float]":
    """Compute quality metrics for a candidate token list.

    Returns a dict with keys used both for scoring and for diagnostics:
      mean_confidence         — average OCR confidence (0..1)
      low_conf_ratio          — fraction of tokens below 0.60 confidence
      replacement_char_ratio  — fraction of characters that are U+FFFD or control chars
      duplicate_ratio         — fraction of token texts that are exact duplicates
      horizontal_ratio        — fraction of tokens with width >= height (horizontal text)
      char_count              — total non-whitespace characters
      token_count             — number of tokens
    """
    import math as _math
    import unicodedata as _ud

    n = len(tokens)
    if not n:
        return {
            "mean_confidence": 0.0, "low_conf_ratio": 1.0,
            "replacement_char_ratio": 1.0, "duplicate_ratio": 1.0,
            "horizontal_ratio": 0.0, "char_count": 0, "token_count": 0,
        }

    confidences = [t.confidence for t in tokens if t.confidence is not None]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    low_conf = sum(1 for c in confidences if c < 0.60) / max(len(confidences), 1)

    all_chars = "".join(t.text for t in tokens)
    bad_chars = sum(
        1 for ch in all_chars
        if ch == "�" or (_ud.category(ch).startswith("C") and ch not in " \t\n")
    )
    repl_ratio = bad_chars / max(len(all_chars), 1)

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
    }


def _score_candidate(tokens: "list[OcrToken]") -> float:
    """Score a candidate token list for selection. Higher is better.

    Scoring components:
      + mean_confidence              — primary quality signal
      + char_count bonus             — logarithmic, capped at 0.08, so content
                                       coverage matters but never overrides confidence
      + horizontal_ratio * 0.02      — small bonus for coherent horizontal text
      - low_conf_ratio * 0.15        — penalise high fraction of uncertain tokens
      - replacement_char_ratio * 0.30 — penalise garbled / control characters
      - duplicate_ratio * 0.10       — penalise repeated token texts (hallucination)

    A candidate may not win solely by having more characters if those characters
    are low-confidence, garbled, or duplicated (§22 rollback rule).
    """
    import math as _math
    if not tokens:
        return -_math.inf
    m = _candidate_metrics(tokens)
    score = (
        m["mean_confidence"]
        + min(0.08, _math.log1p(m["char_count"]) * 0.012)
        + m["horizontal_ratio"] * 0.02
        - m["low_conf_ratio"] * 0.15
        - m["replacement_char_ratio"] * 0.30
        - m["duplicate_ratio"] * 0.10
    )
    return score


def _best_candidate(
    candidates: "list[tuple[str, list[OcrToken]]]",
) -> "tuple[list[OcrToken], list[dict[str, Any]]]":
    """Return (best tokens, per-candidate diagnostics) sorted highest-score first.

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
    scored.sort(key=lambda x: x[0], reverse=True)

    _best_score, best_label, best_tokens, _best_metrics = scored[0]
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
            "score": round(score, 4) if _math.isfinite(score) else None,
            "selected": label == best_label,
        }
        for score, label, tokens, metrics in scored
    ]
    return best_tokens, diagnostics


def _adaptive_candidates(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
) -> "list[tuple[str, list[tuple[Any, Any, Any]]]]":
    """Run 2 EasyOCR candidates (default + high-recall) and return raw results.

    Used when quality_policy='adaptive' or quality_variants=True.  The high-recall
    candidate uses lower CRAFT thresholds to catch faint or small text that the
    default profile may miss.  Both share the same decoder and recognizer settings.
    """
    # Candidate A: default profile
    raw_a, _ = _run_easyocr(reader, img, **base_kwargs)

    # Candidate B: high-recall — lower detection thresholds
    hr_kwargs = dict(base_kwargs)
    hr_kwargs["text_threshold"] = min(base_kwargs["text_threshold"], 0.55)
    hr_kwargs["low_text"] = min(base_kwargs["low_text"], 0.30)
    hr_kwargs["link_threshold"] = min(base_kwargs["link_threshold"], 0.35)
    hr_kwargs["min_size"] = max(1, base_kwargs["min_size"] // 2)
    raw_b, _ = _run_easyocr(reader, img, **hr_kwargs)

    return [("default", raw_a), ("high_recall", raw_b)]


def _exhaustive_candidates(
    reader: "Any",
    img: "Any",
    base_kwargs: "dict[str, Any]",
) -> "list[tuple[str, list[tuple[Any, Any, Any]]]]":
    """Run 4 EasyOCR candidates (default, high-recall, beamsearch, layout-sensitive).

    Used when quality_policy='exhaustive'.  More expensive but maximises coverage
    and confidence for demanding extractions.
    """
    results = _adaptive_candidates(reader, img, base_kwargs)

    # Candidate C: beamsearch decoder (if not already)
    if base_kwargs.get("decoder") != "beamsearch":
        bs_kwargs = dict(base_kwargs)
        bs_kwargs["decoder"] = "beamsearch"
        raw_c, _ = _run_easyocr(reader, img, **bs_kwargs)
        results.append(("beamsearch", raw_c))

    # Candidate D: layout-sensitive — conservative merging to avoid cross-gutter joins
    ls_kwargs = dict(base_kwargs)
    ls_kwargs["width_ths"] = min(base_kwargs["width_ths"], 0.25)
    ls_kwargs["add_margin"] = min(base_kwargs["add_margin"], 0.05)
    raw_d, _ = _run_easyocr(reader, img, **ls_kwargs)
    results.append(("layout_sensitive", raw_d))

    return results


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
      'adaptive': two candidates (default + high-recall); best wins.
      'exhaustive': four candidates (default, high-recall, beamsearch,
                    layout-sensitive); best wins.

    See module docstring for all tunable environment variables.
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        self._closed = False
        from structured_pdf_text.ocr.env import env_bool, env_float, env_int
        from structured_pdf_text.ocr.languages import backend_language, canonical_language
        self._language = canonical_language(config.language)
        self._langs = [backend_language(config.language, "easyocr")]

        # --- env-var + config-derived configuration ---
        raw_decoder = os.environ.get("EASYOCR_DECODER", "greedy").strip().lower()
        self._decoder: str = (
            raw_decoder if raw_decoder in ("greedy", "beamsearch", "wordbeamsearch") else "greedy"
        )
        self._beamwidth = env_int("EASYOCR_BEAMWIDTH", 5, minimum=1)
        self._workers = _resolve_workers(config.num_threads)
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

        # Apply PyTorch thread limits before the Reader (and its model loading)
        # initialises, so all inference calls inherit the constrained thread pool.
        self._torch_num_threads, self._torch_num_interop_threads = _apply_torch_threads(config.num_threads)

        # --- reader init ---
        easyocr_mod = _import_easyocr()
        recog_network = os.environ.get("EASYOCR_RECOG_NETWORK", "")
        self._recog_network = recog_network or "latin_g2"
        # Always resolve from _model_cache_dir() so the Reader and the readiness
        # probe check the same directory regardless of EASYOCR_MODULE_PATH being set.
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
        self.reset_page_diagnostics()

    def reset_page_diagnostics(self) -> None:
        """Start a fresh per-page diagnostic accumulator for the extraction pipeline."""
        self._easyocr_calls = 0
        self._easyocr_fallback_count = 0
        self._easyocr_fallback_reasons: list[str] = []
        self.last_easyocr_fallback_used = False
        self.last_easyocr_fallback_reason: str | None = None
        self._last_candidate_diagnostics: list[dict[str, Any]] = []

    def consume_page_diagnostics(self) -> dict[str, Any]:
        """Return and reset accumulated diagnostics for one page.

        Keys include per-call fallback counters and, when multi-candidate
        policies were used, a ``candidate_diagnostics`` list with one entry per
        candidate carrying token_count, char_count, mean_confidence, score and
        a ``selected`` flag indicating which candidate was chosen.
        """
        result = {
            "easyocr_calls": self._easyocr_calls,
            "easyocr_fallback_count": self._easyocr_fallback_count,
            "easyocr_fallback_rate": (
                self._easyocr_fallback_count / self._easyocr_calls
                if self._easyocr_calls else 0.0
            ),
            "easyocr_fallback_reasons": list(self._easyocr_fallback_reasons),
            "candidate_diagnostics": list(self._last_candidate_diagnostics),
        }
        self.reset_page_diagnostics()
        return result

    def _record_call(self, fallback: dict[str, Any] | None) -> None:
        self._easyocr_calls += 1
        if fallback is not None:
            self._easyocr_fallback_count += 1
            reason = str(fallback.get("primary_error", "unknown"))
            self._easyocr_fallback_reasons.append(reason)
            self.last_easyocr_fallback_used = True
            self.last_easyocr_fallback_reason = reason

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
        t0 = time.perf_counter()
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
    ) -> list[OcrToken]:
        if self._closed:
            raise RuntimeError("EasyOCR backend is closed")
        img = _to_numpy(page_image)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page

        policy = (quality_policy or "").strip().lower()
        use_variants = quality_variants or policy in ("adaptive", "exhaustive")

        if use_variants:
            base_kwargs = self._run_kwargs()
            if policy == "exhaustive":
                raw_candidates = _exhaustive_candidates(self._reader, img, base_kwargs)
            else:
                raw_candidates = _adaptive_candidates(self._reader, img, base_kwargs)
            # Convert each candidate's raw result to pipeline tokens, pick best
            pipeline_candidates: list[tuple[str, list[OcrToken]]] = []
            for label, raw in raw_candidates:
                pl_tokens = _result_to_pipeline_tokens(raw, page_index, self._language)
                pipeline_candidates.append((label, pl_tokens))
            tokens, cand_diag = _best_candidate(pipeline_candidates)
            self._last_candidate_diagnostics = cand_diag
            self._easyocr_calls += len(raw_candidates)
        else:
            raw, fallback = _run_easyocr(self._reader, img, **self._run_kwargs())
            self._record_call(fallback)
            tokens = _result_to_pipeline_tokens(raw, page_index, self._language)
            self._last_candidate_diagnostics = []

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
            raise RuntimeError("EasyOCR backend is closed")
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
        if self._closed:
            return
        self._reader = None
        self._closed = True
