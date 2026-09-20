from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

from structured_pdf_text.geometry import BBox

sys.path.insert(0, str(Path(__file__).resolve().parent / "native_text_fidelity"))

from b1_evaluate import render_report
from b1_structure import (
    B1Category,
    audit_document_structure,
    audit_page_structure,
)


def _line(text: str, line_id: str, x: float, y: float):
    return SimpleNamespace(text=text, line_id=line_id, bbox=BBox(x, y, x + 80, y + 10))


def _page(regions, tables=()):
    return SimpleNamespace(page_index=0, regions=regions, tables=list(tables))


def _region(region_id: str, *lines, kind=""):
    return SimpleNamespace(region_id=region_id, kind=kind, native_lines=list(lines), ocr_lines=[])


def _unit(unit_id: str, text: str, region_id: str, order: int, bbox):
    return {
        "unit_id": unit_id,
        "exact_text": text,
        "region_id": region_id,
        "logical_reading_order": order,
        "bbox_top_origin_pt": list(bbox),
    }


def test_b1_matches_units_and_region_relationships_without_using_native_order():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}, {"region_id": "R2"}],
        "units": [
            _unit("U1", "primeiro", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "segundo", "R2", 2, (0, 20, 80, 30)),
        ],
        "tables": [],
    }
    observed = _page([
        _region("observed-a", _line("primeiro", "line-1", 0, 0)),
        _region("observed-b", _line("segundo", "line-2", 0, 20)),
    ])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 2
    assert summary.auditable
    assert not any(f.category is B1Category.UNIT_MISSING for f in findings)


def test_b1_report_separates_geometry_absence_and_partition_diagnostics():
    reference = {
        "pages": [{
            "page": 1,
            "family": "synthetic",
            "regions": [{"region_id": "R1"}],
            "units": [_unit("U1", "deslocado", "R1", 1, (0, 0, 80, 10))],
            "tables": [],
        }],
    }
    summary, findings = audit_document_structure(
        reference,
        SimpleNamespace(pages=[_page([_region("observed", _line("deslocado", "line-1", 0, 100))])]),
    )

    report = render_report(reference, summary, findings)

    assert "text_present_geometry_inconsistent" in report
    assert "unresolved_text_absence" in report
    assert "synthetic" in report


def test_b1_preserves_duplicate_occurrences():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "repetido", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "repetido", "R1", 2, (0, 20, 80, 30)),
        ],
        "tables": [],
    }
    # Duplicate text remains one-to-one by occurrence.
    observed = _page([_region("observed", _line("repetido", "line-2", 0, 20), _line("repetido", "line-1", 0, 0))])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 2
    assert not any(f.category is B1Category.UNIT_MISSING for f in findings)


def test_b1_flags_logical_order_when_texts_are_geometrically_inverted():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "primeiro", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "segundo", "R1", 2, (0, 20, 80, 30)),
        ],
        "tables": [],
    }
    observed = _page([_region(
        "observed",
        _line("segundo", "line-2", 0, 0),
        _line("primeiro", "line-1", 0, 20),
    )])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.categories[B1Category.READING_ORDER_MISMATCH.value] == 1
    assert not any(finding.category is B1Category.UNIT_MISSING for finding in findings)


def test_b1_separates_exact_text_relocated_from_text_missing():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "deslocado", "R1", 1, (0, 0, 80, 10))],
        "tables": [],
    }
    observed = _page([_region("observed", _line("deslocado", "line-1", 0, 100))])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_GEOMETRY_MISMATCH.value] == 1
    assert summary.categories.get(B1Category.UNIT_MISSING.value, 0) == 0
    assert not summary.auditable


def test_b1_does_not_reuse_geometry_mismatch_as_reading_order_evidence():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "deslocado", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "segundo", "R1", 2, (0, 20, 80, 30)),
        ],
        "tables": [],
    }
    observed = _page([_region(
        "observed",
        _line("segundo", "line-2", 0, 20),
        _line("deslocado", "line-1", 0, 100),
    )])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.categories[B1Category.UNIT_GEOMETRY_MISMATCH.value] == 1
    assert summary.categories.get(B1Category.READING_ORDER_MISMATCH.value, 0) == 0
    assert not summary.auditable


