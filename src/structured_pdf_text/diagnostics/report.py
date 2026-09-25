"""Human-readable one-line-per-page extraction report.

Formats a ``StructuredDocument`` as a compact multi-line string where the
first line contains document-level summary facts and each subsequent line
contains per-page strategy, reason codes, and key diagnostic values. Intended
for CLI output and log emission; not a structured format.
"""
from __future__ import annotations

from structured_pdf_text.document import StructuredDocument


def document_report(document: StructuredDocument) -> str:
    """Format a ``StructuredDocument`` as a human-readable diagnostic report.

    Produces one summary line with document-level facts (status, page counts,
    timing, repeated regions) followed by one line per page showing strategy,
    reason codes, native character count, text length, coverage scores, layout
    decisions, and per-page timing. All values are read directly from the
    document's diagnostic structures with no recomputation.
    """
    lines = [
        f"status={document.diagnostics.status.value}",
        f"pages={document.diagnostics.page_count}",
        f"native_pages={document.diagnostics.native_pages}",
        f"mixed_pages={document.diagnostics.mixed_pages}",
        f"ocr_pages={document.diagnostics.ocr_pages}",
        f"total_ms={document.diagnostics.facts.get('total_ms', '?')}",
        f"assemble_ms={document.diagnostics.facts.get('assemble_ms', '?')}",
        f"memory={document.diagnostics.facts.get('memory', {})}",
        f"repeated_regions={len(document.diagnostics.facts.get('repeated_headers_footers', {}))}",
    ]
    for page in document.pages:
        reasons = ",".join(reason.value for reason in page.diagnostics.reasons) or "none"
        lines.append(
            "page="
            f"{page.page_index + 1} strategy={page.diagnostics.strategy.value} "
                f"reasons={reasons} native_chars={page.diagnostics.native_chars} "
                f"text_len={page.diagnostics.native_text_length} "
            f"native_score={page.diagnostics.facts.get('native_text_score', '?')} "
            f"text_coverage={page.diagnostics.facts.get('text_coverage', '?')} "
            f"image_coverage={page.diagnostics.facts.get('image_coverage', '?')} "
            f"tables={page.diagnostics.tables} "
            f"flow_mode={page.diagnostics.facts.get('reading_flow_mode', '?')} "
            f"lanes={page.diagnostics.facts.get('reading_lane_count', '?')} "
            f"gutters={page.diagnostics.facts.get('reading_gutter_count', '?')} "
            f"table_geometry_valid={page.diagnostics.facts.get('table_geometry_valid', '?')} "
            f"table_token_coverage={page.diagnostics.facts.get('table_token_coverage', '?')} "
            f"table_provenance={page.diagnostics.facts.get('table_source_provenance', '?')} "
            f"ocr_outcome={page.diagnostics.facts.get('ocr_outcome', 'not_requested')} "
            f"page_ms={page.diagnostics.processing_time_ms if page.diagnostics.processing_time_ms is not None else '?'}"
        )
    return "\n".join(lines)
