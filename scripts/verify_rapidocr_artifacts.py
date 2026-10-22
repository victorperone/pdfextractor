#!/usr/bin/env python3
"""Verify pinned RapidOCR PP-OCRv6 profiles or the archived PP-OCRv5 pair."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from structured_pdf_text.ocr.pipelines.rapidocr.models import (
    PROFILES,
    artifact_paths,
    verify_artifacts,
)


EXPECTED_REC_SHA256 = "b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a"
EXPECTED_KEYS_SHA256 = "3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff"
REQUIRED_PT_BR = "ãõáéíóúçêô"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(recognizer: Path, dictionary: Path) -> None:
    if not recognizer.is_file() or not dictionary.is_file():
        raise ValueError("RapidOCR recognizer and dictionary files must both exist")
    actual = sha256_file(recognizer)
    if actual != EXPECTED_REC_SHA256:
        raise ValueError(
            f"RapidOCR recognizer SHA-256 mismatch: expected {EXPECTED_REC_SHA256}, got {actual}"
        )
    actual_keys = sha256_file(dictionary)
    if actual_keys != EXPECTED_KEYS_SHA256:
        raise ValueError(
            f"RapidOCR dictionary SHA-256 mismatch: expected {EXPECTED_KEYS_SHA256}, got {actual_keys}"
        )
    chars = set(dictionary.read_text(encoding="utf-8").casefold())
    missing = "".join(character for character in REQUIRED_PT_BR if character not in chars)
    if missing:
        raise ValueError(f"RapidOCR dictionary is missing pt-BR characters: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-v5", action="store_true", help="Verify the historical v5 Latin pair")
    parser.add_argument("--size", choices=("medium", "small"), default="medium")
    parser.add_argument("--cache-home", type=Path, help="Project cache root; models live below <root>/rapidocr")
    parser.add_argument("--detector", type=Path)
    parser.add_argument("--recognizer", type=Path)
    parser.add_argument("--dictionary", type=Path)
    parser.add_argument("--classifier", type=Path)
    args = parser.parse_args()
    try:
        if args.legacy_v5:
            if not args.recognizer or not args.dictionary:
                raise ValueError("--legacy-v5 requires --recognizer and --dictionary")
            verify(args.recognizer, args.dictionary)
        else:
            supplied = (args.detector, args.recognizer, args.dictionary, args.classifier)
            if any(supplied) and not all(supplied):
                raise ValueError("Supply all four v6 artifacts together")
            paths = supplied if all(supplied) else artifact_paths(args.size, args.cache_home)
            hashes = verify_artifacts(args.size, paths)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if args.legacy_v5:
        print("Historical RapidOCR PP-OCRv5 Latin pair checksums and pt-BR characters: OK")
    else:
        print(f"RapidOCR {PROFILES[args.size].profile} artifact checksums and Portuguese charset: OK ({hashes})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
