"""VQ-05 — _replace_tokens_in_box must not silently delete existing content.

Conservative mode (the default after VQ-05) requires that an empty OCR result
cannot wipe existing native tokens. Also covers the guard that the replacement
list must be non-empty or explicitly acknowledged as "empty region".
"""
from __future__ import annotations

import pytest

from structured_pdf_text.api import _replace_tokens_in_box
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=200.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _ocr_token(text: str, x0: float = 0.0, x1: float = 100.0, conf: float = 0.95) -> OcrToken:
    return OcrToken(
        text=text,
        bbox=BBox(x0=x0, y0=5, x1=x1, y1=15),
        confidence=conf,
        language="pt",
        source=SourceKind.OCR_REGION,
    )


class TestReplaceTokensConservative:
    """VQ-05: conservative=True (default) must prevent empty wipeout."""

    def test_empty_replacement_preserves_existing(self):
        """An empty OCR result must not delete existing tokens."""
        original = [_ocr_token("R$ 1.234,00", x0=0, x1=100)]
        region_box = _bbox()

        result = _replace_tokens_in_box(
            tokens=original,
            box=region_box,
            replacement=[],   # empty OCR output
            conservative=True,
        )
        texts = [t.text for t in result]
        assert "R$ 1.234,00" in texts, "Existing content must be preserved when replacement is empty"

    def test_non_empty_replacement_proceeds(self):
        """Valid OCR replacement tokens replace the tokens inside the box."""
        original = [_ocr_token("garbled", x0=0, x1=100)]
        region_box = _bbox()
        replacement = [_ocr_token("R$ 1.234,00", x0=0, x1=100)]

        result = _replace_tokens_in_box(
            tokens=original,
            box=region_box,
            replacement=replacement,
            conservative=True,
        )
        texts = [t.text for t in result]
        assert "R$ 1.234,00" in texts, "OCR replacement must be applied when non-empty"

    def test_non_conservative_allows_empty_replacement(self):
        """conservative=False permits empty wipeout (explicit intent)."""
        original = [_ocr_token("anything", x0=0, x1=100)]
        region_box = _bbox()

        result = _replace_tokens_in_box(
            tokens=original,
            box=region_box,
            replacement=[],
            conservative=False,
        )
        texts = [t.text for t in result]
        assert "anything" not in texts, "Non-conservative mode allows empty replacement"

    def test_tokens_outside_box_always_preserved(self):
        """Tokens outside the replacement box are never touched."""
        inside_token = _ocr_token("inside", x0=5, x1=95)
        outside_token = _ocr_token("outside", x0=500, x1=600)
        original = [inside_token, outside_token]
        region_box = _bbox(x0=0, x1=200)

        result = _replace_tokens_in_box(
            tokens=original,
            box=region_box,
            replacement=[_ocr_token("replaced", x0=0, x1=100)],
            conservative=True,
        )
        texts = [t.text for t in result]
        assert "outside" in texts, "Token outside box must never be removed"

    def test_conservative_default_is_true(self):
        """Calling without explicit conservative= behaves conservatively."""
        original = [_ocr_token("keep me", x0=0, x1=100)]
        region_box = _bbox()

        # Default call: no conservative= argument
        result = _replace_tokens_in_box(
            tokens=original,
            box=region_box,
            replacement=[],
        )
        texts = [t.text for t in result]
        assert "keep me" in texts, "Default must be conservative"
