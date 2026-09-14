from __future__ import annotations

from structured_pdf_text.document import OcrToken, TextToken


def find_native_candidates(ocr_token: OcrToken, native_tokens: list[TextToken]) -> list[TextToken]:
    return [token for token in native_tokens if ocr_token.bbox.overlap_ratio(token.bbox) > 0.5]
