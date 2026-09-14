from __future__ import annotations

import json
from pathlib import Path

from structured_pdf_text.document import NativePageEvidence, to_plain_data


def dump_native_page_json(page: NativePageEvidence, indent: int | None = 2) -> str:
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