def test_b1_keeps_non_flow_roles_out_of_global_line_order():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            {**_unit("U1", "borda", "R1", 1, (0, 100, 80, 110)), "role": "edge_top"},
            _unit("U2", "corpo", "R1", 2, (0, 20, 80, 30)),
            {**_unit("U3", "célula", "R1", 3, (0, 40, 80, 50)), "role": "table_cell"},
        ],
        "tables": [],
    }
    observed = _page([_region(
        "observed",
        _line("corpo", "line-2", 0, 20),
        _line("célula", "line-3", 0, 40),
        _line("borda", "line-1", 0, 100),
    )])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 3
    assert summary.categories.get(B1Category.READING_ORDER_MISMATCH.value, 0) == 0
    assert summary.auditable


def test_b1_classifies_complete_unit_split_across_adjacent_lines():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "texto completo", "R1", 1, (0, 0, 160, 20))],
        "tables": [],
    }
    observed = _page([_region(
        "observed",
        _line("texto", "line-1", 0, 0),
        _line("completo", "line-2", 0, 10),
    )])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 1
    assert summary.auditable
    fragmented = next(finding for finding in findings if finding.category is B1Category.UNIT_FRAGMENTED)
    assert fragmented.observed_id == "line-1,line-2"
    assert fragmented.observed_text == "texto completo"


