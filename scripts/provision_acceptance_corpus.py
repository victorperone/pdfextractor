#!/usr/bin/env python3
"""Verify that local acceptance corpus files match the versioned lock manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def verify_corpus(root: Path, lock_path: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("schema_version") != "1.0":
        raise ValueError(f"Unsupported corpus lock schema: {lock.get('schema_version')!r}")
    if lock.get("corpus_id") != "CORPUS-STRESS-OCR-MARKDOWN-V4":
        raise ValueError("Unexpected corpus_id in lock manifest")
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"pdf", "reference", "manifest", "validation"}:
        raise ValueError("Corpus lock must define pdf, reference, manifest, and validation artifacts")

    actual_paths: dict[str, Path] = {}
    for key, record in artifacts.items():
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe corpus path for {key}: {relative}")
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Required corpus artifact is missing: {path}")
        size = path.stat().st_size
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if size != record.get("size_bytes"):
            raise ValueError(f"Size mismatch for {key}: expected {record.get('size_bytes')}, got {size}")
        if digest != record.get("sha256"):
            raise ValueError(f"SHA-256 mismatch for {key}: expected {record.get('sha256')}, got {digest}")
        actual_paths[key] = path

    manifest = json.loads(actual_paths["manifest"].read_text(encoding="utf-8"))
    expected_pages = lock.get("page_count")
    if manifest.get("page_count") != expected_pages:
        raise ValueError(
            f"Manifest page count mismatch: expected {expected_pages}, got {manifest.get('page_count')}"
        )
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(actual_paths["pdf"]))
    try:
        actual_pages = len(pdf)
    finally:
        pdf.close()
    if actual_pages != expected_pages:
        raise ValueError(f"PDF page count mismatch: expected {expected_pages}, got {actual_pages}")
    validation = actual_paths["validation"].read_text(encoding="utf-8")
    if "[FAIL]" in validation or f"[OK] Quantidade de páginas no PDF | {expected_pages}" not in validation:
        raise ValueError("Corpus validation report is failed or does not confirm the locked page count")
    return {"corpus_id": lock["corpus_id"], "page_count": actual_pages, "artifacts": artifacts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path,
        default=Path("corpus/acceptance-corpus.lock.json"),
    )
    args = parser.parse_args(argv)
    corpus_dir = args.manifest.parent
    try:
        result = verify_corpus(corpus_dir, args.manifest)
    except Exception as exc:
        print(f"Acceptance corpus verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Acceptance corpus verified: {result['corpus_id']} ({result['page_count']} pages)")
    for name, record in result["artifacts"].items():
        print(f"  {name}: sha256={record['sha256']} size={record['size_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
