"""Canonical page content assembly.

Responsibility: receive a StructuredPage with regions and physical tables already
detected, and produce the final ordered, deduplicated PageContentBlock list that
represents the page. No rendering decisions are made here.

Rule: a line is suppressed from prose only when a valid StructuredTable with
renderable content physically claims it via cell bbox overlap (preferred) or
fragment bbox (fallback). A TABLE region label alone is not sufficient.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from structured_pdf_text.document import (
    ContentKind,
    LayoutRegion,
    PageContentBlock,
    RegionKind,
    StructuredPage,
    StructuredTable,
    TableFragment,
    TextLine,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.line_detector import lines_to_text
from structured_pdf_text.text.normalize import normalize_text
from structured_pdf_text.text.reading_order import (
    ReadingOrderDecision,
    order_lines_in_region,
    order_regions,
)


@dataclass(frozen=True, slots=True)
class PageContentAssemblyResult:
    blocks: tuple[PageContentBlock, ...]
    reading_text: str
    reading_decision: ReadingOrderDecision
    claimed_table_lines: int
    table_fallbacks: int
    orphan_tables: int
    assembly_ms: float


def assemble_page_content(page: StructuredPage) -> PageContentAssemblyResult:
    """Build the canonical content blocks for a page.

    Processes regions in reading order, interleaving text and table blocks.
    Every line either becomes prose or is claimed by a table — never both,
    never neither (INV-01, INV-02). Tables without renderable content fall
    back to prose (INV-05). Physical tables stay on their physical page (INV-03).
    """
    t0 = time.perf_counter()

    ordered_regions, region_edges = order_regions(page.regions)
    reading_decision = ReadingOrderDecision(
        region_order=tuple(r.region_id for r in ordered_regions),
        column_groups=0,
        rotated_lines=0,
        table_regions=0,
        native_order_consistency=None,
        region_edges=region_edges,
        deduplicated_lines=0,
    )

    physical_tables = _physical_tables_for_page(page)
    emitted_table_ids: set[str] = set()

    blocks: list[PageContentBlock] = []
    claimed_table_lines = 0
    table_fallbacks = 0

    for region in ordered_regions:
        region_blocks, claimed, fallbacks = _build_region_blocks(
            region=region,
            page_index=page.page_index,
            tables=physical_tables,
            emitted_table_ids=emitted_table_ids,
        )
        blocks.extend(region_blocks)
        claimed_table_lines += claimed
        table_fallbacks += fallbacks

    orphan_blocks, orphan_count = _insert_orphan_tables(
        page_index=page.page_index,
        tables=physical_tables,
        emitted_table_ids=emitted_table_ids,
    )
    blocks.extend(orphan_blocks)

    blocks = _reindex_blocks(blocks)

    reading_text = _build_reading_text(blocks)

    assembly_ms = (time.perf_counter() - t0) * 1000

    return PageContentAssemblyResult(
        blocks=tuple(blocks),
        reading_text=reading_text,
        reading_decision=reading_decision,
        claimed_table_lines=claimed_table_lines,
        table_fallbacks=table_fallbacks,
        orphan_tables=orphan_count,
        assembly_ms=assembly_ms,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _physical_tables_for_page(page: StructuredPage) -> list[StructuredTable]:
    """Return tables that have at least one fragment on this page."""
    return [
        table
        for table in page.tables
        if any(f.page_index == page.page_index for f in table.page_fragments)
    ]


def _table_fragment_bbox(table: StructuredTable, page_index: int) -> BBox | None:
    """Return the bbox of the table's fragment on the given page, if any."""
    for fragment in table.page_fragments:
        if fragment.page_index == page_index and fragment.bbox is not None:
            return fragment.bbox
    return None


def _table_has_renderable_content(table: StructuredTable) -> bool:
    """Return True when the table can produce a non-empty serialization."""
    if table.column_count <= 0 or table.row_count <= 0:
        return False
    if not table.cells:
        return False
    return any(cell.text.strip() for cell in table.cells)


def _line_claimed_by_table(
    line: TextLine,
    table: StructuredTable,
    page_index: int,
) -> bool:
    """Return True when the table physically claims this line.

    Priority:
    1. Cell bboxes — preferred; avoids claiming lines outside the actual cells.
    2. Fragment bbox — fallback when cells have no bboxes.
    """
    if not _table_has_renderable_content(table):
        return False

    cell_bboxes = [cell.bbox for cell in table.cells if cell.bbox is not None]
    if cell_bboxes:
        line_cx, line_cy = line.bbox.cx, line.bbox.cy
        return any(
            cb.x0 <= line_cx <= cb.x1 and cb.y0 <= line_cy <= cb.y1
            for cb in cell_bboxes
        )

    fragment_bbox = _table_fragment_bbox(table, page_index)
    if fragment_bbox is not None:
        return (
            fragment_bbox.x0 <= line.bbox.cx <= fragment_bbox.x1
            and fragment_bbox.y0 <= line.bbox.cy <= fragment_bbox.y1
        )

    return False


