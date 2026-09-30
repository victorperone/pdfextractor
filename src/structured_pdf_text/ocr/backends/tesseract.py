"""Tesseract 5 backend — OCRBackend implementation using the Tesseract CLI.

No Python wrapper dependency: uses subprocess to call the tesseract executable
and parses the TSV output directly. Requires:
  - tesseract 5.x in PATH
  - por.traineddata in tessdata directory (for Portuguese)

Language mapping: config.language "pt" → Tesseract "-l por"
PSM 3 (auto page segmentation) and OEM 1 (LSTM only) are the defaults.
Override via environment variables:
    TESSERACT_PSM=6     (e.g. single uniform text block)
    TESSERACT_OEM=3     (e.g. legacy + LSTM)
    TESSERACT_LANG=por  (override language directly)
"""
from __future__ import annotations

import csv
import io
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

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
                # Extract the path from e.g. 'List of available languages in "C:\...tessdata/" (3):'
                import re
                m = re.search(r'"([^"]+)"', line)
                if m:
                    info["tessdata"] = m.group(1)
                    break
    except Exception:
        pass
    return info


def _to_pil(image: object):
    """Convert numpy array or passthrough PIL Image."""
    try:
        import numpy as np
        if isinstance(image, np.ndarray):
            from PIL import Image
            return Image.fromarray(image)
    except ImportError:
        pass
    return image  # assume already PIL


def _run_tesseract_tsv(image: object, lang: str, psm: int, oem: int) -> str:
    """Run tesseract on image, return TSV output string."""
    pil = _to_pil(image)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        pil.save(tmp, format="PNG")
        result = subprocess.run(
            ["tesseract", tmp, "stdout",
             "--oem", str(oem),
             "--psm", str(psm),
             "-l", lang,
             "tsv"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        return result.stdout
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _parse_tsv(tsv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    return list(reader)


def _tsv_to_ocr_tokens(rows: list[dict], source_engine: str) -> list[OCRToken]:
    tokens = []
    for row in rows:
        # level 5 = word; skip empty or invalid
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        conf_raw = row.get("conf", "-1")
        try:
            conf = float(conf_raw)
        except (ValueError, TypeError):
            conf = -1.0
        if conf < 0:
            continue  # -1 means rejected by Tesseract

        try:
            left = float(row["left"])
            top = float(row["top"])
            width = float(row["width"])
            height = float(row["height"])
        except (KeyError, ValueError, TypeError):
            continue

        x0, y0, x1, y1 = left, top, left + width, top + height
        polygon = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        tokens.append(OCRToken(
            text=text,
            polygon_px=polygon,
            bbox_px=(x0, y0, x1, y1),
            confidence_native=conf,
            confidence_scale="0..100",
            level="word",
            source_engine=source_engine,
        ))
    return tokens


def _tsv_to_pipeline_tokens(
    rows: list[dict], page_index: int, language: str,
    offset_x: float = 0.0, offset_y: float = 0.0,
) -> list[OcrToken]:
    tokens = []
    for row in rows:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        conf_raw = row.get("conf", "-1")
        try:
            conf = float(conf_raw) / 100.0
        except (ValueError, TypeError):
            conf = 0.0
        if conf < 0:
            continue

        try:
            left = float(row["left"]) + offset_x
            top = float(row["top"]) + offset_y
            width = float(row["width"])
            height = float(row["height"])
        except (KeyError, ValueError, TypeError):
            continue

        try:
            bbox = BBox(left, top, left + width, top + height)
        except Exception:
            continue
        tokens.append(OcrToken(
            text=text,
            bbox=bbox,
            confidence=max(0.0, min(1.0, conf)),
            language=language,
            source=SourceKind.OCR_PAGE,
        ))
    return tokens


class TesseractBackend:
    """OCRBackend using the Tesseract 5 CLI (subprocess, no pytesseract).

    Satisfies both OCRBackend (benchmark) and OcrEngine (pipeline) protocols.
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
        try:
            tsv = _run_tesseract_tsv(
                request.image, self._tess_lang, self._psm, self._oem
            )
            rows = _parse_tsv(tsv)
            tokens = tuple(_tsv_to_ocr_tokens(rows, "tesseract"))
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
    ) -> list[OcrToken]:
        try:
            tsv = _run_tesseract_tsv(
                page_image, self._tess_lang, self._psm, self._oem
            )
            return _tsv_to_pipeline_tokens(
                _parse_tsv(tsv), page_index, self._language
            )
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
            from PIL import Image

            pil = _to_pil(page_image)
            arr = np.array(pil)
            x0, y0 = int(region_bbox.x0), int(region_bbox.y0)
            x1, y1 = int(region_bbox.x1), int(region_bbox.y1)
            crop_pil = Image.fromarray(arr[y0:y1, x0:x1])

            tsv = _run_tesseract_tsv(
                crop_pil, self._tess_lang, self._psm, self._oem
            )
            return _tsv_to_pipeline_tokens(
                _parse_tsv(tsv), page_index, self._language,
                offset_x=float(x0), offset_y=float(y0),
            )
        except Exception:
            return []

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
