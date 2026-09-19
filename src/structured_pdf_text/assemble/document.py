from __future__ import annotations

import dataclasses
import time

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
from structured_pdf_text.assemble.content import _build_reading_text, _reindex_blocks
from structured_pdf_text.assemble.conservation import record_content_conservation
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
                page.page_index: _page_continuation_titles(page)
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
        blocks = list(result.blocks)
        _apply_repeated_suppression(
            blocks,
            page,
            repeated,
            preserve_headers_footers,
        )
        blocks, conservation, records = record_content_conservation(
            page,
            blocks,
            canonical_line_order=result.canonical_line_order,
        )
        old_block_ids = {id(block): block.block_id for block in blocks}
        blocks = _reindex_blocks(blocks)
        owner_id_remap = {
            old_block_ids[id(block)]: block.block_id
            for block in blocks
            if id(block) in old_block_ids
        }
        records = tuple(
            dataclasses.replace(
                record,
                owner_id=owner_id_remap.get(record.owner_id, record.owner_id),
            )
            for record in records
        )
        if conservation.fallback_lines:
            page.diagnostics.warnings.append(
                f"unaccounted_content: {conservation.fallback_lines} line(s) recovered by text fallback"
            )
        if conservation.duplicate_assignment_count:
            page.diagnostics.warnings.append(
                "content_conservation_duplicate_assignment"
            )
        page_reading_text = _build_reading_text(blocks)

        page.diagnostics.facts.update(
            {
                "content_block_count": len(blocks),
                "content_text_block_count": sum(
                    1 for b in blocks if b.kind not in (ContentKind.TABLE, ContentKind.FIGURE)
                ),
                "content_table_block_count": sum(1 for b in blocks if b.kind == ContentKind.TABLE),
                "content_table_fallbacks": result.table_fallbacks,
                "content_claimed_table_lines": result.claimed_table_lines,
                "content_orphan_tables": result.orphan_tables,
                "decorative_block_count": sum(1 for b in blocks if b.decorative),
                "list_segment_count": result.list_segment_count,
                "list_item_count": result.list_item_count,
                "list_inferred_marker_count": result.list_inferred_marker_count,
                "list_continuation_count": result.list_continuation_count,
                "list_unassigned_line_count": result.list_unassigned_line_count,
                "content_source_region_count": len(page.regions),
                "content_assembly_ms": round(result.assembly_ms, 3),
                "content_transformed_sources": [
                    {
                        "source_line_id": record.line_id,
                        "target_line_id": record.target_line_id,
                        "owner_id": record.owner_id,
                        "disposition": record.disposition.value,
                        "reason": record.reason,
                    }
                    for record in records
                    if record.reason == "script_merge"
                ],
                "content_block_order": [
                    {
                        "id": b.block_id,
                        "kind": b.kind.value,
                        "table_id": b.table_id,
                    }
                    for b in blocks
                ],
                **conservation.to_facts(),
            }
        )

        assembled_pages.append(
            dataclasses.replace(
                page,
                content_blocks=blocks,
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


def _page_continuation_titles(page: StructuredPage) -> tuple[str, ...]:
    """Collect title-like top-band text for cross-page decisions."""
    top_limit = page.bbox.y0 + page.bbox.height * 0.22
    candidates: list[str] = []
    for region in page.regions:
        for line in region.native_lines:
            text = line.text.strip()
            if not text or len(text) > 180:
                continue
            if region.kind == RegionKind.TITLE or line.bbox.y0 <= top_limit:
                candidates.append(text)
    return tuple(dict.fromkeys(candidates))


def _apply_repeated_suppression(
    blocks: list,
    page: StructuredPage,
    repeated: dict[str, list[int]],
    preserve_headers_footers: bool,
) -> None:
    """Suppress only blocks wholly owned by confirmed repeated furniture.

    A block may contain both an edge candidate and unique semantic content.
    In that case the safe behaviour is to preserve the complete block rather
    than allowing one repeated line to suppress unrelated lines.

    Partial suppression can be introduced later only with explicit line-level
    block splitting and conservation accounting.
    """
    if preserve_headers_footers or not repeated:
        return

    line_keys = repeated_line_keys(page)
    repeated_keys = set(repeated)

    repeated_role_by_line_id: dict[str, str] = {}

    for region in page.regions:
        for line in [*region.native_lines, *region.ocr_lines]:
            line_id = line.line_id or f"line:{id(line)}"
            key = line_keys.get(line_id)

            if key not in repeated_keys:
                continue
            if page.page_index not in repeated[key]:
                continue

            if key.startswith("header:"):
                reason = "repeated_header"
            elif key.startswith("footer:"):
                reason = "repeated_footer"
            else:
                continue

            previous = repeated_role_by_line_id.get(line_id)

            # If the same source line is ambiguously classified as two
            # different furniture roles, preserving it is safer than
            # suppressing it.
            if previous is not None and previous != reason:
                repeated_role_by_line_id[line_id] = "ambiguous"
            else:
                repeated_role_by_line_id[line_id] = reason

    for block in blocks:
        block_line_ids = {
            line_id
            for line_id in block.line_ids
            if line_id
        }

        if not block_line_ids:
            continue

        roles = {
            repeated_role_by_line_id.get(line_id)
            for line_id in block_line_ids
        }

        # Every line owned by this block must be confirmed as repeated
        # furniture. A partial match must never suppress unique content.
        if None in roles:
            continue

        # Mixed or ambiguous header/footer ownership is also preserved.
        if len(roles) != 1:
            continue

        reason = next(iter(roles))

        if reason == "ambiguous":
            continue

        block.suppressed = True
        block.suppression_reason = reason
