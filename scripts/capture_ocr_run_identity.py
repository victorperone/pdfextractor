#!/usr/bin/env python3
"""Attach the effective backend identity to completed E2E run manifests.

This is useful for older manifests created before evaluate_e2e.py captured
identity before closing the extractor. The backend is instantiated in a
separate process per deployment profile, matching the benchmark's process
isolation and avoiding Paddle/Torch cross-runtime interactions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CONFIGURATIONS = {
    "paddle": ("paddle", None),
    "rapidocr-onnxruntime": ("rapidocr", "onnxruntime"),
    "rapidocr-openvino": ("rapidocr", "openvino"),
    "easyocr": ("easyocr", None),
    "tesseract": ("tesseract", None),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", choices=tuple(CONFIGURATIONS))
    parser.add_argument("run_manifests", nargs="+", type=Path)
    args = parser.parse_args()

    from structured_pdf_text.api import PdfTextExtractor
    from structured_pdf_text.config import (
        ExtractionMode,
        best_extraction_config,
        best_ocr_render_scale,
    )

    engine, provider = CONFIGURATIONS[args.configuration]
    config = replace(
        best_extraction_config(language="pt-BR", preserve_headers=False),
        ocr_engine=engine,
        ocr_provider=provider,
        mode=ExtractionMode.BALANCED,
        ocr_render_scale=best_ocr_render_scale(engine),
    )
    extractor = PdfTextExtractor(config)
    try:
        extractor._ensure_ocr_engine()
        backend = extractor.ocr_engine
        if backend is None or not hasattr(backend, "identity"):
            raise RuntimeError(f"{args.configuration} did not expose an OCR identity")
        identity = asdict(backend.identity)
    finally:
        extractor.close()

    for path in args.run_manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("engine") != engine:
            raise ValueError(f"Engine mismatch in {path}: {manifest.get('engine')!r} != {engine!r}")
        if manifest.get("run_id", "").endswith("rapidocr-onnxruntime") and provider != "onnxruntime":
            raise ValueError(f"Provider mismatch in {path}")
        if manifest.get("run_id", "").endswith("rapidocr-openvino") and provider != "openvino":
            raise ValueError(f"Provider mismatch in {path}")
        manifest["engine_identity"] = identity
        manifest["engine_identity_capture"] = "post_run_same_environment_artifact_snapshot"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
