#!/usr/bin/env python3
"""Preflight check for all five OCR backends.

Imports each backend, calls healthcheck(), and prints a status table.
Exit code 0 when all backends report "ready", 1 when any report otherwise.

Usage:
    python scripts/preflight_ocr_backends.py [--language pt]

Environment variables are read from the current shell (TESSERACT_TESSDATA_DIR,
EASYOCR_MODULE_PATH, RAPIDOCR_REC_MODEL, PADDLE_PDX_CACHE_HOME, etc.).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from structured_pdf_text.config import ExtractorConfig, ExtractionMode


def _check(engine: str, language: str) -> tuple[str, str]:
    """Return (status, detail) for one engine."""
    try:
        config = ExtractorConfig(
            mode=ExtractionMode.OCR,
            language=language,
            ocr_engine=engine,
        )
        from structured_pdf_text.ocr.factory import build_ocr_backend
        backend = build_ocr_backend(config)
        identity = backend.identity
        status = backend.healthcheck()
        versions = ", ".join(
            f"{k}={v}" for k, v in identity.package_versions.items()
        )
        hashes = identity.artifact_hashes
        hash_summary = (
            f", hashes={len(hashes)}" if hashes else ", hashes=none"
        )
        detail = f"{versions}{hash_summary}"
        if engine in {"rapidocr-onnx", "rapidocr-openvino"}:
            extra = identity.extra
            detail += (
                f", language_profile={identity.profile}"
                f", recognizer={extra.get('rec_model') or 'bundled'}"
                f", dictionary={extra.get('rec_keys') or 'bundled'}"
            )
            if language.lower().startswith("pt"):
                from structured_pdf_text.ocr.backends.rapidocr import (
                    portuguese_dictionary_profile,
                )

                profile, missing = portuguese_dictionary_profile(extra.get("rec_keys"))
                if profile != "latin/pt-compatible":
                    missing_detail = "".join(missing) or "unreadable/missing dictionary"
                    detail += f", missing Portuguese character coverage: {missing_detail}"
                    backend.close()
                    return "not_ready_for_pt_comparison", detail
        backend.close()
        return status, detail
    except Exception as exc:
        return "error", str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="pt")
    args = parser.parse_args()

    engines = [
        "paddle",
        "rapidocr-onnx",
        "rapidocr-openvino",
        "easyocr",
        "tesseract",
    ]

    col = max(len(e) for e in engines) + 2
    print(f"\n{'Engine':<{col}}  {'Status':<10}  Detail")
    print("-" * 80)

    all_ready = True
    for engine in engines:
        status, detail = _check(engine, args.language)
        marker = "[ok]" if status == "ready" else "[!!]"
        if status != "ready":
            all_ready = False
        print(f"{marker}  {engine:<{col}}  {status:<10}  {detail}")

    print()
    if all_ready:
        print("All engines: READY")
        return 0
    else:
        print("One or more engines are NOT READY — check setup before running benchmarks.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
