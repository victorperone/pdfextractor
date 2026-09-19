from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "native_text_fidelity"))

from a1_compare import compare_page_units  # noqa: E402


def _unit(unit_id: str, text: str, bbox=(0.0, 0.0, 100.0, 10.0), **extra):
    return {"unit_id": unit_id, "exact_text": text, "bbox_top_origin_pt": list(bbox), "font_size_pt": 8.0, **extra}


def _char(index: int, text: str, bbox=(0.0, 0.0, 100.0, 10.0), **extra):
    return {"page_index": 0, "char_index": index, "text": text, "bbox_top_origin_pt": list(bbox), "unicode_codepoint": ord(text[0]) if text else None, **extra}


def _categories(units, chars):
    return [row.category for row in compare_page_units(units, chars, page=1, source_stage="raw_pdfium") if row.unit_id]


def test_fragmented_occurrence_is_complete_and_not_reading_order_sensitive():
    units = [_unit("U1", "AB")]
    chars = [_char(0, "A", (0, 0, 40, 10)), _char(1, "B", (40, 0, 80, 10))]
    assert _categories(units, chars) == ["fragmented_but_complete"]


def test_duplicate_text_at_distinct_bboxes_consumes_two_occurrences():
    units = [_unit("U1", "SAME", (0, 0, 40, 10)), _unit("U2", "SAME", (60, 0, 100, 10))]
    chars = [_char(0, "SAME", (0, 0, 40, 10)), _char(1, "SAME", (60, 0, 100, 10))]
    assert _categories(units, chars) == ["matched_exact", "matched_exact"]


def test_whitespace_only_change_is_reported_without_non_space_loss():
    units = [_unit("U1", "A  B")]
    chars = [_char(0, "A B")]
    rows = compare_page_units(units, chars, page=1, source_stage="raw_pdfium")
    assert rows[0].category == "spacing_changed"
    assert rows[0].observed_text == "A B"


def test_ligature_is_unicode_substitution_but_wrong_identifier_is_not_normalized():
    units = [_unit("U1", "office", (0, 0, 50, 10)), _unit("U2", "ID-123", (60, 0, 110, 10))]
    chars = [_char(0, "ofﬁce", (0, 0, 50, 10)), _char(1, "ID-124", (60, 0, 110, 10))]
    assert _categories(units, chars) == ["unicode_substitution", "text_missing"]


def test_adjacent_table_cells_are_associated_by_geometry():
    units = [_unit("CELL-A", "10", (10, 20, 30, 30)), _unit("CELL-B", "20", (31, 20, 51, 30))]
    chars = [_char(0, "10", (10, 20, 30, 30)), _char(1, "20", (31, 20, 51, 30))]
    assert _categories(units, chars) == ["matched_exact", "matched_exact"]


def test_consumed_match_cannot_be_reused_and_native_extra_is_retained():
    units = [_unit("U1", "SAME"), _unit("U2", "SAME")]
    chars = [_char(0, "SAME"), _char(1, "EXTRA", (0, 20, 50, 30))]
    rows = compare_page_units(units, chars, page=1, source_stage="raw_pdfium")
    assert [row.category for row in rows if row.unit_id] == ["matched_exact", "duplicate_occurrence"]
    assert any(row.category == "unmatched_native" and row.observed_text == "EXTRA" for row in rows)


def test_invalid_reference_geometry_is_not_assessable_and_invalid_mapping_is_visible():
    units = [_unit("U1", "A", (0, 0, 0, 10)), _unit("U2", "B", (20, 0, 30, 10))]
    chars = [_char(0, "B", (20, 0, 30, 10), unicode_mapping_failed=True)]
    rows = compare_page_units(units, chars, page=1, source_stage="native_adapter")
    assert rows[0].category == "not_assessable"
    assert rows[1].category == "native_mapping_invalid"


def test_rotated_unit_uses_canonical_geometry_without_reversing_text():
    units = [_unit("U1", "ROT", (10, 100, 20, 150), rotation_deg=90)]
    chars = [_char(0, "ROT", (10, 100, 20, 150), angle=90)]
    assert _categories(units, chars) == ["matched_exact"]


def test_crlf_in_observation_is_a_spacing_difference():
    units = [_unit("U1", "A B")]
    chars = [_char(0, "A\r\nB")]
    assert _categories(units, chars) == ["spacing_changed"]
