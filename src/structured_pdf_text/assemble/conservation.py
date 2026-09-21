"""Content accounting for the canonical page assembly.

The ledger is deliberately independent from rendering.  It records which
canonical line received ownership and creates a text fallback for any line
that assembly failed to place in a block.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from structured_pdf_text.document import (
    ContentDisposition,
    ContentKind,
    PageContentBlock,
    StructuredPage,
    TextLine,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.lists import segment_list_lines
from structured_pdf_text.text.normalize import normalize_reading_text


@dataclass(frozen=True, slots=True)
class ContentDispositionRecord:
    line_id: str
    disposition: ContentDisposition
    owner_id: str | None
    reason: str
    target_line_id: str | None = None


@dataclass(frozen=True, slots=True)
class TransformedContentSource:
    """A source identity consumed by a visible line transformation.

    Transformed sources are part of conservation accounting, but are not
    visible lines and therefore must never participate in fallback creation.
    """

    source_line_id: str
    target_line_id: str
    disposition: ContentDisposition = ContentDisposition.DEDUPLICATED
    reason: str = "script_merge"


@dataclass(frozen=True, slots=True)
class ContentConservationSummary:
    accepted_lines: int
    rendered_lines: int
    table_owned_lines: int
    figure_owned_lines: int
    suppressed_repeated_header_lines: int
    suppressed_repeated_footer_lines: int
    suppressed_decorative_lines: int
    deduplicated_lines: int
    suppressed_policy_lines: int
    unaccounted_lines: int
    duplicate_assignment_count: int
    fallback_lines: int
    duplicate_claims_detected: int = 0
    duplicate_claims_resolved: int = 0
    transformed_source_lines: int = 0

    def to_facts(self) -> dict[str, int]:
        return {
            "content_accepted_lines": self.accepted_lines,
            "content_rendered_lines": self.rendered_lines,
            "content_table_owned_lines": self.table_owned_lines,
            "content_figure_owned_lines": self.figure_owned_lines,
            "content_suppressed_repeated_header_lines": self.suppressed_repeated_header_lines,
            "content_suppressed_repeated_footer_lines": self.suppressed_repeated_footer_lines,
            "content_suppressed_decorative_lines": self.suppressed_decorative_lines,
            "content_deduplicated_lines": self.deduplicated_lines,
            "content_suppressed_policy_lines": self.suppressed_policy_lines,
            "content_unaccounted_lines": self.unaccounted_lines,
            "content_duplicate_assignment_count": self.duplicate_assignment_count,
            "content_fallback_lines": self.fallback_lines,
            "content_duplicate_claims_detected": self.duplicate_claims_detected,
            "content_duplicate_claims_resolved": self.duplicate_claims_resolved,
            "content_transformed_source_lines": self.transformed_source_lines,
        }


def line_identity(line: TextLine, *, fallback_namespace: str = "line") -> str:
    """Return the stable line identity, with a runtime fallback for fixtures."""
    if line.line_id:
        return line.line_id
    return f"{fallback_namespace}:{id(line)}"


def accepted_page_lines(page: StructuredPage) -> list[tuple[str, TextLine]]:
    """Collect unique native/OCR lines accepted into the page IR."""
    all_lines: list[tuple[str, TextLine]] = []
    seen: set[str] = set()
    for region in page.regions:
        for line in [*region.native_lines, *region.ocr_lines]:
            identity = _canonical_line_id(line_identity(line))
            if identity in seen:
                continue
            seen.add(identity)
            all_lines.append((identity, line))

    transformed_ids = {
        source.source_line_id
        for source in _transformed_content_sources_from_lines(
            line for _, line in all_lines
        )
    }
    return [
        (line_id, line)
        for line_id, line in all_lines
        if line_id not in transformed_ids
    ]


def _transformed_content_sources_from_lines(
    lines: Iterable[TextLine],
) -> tuple[TransformedContentSource, ...]:
    """Collect transformed source identities without materializing lines."""
    sources: dict[str, TransformedContentSource] = {}
    for line in lines:
        target_line_id = _canonical_line_id(line_identity(line))
        for source_id in line.merged_source_line_ids:
            source_line_id = _canonical_line_id(source_id)
            if source_line_id == target_line_id:
                continue
            sources.setdefault(
                source_line_id,
                TransformedContentSource(
                    source_line_id=source_line_id,
                    target_line_id=target_line_id,
                ),
            )
    return tuple(sources.values())


def transformed_content_sources(page: StructuredPage) -> tuple[TransformedContentSource, ...]:
    """Return non-renderable source identities consumed by line transforms."""
    lines = (
        line
        for region in page.regions
        for line in [*region.native_lines, *region.ocr_lines]
    )
    return _transformed_content_sources_from_lines(lines)


def record_content_conservation(
    page: StructuredPage,
    blocks: list[PageContentBlock],
    canonical_line_order: tuple[str, ...] | None = None,
) -> tuple[list[PageContentBlock], ContentConservationSummary, tuple[ContentDispositionRecord, ...]]:
    """Account for every accepted line and add safe text fallbacks if needed."""
    accepted = accepted_page_lines(page)
    accepted_map = {
        _canonical_line_id(line_id): line
        for line_id, line in accepted
    }
    transformed_sources = transformed_content_sources(page)

    # The canonical assembler owns this sequence. Conservation must consume it
    # rather than independently re-running geometry or reading-order logic.
    order_source = canonical_line_order or tuple(line_id for line_id, _ in accepted)
    accepted_order: dict[str, int] = {}
    for line_id in order_source:
        canonical = _canonical_line_id(line_id)
        if canonical in accepted_map and canonical not in accepted_order:
            accepted_order[canonical] = len(accepted_order)
    # Keep direct callers safe when a custom order omits an accepted line. The
    # omitted identities are appended deterministically, never geometrically
    # re-sorted, and remain eligible for fallback.
    for line_id, _ in accepted:
        canonical = _canonical_line_id(line_id)
        accepted_order.setdefault(canonical, len(accepted_order))

    # ── Phase 1: enforce exactly-once ownership ───────────────────────────────
    claims_detected, claims_resolved = _resolve_duplicate_ownership(
        blocks,
        accepted_map,
        accepted_order,
    )

    # ── Phase 2: build ledger records ─────────────────────────────────────────
    records: dict[str, ContentDispositionRecord] = {}
    # After resolution there should be no structural duplicates.  The counter
    # is kept for diagnostic truthfulness in case a pathological input slips
    # through (e.g. a block whose line_ids were not reachable via accepted_map).
    duplicate_assignment_count = 0

    for block in blocks:
        disposition, reason = _block_disposition(block)
        block_line_ids = {_canonical_line_id(line_id) for line_id in block.line_ids}
        for line_id in block_line_ids:
            if line_id not in accepted_map:
                continue
            if line_id in records:
                duplicate_assignment_count += 1
                continue
            records[line_id] = ContentDispositionRecord(
                line_id=line_id,
                disposition=disposition,
                owner_id=block.block_id,
                reason=reason,
            )

    # ── Phase 3: unaccounted lines → positional fallbacks ────────────────────
    unaccounted = sorted(
        (
            (line_id, line)
            for line_id, line in accepted
            if line_id not in records and line.text.strip()
        ),
        key=lambda item: accepted_order.get(item[0], len(accepted_order)),
    )
    fallback_blocks: list[PageContentBlock] = []
    for index, (line_id, line) in enumerate(unaccounted, start=1):
        text = normalize_reading_text(line.text).strip()
        if not text:
            continue
        fb = PageContentBlock(
            block_id=f"page-{page.page_index + 1}:conservation-fallback-{index}",
            page_index=page.page_index,
            kind=ContentKind.TEXT,
            bbox=line.bbox,
            order_index=0,  # final order set by _insert_fallbacks_at_position
            text=text,
            source_region_ids=["conservation-fallback"],
            line_ids=[line_id],
        )
        fallback_blocks.append(fb)
        records[line_id] = ContentDispositionRecord(
            line_id=line_id,
            disposition=ContentDisposition.RENDERED,
            owner_id=fb.block_id,
            reason="unaccounted_content_fallback",
        )

    # Register transformed sources only after visible ownership/fallback is
    # final, so their records can point to both target_line_id and final block.
    for source in transformed_sources:
        target_record = records.get(source.target_line_id)
        owner_id = target_record.owner_id if target_record is not None else None
        records[source.source_line_id] = ContentDispositionRecord(
            line_id=source.source_line_id,
            disposition=source.disposition,
            owner_id=owner_id,
            reason=source.reason,
            target_line_id=source.target_line_id,
        )

    # Insert fallbacks at the correct reading-order position instead of appending.
    if fallback_blocks:
        _insert_fallbacks_at_position(blocks, fallback_blocks, accepted_order)

    # ── Phase 4: compute real unaccounted after recovery ─────────────────────
    real_unaccounted = sum(
        1 for line_id, line in accepted
        if line_id not in records
        and line.text.strip()
        and normalize_reading_text(line.text).strip()
    )

    # ── Phase 5: build summary ────────────────────────────────────────────────
    counts = {disposition: 0 for disposition in ContentDisposition}
    for record in records.values():
        counts[record.disposition] += 1
    summary = ContentConservationSummary(
        accepted_lines=len(accepted) + len(transformed_sources),
        rendered_lines=counts[ContentDisposition.RENDERED],
        table_owned_lines=counts[ContentDisposition.TABLE_OWNED],
        figure_owned_lines=counts[ContentDisposition.FIGURE_OWNED],
        suppressed_repeated_header_lines=counts[ContentDisposition.SUPPRESSED_REPEATED_HEADER],
        suppressed_repeated_footer_lines=counts[ContentDisposition.SUPPRESSED_REPEATED_FOOTER],
        suppressed_decorative_lines=counts[ContentDisposition.SUPPRESSED_DECORATIVE],
        deduplicated_lines=counts[ContentDisposition.DEDUPLICATED],
        suppressed_policy_lines=counts[ContentDisposition.SUPPRESSED_POLICY],
        unaccounted_lines=real_unaccounted,
        duplicate_assignment_count=duplicate_assignment_count,
        fallback_lines=len(fallback_blocks),
        duplicate_claims_detected=claims_detected,
        duplicate_claims_resolved=claims_resolved,
        transformed_source_lines=len(transformed_sources),
    )
    return blocks, summary, tuple(records.values())


def _resolve_duplicate_ownership(
    blocks: list[PageContentBlock],
    accepted_map: dict[str, TextLine],
    accepted_order: dict[str, int],
) -> tuple[int, int]:
    """Remove duplicate line claims from losing blocks, rebuilding their content.

    Claimants are ranked by semantic ownership. Tables and OCR-bearing figures
    own physical content before prose; generic table fallbacks are weakest.
    Canonical line order is only a tie-breaker between equivalent claimants.
    Losing claimants have the line stripped from their line_ids and their
    text/bbox recomputed. A block that loses all its lines is suppressed rather
    than deleted so that block-level diagnostics remain accurate.

    Returns (claims_detected, claims_resolved).
    """
    # A malformed upstream block can repeat the same canonical line ID inside
    # its own claim list.  This is not a conflict between semantic owners, but
    # it is still a conservation violation: keeping the duplicate would make
    # the block text and its provenance disagree.  Keep the first visual claim
    # and rebuild the block before ranking inter-block claimants.
    intra_block_duplicates = 0
    for block in blocks:
        unique_ids: list[str] = []
        seen_ids: set[str] = set()
        for line_id in block.line_ids:
            canonical = _canonical_line_id(line_id)
            if canonical in seen_ids:
                intra_block_duplicates += 1
                continue
            seen_ids.add(canonical)
            unique_ids.append(line_id)
        if len(unique_ids) != len(block.line_ids):
            _rebuild_block_from_lines(block, unique_ids, accepted_map)

    # First pass: collect claimants and choose the semantic owner explicitly.
    claimants: dict[str, list[tuple[int, int, int, PageContentBlock]]] = {}
    for block_index, block in enumerate(blocks):
        for lid in block.line_ids:
            canonical = _canonical_line_id(lid)
            if canonical not in accepted_map:
                continue
            claimants.setdefault(canonical, []).append(
                (
                    _ownership_priority(block),
                    -accepted_order.get(canonical, len(accepted_order)),
                    -block_index,
                    block,
                )
            )

    owner_by_line: dict[str, str] = {}
    for canonical, candidates in claimants.items():
        owner_by_line[canonical] = max(candidates, key=lambda item: item[:3])[3].block_id

    # Second pass: strip duplicate claims and rebuild affected blocks.
    claims_detected = intra_block_duplicates
    claims_resolved = intra_block_duplicates
    for block in blocks:
        losing_lids = [
            lid for lid in block.line_ids
            if _canonical_line_id(lid) in accepted_map
            and owner_by_line.get(_canonical_line_id(lid)) != block.block_id
        ]
        if not losing_lids:
            continue

        claims_detected += len(losing_lids)
        losing_set = set(losing_lids)
        remaining_ids = [lid for lid in block.line_ids if lid not in losing_set]

        if remaining_ids:
            remaining_lines = _rebuild_block_from_lines(block, remaining_ids, accepted_map)
            if remaining_lines:
                if block.kind == ContentKind.LIST:
                    segmentation = segment_list_lines(
                        remaining_lines,
                        allow_single=True,
                    )
                    if (
                        segmentation.segments
                        and all(segment.is_list for segment in segmentation.segments)
                        and any(segment.items for segment in segmentation.segments)
                    ):
                        block.list_items = [
                            item
                            for segment in segmentation.segments
                            for item in segment.items
                        ]
                    else:
                        # Preserve the text, but do not claim list structure
                        # when the surviving lines are no longer a safe list.
                        block.kind = ContentKind.TEXT
                        block.list_items = []
                else:
                    block.list_items = []
        else:
            # Every line in this block was already owned elsewhere.
            block.line_ids = []
            block.suppressed = True
            block.suppression_reason = "all_lines_deduplicated"

        claims_resolved += len(losing_lids)

    return claims_detected, claims_resolved


def _rebuild_block_from_lines(
    block: PageContentBlock,
    line_ids: list[str],
    accepted_map: dict[str, TextLine],
) -> list[TextLine]:
    """Rebuild a block after removing repeated or losing line claims."""
    remaining_lines = [
        accepted_map[_canonical_line_id(line_id)]
        for line_id in line_ids
        if _canonical_line_id(line_id) in accepted_map
    ]
    block.line_ids = line_ids
    if not remaining_lines:
        block.text = ""
        block.list_items = []
        return []
    block.text = "\n".join(
        text for text in (
            normalize_reading_text(line.text).strip() for line in remaining_lines
        ) if text
    )
    block.bbox = BBox.union_all([line.bbox for line in remaining_lines])
    return remaining_lines


def _ownership_priority(block: PageContentBlock) -> int:
    """Rank semantic claimants before considering their arrival order."""
    if block.kind == ContentKind.TABLE:
        return 300
    if block.kind == ContentKind.FIGURE:
        return 250
    if block.suppressed:
        return 50
    if block.fallback_from_table:
        return 100
    return 200


def _insert_fallbacks_at_position(
    blocks: list[PageContentBlock],
    fallback_blocks: list[PageContentBlock],
    accepted_order: dict[str, int],
) -> None:
    """Insert fallback blocks at the reading-order position of their source line.

    Each fallback is inserted after the last existing block whose lines all
    precede the fallback's source line in the accepted reading order.  This
    preserves the reading order established by the assembler instead of
    appending fallbacks to the end of the page.
    """
    def _block_max_accepted_idx(block: PageContentBlock) -> int | None:
        indices = [
            accepted_order.get(_canonical_line_id(lid), -1)
            for lid in block.line_ids
        ]
        return max(indices) if indices else None

    for fallback in fallback_blocks:
        fallback_lid = (
            _canonical_line_id(fallback.line_ids[0]) if fallback.line_ids else ""
        )
        fallback_idx = accepted_order.get(fallback_lid, len(accepted_order))

        insert_pos = 0
        for i, block in enumerate(blocks):
            block_max_idx = _block_max_accepted_idx(block)
            if block_max_idx is not None and block_max_idx < fallback_idx:
                insert_pos = i + 1

        blocks.insert(insert_pos, fallback)


def _block_disposition(block: PageContentBlock) -> tuple[ContentDisposition, str]:
    if block.suppressed:
        reason = block.suppression_reason or "explicit_policy"
        if reason == "repeated_header":
            return ContentDisposition.SUPPRESSED_REPEATED_HEADER, reason
        if reason == "repeated_footer":
            return ContentDisposition.SUPPRESSED_REPEATED_FOOTER, reason
        if reason == "decorative":
            return ContentDisposition.SUPPRESSED_DECORATIVE, reason
        return ContentDisposition.SUPPRESSED_POLICY, reason
    if block.kind == ContentKind.TABLE:
        return ContentDisposition.TABLE_OWNED, "table_block"
    if block.kind == ContentKind.FIGURE:
        return ContentDisposition.FIGURE_OWNED, "figure_block"
    return ContentDisposition.RENDERED, "content_block"


def _canonical_line_id(line_id: str) -> str:
    """Map lossless visual sub-lines back to their source native line."""
    marker = ":lane-"
    return line_id.split(marker, 1)[0] if marker in line_id else line_id
