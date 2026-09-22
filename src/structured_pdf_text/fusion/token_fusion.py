from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.document import OcrToken, TextLine, TextToken
from structured_pdf_text.text.normalize import normalize_text

from .conflicts import TokenConflict
from .align import find_native_candidates


@dataclass(frozen=True, slots=True)
class FusionResult:
    unmatched_ocr_tokens: tuple[OcrToken, ...]
    matched_ocr_tokens: int
    conflicts: tuple[TokenConflict, ...]


def fuse_native_tokens(native_tokens: list[TextToken]) -> list[TextToken]:
    """Remove spatially-duplicate native tokens with identical text.

    Some PDFs embed an invisible duplicate text layer (copy-protection,
    tagged-PDF artefacts, or rendering quirks).  When two native tokens
    share more than 50 % IoU and identical normalised text, the second
    occurrence is dropped so that OCR tokens are not falsely classified
    as unmatched supplemental content.
    """
    if len(native_tokens) <= 1:
        return native_tokens
    kept: list[TextToken] = []
    for token in native_tokens:
        token_key = normalize_text(token.text).replace(" ", "")
        is_duplicate = any(
            existing.bbox.iou(token.bbox) > 0.5
            and normalize_text(existing.text).replace(" ", "") == token_key
            for existing in kept
        )
        if not is_duplicate:
            kept.append(token)
    return kept


def fuse_native_and_ocr(
    native_lines: list[TextLine],
    ocr_tokens: list[OcrToken],
) -> FusionResult:
    """Compare OCR words with native evidence without silently replacing it."""
    raw_native = [token for line in native_lines for token in line.tokens if not token.text.isspace()]
    native_tokens = fuse_native_tokens(raw_native)
    unmatched: list[OcrToken] = []
    conflicts: list[TokenConflict] = []
    matched = 0
    for ocr_token in ocr_tokens:
        candidates = find_native_candidates(ocr_token, native_tokens)
        if not candidates:
            unmatched.append(ocr_token)
            continue
        native_text = "".join(token.text for token in sorted(candidates, key=lambda item: item.bbox.x0))
        native_key = normalize_text(native_text).replace(" ", "")
        ocr_key = normalize_text(ocr_token.text).replace(" ", "")
        if native_key == ocr_key:
            matched += 1
        else:
            conflicts.append(
                TokenConflict(
                    chosen=native_text,
                    alternatives=[ocr_token.text],
                    reason="native_ocr_text_mismatch",
                )
            )
    return FusionResult(tuple(unmatched), matched, tuple(conflicts))
