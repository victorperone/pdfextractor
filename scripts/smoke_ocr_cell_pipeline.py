#!/usr/bin/env python3
"""Exercise a real selected OCR pipeline, authorized PDF render and typed cell commit."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re

from structured_pdf_text.config import ExtractorConfig, effective_ocr_quality_policy
from structured_pdf_text.document import TableCell
from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource
from structured_pdf_text.ocr.factory import build_ocr_pipeline
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.registry import PUBLIC_ENGINES


def run(args, fixture):
    from reportlab.pdfgen import canvas

    writer = canvas.Canvas(str(fixture), pagesize=(640, 130), invariant=1)
    writer.setFont("Helvetica", 28)
    writer.drawString(25, 55, "AÇÃO R$ -42,90 ID AB-009")
    writer.save()
    config = ExtractorConfig(
        mode="balanced", language="pt-BR", ocr_engine=args.engine,
        ocr_provider=args.provider, rapidocr_model_size=args.model_size,
        paddle_model_profile=args.paddle_profile, ocr_cache_home=args.cache_home,
        ocr_quality_policy="baseline", num_threads=args.threads,
    )
    if args.engine == "paddle" and args.paddle_mode != "automatic":
        # Exercise both real transports on Linux; this harness override is not
        # a production routing option or evidence of Windows validation.
        from unittest.mock import patch

        with patch("structured_pdf_text.ocr.backends.paddle._has_torch_conflict",
                   return_value=args.paddle_mode == "worker"):
            pipeline = build_ocr_pipeline(config)
    else:
        pipeline = build_ocr_pipeline(config)
    try:
        with PdfiumNativeEvidenceSource(fixture) as source:
            page = source.extract_page(0)
            coordinator = PageCoordinator(
                pipeline, source, extraction_id="cell-smoke", document_id=fixture.stem,
                selection=pipeline.descriptor.selection, page_index=0, page_bbox=page.bbox,
                rotation=page.objects.rotation, quality_policy=effective_ocr_quality_policy(config),
                security_limits=config.security_limits,
            )
            facade = OCRPageFacade(coordinator)
            try:
                cell = TableCell(0, 0, 1, 1, page.bbox, "", [], .0)
                applied = facade.refine_cell(
                    table_id="smoke-table", page_index=0, cell=cell,
                    quality_policy="baseline", base_scale=1.5, header_text="",
                )
                compact = re.sub(r"\s+", "", cell.text.casefold())
                checks = {
                    "cell_applied": applied,
                    "accent": "ação" in compact,
                    "signed_amount": "-42,90" in compact or "-42.90" in compact,
                    "identifier": "ab-009" in compact,
                    "lineage": bool(cell.tokens) and all(token.derived_from_ids for token in cell.tokens),
                }
                return {
                    "schema_version": "ocr-cell-smoke-1.0",
                    "status": "passed" if all(checks.values()) else "failed",
                    "checks": checks, "text": cell.text,
                    "requested_paddle_mode": args.paddle_mode if args.engine == "paddle" else None,
                    "identity": asdict(facade.identity),
                    "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                    "work": coordinator.diagnostics(),
                    "engine_diagnostics": facade.consume_page_diagnostics(),
                    "tokens": [
                        {"text": token.text, "bbox": asdict(token.bbox),
                         "confidence": token.confidence, "evidence_id": token.evidence_id,
                         "derived_from_ids": token.derived_from_ids, "owner_id": token.owner_id,
                         "provenance": token.provenance,
                         "ocr_provenance": asdict(token.ocr_provenance) if token.ocr_provenance else None,
                         "transformation_type": token.transformation_type,
                         "decision_reason": token.decision_reason}
                        for token in cell.tokens
                    ],
                }
            finally:
                facade.close()
    finally:
        pipeline.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=PUBLIC_ENGINES, required=True)
    parser.add_argument("--provider", choices=("onnxruntime", "openvino"))
    parser.add_argument("--model-size", choices=("medium", "small"))
    parser.add_argument("--paddle-profile", default="pt")
    parser.add_argument("--paddle-mode", choices=("automatic", "direct", "worker"), default="automatic")
    parser.add_argument("--cache-home", type=Path)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = args.output.with_suffix(".pdf")
    if fixture.exists():
        parser.error(f"Fixture already exists: {fixture}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the namespace before runtime initialization; never overwrite a report.
    with args.output.open("x", encoding="utf-8") as output:
        try:
            report = run(args, fixture)
        except Exception as exc:
            report = {"schema_version": "ocr-cell-smoke-1.0", "status": "error",
                      "error": f"{type(exc).__name__}: {exc}",
                      "error_details": getattr(exc, "details", {}),
                      "error_stage": getattr(exc, "stage", None)}
        output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "checks": report.get("checks"), "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
