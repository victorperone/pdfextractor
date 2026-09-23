"""Data structures representing token-level conflicts between native and OCR text."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenConflict:
    """A single token-level conflict between the native PDF layer and OCR output.

    Attributes:
        chosen: The text that was kept.  Native PDF evidence is always
            preferred when a native candidate exists, so this field normally
            contains the native token text.
        alternatives: Texts that were considered but not chosen; typically
            contains the OCR token's text.
        reason: Machine-readable label explaining why a conflict occurred
            (e.g., ``"native_ocr_text_mismatch"``).
    """

    chosen: str
    alternatives: list[str]
    reason: str
