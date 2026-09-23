"""Spatial alignment helpers for matching OCR tokens to native PDF tokens."""

from __future__ import annotations

from structured_pdf_text.document import OcrToken, TextToken


def find_native_candidates(ocr_token: OcrToken, native_tokens: list[TextToken]) -> list[TextToken]:
    """Return native tokens whose bounding box substantially overlaps an OCR token.

    The overlap test divides the intersection area by the *native token's*
    own area, not by the OCR token's area.  This is intentional: when an OCR
    word spans several small native characters (e.g., an individual glyph per
    token), each character's own overlap-ratio can still exceed 0.5, whereas
    dividing by the larger OCR bbox would push the ratio toward 1/N and cause
    every candidate to fall below the threshold.

    Args:
        ocr_token: The OCR word whose bounding box defines the search region.
        native_tokens: Pool of native tokens to test against.

    Returns:
        Native tokens where more than 50 % of the native token's area lies
        inside *ocr_token*'s bounding box.
    """
    return [token for token in native_tokens if token.bbox.overlap_ratio(ocr_token.bbox) > 0.5]
