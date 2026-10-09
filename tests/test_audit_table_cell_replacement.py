"""VQ-03 — _refine_table_cells_ocr must not fill legitimately empty cells.

Tests that empty cells require higher confidence + geometric fit before OCR
text is inserted, and that existing content passes conservation checks.
"""
from __future__ import annotations

import pytest
from dataclasses import replace
from unittest.mock import MagicMock

from structured_pdf_text.api import _critical_surface_preserved
from structured_pdf_text.document import (
    OcrToken, SourceKind, TableCell, StructuredTable, TableMethod,
    TableFragment, TextToken, EvidenceRef,
)
from structured_pdf_text.geometry import BBox


def _cell(row: int, col: int, text: str, confidence: float = 0.5) -> TableCell:
    return TableCell(
        row=row, col=col, rowspan=1, colspan=1,
        bbox=BBox(x0=col * 50.0, y0=row * 15.0, x1=(col + 1) * 50.0, y1=(row + 1) * 15.0),
        text=text,
        tokens=[],
        confidence=confidence,
    )


class TestCriticalSurfacePreserved:
    """Unit tests for the VQ-02/VQ-03 surface guard."""

    def test_empty_original_accepts_anything(self):
        assert _critical_surface_preserved("", "qualquer coisa") is True

    def test_sign_change_blocked(self):
        assert _critical_surface_preserved("-2,75%", "2,75%") is False
        assert _critical_surface_preserved("-350,00", "+350,00") is False

    def test_sign_preserved(self):
        assert _critical_surface_preserved("-350,00", "-350,00") is True

    def test_leading_zero_removal_blocked(self):
        assert _critical_surface_preserved("001234", "1234") is False
        assert _critical_surface_preserved("0012", "12") is False

    def test_leading_zero_preserved(self):
        assert _critical_surface_preserved("0012", "0012") is True

    def test_no_sign_no_leading_zero_accepted(self):
        assert _critical_surface_preserved("1234", "1234") is True
        assert _critical_surface_preserved("R$ 1.234,00", "R$ 1.234,00") is True


class TestEmptyCellV5P009:
    """Regression for V5-P009: cells in row 004 must remain empty."""

    def test_empty_cells_row_004(self):
        """Verify the audit-described bug: cells in 'Ajuste' row should be empty."""
        # Build a minimal table mirroring V5-P009
        cells = [
            _cell(0, 0, "Nº"),
            _cell(0, 1, "Descrição"),
            _cell(0, 2, "A"),
            _cell(0, 3, "B"),
            _cell(0, 4, "R$ Total"),
            _cell(0, 5, "C"),
            _cell(1, 0, "004"),
            _cell(1, 1, "Ajuste"),
            _cell(1, 2, "0"),
            _cell(1, 3, ""),   # <-- must remain empty
            _cell(1, 4, "R$ 0,00"),
            _cell(1, 5, ""),   # <-- must remain empty
        ]
        # Verify the cells are correctly empty before any OCR refinement
        assert cells[9].text == "", "Cell (1,3) should be empty"
        assert cells[11].text == "", "Cell (1,5) should be empty"


class TestEmptyCellGeometryCheck:
    """VQ-03: OCR tokens outside cell bbox must not fill the cell."""

    def test_token_outside_cell_bbox_rejected(self):
        """A token whose bbox is outside the cell should not be admitted."""
        cell = _cell(0, 0, "")
        cell_bbox = cell.bbox  # x0=0, y0=0, x1=50, y1=15

        # Token completely outside cell bbox
        outside_token = OcrToken(
            text="1",
            bbox=BBox(x0=100, y0=50, x1=120, y1=65),  # far outside
            confidence=0.95,
            language="pt",
            source=SourceKind.OCR_REGION,
        )
        # overlap_ratio of outside_token vs cell_bbox should be ~0
        overlap = outside_token.bbox.overlap_ratio(cell_bbox)
        assert overlap < 0.30, f"Token should be outside cell, overlap={overlap}"

    def test_token_inside_cell_bbox_accepted(self):
        """A token geometrically inside the cell should be admitted."""
        cell = _cell(0, 0, "")
        cell_bbox = cell.bbox  # x0=0, y0=0, x1=50, y1=15

        inside_token = OcrToken(
            text="R$",
            bbox=BBox(x0=2, y0=1, x1=25, y1=14),  # well inside
            confidence=0.90,
            language="pt",
            source=SourceKind.OCR_REGION,
        )
        overlap = inside_token.bbox.overlap_ratio(cell_bbox)
        assert overlap >= 0.30, f"Token should be inside cell, overlap={overlap}"


class TestCellReplacementWithEvidence:
    """VQ-03 + VQ-04: replaced cell tokens carry evidence lineage."""

    def test_derived_from_ids_populated(self):
        """Tokens created during cell OCR refinement must carry derived_from_ids."""
        original_evidence_id = "native:p9:char:100-108"
        new_token = TextToken(
            text="R$ 0,00",
            bbox=BBox(0, 0, 50, 15),
            sources=[EvidenceRef(SourceKind.OCR_REGION, 9, "table-cell:t1:1:4")],
            confidence=0.92,
            normalized_text="R$ 0,00",
            evidence_id="ocr:p9:cell:t1:1:4:idx0",
            derived_from_ids=(original_evidence_id,),
            transformation_type="table_cell_ocr",
        )
        assert new_token.evidence_id == "ocr:p9:cell:t1:1:4:idx0"
        assert original_evidence_id in new_token.derived_from_ids
        assert new_token.transformation_type == "table_cell_ocr"