def test_b1_reconstructs_punctuation_split_into_a_separate_native_line():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "ação, revisão", "R1", 1, (0, 0, 120, 10))],
        "tables": [],
    }
    first = _line("ação revisão", "line-1", 0, 0)
    punctuation = _line(",", "line-2", 25, 0)
    first.tokens = [
        SimpleNamespace(text="ação", bbox=BBox(0, 0, 20, 10)),
        SimpleNamespace(text=" revisão", bbox=BBox(30, 0, 80, 10)),
    ]
    punctuation.tokens = [SimpleNamespace(text=",", bbox=BBox(20, 0, 25, 10))]
    observed = _page([_region("observed", first, punctuation)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 1
    fragmented = next(finding for finding in findings if finding.category is B1Category.UNIT_FRAGMENTED)
    assert fragmented.observed_text == "ação, revisão"


def test_b1_reconstructs_table_tokens_in_horizontal_order_despite_baseline_variation():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "Equipamento auditável", "R1", 1, (0, 0, 100, 10))],
        "tables": [],
    }
    line = _line("Equipamento auditável 00007", "line-1", 0, 0)
    line.tokens = [
        SimpleNamespace(text="Equipamento", bbox=BBox(0, 6, 45, 10)),
        SimpleNamespace(text=" auditável", bbox=BBox(45, 0, 95, 4)),
        SimpleNamespace(text=" 00007", bbox=BBox(100, 6, 125, 10)),
    ]
    observed = _page([_region("observed", line)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 1
    fragmented = next(finding for finding in findings if finding.category is B1Category.UNIT_FRAGMENTED)
    assert fragmented.observed_text == "Equipamento auditável"


def test_b1_orders_fragmented_units_by_consumed_token_geometry():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "vinculados", "R1", 1, (100, 80, 150, 90)),
            _unit("U2", "COLUNA 2", "R1", 2, (100, 100, 150, 110)),
        ],
        "tables": [],
    }
    first = _line("vinculados", "line-first", 100, 80)
    expected_text = "COLUNA 2"
    wide = _line("prefix " + expected_text, "line-wide", 0, 100)
    wide.bbox = BBox(0, 100, 200, 110)
    wide.tokens = [
        *[
            SimpleNamespace(text=character, bbox=BBox(index * 5, 100, index * 5 + 4, 110))
            for index, character in enumerate("prefix ")
        ],
        *[
            SimpleNamespace(text=character, bbox=BBox(100 + index * 5, 100, 104 + index * 5, 110))
            for index, character in enumerate(expected_text)
        ],
    ]
    fillers = [
        _line(f"left-{index}", f"left-{index}", 0, 200 + index * 12)
        for index in range(3)
    ] + [
        _line(f"right-{index}", f"right-{index}", 100, 200 + index * 12)
        for index in range(3)
    ]
    observed = _page([_region("observed", wide, first, *fillers)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 2
    assert summary.categories.get(B1Category.READING_ORDER_MISMATCH.value, 0) == 0
    assert all(finding.category is not B1Category.UNIT_MISSING for finding in findings)


def test_b1_allows_disjoint_units_to_share_one_observed_line():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "Equipamento auditável", "R1", 1, (0, 0, 100, 10)),
            _unit("U2", "00007", "R1", 2, (100, 0, 125, 10)),
        ],
        "tables": [],
    }
    line = _line("Equipamento auditável 00007", "line-1", 0, 0)
    line.bbox = BBox(0, 0, 140, 10)
    line.tokens = [
        SimpleNamespace(text="Equipamento", bbox=BBox(0, 6, 45, 10)),
        SimpleNamespace(text=" auditável", bbox=BBox(45, 0, 95, 4)),
        SimpleNamespace(text=" 00007", bbox=BBox(100, 6, 125, 10)),
    ]
    observed = _page([_region("observed", line)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 2
    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 2
    assert summary.categories.get(B1Category.UNIT_MISSING.value, 0) == 0
    assert [finding.observed_text for finding in findings if finding.category is B1Category.UNIT_FRAGMENTED] == [
        "Equipamento auditável",
        " 00007",
    ]


def test_b1_accepts_geometry_bounded_spacing_loss_as_fragmented():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "RÓTULO 019 • LEITURA INVERTIDA", "R1", 1, (0, 0, 180, 10))],
        "tables": [],
    }
    line = _line("RÓTULO019 • LEITURAINVERTIDA", "line-1", 0, 0)
    line.bbox = BBox(0, 0, 180, 10)
    line.tokens = [SimpleNamespace(text=line.text, bbox=BBox(0, 0, 180, 10))]
    observed = _page([_region("observed", line)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 1
    assert findings[0].observed_text == "RÓTULO019 • LEITURAINVERTIDA"


def test_b1_reconstructs_right_to_left_tokens_in_native_direction():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "RÓTULO 019 • LEITURA INVERTIDA", "R1", 1, (0, 0, 180, 10))],
        "tables": [],
    }
    observed_text = "RÓTULO019 • LEITURAINVERTIDA"
    line = _line(observed_text, "line-1", 0, 0)
    line.bbox = BBox(0, 0, 180, 10)
    line.direction = SimpleNamespace(value="right_to_left")
    line.tokens = [
        SimpleNamespace(text=character, bbox=BBox(170 - index * 5, 0, 174 - index * 5, 10))
        for index, character in enumerate(observed_text)
    ]
    observed = _page([_region("observed", line)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_FRAGMENTED.value] == 1
    assert findings[0].observed_text == observed_text


def test_b1_classifies_compatibility_ligature_without_hiding_it_as_exact():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [_unit("U1", "Ligatura: ﬁ", "R1", 1, (0, 0, 100, 10))],
        "tables": [],
    }
    line = _line("Ligatura: fi", "line-1", 0, 0)
    line.tokens = [SimpleNamespace(text=line.text, bbox=BBox(0, 0, 100, 10))]
    observed = _page([_region("observed", line)])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.matched_units == 1
    assert summary.categories[B1Category.UNIT_UNICODE_SUBSTITUTION.value] == 1
    assert summary.categories.get(B1Category.UNIT_MISSING.value, 0) == 0
    assert summary.auditable
    assert findings[0].observed_text == "Ligatura: fi"


