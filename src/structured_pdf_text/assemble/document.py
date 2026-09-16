from __future__ import annotations

import dataclasses
import time
import unicodedata

from structured_pdf_text.document import (
    ContentKind,
    DocumentDiagnostics,
    DocumentMetadata,
    ExtractionStatus,
    PageStrategy,
    RegionKind,
    StructuredDocument,
    StructuredPage,
    StructuredTable,
)
from structured_pdf_text.assemble.content import assemble_page_content
from structured_pdf_text.assemble.repeated_regions import detect_repeated_headers_footers, repeated_line_keys
from structured_pdf_text.layout.heading import assign_heading_levels
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

    pages = assign_heading_levels(pages)
    repeated = detect_repeated_headers_footers(pages)

    # Build canonical content blocks for each page now that heading levels
    # and repeated header/footer detection are complete.
    assembled_pages: list[StructuredPage] = []
    for page in pages:
        result = assemble_page_content(page)

        page_reading_text = _filter_reading_text(
            result.reading_text,
            page,
            repeated,
            preserve_headers_footers,
        )

        page.diagnostics.facts.update(
            {
                "content_block_count": len(result.blocks),
                "content_text_block_count": sum(
                    1 for b in result.blocks if b.kind not in (ContentKind.TABLE, ContentKind.FIGURE)
                ),
                "content_table_block_count": sum(
                    1 for b in result.blocks if b.kind == ContentKind.TABLE
                ),
                "content_table_fallbacks": result.table_fallbacks,
                "content_claimed_table_lines": result.claimed_table_lines,
                "content_orphan_tables": result.orphan_tables,
                "content_source_region_count": len(page.regions),
                "content_assembly_ms": round(result.assembly_ms, 3),
                "content_block_order": [
                    {
                        "id": b.block_id,
                        "kind": b.kind.value,
                        "table_id": b.table_id,
                    }
                    for b in result.blocks
                ],
            }
        )

        assembled_pages.append(
            dataclasses.replace(
                page,
                content_blocks=list(result.blocks),
                reading_text=page_reading_text,
            )
        )

    pages = assembled_pages

    raw_text = "\n\n".join(page.raw_text.strip() for page in pages if page.raw_text.strip())
    reading_text = "\n\n".join(page.reading_text for page in pages if page.reading_text.strip())

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


def _filter_reading_text(
    reading_text: str,
    page: StructuredPage,
    repeated: dict[str, list[int]],
    preserve_headers_footers: bool,
) -> str:
    """Remove repeated header/footer lines from the canonical reading text."""
    if preserve_headers_footers or not reading_text or not repeated:
        return reading_text
    repeated_keys = set(repeated)
    line_keys = repeated_line_keys(page)
    retained = []
    for line in reading_text.splitlines():
        stripped = unicodedata.normalize("NFC", line.strip())
        text_key = line_keys.get(stripped)
        pos_key = line_keys.get(f"__pos__{stripped}")
        if text_key in repeated_keys or pos_key in repeated_keys:
            continue
        retained.append(line)
    return "\n".join(retained).strip()
