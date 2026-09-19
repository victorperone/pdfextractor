#!/usr/bin/env python3
"""Run the independent A2 conservation audit on an extracted PDF document."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractionMode, ExtractorConfig

try:
    from .a1_schema import write_json, write_jsonl
    from .a2_conservation import audit_document_conservation
except ImportError:  # script execution from this directory
    from a1_schema import write_json, write_jsonl
    from a2_conservation import audit_document_conservation


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(pdf: Path, output: Path, pages: list[int] | None = None) -> Path:
    page_indices = None if pages is None else tuple(page - 1 for page in pages)
    config = ExtractorConfig(
        mode=ExtractionMode.NATIVE,
        enable_ocr=False,
        enable_layout=False,
        enable_tables=False,
        enable_complexity_render=False,
        page_indices=page_indices,
    )
    document = PdfTextExtractor(config=config).extract(pdf)
    summary, findings = audit_document_conservation(document)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "a2_summary.json", summary.to_dict())
    write_jsonl(output / "a2_findings.jsonl", [finding.to_dict() for finding in findings])
    write_json(
        output / "a2_metadata.json",
        {
            "schema": "pdfextractor.native_text_fidelity.a2.run.v1",
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "pdf": str(pdf),
            "pdf_sha256": _sha256(pdf),
            "pages_requested": pages,
            "pages_extracted": len(document.pages),
            "mode": "native",
            "ocr": False,
            "layout": False,
            "tables": False,
            "summary": summary.to_dict(),
        },
    )
    return output


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("pdf", type=Path)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pages", type=int, nargs="*")
    return command


if __name__ == "__main__":
    args = parser().parse_args()
    print(run(args.pdf, args.output, args.pages))
