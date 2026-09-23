"""JSON renderer for ``StructuredDocument``.

Serialises the complete document object tree to a JSON string via the
document's own ``to_dict`` method, which ensures that all nested evidence and
diagnostic structures are included.
"""
from __future__ import annotations

import json

from structured_pdf_text.document import StructuredDocument


def render_json(document: StructuredDocument, indent: int | None = 2) -> str:
    """Serialise a ``StructuredDocument`` to a JSON string.

    Uses ``document.to_dict()`` so the complete typed structure (pages, tables,
    diagnostics, metadata) is preserved. Non-ASCII characters are included
    verbatim (``ensure_ascii=False``).
    """
    return json.dumps(document.to_dict(), ensure_ascii=False, indent=indent)
