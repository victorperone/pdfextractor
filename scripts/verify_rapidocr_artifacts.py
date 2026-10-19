#!/usr/bin/env python3
"""Verify the pinned RapidOCR PP-OCRv5 Latin recognizer/dictionary pair."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


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
    parser.add_argument("--recognizer", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.recognizer, args.dictionary)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print("RapidOCR PP-OCRv5 Latin recognizer/dictionary checksums and pt-BR characters: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
