"""Provisional per-page assembly.

Builds a ``StructuredPage`` from the layout and evidence outputs of a single
page pipeline run. At this stage ``reading_text`` and ``content_blocks`` are
intentionally left empty; they are filled by ``assemble_document`` after
heading levels and repeated header/footer detection are complete across all
pages.
"""
from __future__ import annotations

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    PageDiagnostics,
    StructuredPage,
    StructuredTable,
)
from structured_pdf_text.assemble.content import prose_flow_lines_by_region
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
    flow_lines_by_region = prose_flow_lines_by_region(regions, tables, page_index)
    reading_lines, reading_decision = order_region_lines(
        regions,
        flow_lines_by_region=flow_lines_by_region or None,
    )

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
            "reading_flow_mode": reading_decision.flow_mode,
            "reading_form_score": reading_decision.form_score,
            "reading_column_score": reading_decision.column_score,
            "reading_lane_count": reading_decision.lane_count,
            "reading_gutter_count": reading_decision.gutter_count,
            "reading_spanning_band_count": reading_decision.spanning_band_count,
            "reading_flow_segment_count": reading_decision.flow_segment_count,
            "reading_fallback_used": reading_decision.fallback_used,
            "reading_line_preservation_ok": reading_decision.line_preservation_ok,
            "figure_caption_edges": [list(edge) for edge in reading_decision.figure_caption_edges],
            "mixed_content_edges": [list(edge) for edge in reading_decision.mixed_content_edges],
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
