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

Optimization notes (Fase 9)
----------------------------
- --dpi: DPI hint computed from config.ocr_render_scale (72 × scale).
  Without this, Tesseract defaults to 70 DPI internally, treating small text
  as noise and deleting it — the primary cause of the 27.4% deletion_rate.
- textord_min_linesize=2.5: fixes a Tesseract bug where Portuguese diacritics
  (ã, ç, ê, õ) are read as a separate line of marks above the text (issue #4276).
- tessedit_char_blacklist=`: backtick in output creates invalid Markdown fences;
  blacklisting it eliminates the 3.57% invalid_markdown_rate entirely.
- textord_noise_rejrows/words=0: disables aggressive line/word deletion that
  misclassifies valid text as noise on low-DPI or uneven-scan pages.
- crunch_del_rating=40 (default 60): raises the confidence floor at which words
  are silently deleted; preserves more borderline-quality tokens.
- language_model_penalty_non_dict_word=0.05 (default 0.15): reduces the penalty
  for financial vocabulary (CNPJ, ATIVO, EBITDA, etc.) not in the PT dictionary.
- preserve_interword_spaces=1: preserves column spacing in table output.
- CLAHE preprocessing: local contrast enhancement applied before OCR using
  cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8)).  Falls back gracefully
  if OpenCV is unavailable.  Result is saved as grayscale PNG (Tesseract reads
  grayscale directly; saving as 'L' mode avoids redundant color conversion).

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
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.backends._parser_utils import safe_crop_array
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


def _tesseract_info() -> dict[str, str]:
    """Return version and tessdata directory from the installed Tesseract."""
    info: dict[str, str] = {"version": "unknown", "tessdata": "unknown"}
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout or result.stderr or ""
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("tesseract"):
                info["version"] = stripped.split()[-1]
        # tessdata dir is reported by --list-langs
        langs_result = subprocess.run(
            ["tesseract", "--list-langs"],
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
) -> str:
    """Run tesseract on image, return TSV output string."""
    pil = _to_pil(image)
    pil = _clahe_preprocess(pil)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        pil.save(tmp, format="PNG")
        cmd = [
            "tesseract", tmp, "stdout",
            "--oem", str(oem),
            "--psm", str(psm),
            "-l", lang,
            "--dpi", str(dpi),
            "-c", "textord_min_linesize=2.5",
            "-c", "tessedit_char_blacklist=`",
            "-c", "textord_noise_rejrows=0",
            "-c", "textord_noise_rejwords=0",
            "-c", "crunch_del_rating=40",
            "-c", "language_model_penalty_non_dict_word=0.05",
            "-c", "preserve_interword_spaces=1",
            *extra_flags,
            "tsv",
        ]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        return result.stdout
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
) -> list[OCRToken]:
    tokens = []
    for row in rows:
        parsed = _valid_word_row(row, conf_min=conf_min)
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
            source=SourceKind.OCR_PAGE,
        ))
    return tokens


class TesseractBackend:
    """OCRBackend using the Tesseract 5 CLI (subprocess, no pytesseract).

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.

    See module docstring for all tunable environment variables.
    """

    def __init__(self, config: "ExtractorConfig") -> None:
        self._config = config
        self._language = config.language
        self._tess_lang = os.environ.get(
            "TESSERACT_LANG",
            _LANG_MAP.get(config.language, "por"),
        )
        self._psm = int(os.environ.get("TESSERACT_PSM", "3"))
        self._oem = int(os.environ.get("TESSERACT_OEM", "1"))

        # DPI: computed from render scale so Tesseract never falls back to 70 DPI.
        default_dpi = int(72 * getattr(config, "ocr_render_scale", 2.0))
        self._dpi = int(os.environ.get("TESSERACT_DPI", str(default_dpi)))

        # Optional tessdata_best directory.
        tessdata_dir = os.environ.get("TESSERACT_TESSDATA_DIR", "")
        self._extra_flags: list[str] = (
            ["--tessdata-dir", tessdata_dir] if tessdata_dir else []
        )

        # Minimum word confidence filter (0 = no filter, matches previous behaviour).
        self._conf_min = float(os.environ.get("TESSERACT_CONF_MIN", "0"))

        info = _tesseract_info()
        self._version = info["version"]
        self._tessdata = info["tessdata"]

    # ------------------------------------------------------------------
    # OCRBackend — identity and capabilities
    # ------------------------------------------------------------------

    @property
    def identity(self) -> OCRBackendIdentity:
        return OCRBackendIdentity(
            engine="tesseract",
            runtime="tesseract-cli",
            profile=f"{self._tess_lang}-psm{self._psm}-oem{self._oem}",
            language=self._language,
            device="cpu",
            package_versions={
                "tesseract": self._version,
                "tessdata_dir": self._tessdata,
                "lang": self._tess_lang,
            },
            artifact_hashes={},
        )

    @property
    def capabilities(self) -> OCRCapabilities:
        return OCRCapabilities(
            detection=True,
            recognition=True,
            line_orientation=False,
            page_orientation=True,  # OSD mode
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
            tsv = _run_tesseract_tsv(
                request.image, self._tess_lang, self._psm, self._oem,
                dpi=dpi, extra_flags=self._extra_flags,
            )
            rows = _parse_tsv(tsv)
            tokens = tuple(_tsv_to_ocr_tokens(rows, "tesseract", self._conf_min))
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
        tsv = _run_tesseract_tsv(
            page_image, self._tess_lang, self._psm, self._oem,
            dpi=self._dpi, extra_flags=self._extra_flags,
        )
        return _tsv_to_pipeline_tokens(
            _parse_tsv(tsv), page_index, self._language,
            conf_min=self._conf_min,
        )

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: "BBox",
    ) -> list[OcrToken]:
        import numpy as np
        from PIL import Image

        pil = _to_pil(page_image)
        arr = np.array(pil)
        crop_arr, (cx0, cy0, _cx1, _cy1) = safe_crop_array(
            arr, region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1,
        )
        if crop_arr.size == 0:
            return []
        crop_pil = Image.fromarray(crop_arr)

        tsv = _run_tesseract_tsv(
            crop_pil, self._tess_lang, self._psm, self._oem,
            dpi=self._dpi, extra_flags=self._extra_flags,
        )
        return _tsv_to_pipeline_tokens(
            _parse_tsv(tsv), page_index, self._language,
            offset_x=float(cx0), offset_y=float(cy0),
            conf_min=self._conf_min,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def healthcheck(self) -> str:
        try:
            result = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout + result.stderr
            if self._tess_lang in output:
                return "ready"
            return "missing"
        except FileNotFoundError:
            return "missing"
        except Exception:
            return "unknown"

    def close(self) -> None:
        pass
