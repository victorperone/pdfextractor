"""VQ-09 — Visual table detection must not be blocked by native tables.

Tests that _has_uncovered_image_area and the visual detection guard allow
hybrid pages (native-ruled + raster table) to produce two distinct tables.
"""
from __future__ import annotations

import pytest

from structured_pdf_text.api import _has_uncovered_image_area
from structured_pdf_text.document import StructuredTable, TableCell, TableFragment, TableMethod
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=200.0, y1=100.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


class _FakeImage:
    """Stub representing a rendered page object."""
    def __init__(self, width: int = 595, height: int = 842):
        self.width = width
        self.height = height


class _FakeImageItem:
    def __init__(self, bbox: BBox):
        self.bbox = bbox


class _FakeObjects:
    def __init__(self, images: list):
        self.images = images
        self.rotation = 0


class _FakeNativePage:
    def __init__(self, page_bbox: BBox, image_bboxes: list[BBox]):
        self.bbox = page_bbox
        self.objects = _FakeObjects([_FakeImageItem(b) for b in image_bboxes])


def _native_table(page_index: int, bbox: BBox, table_id: str = "t0") -> StructuredTable:
    fragment = TableFragment(page_index=page_index, bbox=bbox, row_start=0, row_end=1)
    return StructuredTable(
        table_id=table_id,
        page_fragments=[fragment],
        cells=[TableCell(row=0, col=0, rowspan=1, colspan=1, bbox=bbox, text="X", tokens=[], confidence=1.0)],
        column_count=1,
        row_count=1,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
    )


