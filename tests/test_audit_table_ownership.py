"""VQ-04 — _table_token_claim_strength must use evidence_id, not id(token).

Python id() is not stable after object replacement in the OCR refinement loop.
After VQ-04, ownership is resolved via the stable evidence_id field.
"""
from __future__ import annotations

import pytest
from dataclasses import replace

from structured_pdf_text.assemble.content import (
    _table_token_claim_strength,
    _table_claims_token,
)
from structured_pdf_text.document import (
    EvidenceRef, SourceKind, StructuredTable, TableCell, TableFragment,
    TableMethod, TextToken,
)
from structured_pdf_text.geometry import BBox


def _bbox(x0=0.0, y0=0.0, x1=100.0, y1=20.0) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _token(text: str, evidence_id: str | None = None, derived_from_ids: tuple[str, ...] = ()) -> TextToken:
    return TextToken(
        text=text,
        bbox=_bbox(x0=5, y0=5, x1=50, y1=15),
        sources=[EvidenceRef(SourceKind.NATIVE_PDF, 0, "r0")],
        confidence=1.0,
        normalized_text=text,
        evidence_id=evidence_id,
        derived_from_ids=derived_from_ids,
    )


def _table_with_cell_token(token: TextToken, table_id: str = "t0") -> StructuredTable:
    cell = TableCell(
        row=0, col=0, rowspan=1, colspan=1,
        bbox=_bbox(),
        text=token.text,
        tokens=[token],
        confidence=1.0,
    )
    fragment = TableFragment(page_index=0, bbox=_bbox(), row_start=0, row_end=1)
    return StructuredTable(
        table_id=table_id,
        page_fragments=[fragment],
        cells=[cell],
        column_count=1,
        row_count=1,
        confidence=1.0,
        method=TableMethod.STRICT_GRID,
    )


class TestTableTokenClaimStrength:
    def test_evidence_id_match_returns_strong(self):
        """Token with matching evidence_id is claimed regardless of python id()."""
        eid = "native:p0:char:10-20"
        tok = _token("valor", evidence_id=eid)
        # Simulate object replacement: create a NEW object with same evidence_id
        replaced_tok = replace(tok, text="valor")
        table = _table_with_cell_token(tok)  # original in cell

        # replaced_tok is a different Python object but same evidence_id
        assert id(replaced_tok) != id(tok)
        strength = _table_token_claim_strength(replaced_tok, table, page_index=0)
        assert strength > 0, "Replaced token with same evidence_id must be claimed"

    def test_derived_from_ids_match_claims_refined_token(self):
        """Refined token references original evidence_id in derived_from_ids."""
        original_eid = "native:p0:char:10-20"
        original_tok = _token("1.234", evidence_id=original_eid)
        table = _table_with_cell_token(original_tok)

        # OCR-refined replacement that didn't keep the original in cell.tokens
        refined_tok = _token(
            "1.234,00",
            evidence_id="ocr:p0:cell:t0:0:0:idx0",
            derived_from_ids=(original_eid,),
        )
        strength = _table_token_claim_strength(refined_tok, table, page_index=0)
        assert strength > 0, "Refined token referencing original eid must be claimed"

    def test_unrelated_token_not_claimed(self):
        """Token with a completely different evidence_id and no overlap must not be claimed."""
        cell_tok = _token("valor", evidence_id="native:p0:char:10-20")
        table = _table_with_cell_token(cell_tok)

        unrelated = _token("outro", evidence_id="native:p0:char:200-210")
        # Place unrelated token far outside the table bbox
        unrelated = replace(unrelated, bbox=BBox(x0=500, y0=500, x1=600, y1=515))
        strength = _table_token_claim_strength(unrelated, table, page_index=0)
        assert strength == 0, "Unrelated token must not be claimed by this table"

    def test_none_evidence_id_falls_back_to_python_id(self):
        """Legacy tokens without evidence_id fall back to id() matching."""
        tok = _token("legacy", evidence_id=None)
        table = _table_with_cell_token(tok)

        # SAME object — python id matches
        strength = _table_token_claim_strength(tok, table, page_index=0)
        assert strength > 0, "Same python object without evidence_id must still be claimed"

    def test_replaced_object_without_evidence_id_not_claimed(self):
        """Replaced object without evidence_id loses its claim (pre-VQ-04 bug).

        This test documents the motivating bug that VQ-04 fixes: object replacement
        during OCR refinement would break ownership because id(new_obj) != id(old_obj).
        """
        original = _token("valor", evidence_id=None)
        table = _table_with_cell_token(original)

        replaced = replace(original, text="valor")  # different python id
        assert id(replaced) != id(original)
        strength = _table_token_claim_strength(replaced, table, page_index=0)
        # Without evidence_id, the replaced object cannot be matched
        assert strength == 0, (
            "Pre-VQ-04: replaced object without evidence_id loses ownership. "
            "VQ-04 fixes this via stable evidence_id."
        )


class TestTableClaimsToken:
    def test_claimed_token_returns_true(self):
        eid = "native:p0:char:50-60"
        tok = _token("X", evidence_id=eid)
        table = _table_with_cell_token(tok)
        assert _table_claims_token(tok, table, page_index=0)

    def test_unclaimed_token_returns_false(self):
        eid = "native:p0:char:50-60"
        tok = _token("X", evidence_id=eid)
        table = _table_with_cell_token(tok)

        other = _token("Y", evidence_id="native:p0:char:999-1000")
        other = replace(other, bbox=BBox(x0=500, y0=500, x1=600, y1=515))
        assert not _table_claims_token(other, table, page_index=0)