_REGION_TO_CONTENT_KIND: dict[RegionKind, ContentKind] = {
    RegionKind.TEXT: ContentKind.TEXT,
    RegionKind.TITLE: ContentKind.TITLE,
    RegionKind.LIST: ContentKind.LIST,
    RegionKind.TABLE: ContentKind.TABLE,
    RegionKind.FIGURE: ContentKind.FIGURE,
    RegionKind.CAPTION: ContentKind.CAPTION,
    RegionKind.HEADER: ContentKind.HEADER,
    RegionKind.FOOTER: ContentKind.FOOTER,
    RegionKind.FOOTNOTE: ContentKind.FOOTNOTE,
    RegionKind.MARGINALIA: ContentKind.MARGINALIA,
    RegionKind.UNKNOWN: ContentKind.UNKNOWN,
}


def _content_kind_from_region(region_kind: RegionKind) -> ContentKind:
    return _REGION_TO_CONTENT_KIND.get(region_kind, ContentKind.UNKNOWN)


def _normalized_lines_text(lines: list[TextLine]) -> str:
    """Produce normalized text from a list of lines (NFC, no control chars)."""
    return normalize_text(lines_to_text(lines)).strip()


def _build_region_blocks(
    region: LayoutRegion,
    page_index: int,
    tables: list[StructuredTable],
    emitted_table_ids: set[str],
) -> tuple[list[PageContentBlock], int, int]:
    """Build blocks for one region, interleaving prose and table blocks.

    Returns (blocks, claimed_table_lines, table_fallbacks).
    """
    ordered_lines, _ = order_lines_in_region(region)
    kind = _content_kind_from_region(region.kind)

    # Find tables that intersect this region (can be >1 — INV-41).
    candidate_tables = [
        t for t in tables
        if _table_intersects_region(t, region, page_index)
        and t.table_id not in emitted_table_ids
    ]
    # Sort candidate tables by their fragment position inside the region.
    candidate_tables.sort(
        key=lambda t: _table_fragment_sort_key(t, page_index)
    )

    if not ordered_lines and not candidate_tables:
        return [], 0, 0

    # When there are no intersecting tables with renderable content, emit the
    # region as prose (or as a fallback if it was labelled TABLE).
    usable_tables = [t for t in candidate_tables if _table_has_renderable_content(t)]
    if not usable_tables:
        fallback = kind == ContentKind.TABLE
        blocks = _emit_prose_block(
            lines=ordered_lines,
            region=region,
            kind=ContentKind.TEXT if fallback else kind,
            page_index=page_index,
            fallback_from_table=fallback,
        )
        return blocks, 0, (1 if fallback and ordered_lines else 0)

    # Partition lines: claimed by a table vs. prose.
    blocks: list[PageContentBlock] = []
    claimed_count = 0
    table_fallbacks = 0

    # Walk lines in order, emitting prose chunks and table blocks as we go.
    pending_prose: list[TextLine] = []

    def flush_prose(prose_lines: list[TextLine]) -> None:
        if not prose_lines:
            return
        prose_kind = ContentKind.TEXT if kind == ContentKind.TABLE else kind
        blocks.extend(
            _emit_prose_block(
                lines=prose_lines,
                region=region,
                kind=prose_kind,
                page_index=page_index,
                fallback_from_table=(kind == ContentKind.TABLE),
            )
        )

    # Track which table owns which line cluster.
    emitted_in_pass: set[str] = set()

    for line in ordered_lines:
        owner = next(
            (t for t in usable_tables if _line_claimed_by_table(line, t, page_index)),
            None,
        )
        if owner is None:
            pending_prose.append(line)
            continue

        # Emit any accumulated prose before this table.
        flush_prose(pending_prose)
        pending_prose = []
        claimed_count += 1

        if owner.table_id not in emitted_table_ids and owner.table_id not in emitted_in_pass:
            emitted_in_pass.add(owner.table_id)
            blocks.append(
                _emit_table_block(owner, page_index, region)
            )

    # Emit remaining prose after the last table.
    flush_prose(pending_prose)

    # Mark all emitted tables as done for the page.
    for table_id in emitted_in_pass:
        emitted_table_ids.add(table_id)

    return blocks, claimed_count, table_fallbacks


