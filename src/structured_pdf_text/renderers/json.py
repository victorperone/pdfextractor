from __future__ import annotations

import json

from structured_pdf_text.document import StructuredDocument


def render_json(document: StructuredDocument, indent: int | None = 2) -> str:
    return json.dumps(document.to_dict(), ensure_ascii=False, indent=indent)
