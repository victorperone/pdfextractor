"""Tesseract 5 backend — OCRBackend implementation using the Tesseract CLI.

No Python wrapper dependency: uses subprocess to call the tesseract executable
and parses the TSV output directly. Requires:
  - tesseract 5.x in PATH
  - por.traineddata in tessdata directory (for Portuguese)

Language mapping: config.language "pt" → Tesseract "-l por"
PSM 3 (auto page segmentation) and OEM 1 (LSTM only) are the defaults.

Environment variables
---------------------
TESSERACT_LANG           Override language (default: derived from config, e.g. "por").
                         Use "por+eng" for documents with mixed Portuguese/English.
TESSERACT_PSM            Page segmentation mode (default: 3 = auto).
                         Alternatives: 4 (single column), 6 (uniform block),
                         11 (sparse text — recovers more on complex layouts).
TESSERACT_OEM            OCR engine mode (default: 1 = LSTM only).
TESSERACT_DPI            Override DPI hint (default: 72 × ocr_render_scale,
                         e.g. 144 for the default scale=2.0).
                         Set to 300 if rendering PDF pages at 300 DPI.
TESSERACT_TESSDATA_DIR   Path to a tessdata directory; enables tessdata_best.
                         Download por.traineddata from:
                         https://github.com/tesseract-ocr/tessdata_best
                         then: $env:TESSERACT_TESSDATA_DIR = "C:\\tessdata_best"
TESSERACT_CONF_MIN       Minimum word confidence (0–100, default: 0 = no filter).
                         Values 30–50 can remove low-quality noise tokens.
TESSERACT_OSD            Set to 1 to enable OSD pre-flight (PSM 0) before each page.
                         When confidence >= TESSERACT_OSD_CONF_MIN the image is
                         rotated before recognition. Requires osd.traineddata.
                         Default: 0 (disabled). Enables page_orientation capability.
TESSERACT_OSD_CONF_MIN   Minimum OSD orientation confidence to apply rotation
                         (default: 2.0). Values below this are treated as "no rotation".

Named profiles
--------------
The default profile uses upstream Tesseract segmentation and recognition
settings. ``tesseract-pt-financial-v1`` and ``tesseract-degraded-scan-v1``
enable corpus-tuned parameters; the degraded profile also applies CLAHE.

OMP_THREAD_LIMIT note: set OMP_THREAD_LIMIT=1 in the environment when running
many Tesseract processes in parallel — the default 4 OMP threads per process
causes contention and is slower than 1 thread × N parallel processes.
"""
from __future__ import annotations

import csv
import io
import math
import os
import subprocess
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import safe_crop_array, sha256_file
from structured_pdf_text.ocr.contracts import (
    OCRBackendIdentity,
    OCRCapabilities,
    OCRRequest,
    OCRResult,
    OCRToken,
)

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig


_LANG_MAP: dict[str, str] = {
    "pt": "por",
    "por": "por",
    "en": "eng",
    "eng": "eng",
}


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _tesseract_info(executable: str = "tesseract", extra_flags: list[str] | None = None) -> dict[str, str]:
    """Return version and tessdata directory from the installed Tesseract."""
    info: dict[str, str] = {"version": "unknown", "tessdata": "unknown"}
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout or result.stderr or ""
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("tesseract"):
                info["version"] = stripped.split()[-1]
        # tessdata dir is reported by --list-langs
        langs_result = subprocess.run(
            [executable, *(extra_flags or []), "--list-langs"],
            capture_output=True, text=True, timeout=10,
        )
        langs_output = langs_result.stdout + langs_result.stderr
        for line in langs_output.splitlines():
            if "tessdata" in line.lower() and ("/" in line or "\\" in line):
                import re
                m = re.search(r'"([^"]+)"', line)
                if m:
                    info["tessdata"] = m.group(1)
                    break
    except Exception:
        pass
    return info


