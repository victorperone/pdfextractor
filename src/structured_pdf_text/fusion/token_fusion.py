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
    """Keep the native token stream as the authoritative base."""
    return native_tokens


def fuse_native_and_ocr(
    native_lines: list[TextLine],
    ocr_tokens: list[OcrToken],
) -> FusionResult:
    """Compare OCR words with native evidence without silently replacing it."""
    native_tokens = [token for line in native_lines for token in line.tokens if not token.text.isspace()]
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