class TestHasUncoveredImageArea:
    """VQ-09: _has_uncovered_image_area drives whether visual detection runs."""

    def test_no_images_returns_false(self):
        """Page without images should not trigger visual detection."""
        page = _FakeNativePage(_bbox(x1=595, y1=842), image_bboxes=[])
        assert not _has_uncovered_image_area(page, tables=[])

    def test_image_with_no_tables_returns_true(self):
        """Page with an image and no tables must trigger visual detection."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=400, x1=595, y1=842)],
        )
        assert _has_uncovered_image_area(page, tables=[])

    def test_image_not_covered_by_native_table_returns_true(self):
        """Image in bottom half, native table in top half — visual detection runs."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=450, x1=595, y1=842)],
        )
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert _has_uncovered_image_area(page, tables=[native])

    def test_image_fully_covered_by_native_table_returns_false(self):
        """Image almost entirely inside native table bbox — visual detection skips."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=50, y0=50, x1=545, y1=390)],
        )
        # Native table covers the same area
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert not _has_uncovered_image_area(page, tables=[native])

    def test_two_images_one_covered_one_not(self):
        """Two images: one inside native table, one outside — returns True."""
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[
                _bbox(x0=50, y0=50, x1=545, y1=390),   # inside native table
                _bbox(x0=0, y0=500, x1=595, y1=800),   # outside native table
            ],
        )
        native = _native_table(0, _bbox(x0=0, y0=0, x1=595, y1=400))
        assert _has_uncovered_image_area(page, tables=[native])


class TestVQ09VisualDetectionGuard:
    """VQ-09: visual detection condition changed from 'not tables' to 'uncovered image'."""

    def test_condition_allows_hybrid_page(self):
        """On a hybrid page, _has_uncovered_image_area returns True even with tables."""
        # V5-P019 scenario: native table in top half, raster image in bottom half
        page = _FakeNativePage(
            _bbox(x1=595, y1=842),
            image_bboxes=[_bbox(x0=0, y0=450, x1=595, y1=820)],
        )
        native = _native_table(0, _bbox(x0=50, y0=50, x1=545, y1=400), table_id="native")
        result = _has_uncovered_image_area(page, tables=[native])
        assert result, "Hybrid page with native + raster image must trigger visual detection"

    def test_pure_native_page_no_image_skips_visual(self):
        """Page with native table but no images must not run visual detection."""
        page = _FakeNativePage(_bbox(x1=595, y1=842), image_bboxes=[])
        native = _native_table(0, _bbox(x0=50, y0=50, x1=545, y1=400))
        result = _has_uncovered_image_area(page, tables=[native])
        assert not result, "Page without images must not run visual detection"


class TestMixedVisualTablesIntegration:
    """PDFium and API integration; only recognition/grid discovery are faked."""

    def test_hybrid_produces_two_tables_without_duplicate_text(self, tmp_path, monkeypatch):
        from ocr_domain_fixtures import hybrid_table_pdf, install_table_structure, extract_hybrid
        from structured_pdf_text.renderers import render_markdown

        calls = install_table_structure(monkeypatch)
        document, engine = extract_hybrid(hybrid_table_pdf(tmp_path / "hybrid.pdf"))
        tables = document.pages[0].tables
        assert len(tables) == 2
        native = next(table for table in tables if table.method == TableMethod.STRICT_GRID)
        raster = next(table for table in tables if table.method == TableMethod.VISUAL_MODEL)
        assert native.page_fragments[0].bbox.intersection(raster.page_fragments[0].bbox) is None
        assert raster.page_fragments[0].bbox == BBox(25, 380, 370, 500)
        assert (raster.row_count, raster.column_count) == (3, 3)
        assert raster.cells[4].text == "-42,90"
        assert raster.cells[5].text == "AB-009"
        assert all(token.evidence_id and token.derived_from_ids
                   for cell in raster.cells for token in cell.tokens)
        markdown = render_markdown(document)
        assert markdown.count("<table>") == 2
        assert markdown.count("Papel") == 1
        assert markdown.count("Caneta") == 1
        assert not document.pages[0].diagnostics.warnings
        assert (400, 600) in calls and (345, 120) in calls
        assert len(engine.calls) > 1  # Includes work discovered after initial OCR.

    def test_visual_table_overlapping_native_is_discarded(self, tmp_path, monkeypatch):
        """The real API rejects a visual candidate duplicating the native grid."""
        from ocr_domain_fixtures import hybrid_table_pdf, install_table_structure, extract_hybrid

        calls = install_table_structure(monkeypatch, duplicate_native=True)
        document, _ = extract_hybrid(hybrid_table_pdf(tmp_path / "overlap.pdf"))
        assert (400, 600) in calls
        assert len(document.pages[0].tables) == 1
        assert document.pages[0].tables[0].method == TableMethod.STRICT_GRID

    def test_refined_grid_with_shifted_boxes_keeps_prior_figure_ownership(self, tmp_path, monkeypatch):
        """Real recognizers move boxes between passes while preserving cell text."""
        from dataclasses import replace
        from ocr_domain_fixtures import TableFixtureOCR, hybrid_table_pdf, install_table_structure, extract_hybrid
        from structured_pdf_text.renderers import render_markdown

        original = TableFixtureOCR.recognize_page
        def shifted(self, *args, **kwargs):
            tokens = original(self, *args, **kwargs)
            delta = 0 if len(self.calls) == 1 else .25
            return [replace(token, bbox=BBox(token.bbox.x0, token.bbox.y0 + delta,
                                             token.bbox.x1, token.bbox.y1 + delta)) for token in tokens]
        monkeypatch.setattr(TableFixtureOCR, "recognize_page", shifted)
        install_table_structure(monkeypatch)
        document, _ = extract_hybrid(hybrid_table_pdf(tmp_path / "shifted.pdf"))
        markdown = render_markdown(document)
        assert markdown.count("Papel") == 1
        assert markdown.count("Caneta") == 1
        assert not document.pages[0].diagnostics.warnings

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_real_pdf_presentation_rotation_preserves_both_table_frames(self, tmp_path, monkeypatch, rotation):
        import pypdfium2 as pdfium
        from ocr_domain_fixtures import hybrid_table_pdf, install_table_structure, extract_hybrid

        path = hybrid_table_pdf(tmp_path / "canonical.pdf")
        rotated = tmp_path / "rotated.pdf"
        with pdfium.PdfDocument(path) as document:
            page = document[0]
            try:
                page.set_rotation(rotation)
                page.gen_content()
            finally:
                page.close()
            document.save(rotated)
        install_table_structure(monkeypatch)
        document, _ = extract_hybrid(rotated)
        raster = next(table for table in document.pages[0].tables if table.method == TableMethod.VISUAL_MODEL)
        assert raster.page_fragments[0].bbox == BBox(25, 380, 370, 500)
        assert len(document.pages[0].tables) == 2
        assert (raster.row_count, raster.column_count) == (3, 3)
        assert raster.cells[4].text == "-42,90"
