"""Serialisation helpers for native page evidence.

Dumps a ``NativePageEvidence`` object to JSON for offline inspection, regression
fixtures, and debugging. The evidence object is converted through
``to_plain_data`` which recursively transforms dataclass fields to plain Python
types so the result is round-trippable without custom decoders.
"""
from __future__ import annotations

import json
from pathlib import Path

from structured_pdf_text.document import NativePageEvidence, to_plain_data


def dump_native_page_json(page: NativePageEvidence, indent: int | None = 2) -> str:
    """Serialise a ``NativePageEvidence`` to a JSON string.

    Non-ASCII characters are preserved verbatim. The default indent of 2
    produces a human-readable output suitable for diff-based regression tests.
    """
    return json.dumps(to_plain_data(page), ensure_ascii=False, indent=indent)


def write_native_page_json(
    page: NativePageEvidence,
    path: str | Path,
    indent: int | None = 2,
) -> Path:
    """Write one immutable PDFium page dump without changing the evidence."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(dump_native_page_json(page, indent=indent) + "\n", encoding="utf-8")
    return output