def _table_intersects_region(
    table: StructuredTable,
    region: LayoutRegion,
    page_index: int,
) -> bool:
    frag_bbox = _table_fragment_bbox(table, page_index)
    if frag_bbox is None:
        return False
    return (
        region.bbox.overlap_ratio(frag_bbox) > 0.0
        or frag_bbox.overlap_ratio(region.bbox) > 0.0
    )


def _table_fragment_sort_key(table: StructuredTable, page_index: int) -> tuple[float, float]:
    bbox = _table_fragment_bbox(table, page_index)
    return (bbox.y0, bbox.x0) if bbox else (float("inf"), float("inf"))


def _emit_prose_block(
    lines: list[TextLine],
    region: LayoutRegion,
    kind: ContentKind,
    page_index: int,
    fallback_from_table: bool = False,
) -> list[PageContentBlock]:
    if not lines:
        return []
    text = _normalized_lines_text(lines)
    if not text:
        return []
    bbox = BBox(
        x0=min(l.bbox.x0 for l in lines),
        y0=min(l.bbox.y0 for l in lines),
        x1=max(l.bbox.x1 for l in lines),
        y1=max(l.bbox.y1 for l in lines),
    )
    return [
        PageContentBlock(
            block_id=f"page-{page_index + 1}:region-{region.region_id}",
            page_index=page_index,
            kind=kind,
            bbox=bbox,
            order_index=0,  # reindexed by _reindex_blocks
            text=text,
            heading_level=region.heading_level if kind == ContentKind.TITLE else None,
            source_region_ids=[region.region_id],
            fallback_from_table=fallback_from_table,
        )
    ]


def _emit_table_block(
    table: StructuredTable,
    page_index: int,
    region: LayoutRegion,
) -> PageContentBlock:
    frag_bbox = _table_fragment_bbox(table, page_index)
    bbox = frag_bbox or region.bbox
    return PageContentBlock(
        block_id=f"page-{page_index + 1}:table-{table.table_id}",
        page_index=page_index,
        kind=ContentKind.TABLE,
        bbox=bbox,
        order_index=0,  # reindexed by _reindex_blocks
        table_id=table.table_id,
        source_region_ids=[region.region_id],
        confidence=table.confidence,
    )


def _insert_orphan_tables(
    page_index: int,
    tables: list[StructuredTable],
    emitted_table_ids: set[str],
) -> tuple[list[PageContentBlock], int]:
    """Emit valid tables that were not claimed by any region (INV-43)."""
    orphan_blocks: list[PageContentBlock] = []
    for table in tables:
        if table.table_id in emitted_table_ids:
            continue
        if not _table_has_renderable_content(table):
            continue
        frag_bbox = _table_fragment_bbox(table, page_index)
        bbox = frag_bbox or BBox(x0=0, y0=0, x1=1, y1=1)
        orphan_blocks.append(
            PageContentBlock(
                block_id=f"page-{page_index + 1}:orphan-{table.table_id}",
                page_index=page_index,
                kind=ContentKind.TABLE,
                bbox=bbox,
                order_index=0,
                table_id=table.table_id,
                source_region_ids=[],
            )
        )
        emitted_table_ids.add(table.table_id)

    orphan_blocks = _order_content_blocks(orphan_blocks)
    return orphan_blocks, len(orphan_blocks)


def _order_content_blocks(blocks: list[PageContentBlock]) -> list[PageContentBlock]:
    """Sort blocks by geometric position (y0, then x0). Encapsulated for future improvement."""
    return sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))


def _reindex_blocks(blocks: list[PageContentBlock]) -> list[PageContentBlock]:
    """Assign deterministic order_index values after all blocks are collected."""
    ordered = _order_content_blocks(blocks)
    result: list[PageContentBlock] = []
    for i, block in enumerate(ordered):
        block.order_index = i
        # Reassign deterministic block_id based on final order.
        page_num = block.page_index + 1
        block.block_id = f"page-{page_num}:block-{i + 1}"
        result.append(block)
    return result


def _build_reading_text(blocks: list[PageContentBlock]) -> str:
    """Build reading_text from blocks in order_index sequence."""
    parts: list[str] = []
    for block in sorted(blocks, key=lambda b: b.order_index):
        if block.kind in (ContentKind.HEADER, ContentKind.FOOTER, ContentKind.FIGURE):
            continue
        if block.kind == ContentKind.TABLE:
            continue
        if block.text:
            parts.append(block.text)
    return "\n\n".join(parts)
