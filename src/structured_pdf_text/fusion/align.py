from __future__ import annotations

from structured_pdf_text.document import OcrToken, TextToken


def find_native_candidates(ocr_token: OcrToken, native_tokens: list[TextToken]) -> list[TextToken]:
    # Use the native token's own area as the denominator: we want tokens where
    # >50% of the native token is contained within the OCR token's bbox.
    # The previous form (ocr_token.bbox.overlap_ratio) divided by OCR-word area,
    # making individual characters score ~1/N and never exceed 0.5.
    return [token for token in native_tokens if token.bbox.overlap_ratio(ocr_token.bbox) > 0.5]
