from __future__ import annotations

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    PageDiagnostics,
    StructuredPage,
    StructuredTable,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.line_detector import lines_to_text
from structured_pdf_text.text.reading_order import order_region_lines


def assemble_page(
    page_index: int,
    page_bbox: BBox,
    regions: list[LayoutRegion],
    tables: list[StructuredTable],
    diagnostics: PageDiagnostics,
    raw_text: str,
    native_evidence: NativePageEvidence | None = None,
) -> StructuredPage:
    """Build a provisional StructuredPage.

    reading_text and content_blocks are populated later by assemble_document()
    after heading levels and repeated header/footer detection are complete.
    raw_text is finalised here because it represents the unprocessed evidence
    layer and does not depend on heading or repetition analysis.
    """
    reading_lines, reading_decision = order_region_lines(regions)

    # F02: raw_text must include OCR-recovered content. For scan pages
    # (native layer empty) the ordered lines already contain OCR text; use
    # them instead of returning an empty string.
    if not raw_text.strip() and reading_lines:
        raw_text = lines_to_text(reading_lines)

    diagnostics.facts.update(
        {
            "reading_region_count": len(regions),
            "reading_column_groups": reading_decision.column_groups,
            "reading_rotated_lines": reading_decision.rotated_lines,
            "reading_table_regions": reading_decision.table_regions,
            "reading_region_order": list(reading_decision.region_order),
            "native_order_consistency": reading_decision.native_order_consistency,
            "reading_region_edges": [list(edge) for edge in reading_decision.region_edges],
            "reading_deduplicated_lines": reading_decision.deduplicated_lines,
        }
    )
    return StructuredPage(
        page_index=page_index,
        bbox=page_bbox,
        regions=regions,
        tables=tables,
        raw_text=raw_text,
        reading_text="",  # filled by assemble_document() via assemble_page_content()
        diagnostics=diagnostics,
        native_evidence=native_evidence,
        content_blocks=[],  # filled by assemble_document() via assemble_page_content()
    )
