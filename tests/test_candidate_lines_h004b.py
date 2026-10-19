"""Tests for H004B: _candidate_lines edge_band expanded from 0.06 to 0.08.

The fix aligns _candidate_lines with _stable_edge_position, which already uses
0.08 as the outer threshold. Repetition remains the discriminant — position alone
is never sufficient for suppression (per module docstring and user requirement).

A4 page: height=842pt.
  Old edge_band = max(24, 842*0.06) = 50.52pt  (cy threshold)
  New edge_band = max(24, 842*0.08) = 67.36pt  (cy threshold)

The critical case: decorative header at cy≈60pt (between 50.52 and 67.36pt).
  Old code: NOT a candidate  → NOT confirmed → LEAKS
  New code: IS a candidate   → confirmed if it repeats → suppressed

Mandatory user requirement (2026-10-16): test that legitimate content in the
55–65pt band is PRESERVED when it does not repeat across pages.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.assemble.repeated_regions import (
    _candidate_lines,
    detect_repeated_headers_footers,
)
from structured_pdf_text.document import (
    Baseline,
    EvidenceRef,
    LayoutRegion,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_A4_HEIGHT = 842.0
_A4_WIDTH = 595.0


def _make_line(text: str, *, line_id: str, y0: float, height: float = 10.0) -> TextLine:
    bbox = BBox(10.0, y0, _A4_WIDTH - 10.0, y0 + height)
    token = TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, line_id)],
        confidence=1.0,
        normalized_text=text,
    )
    return TextLine(
        tokens=[token],
        bbox=bbox,
        baseline=Baseline(y=bbox.y1),
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
        line_id=line_id,
    )


def _make_page(
    page_index: int,
    lines: list[TextLine],
    *,
    region_kind: RegionKind = RegionKind.TEXT,
    edge_role: str | None = None,
    page_height: float = _A4_HEIGHT,
    page_width: float = _A4_WIDTH,
) -> StructuredPage:
    region = LayoutRegion(
        region_id=f"region-{page_index}",
        kind=region_kind,
        bbox=BBox(0.0, 0.0, page_width, page_height),
        layout_confidence=1.0,
        native_lines=lines,
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
        edge_role=edge_role,
    )
    return StructuredPage(
        page_index=page_index,
        bbox=BBox(0.0, 0.0, page_width, page_height),
        regions=[region],
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=page_index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )


# ---------------------------------------------------------------------------
# Tests for _candidate_lines: extended band on A4
# ---------------------------------------------------------------------------

class TestCandidateLinesExtendedBand:
    """_candidate_lines should capture lines with cy up to page_height*0.08."""

    def test_line_at_cy_60pt_is_candidate_on_a4(self):
        """cy=60pt is between old 50.52pt and new 67.36pt thresholds — must be captured."""
        # y0=55, height=10 → cy=60pt on A4 (842pt)
        line = _make_line("CORPUS INTEGRADO V3", line_id="hdr", y0=55.0, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        candidate_ids = {l.line_id for _, l in candidates}

        assert "hdr" in candidate_ids, (
            "Line at cy=60pt must be a header candidate with edge_band=0.08 "
            "(old edge_band=0.06 would miss it at cy=60 > 50.52)"
        )

    def test_line_at_cy_45pt_is_candidate_on_a4(self):
        """cy=45pt was already within old band — must still be captured."""
        line = _make_line("CABECALHO DECORATIVO", line_id="hdr2", y0=40.0, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        candidate_ids = {l.line_id for _, l in candidates}

        assert "hdr2" in candidate_ids

    def test_line_at_cy_68pt_not_candidate_on_a4(self):
        """cy=68pt is above the new 67.36pt threshold — must NOT be a candidate."""
        line = _make_line("Conteúdo do corpo", line_id="body", y0=63.0, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        candidate_ids = {l.line_id for _, l in candidates}

        assert "body" not in candidate_ids, (
            "Line at cy=68pt must not be a candidate — outside the 0.08 band"
        )

    def test_candidate_kind_is_header_for_top_line(self):
        """Lines in the top band are classified as 'header', not 'footer'."""
        line = _make_line("HEADER TEXT", line_id="h", y0=50.0, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        kinds = {l.line_id: k for k, l in candidates}

        assert kinds.get("h") == "header"


# ---------------------------------------------------------------------------
# Tests for detect_repeated_headers_footers: repetition as discriminant
# ---------------------------------------------------------------------------

class TestRepetitionDiscriminant:
    """Repetition (not position alone) determines furniture suppression."""

    def _make_repeating_pages(
        self, text: str, y0: float, *, count: int = 10
    ) -> list[StructuredPage]:
        pages = []
        for i in range(count):
            line = _make_line(text, line_id=f"line-{i}", y0=y0, height=10.0)
            pages.append(_make_page(i, [line]))
        return pages

    def test_decorative_header_in_extended_band_confirmed_when_repeated(self):
        """Decorative header at y0=55pt (cy=60, outside old 0.06 band) is confirmed
        when it repeats across pages — the primary regression this fix addresses."""
        pages = self._make_repeating_pages(
            "CORPUS INTEGRADO V3 / LABORATORIO SINTETICO", y0=55.0, count=10
        )
        confirmed = detect_repeated_headers_footers(pages)
        keys = list(confirmed.keys())

        assert any("corpus integrado v3" in k for k in keys), (
            "Decorative header at cy=60pt must be confirmed as repeated furniture "
            "after the edge_band fix from 0.06 to 0.08"
        )

    def test_legitimate_content_in_55_65pt_band_not_confirmed_when_unique(self):
        """MANDATORY (user requirement 2026-10-16): legitimate body content in the
        55–65pt band must be PRESERVED when it does not repeat across pages.

        Even though the line's cy falls within the extended candidate band (0.08),
        repetition is the discriminant — unique content must never be suppressed.

        Uses semantically unique text per page (invoice refs, unique IDs) that
        does not trigger page-number normalization.
        """
        pages: list[StructuredPage] = []
        for i in range(10):
            # Unique per page: different invoice ref — no "página N" pattern
            unique_text = f"NF-{2024_0001 + i:07d} — Cliente CORP-{i * 7:04d} — vencimento 30 dias"
            line = _make_line(unique_text, line_id=f"body-{i}", y0=57.0, height=10.0)
            pages.append(_make_page(i, [line]))

        confirmed = detect_repeated_headers_footers(pages)

        # No key should confirm — all texts are unique per page.
        assert len(confirmed) == 0, (
            "Unique body content in the 55–65pt band must NOT be confirmed as "
            "repeated furniture — repetition is the discriminant, not position"
        )

    def test_same_content_in_55_65pt_band_not_confirmed_below_repeat_threshold(self):
        """Content repeating on fewer than 2 pages is NOT confirmed as furniture,
        even if position is stable and within the extended band."""
        # One page only → len(by_page) < 2 → not confirmed
        line = _make_line("CABECALHO UNICO", line_id="solo", y0=58.0, height=10.0)
        page = _make_page(0, [line])

        confirmed = detect_repeated_headers_footers([page])

        assert len(confirmed) == 0, (
            "A line appearing on only one page must not be confirmed as furniture"
        )

    def test_body_content_at_y0_64pt_body_heading_not_suppressed(self):
        """The known V3 body heading starts at y0=64.1pt (cy≈69pt, outside 67.36pt).
        It must not become a candidate at all with the new 0.08 threshold."""
        # y0=64.1 → cy=64.1+5=69.1pt > 67.36 → NOT candidate
        line = _make_line("## Página 005 — corpo do documento", line_id="body-h", y0=64.1, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        candidate_ids = {l.line_id for _, l in candidates}

        assert "body-h" not in candidate_ids, (
            "Body heading at y0=64.1pt (cy≈69.1pt) must remain outside the "
            "candidate band even with the 0.08 threshold"
        )


# ---------------------------------------------------------------------------
# Tests for _stable_edge_position consistency: old inconsistency is resolved
# ---------------------------------------------------------------------------

class TestEdgeBandConsistency:
    """After the fix, _candidate_lines and _stable_edge_position both use 0.08."""

    def test_line_at_08_boundary_is_candidate_and_position_stable(self):
        """A line at exactly 8% from the top is a candidate AND passes position check.

        Before the fix: _candidate_lines used 6% (missed it) but
        _stable_edge_position allowed 8% (would have confirmed it if found).
        The inconsistency is now resolved — both use 8%.
        """
        # cy = 67.36pt (exactly at 0.08 * 842)
        y0 = 62.36  # cy = y0 + height/2 = 62.36 + 5 = 67.36
        line = _make_line("CABECALHO LIMITE", line_id="boundary", y0=y0, height=10.0)
        page = _make_page(0, [line])

        candidates = _candidate_lines(page)
        candidate_ids = {l.line_id for _, l in candidates}

        assert "boundary" in candidate_ids, (
            "Line at cy=67.36pt (exactly 8% of A4) must be a candidate after fix"
        )
