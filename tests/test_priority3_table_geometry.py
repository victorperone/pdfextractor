from __future__ import annotations

from structured_pdf_text.api import (
    _merge_ocr_region_tokens_into_table_cells,
    _rebuild_table_ocr_lines,
    _validate_detected_tables,
)
from structured_pdf_text.document import (
    EvidenceRef,
    LayoutRegion,
    OcrToken,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.config import OcrQualityThresholds
from structured_pdf_text.ocr.paddle import _make_candidate, _spatial_consensus
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
from structured_pdf_text.tables.validation import (
    build_table_construction_diagnostics,
    validate_table_geometry,
)


def _token(text: str, x: float, y: float, *, provenance: str | None = None) -> TextToken:
    bbox = BBox(x, y, x + 20.0, y + 10.0)
    return TextToken(
        text=text,
        bbox=bbox,
        sources=[EvidenceRef(SourceKind.OCR_PAGE, 0, f"ocr:{text}:{x}:{y}")],
        confidence=0.95,
        normalized_text=text,
        provenance=provenance,
    )


def _line(token: TextToken) -> TextLine:
    return TextLine(
        tokens=[token],
        bbox=token.bbox,
        baseline=None,
        direction=WritingDirection.LEFT_TO_RIGHT,
        native_order_min=None,
        native_order_max=None,
    )


def _table(*, reverse_rows: bool = False, reverse_columns: bool = False) -> StructuredTable:
    cells: list[TableCell] = []
    row_count = 2
    column_count = 5
    for row in range(row_count):
        for col in range(column_count):
            effective_row = row_count - 1 - row if reverse_rows else row
            effective_col = column_count - 1 - col if reverse_columns else col
            bbox = BBox(
                effective_col * 100.0,
                effective_row * 40.0,
                (effective_col + 1) * 100.0,
                (effective_row + 1) * 40.0,
            )
            token = _token(f"r{row}c{col}", bbox.x0 + 10.0, bbox.y0 + 10.0)
            cells.append(
                TableCell(
                    row=row,
                    col=col,
                    rowspan=1,
                    colspan=1,
                    bbox=bbox,
                    text=token.text,
                    tokens=[token],
                    confidence=0.95,
                )
            )
    return StructuredTable(
        table_id="table-1",
        page_fragments=[TableFragment(0, BBox(0, 0, 500, 80), 0, 1)],
        cells=cells,
        column_count=column_count,
        row_count=row_count,
        confidence=0.95,
        method=TableMethod.TEXT_TRACKS,
    )


def test_geometry_validation_ignores_random_source_line_order() -> None:
    table = _table()
    source_lines = [
        _line(token)
        for cell in reversed(table.cells)
        for token in cell.tokens
    ]
    result = validate_table_geometry(table, source_lines=source_lines)
    diagnostics = build_table_construction_diagnostics(table, source_lines=source_lines)

    assert result.valid
    assert result.row_monotonicity == 1.0
    assert result.column_monotonicity == 1.0
    assert result.token_coverage == 1.0
    assert diagnostics.line_texts[0] == "r1c4"
    assert diagnostics.row_monotonic
    assert diagnostics.column_monotonic


def test_geometry_validation_rejects_inverted_rows_without_reversing_them() -> None:
    table = _table(reverse_rows=True)
    result = validate_table_geometry(table)

    assert not result.valid
    assert "row_order_not_monotonic" in result.reasons
    assert table.cells[0].row == 0
    assert table.cells[0].bbox is not None and table.cells[0].bbox.y0 == 40.0


def test_rtl_context_allows_decreasing_column_anchors() -> None:
    table = _table(reverse_columns=True)
    result = validate_table_geometry(
        table,
        writing_direction=WritingDirection.RIGHT_TO_LEFT,
    )

    assert result.valid
    assert result.column_monotonicity == 1.0


def test_reconstructed_ocr_lines_keep_provenance_and_canonical_geometry() -> None:
    tokens = [
        OcrToken(
            text="valor",
            bbox=BBox(10, 20, 50, 30),
            confidence=0.9,
            language="pt",
            source=SourceKind.OCR_PAGE,
            rotation=0,
            provenance="quality_variant:sharpness",
        )
    ]
    lines = reconstruct_ocr_lines(tokens, 0, BBox(0, 0, 100, 100))

    assert lines[0].tokens[0].provenance == "quality_variant:sharpness"
    assert lines[0].bbox == BBox(10, 20, 50, 30)


def test_embedded_figure_ocr_is_assigned_to_native_table_cell() -> None:
    table = _table()
    ocr_token = OcrToken(
        text="CÉLULA OCRS-033",
        bbox=BBox(20, 10, 80, 25),
        confidence=0.92,
        language="pt",
        source=SourceKind.OCR_REGION,
        provenance="targeted_region_recovery",
    )
    region = LayoutRegion(
        region_id="embedded-figure",
        kind=RegionKind.FIGURE,
        bbox=BBox(10, 0, 90, 40),
        layout_confidence=1.0,
        native_lines=[],
        ocr_tokens=[ocr_token],
        quality=RegionQuality(RegionDecision.OCR_REGION),
    )

    consumed = _merge_ocr_region_tokens_into_table_cells(
        tables=[table],
        regions=[region],
        page_index=0,
    )

    assert consumed == {id(ocr_token)}
    assert "CÉLULA OCRS-033" in table.cells[0].text
    assert table.cells[0].tokens[-1].provenance == "targeted_region_recovery"


def test_spatial_consensus_records_its_provenance_without_changing_bbox() -> None:
    page = BBox(0, 0, 100, 100)
    thresholds = OcrQualityThresholds()
    primary_token = OcrToken(
        text="errado",
        bbox=BBox(10, 20, 50, 30),
        confidence=0.40,
        language="pt",
        source=SourceKind.OCR_PAGE,
        provenance="baseline",
    )
    alternate_token = OcrToken(
        text="correto",
        bbox=BBox(10, 20, 50, 30),
        confidence=0.70,
        language="pt",
        source=SourceKind.OCR_PAGE,
        provenance="quality_variant:sharpness",
    )
    primary = _make_candidate("baseline", [primary_token], None, page, thresholds, family="baseline")
    alternate = _make_candidate("sharpness", [alternate_token], None, page, thresholds, family="sharpness")
    merged, replacements, _ = _spatial_consensus(primary, [primary, alternate], thresholds=thresholds)

    assert replacements == 1
    assert merged[0].bbox == alternate_token.bbox
    assert "spatial_consensus" in (merged[0].provenance or "")


def test_table_rebuild_uses_cell_coordinates_over_input_line_order() -> None:
    table = _table()
    source_lines = [
        _line(token)
        for cell in reversed(table.cells)
        for token in cell.tokens
    ]
    rebuilt = _rebuild_table_ocr_lines(table, source_lines, 0)

    assert [line.text.replace(" ", "") for line in rebuilt] == [
        "r0c0r0c1r0c2r0c3r0c4",
        "r1c0r1c1r1c2r1c3r1c4",
    ]


def test_rotation_metadata_does_not_reverse_canonical_table() -> None:
    """rotation=180 on tokens with canonical bboxes must not invert the cell grid.

    Tokens arrive with bboxes already in canonical page coordinates because
    paddle._tokens_from_result applies box_transform (specifically
    _half_turn_box_to_original) before emitting them.  token.rotation is
    provenance metadata, not an instruction to re-apply an axis flip.
    """
    table = _table()  # normal table — rows and columns in canonical order
    for cell in table.cells:
        for token in cell.tokens:
            token.rotation = 180
    source_lines = [
        _line(token)
        for cell in reversed(table.cells)
        for token in cell.tokens
    ]

    rebuilt = _rebuild_table_ocr_lines(table, source_lines, 0)

    # Output must follow cell.row / cell.col order, unchanged by rotation metadata.
    assert [line.text.replace(" ", "") for line in rebuilt] == [
        "r0c0r0c1r0c2r0c3r0c4",
        "r1c0r1c1r1c2r1c3r1c4",
    ]


def test_cell_grid_not_mutated_after_rebuild() -> None:
    """_rebuild_table_ocr_lines must not mutate cell.row, cell.col, or cell.bbox."""
    table = _table()
    snapshot = [(c.row, c.col, c.bbox) for c in table.cells]
    source_lines = [_line(token) for cell in table.cells for token in cell.tokens]

    _rebuild_table_ocr_lines(table, source_lines, 0)

    after = [(c.row, c.col, c.bbox) for c in table.cells]
    assert after == snapshot


def test_invalid_table_is_rejected_with_non_fatal_warning() -> None:
    table = _table(reverse_rows=True)
    region = LayoutRegion(
        region_id="table-region",
        kind=RegionKind.TABLE,
        bbox=BBox(0, 0, 500, 80),
        layout_confidence=1.0,
        native_lines=[],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    warnings: list[str] = []
    tables, validations, _, _ = _validate_detected_tables(
        tables=[table],
        regions=[region],
        extra_lines=[],
        table_ocr_overrides={},
        warnings=warnings,
    )

    assert tables == []
    assert validations[0]["valid"] is False
    assert "table_structure_uncertain" in warnings
