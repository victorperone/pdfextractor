from __future__ import annotations

from structured_pdf_text.assemble.conservation import record_content_conservation
from structured_pdf_text.assemble.document import assemble_document
from structured_pdf_text.document import (
    Baseline,
    ContentKind,
    EvidenceRef,
    LayoutRegion,
    OcrToken,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    StructuredTable,
    TableCell,
    TableFragment,
    TableMethod,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.paddle import _make_candidate, _select_best_candidate
from structured_pdf_text.ocr.quality import OcrQualityThresholds
from structured_pdf_text.tables.validation import validate_table_geometry


def _line(text: str, bbox: BBox, line_id: str) -> TextLine:
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


def _page(index: int, regions: list[LayoutRegion]) -> StructuredPage:
    return StructuredPage(
        page_index=index,
        bbox=BBox(0, 0, 600, 800),
        regions=regions,
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=PageDiagnostics(
            page_index=index,
            strategy=PageStrategy.NATIVE,
            reasons=[],
            native_chars=0,
            native_text_length=0,
        ),
    )


def _region(kind: RegionKind, text: str, bbox: BBox, line_id: str) -> LayoutRegion:
    return LayoutRegion(
        region_id=f"region-{line_id}",
        kind=kind,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=[_line(text, bbox, line_id)],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )


def test_unique_edge_content_is_preserved_when_suppression_is_enabled() -> None:
    pages = [
        _page(0, [_region(RegionKind.HEADER, "Relatório Cliente A", BBox(0, 0, 220, 20), "p0-header")]),
        _page(1, [_region(RegionKind.HEADER, "Relatório Cliente B", BBox(0, 0, 220, 20), "p1-header")]),
    ]

    document = assemble_document(
        pages,
        metadata=type("Metadata", (), {"source_path": "test.pdf", "page_count": 2, "pdfium_version": None})(),
        preserve_headers_footers=False,
    )

    assert "Relatório Cliente A" in document.reading_text
    assert "Relatório Cliente B" in document.reading_text
    assert all(not block.suppressed for page in document.pages for block in page.content_blocks)


def test_confirmed_repeated_header_is_explicitly_suppressed() -> None:
    pages = [
        _page(0, [_region(RegionKind.HEADER, "Documento interno", BBox(0, 0, 220, 20), "p0-header")]),
        _page(1, [_region(RegionKind.HEADER, "Documento interno", BBox(0, 0, 220, 20), "p1-header")]),
    ]

    document = assemble_document(
        pages,
        metadata=type("Metadata", (), {"source_path": "test.pdf", "page_count": 2, "pdfium_version": None})(),
        preserve_headers_footers=False,
    )

    assert "Documento interno" not in document.reading_text
    assert document.pages[0].content_blocks[0].suppression_reason == "repeated_header"
    assert document.pages[0].diagnostics.facts["content_suppressed_repeated_header_lines"] == 1


def test_unaccounted_line_gets_auditable_text_fallback() -> None:
    line = _line("conteúdo preservado", BBox(10, 20, 180, 35), "fallback-line")
    page = _page(0, [_region(RegionKind.TEXT, line.text, line.bbox, line.line_id or "fallback-line")])
    blocks, summary, records = record_content_conservation(page, [])

    assert summary.unaccounted_lines == 0
    assert summary.fallback_lines == 1
    assert blocks[-1].text == "conteúdo preservado"
    assert records[-1].reason == "unaccounted_content_fallback"


def test_ocr_candidate_with_more_spatial_coverage_wins_small_quality_tie() -> None:
    page_bbox = BBox(0, 0, 200, 100)
    thresholds = OcrQualityThresholds()
    high_quality_short = _make_candidate(
        "short",
        [
            # The missing second line is intentional.
            OcrToken("A", BBox(10, 10, 60, 20), 0.94, "pt", SourceKind.OCR_PAGE)
        ],
        None,
        page_bbox,
        thresholds,
    )
    fuller = _make_candidate(
        "full",
        [
            OcrToken("A", BBox(10, 10, 60, 20), 0.93, "pt", SourceKind.OCR_PAGE),
            OcrToken("B", BBox(10, 40, 60, 50), 0.93, "pt", SourceKind.OCR_PAGE),
        ],
        None,
        page_bbox,
        thresholds,
    )

    selected = _select_best_candidate([high_quality_short, fuller], page_bbox, thresholds)

    assert selected.name == "full"
    assert fuller.line_cluster_count > high_quality_short.line_cluster_count


def test_table_source_geometry_rejects_reversed_row_assignment() -> None:
    upper = BBox(10, 10, 100, 30)
    lower = BBox(10, 50, 100, 70)
    table = StructuredTable(
        table_id="reversed-source",
        page_fragments=[TableFragment(0, BBox(10, 10, 200, 70), 0, 1)],
        cells=[
            TableCell(0, 0, 1, 1, lower, "baixo", [] , 1.0),
            TableCell(1, 0, 1, 1, upper, "cima", [] , 1.0),
        ],
        column_count=1,
        row_count=2,
        confidence=0.9,
        method=TableMethod.TEXT_TRACKS,
    )
    source_lines = [
        _line("cima", upper, "source-upper"),
        _line("baixo", lower, "source-lower"),
    ]

    result = validate_table_geometry(table, source_lines=source_lines)

    assert not result.valid
    assert result.source_row_assignment_monotonicity < 1.0
    assert "source_row_assignment_not_monotonic" in result.reasons
