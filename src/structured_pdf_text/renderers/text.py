"""Plain-text renderer for ``StructuredDocument``.

Returns one of the pre-assembled text views from the document without any
additional formatting. This renderer is intentionally trivial: all
reconstruction and ordering decisions are already applied in the document
object.
"""
from __future__ import annotations

from structured_pdf_text.document import StructuredDocument


def render_text(document: StructuredDocument, output: str = "reading") -> str:
    """Return the plain-text view of a ``StructuredDocument``.

    Args:
        document: The assembled document to render.
        output: ``'reading'`` (default) for the reading-order text or
            ``'raw'`` for the unprocessed native character stream.

    Raises:
        ValueError: When ``output`` is not ``'reading'`` or ``'raw'``.
    """
    if output == "raw":
        return document.raw_text
    if output == "reading":
        return document.reading_text
    raise ValueError(f"Unknown text output: {output}")
