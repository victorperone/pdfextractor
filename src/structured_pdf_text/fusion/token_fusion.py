"""Token-level fusion of native PDF text and OCR output.

Combines spatially-aligned native tokens and OCR words into a unified
representation, deduplicating the native layer before comparison and
recording conflicts where the two sources disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.document import OcrToken, TextLine, TextToken
from structured_pdf_text.text.normalize import normalize_text

from .conflicts import TokenConflict
from .align import find_native_candidates


@dataclass(frozen=True, slots=True)
class FusionResult:
    """Result of fusing native PDF tokens with OCR words for one page or region.

    Attributes:
        unmatched_ocr_tokens: OCR words that had no overlapping native token
            and therefore represent content absent from the native layer
            (e.g., text recovered from a scanned region).
        matched_ocr_tokens: Count of OCR words that matched a native token
            with identical normalised text.
        conflicts: Token-level conflicts where native and OCR text differed.
            When ``ocr_authoritative=False`` (default), ``chosen`` is the
            native text.  When ``ocr_authoritative=True`` (§40 OCR_REGION
            path), ``chosen`` is the OCR text.
    """

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
    *,
    ocr_authoritative: bool = False,
) -> FusionResult:
    """Compare OCR words with native evidence without silently replacing it.

    Args:
        native_lines: Native PDF text lines for this region.
        ocr_tokens: OCR tokens to fuse with the native layer.
        ocr_authoritative: When ``True``, OCR text wins in conflicts instead
            of the native layer.  Use this for ``OCR_REGION`` decisions where
            the region was explicitly marked for OCR replacement because the
            native text is unreliable (hidden OCR layer, broken CMap, etc.).
            Defaults to ``False`` (native-first, existing behaviour).
    """
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
            if ocr_authoritative:
                # §40: OCR_REGION — OCR is authoritative, keep OCR text.
                conflicts.append(
                    TokenConflict(
                        chosen=ocr_token.text,
                        alternatives=[native_text],
                        reason="ocr_authoritative_override",
                        chosen_source=ocr_token.source.value,
                        alternative_sources=["native_pdf"],
                        chosen_score=ocr_token.confidence,
                        alternative_scores=[sum(token.confidence for token in candidates) / len(candidates)],
                    )
                )
            else:
                conflicts.append(
                    TokenConflict(
                        chosen=native_text,
                        alternatives=[ocr_token.text],
                        reason="native_ocr_text_mismatch",
                        chosen_source="native_pdf",
                        alternative_sources=[ocr_token.source.value],
                        chosen_score=sum(token.confidence for token in candidates) / len(candidates),
                        alternative_scores=[ocr_token.confidence],
                    )
                )
    return FusionResult(tuple(unmatched), matched, tuple(conflicts))
