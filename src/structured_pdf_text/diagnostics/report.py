from __future__ import annotations

from structured_pdf_text.document import StructuredDocument


def document_report(document: StructuredDocument) -> str:
    lines = [
        f"status={document.diagnostics.status.value}",
        f"pages={document.diagnostics.page_count}",
        f"native_pages={document.diagnostics.native_pages}",
        f"mixed_pages={document.diagnostics.mixed_pages}",
        f"ocr_pages={document.diagnostics.ocr_pages}",
        f"total_ms={document.diagnostics.facts.get('total_ms', '?')}",
        f"assemble_ms={document.diagnostics.facts.get('assemble_ms', '?')}",
        f"timed_out={document.diagnostics.facts.get('timed_out', False)}",
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
                f"page_ms={page.diagnostics.processing_time_ms if page.diagnostics.processing_time_ms is not None else '?'}"
        )
    return "\n".join(lines)
