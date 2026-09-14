from __future__ import annotations

from structured_pdf_text.document import StructuredDocument


def render_text(document: StructuredDocument, output: str = "reading") -> str:
    if output == "raw":
        return document.raw_text
    if output == "reading":
        return document.reading_text
    raise ValueError(f"Unknown text output: {output}")