def test_b1_distinguishes_region_fragmentation_and_table_cell_shape():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "linha", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "linha", "R1", 2, (0, 0, 80, 10)),
        ],
        "tables": [{
            "table_id": "T1", "n_columns": 2,
            "cells": [
                {"row_index": 0, "column_index": 0, "rowspan": 1, "colspan": 2},
            ],
        }],
    }
    observed_table = SimpleNamespace(
        table_id="observed-T1", column_count=2,
        cells=[SimpleNamespace(row=0, col=0, rowspan=1, colspan=1)],
    )
    observed = _page([
        _region("observed-a", _line("linha", "line-1", 0, 0)),
        _region("observed-b", _line("linha", "line-2", 0, 0)),
    ], [observed_table])

    summary, findings = audit_page_structure(reference, observed)
    categories = [finding.category for finding in findings]

    assert B1Category.REGION_FRAGMENTED in categories
    assert B1Category.TABLE_CELL_MISMATCH in categories
    assert not summary.auditable


def test_b1_distinguishes_semantic_region_partition_from_same_kind_fragmentation():
    reference = {
        "page": 1,
        "regions": [{"region_id": "R1"}],
        "units": [
            _unit("U1", "título", "R1", 1, (0, 0, 80, 10)),
            _unit("U2", "corpo", "R1", 2, (0, 20, 80, 30)),
        ],
        "tables": [],
    }
    observed = _page([
        _region("observed-title", _line("título", "line-1", 0, 0), kind="title"),
        _region("observed-text", _line("corpo", "line-2", 0, 20), kind="text"),
    ])

    summary, findings = audit_page_structure(reference, observed)

    assert summary.categories[B1Category.REGION_PARTITIONED.value] == 1
    assert summary.categories.get(B1Category.REGION_FRAGMENTED.value, 0) == 0
    assert summary.auditable
    partition = next(finding for finding in findings if finding.category is B1Category.REGION_PARTITIONED)
    assert partition.observed_id == "observed-text,observed-title"
    assert "observed_region_kinds=text,title" in partition.reasons


def test_b1_normalizes_row_coordinates_for_continued_table_fragments():
    reference = {
        "page": 1,
        "regions": [],
        "units": [],
        "tables": [{
            "table_id": "continued",
            "n_columns": 2,
            "cells": [
                {"row_index": 13, "column_index": 0, "rowspan": 1, "colspan": 1},
                {"row_index": 13, "column_index": 1, "rowspan": 1, "colspan": 1},
                {"row_index": 14, "column_index": 0, "rowspan": 1, "colspan": 1},
                {"row_index": 14, "column_index": 1, "rowspan": 1, "colspan": 1},
            ],
        }],
    }
    observed_table = SimpleNamespace(
        table_id="observed", column_count=2,
        cells=[
            SimpleNamespace(row=0, col=0, rowspan=1, colspan=1),
            SimpleNamespace(row=0, col=1, rowspan=1, colspan=1),
            SimpleNamespace(row=1, col=0, rowspan=1, colspan=1),
            SimpleNamespace(row=1, col=1, rowspan=1, colspan=1),
        ],
    )

    summary, findings = audit_page_structure(reference, _page([], [observed_table]))

    assert summary.categories[B1Category.TABLE_MATCHED.value] == 1
    assert not any(finding.category is B1Category.TABLE_CELL_MISMATCH for finding in findings)


def test_b1_aggregates_missing_pages_and_serializes_document_summary():
    reference = {"pages": [{"page": 1, "regions": [], "units": [], "tables": []}, {"page": 2, "regions": [], "units": [{"unit_id": "U2", "exact_text": "x"}], "tables": []}]}
    observed = SimpleNamespace(pages=[SimpleNamespace(page_index=0, regions=[], tables=[])])

    summary, findings = audit_document_structure(reference, observed)

    assert summary.page_count == 2
    assert summary.matched_units == 0
    assert summary.categories["unit_missing"] == 1
    assert summary.to_dict()["auditable"] is False
    assert findings[0].to_dict()["category"] == "unit_missing"