def _clahe_preprocess(pil_image: object) -> object:
    """Apply CLAHE local contrast enhancement and return grayscale PIL Image.

    Converts to grayscale, applies CLAHE (clipLimit=2.0, tileGridSize=8×8),
    and returns an 'L' mode PIL Image.  Tesseract reads grayscale directly,
    so saving as 'L' avoids a redundant color conversion round-trip.

    Falls back to the original image unchanged if OpenCV is unavailable.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image

        arr = np.asarray(pil_image)
        if arr.ndim == 3:
            try:
                gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            except Exception:
                gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        elif arr.ndim == 2:
            gray = arr.astype(np.uint8)
        else:
            return pil_image
        gray = np.clip(gray, 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        return Image.fromarray(enhanced, mode="L")
    except Exception:
        return pil_image


def _to_pil(image: object) -> object:
    """Convert numpy array or passthrough PIL Image."""
    try:
        import numpy as np
        if isinstance(image, np.ndarray):
            from PIL import Image
            return Image.fromarray(image)
    except ImportError:
        pass
    return image  # assume already PIL


def _run_tesseract_tsv(
    image: object,
    lang: str,
    psm: int,
    oem: int,
    dpi: int,
    extra_flags: list[str],
    executable: str = "tesseract",
    profile: str = "default",
    num_threads: int = -1,
) -> str:
    """Run tesseract on image, return TSV output string."""
    pil = _to_pil(image)
    if profile == "tesseract-degraded-scan-v1":
        pil = _clahe_preprocess(pil)
    profile_options = {
        "default": [],
        "tesseract-pt-financial-v1": [
            "textord_min_linesize=2.5", "textord_noise_rejrows=0",
            "textord_noise_rejwords=0", "crunch_del_rating=40",
            "language_model_penalty_non_dict_word=0.05", "preserve_interword_spaces=1",
        ],
        "tesseract-degraded-scan-v1": [
            "textord_min_linesize=2.5", "textord_noise_rejrows=0",
            "textord_noise_rejwords=0", "crunch_del_rating=40",
            "language_model_penalty_non_dict_word=0.05", "preserve_interword_spaces=1",
        ],
    }
    if profile not in profile_options:
        raise ValueError(f"Unknown Tesseract profile: {profile!r}")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        pil.save(tmp, format="PNG")
        cmd = [
            executable, tmp, "stdout",
            "--oem", str(oem),
            "--psm", str(psm),
            "-l", lang,
            "--dpi", str(dpi),
            *[arg for option in profile_options[profile] for arg in ("-c", option)],
            *extra_flags,
            "tsv",
        ]
        env = None
        if num_threads != -1:
            env = dict(os.environ)
            env["OMP_THREAD_LIMIT"] = str(max(1, os.cpu_count() or 1) if num_threads == 0 else num_threads)
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", timeout=120, env=env,
        )
        if result.returncode != 0:
            stderr_snippet = (result.stderr or "")[:400].strip()
            raise RuntimeError(
                f"tesseract exited with code {result.returncode}: {stderr_snippet}"
            )
        return result.stdout
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _run_tesseract_osd(image: object, extra_flags: list[str], executable: str = "tesseract") -> dict | None:
    """Run Tesseract PSM 0 (OSD-only) and return orientation info, or None on failure.

    Returns a dict with keys ``rotate`` (int, degrees to apply to correct the
    image) and ``confidence`` (float) when OSD succeeds with sufficient output.
    Returns ``None`` when the subprocess fails, times out, or produces no
    parseable output — callers must treat None as "no rotation detected".

    Requires ``osd.traineddata`` in the active tessdata directory.
    """
    pil = _to_pil(image)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        pil.save(tmp, format="PNG")
        cmd = [
            executable, tmp, "stdout",
            "--psm", "0",
            "-l", "osd",
            *extra_flags,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if result.returncode != 0:
            return None
        out = result.stdout
        rotate: int | None = None
        confidence: float | None = None
        for line in out.splitlines():
            if line.startswith("Rotate:"):
                try:
                    rotate = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("Orientation confidence:"):
                try:
                    confidence = float(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if rotate is None or confidence is None:
            return None
        return {"rotate": rotate, "confidence": confidence}
    except Exception:
        return None
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _parse_tsv(tsv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    return list(reader)


def _valid_word_row(
    row: dict,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    conf_min: float = 0.0,
):
    """Parse a valid Tesseract word row into text, score, and bounds."""
    if row.get("level") != "5":
        return None
    text = (row.get("text") or "").strip()
    if not text:
        return None
    try:
        score = float(row["conf"])
        left = float(row["left"]) + offset_x
        top = float(row["top"]) + offset_y
        width = float(row["width"])
        height = float(row["height"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(score) or score < 0.0 or score > 100.0:
        return None
    if score < conf_min:
        return None
    if not all(math.isfinite(value) for value in (left, top, width, height)):
        return None
    if width <= 0.0 or height <= 0.0:
        return None
    right, bottom = left + width, top + height
    if not math.isfinite(right) or not math.isfinite(bottom):
        return None
    return text, score, (left, top, right, bottom)


def _tsv_to_ocr_tokens(
    rows: list[dict],
    source_engine: str,
    conf_min: float = 0.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> list[OCRToken]:
    """Convert Tesseract TSV rows to canonical OCRToken list.

    offset_x/offset_y shift all coordinates into page-pixel space when the
    image passed to Tesseract was a pre-cropped region.
    """
    tokens = []
    for row in rows:
        parsed = _valid_word_row(row, offset_x=offset_x, offset_y=offset_y, conf_min=conf_min)
        if parsed is None:
            continue
        text, score, (x0, y0, x1, y1) = parsed
        polygon = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        tokens.append(OCRToken(
            text=text,
            polygon_px=polygon,
            bbox_px=(x0, y0, x1, y1),
            confidence_native=score,
            confidence_scale="0..100",
            level="word",
            source_engine=source_engine,
        ))
    return tokens


def _tsv_to_pipeline_tokens(
    rows: list[dict], page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
    conf_min: float = 0.0,
    source: "SourceKind" = SourceKind.OCR_PAGE,
) -> list[OcrToken]:
    tokens = []
    for row in rows:
        parsed = _valid_word_row(row, offset_x, offset_y, conf_min=conf_min)
        if parsed is None:
            continue
        text, score, (x0, y0, x1, y1) = parsed
        try:
            bbox = BBox(x0, y0, x1, y1)
        except (TypeError, ValueError):
            continue
        tokens.append(OcrToken(
            text=text,
            bbox=bbox,
            confidence=score / 100.0,
            language=language,
            source=source,
        ))
    return tokens


class TesseractBackend:
    """OCRBackend using the Tesseract 5 CLI (subprocess, no pytesseract).

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.

    See module docstring for all tunable environment variables.
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        from structured_pdf_text.ocr.env import env_bool, env_float, env_int
        from structured_pdf_text.ocr.languages import backend_language, canonical_language
        self._language = canonical_language(config.language)
        self._tesseract_cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract") or "tesseract"
        self._profile = os.environ.get("TESSERACT_PROFILE", "default")
        if self._profile not in {"default", "tesseract-pt-financial-v1", "tesseract-degraded-scan-v1"}:
            from structured_pdf_text.errors import ConfigurationError
            raise ConfigurationError(f"Unsupported TESSERACT_PROFILE: {self._profile!r}")
        tessdata_dir = os.environ.get("TESSERACT_TESSDATA_DIR", "")
        self._effective_tessdata_dir = tessdata_dir or None
        self._extra_flags: list[str] = ["--tessdata-dir", tessdata_dir] if tessdata_dir else []
        self._tess_lang = os.environ.get(
            "TESSERACT_LANG",
            backend_language(config.language, "tesseract"),
        )
        self._psm = env_int("TESSERACT_PSM", 3, allowed=set(range(14)))
        self._oem = env_int("TESSERACT_OEM", 1, allowed={0, 1, 2, 3})

        # DPI: computed from render scale so Tesseract never falls back to 70 DPI.
        default_dpi = int(72 * config.effective_ocr_render_scale())
        self._dpi = env_int("TESSERACT_DPI", default_dpi, minimum=1)

        languages = self._tess_lang.split("+")
        if not languages or any(language not in {"por", "eng"} for language in languages):
            from structured_pdf_text.errors import ConfigurationError
            raise ConfigurationError(f"Unsupported Tesseract language list: {self._tess_lang!r}")

        # Minimum word confidence filter (0 = no filter, matches previous behaviour).
        self._conf_min = env_float("TESSERACT_CONF_MIN", 0.0, minimum=0.0, maximum=100.0)

        # OSD pre-flight: run PSM 0 before recognition and rotate the image
        # when orientation confidence is sufficient (>= 2.0).
        # Disabled by default; enable with TESSERACT_OSD=1.
        self._osd_enabled: bool = env_bool("TESSERACT_OSD", False)
        self._osd_conf_min: float = env_float("TESSERACT_OSD_CONF_MIN", 2.0, minimum=0.0)

        info = _tesseract_info(self._tesseract_cmd, self._extra_flags)
        self._version = info["version"]
        self._tessdata = self._effective_tessdata_dir or info["tessdata"]

        # Hash the .traineddata file at construction time for reproducibility.
        # Returns None if the file is absent (missing model); stored in identity.
        self._artifact_hashes = {}
        for language in languages:
            traineddata_path = Path(self._tessdata) / f"{language}.traineddata"
            digest = sha256_file(traineddata_path)
            if digest:
                self._artifact_hashes[f"{language}.traineddata"] = digest

    def _run_effective_tesseract(self, image: object, dpi: int) -> tuple[str, int, int, int]:
        """Run the configured executable/profile/thread policy for either API.

        Returns TSV, selected OSD rotation, and original image dimensions.
        Keeping this in one place prevents RAW benchmarks and document OCR
        from silently measuring different Tesseract installations.
        """
        pil = _to_pil(image)
        original_width, original_height = pil.size
        osd_rotation = 0
        if self._osd_enabled:
            osd = _run_tesseract_osd(pil, self._extra_flags, self._tesseract_cmd)
            if osd is not None and osd["confidence"] >= self._osd_conf_min and osd["rotate"] != 0:
                pil = pil.rotate(-osd["rotate"], expand=True)
                osd_rotation = int(osd["rotate"]) % 360
        tsv = _run_tesseract_tsv(
            pil, self._tess_lang, self._psm, self._oem,
            dpi=dpi,
            extra_flags=self._extra_flags,
            executable=self._tesseract_cmd,
            profile=self._profile,
            num_threads=self._config.num_threads,
        )
        return tsv, osd_rotation, original_width, original_height

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        tessdata_dir_env = os.environ.get("TESSERACT_TESSDATA_DIR", "")
        effective_language = "+".join(
            "pt-BR" if item == "por" else "en" if item == "eng" else item
            for item in self._tess_lang.split("+")
        )
        return OCRBackendIdentity(
            engine="tesseract",
            runtime="tesseract-cli",
            profile=self._profile,
            language=effective_language,
            device="cpu",
            package_versions={
                "tesseract": self._version,
                "tessdata_dir": self._tessdata,
                "lang": self._tess_lang,
            },
            artifact_hashes=self._artifact_hashes,
            extra={
                "render_scale": self._config.effective_ocr_render_scale(),
                "requested_language": self._language,
                "effective_language": effective_language,
                "effective_tesseract_lang": self._tess_lang,
                "psm": self._psm,
                "oem": self._oem,
                "effective_dpi": self._dpi,
                "conf_min": self._conf_min,
                "clahe": self._profile == "tesseract-degraded-scan-v1",
                "tessdata_dir_override": tessdata_dir_env or None,
                "tesseract_cmd": self._tesseract_cmd,
                "requested_threads": self._config.num_threads,
                "effective_threads": max(1, os.cpu_count() or 1) if self._config.num_threads == 0 else self._config.num_threads,
                "active_page_orientation": self._osd_enabled,
                "osd_conf_min": self._osd_conf_min if self._osd_enabled else None,
            },
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=False,
            page_orientation=self._osd_enabled,  # True only when TESSERACT_OSD=1
            quadrilateral_boxes=False,  # axis-aligned only
            per_token_confidence=True,
        )

    # ------------------------------------------------------------------
    # OCRBackend — canonical recognize method (for benchmarking)
    # ------------------------------------------------------------------

    def recognize(self, request: OCRRequest) -> OCRResult:
        t0 = time.perf_counter()
        # Use DPI from request when available (benchmark sets it explicitly).
        dpi = request.dpi if request.dpi else self._dpi
        try:
            tsv, _rotation, _width, _height = self._run_effective_tesseract(request.image, dpi)
            rows = _parse_tsv(tsv)
            rx0, ry0 = (request.region_bbox[0], request.region_bbox[1]) if request.region_bbox else (0.0, 0.0)
            tokens = tuple(_tsv_to_ocr_tokens(rows, "tesseract", self._conf_min, offset_x=rx0, offset_y=ry0))
            text = " ".join(t.text for t in tokens)
            status = "ok" if tokens else "no_text"
        except FileNotFoundError:
            return OCRResult(
                status="model_missing",
                tokens=(),
                text="",
                engine_identity=self.identity,
                elapsed_total_s=time.perf_counter() - t0,
                warnings=("tesseract executable not found in PATH",),
            )
        except subprocess.TimeoutExpired:
            return OCRResult(
                status="timeout",
                tokens=(),
                text="",
                engine_identity=self.identity,
                elapsed_total_s=time.perf_counter() - t0,
                warnings=("tesseract subprocess timed out",),
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
        tsv, osd_rotation, original_width, original_height = self._run_effective_tesseract(
            page_image, self._dpi
        )
        tokens = _tsv_to_pipeline_tokens(
            _parse_tsv(tsv), page_index, self._language,
            conf_min=self._conf_min,
        )
        if osd_rotation:
            tokens = _map_rotated_tokens_to_original(tokens, osd_rotation, original_width, original_height)
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, original_width, original_height)

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
        *,
        page_bbox: "BBox | None" = None,
    ) -> list[OcrToken]:
        import numpy as np
        from PIL import Image

        pil = _to_pil(page_image)
        arr = np.array(pil)
        from structured_pdf_text.ocr.backends._parser_utils import crop_region_in_raster
        crop_arr, (cx0, cy0, _cx1, _cy1), (width, height) = crop_region_in_raster(arr, region_bbox, page_bbox)
        if crop_arr.size == 0:
            return []
        crop_pil = Image.fromarray(crop_arr)

        tsv = _run_tesseract_tsv(
            crop_pil, self._tess_lang, self._psm, self._oem,
            dpi=self._dpi, extra_flags=self._extra_flags,
            executable=self._tesseract_cmd,
            profile=self._profile,
            num_threads=self._config.num_threads,
        )
        tokens = _tsv_to_pipeline_tokens(
            _parse_tsv(tsv), page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            conf_min=self._conf_min,
            source=SourceKind.OCR_REGION,
        )
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        return map_tokens_to_page(tokens, page_bbox, width, height)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        try:
            result = subprocess.run(
                [self._tesseract_cmd, *self._extra_flags, "--list-langs"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return "unknown"
            output = result.stdout + result.stderr
            available = {
                line.strip() for line in output.splitlines()
                if line.strip() and "tessdata" not in line.casefold()
            }
            required_languages = self._tess_lang.split("+") + (["osd"] if self._osd_enabled else [])
            if all(language in available for language in required_languages):
                return "ready"
            return "missing"
        except FileNotFoundError:
            return "missing"
        except Exception:
            return "unknown"

    def close(self) -> None:
        pass


def _map_rotated_tokens_to_original(
    tokens: list[OcrToken], clockwise_rotation: int, original_width: int, original_height: int
) -> list[OcrToken]:
    """Map boxes from Tesseract's OSD-corrected raster back to input pixels."""
    mapped: list[OcrToken] = []
    for token in tokens:
        box = token.bbox
        if clockwise_rotation == 90:
            bbox = BBox(box.y0, original_height - box.x1, box.y1, original_height - box.x0)
        elif clockwise_rotation == 180:
            bbox = BBox(original_width - box.x1, original_height - box.y1,
                        original_width - box.x0, original_height - box.y0)
        elif clockwise_rotation == 270:
            bbox = BBox(original_width - box.y1, box.x0, original_width - box.y0, box.x1)
        else:
            bbox = box
        mapped.append(OcrToken(text=token.text, bbox=bbox, confidence=token.confidence,
                               language=token.language, source=token.source,
                               rotation=token.rotation, provenance=token.provenance))
    return mapped
