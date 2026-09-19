#!/usr/bin/env python3
"""Run the independent B1 structural audit on an extracted PDF document."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig

try:
    from .a1_schema import write_json, write_jsonl
    from .b1_structure import audit_document_structure
except ImportError:  # script execution from this directory
    from a1_schema import write_json, write_jsonl
    from b1_structure import audit_document_structure


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(pdf: Path, reference: Path, output: Path, pages: list[int] | None = None) -> Path:
    reference_data = json.loads(reference.read_text(encoding="utf-8"))
    selected_pages = None if pages is None else tuple(page - 1 for page in pages)
    config = ExtractorConfig(
        mode=ExtractionMode.NATIVE,
        enable_ocr=False,
        enable_layout=True,
        enable_tables=True,
        enable_complexity_render=False,
        page_indices=selected_pages,
    )
    document = PdfTextExtractor(config=config).extract(pdf)
    if pages is not None:
        selected = set(pages)
        reference_data = {
            **reference_data,
            "pages": [page for page in reference_data["pages"] if int(page["page"]) in selected],
        }
    summary, findings = audit_document_structure(reference_data, document)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "b1_summary.json", summary.to_dict())
    write_jsonl(output / "b1_findings.jsonl", [finding.to_dict() for finding in findings])
    write_json(
        output / "b1_metadata.json",
        {
            "schema": "pdfextractor.native_text_fidelity.b1.run.v1",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf),
            "pdf_sha256": _sha256(pdf),
            "reference": str(reference),
            "reference_sha256": _sha256(reference),
            "pages_requested": pages,
            "pages_extracted": len(document.pages),
            "mode": "native",
            "ocr": False,
            "layout": True,
            "tables": True,
            "summary": summary.to_dict(),
        },
    )
    return output


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("pdf", type=Path)
    command.add_argument("reference", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pages", type=int, nargs="*")
    return command


if __name__ == "__main__":
    args = parser().parse_args()
    print(run(args.pdf, args.reference, args.output, args.pages))
