#!/usr/bin/env python3
"""Check static OCR readiness or run deep pt-BR smoke tests.

Static mode inspects packages, executables, model files and language data
without constructing heavy inference runtimes. Pass ``--deep-smoke`` to load
each deployment configuration and OCR a generated pt-BR sample.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from structured_pdf_text.config import ExtractorConfig, ExtractionMode
from structured_pdf_text.ocr.readiness import ReadinessResult, probe_deep, probe_static


CONFIGURATIONS = (
    ("paddle", "paddle", None),
    ("rapidocr [onnxruntime]", "rapidocr", "onnxruntime"),
    ("rapidocr [openvino]", "rapidocr", "openvino"),
    ("easyocr", "easyocr", None),
    ("tesseract", "tesseract", None),
)


def _check(engine: str, provider: str | None, language: str, deep: bool) -> ReadinessResult:
    try:
        config = ExtractorConfig(
            mode=ExtractionMode.OCR,
            language=language,
            ocr_engine=engine,
            ocr_provider=provider,
        )
        return probe_deep(config) if deep else probe_static(config)
    except Exception as exc:
        from structured_pdf_text.ocr.readiness import ReadinessStatus
        return ReadinessResult(ReadinessStatus.UNKNOWN, "configuration_error", {"message": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--deep-smoke", action="store_true", help="Load each backend and OCR a generated pt-BR sample")
    args = parser.parse_args()

    name_width = max(len(label) for label, _, _ in CONFIGURATIONS) + 2
    print(f"\n{'Configuration':<{name_width}}  {'Status':<12}  Reason / details")
    print("-" * 100)
    all_ready = True
    for label, engine, provider in CONFIGURATIONS:
        result = _check(engine, provider, args.language, args.deep_smoke)
        if result.status.value != "ready":
            all_ready = False
        extra = result.reason_code or ""
        if result.details:
            extra = (extra + " " if extra else "") + str(result.details)
        print(f"{'[ok]' if result.status.value == 'ready' else '[!!]'}  {label:<{name_width}}  {result.status.value:<12}  {extra}")

    if all_ready:
        print("\nAll selected OCR configurations: READY")
        return 0
    print("\nOne or more OCR configurations are not ready.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
