from __future__ import annotations

import time
import unicodedata

from structured_pdf_text.document import (
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    PageStrategy,
    RegionKind,
    StructuredDocument,
    StructuredPage,
    StructuredTable,
)
from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers, repeated_line_keys
from structured_pdf_text.tables.cross_page import resolve_cross_page_tables_with_diagnostics


def assemble_document(
    pages: list[StructuredPage],
    metadata: DocumentMetadata,
    merge_cross_page_tables: bool = False,
    preserve_headers_footers: bool = True,
    document_warnings: list[str] | None = None,
) -> StructuredDocument:
    tables: list[StructuredTable] = []
    for page in pages:
        tables.extend(page.tables)
    cross_page_decisions = []
    cross_page_start = time.perf_counter()
    if merge_cross_page_tables:
        resolution = resolve_cross_page_tables_with_diagnostics(
            tables,
            page_bboxes={page.page_index: page.bbox for page in pages},
            page_titles={
                page.page_index: tuple(
                    line.text
                    for region in page.regions
                    if region.kind == RegionKind.TITLE
                    for line in region.native_lines
                    if line.text.strip()
                )
                for page in pages
            },
        )
        tables = list(resolution.tables)
        cross_page_decisions = [
            {
                "previous_table_id": decision.previous_table_id,
                "following_table_id": decision.following_table_id,
                "previous_page": decision.previous_page,
                "following_page": decision.following_page,
                "accepted": decision.accepted,
                "score": decision.score,
                "reasons": list(decision.reasons),
                "facts": decision.facts,
            }
            for decision in resolution.decisions
        ]
    cross_page_table_ms = (time.perf_counter() - cross_page_start) * 1000
    raw_text = "\n\n".join(page.raw_text.strip() for page in pages if page.raw_text.strip())
    repeated = detect_repeated_headers_footers(pages)
    page_reading_text = [
        _reading_page_text(page, repeated, preserve_headers_footers)
        for page in pages
    ]
    reading_text = "\n\n".join(text for text in page_reading_text if text)
    warnings = list(document_warnings or [])
    warnings.extend(
        f"page {page.page_index + 1}: {warning}"
        for page in pages
        for warning in page.diagnostics.warnings
    )
    status = ExtractionStatus.PARTIAL_SUCCESS if warnings else ExtractionStatus.SUCCESS
    native_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.NATIVE)
    mixed_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.MIXED)
    ocr_pages = sum(1 for page in pages if page.diagnostics.strategy == PageStrategy.OCR_CANDIDATE)
    return StructuredDocument(
        pages=pages,
        tables=tables,
        raw_text=raw_text,
        reading_text=reading_text,
        metadata=metadata,
        diagnostics=DocumentDiagnostics(
            status=status,
            page_count=len(pages),
            native_pages=native_pages,
            mixed_pages=mixed_pages,
            ocr_pages=ocr_pages,
            warnings=warnings,
            facts={
                "repeated_headers_footers": repeated,
                "preserve_headers_footers": preserve_headers_footers,
                "page_boundaries": [page.page_index for page in pages],
                "logical_table_count": len(tables),
                "cross_page_table_decisions": cross_page_decisions,
                "cross_page_table_ms": cross_page_table_ms,
            },
        ),
    )


def _reading_page_text(
    page: StructuredPage,
    repeated: dict[str, list[int]],
    preserve_headers_footers: bool,
) -> str:
    text = page.reading_text.strip()
    if preserve_headers_footers or not text or not repeated:
        return text
    repeated_keys = set(repeated)
    line_keys = repeated_line_keys(page)
    retained = []
    for line in text.splitlines():
        stripped = unicodedata.normalize("NFC", line.strip())
        text_key = line_keys.get(stripped)
        pos_key = line_keys.get(f"__pos__{stripped}")
        if text_key in repeated_keys or pos_key in repeated_keys:
            continue
        retained.append(line)
    return "\n".join(retained).strip()
