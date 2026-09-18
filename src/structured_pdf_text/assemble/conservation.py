"""Content accounting for the canonical page assembly.

The ledger is deliberately independent from rendering.  It records which
canonical line received ownership and creates a text fallback for any line
that assembly failed to place in a block.
"""
from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.document import (
    ContentDisposition,
    ContentKind,
    PageContentBlock,
    StructuredPage,
    TextLine,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.text.normalize import normalize_reading_text


@dataclass(frozen=True, slots=True)
class ContentDispositionRecord:
    line_id: str
    disposition: ContentDisposition
    owner_id: str | None
    reason: str


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
        }


def line_identity(line: TextLine, *, fallback_namespace: str = "line") -> str:
    """Return the stable line identity, with a runtime fallback for fixtures."""
    if line.line_id:
        return line.line_id
    return f"{fallback_namespace}:{id(line)}"


def accepted_page_lines(page: StructuredPage) -> list[tuple[str, TextLine]]:
    """Collect unique native/OCR lines accepted into the page IR."""
    output: list[tuple[str, TextLine]] = []
    seen: set[str] = set()
    for region in page.regions:
        for line in [*region.native_lines, *region.ocr_lines]:
            identity = line_identity(line)
            if identity in seen:
                continue
            seen.add(identity)
            output.append((identity, line))
    return output


def record_content_conservation(
    page: StructuredPage,
    blocks: list[PageContentBlock],
) -> tuple[list[PageContentBlock], ContentConservationSummary, tuple[ContentDispositionRecord, ...]]:
    """Account for every accepted line and add safe text fallbacks if needed."""
    accepted = accepted_page_lines(page)
    accepted_map = dict(accepted)

    # Position of each canonical line_id in reading order (for fallback placement).
    accepted_order: dict[str, int] = {
        _canonical_line_id(lid): idx for idx, (lid, _) in enumerate(accepted)
    }

    # ── Phase 1: enforce exactly-once ownership ───────────────────────────────
    claims_detected, claims_resolved = _resolve_duplicate_ownership(blocks, accepted_map)

    # ── Phase 2: build ledger records ─────────────────────────────────────────
    records: dict[str, ContentDispositionRecord] = {}
    # After resolution there should be no structural duplicates.  The counter
    # is kept for diagnostic truthfulness in case a pathological input slips
    # through (e.g. a block whose line_ids were not reachable via accepted_map).
    duplicate_assignment_count = 0

    # Index lines that were consumed by _merge_script_lines so their source IDs
    # can be registered as DEDUPLICATED once their owner line is known.
    merged_sources: dict[str, str] = {}  # canonical source_id → owner line_identity
    for _, line in accepted:
        owner_id = line_identity(line)
        for src_id in line.merged_source_line_ids:
            merged_sources[_canonical_line_id(src_id)] = owner_id

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

    # Register source lines consumed by script-merge as DEDUPLICATED.
    for src_id, owner_line_id in merged_sources.items():
        if src_id not in records and src_id in accepted_map:
            records[src_id] = ContentDispositionRecord(
                line_id=src_id,
                disposition=ContentDisposition.DEDUPLICATED,
                owner_id=owner_line_id,
                reason="script_merge",
            )

    # ── Phase 3: unaccounted lines → positional fallbacks ────────────────────
    unaccounted = [
        (line_id, line)
        for line_id, line in accepted
        if line_id not in records and line.text.strip()
    ]
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
        accepted_lines=len(accepted),
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
    )
    return blocks, summary, tuple(records.values())


def _resolve_duplicate_ownership(
    blocks: list[PageContentBlock],
    accepted_map: dict[str, TextLine],
) -> tuple[int, int]:
    """Remove duplicate line claims from losing blocks, rebuilding their content.

    First-claim-wins: whichever block is encountered first in the list keeps
    the line.  Subsequent claimants have the line stripped from their line_ids
    and their text/bbox recomputed from the remaining lines.  A block that
    loses all its lines is suppressed rather than deleted so that block-level
    diagnostics remain accurate.

    Returns (claims_detected, claims_resolved).
    """
    # First pass: assign canonical ownership (first block wins).
    first_owner: dict[str, str] = {}  # canonical_line_id → block_id
    for block in blocks:
        for lid in block.line_ids:
            canonical = _canonical_line_id(lid)
            if canonical in accepted_map and canonical not in first_owner:
                first_owner[canonical] = block.block_id

    # Second pass: strip duplicate claims and rebuild affected blocks.
    claims_detected = 0
    claims_resolved = 0
    for block in blocks:
        losing_lids = [
            lid for lid in block.line_ids
            if _canonical_line_id(lid) in accepted_map
            and first_owner.get(_canonical_line_id(lid)) != block.block_id
        ]
        if not losing_lids:
            continue

        claims_detected += len(losing_lids)
        losing_set = set(losing_lids)
        remaining_ids = [lid for lid in block.line_ids if lid not in losing_set]

        if remaining_ids:
            remaining_lines = [
                accepted_map[_canonical_line_id(lid)]
                for lid in remaining_ids
                if _canonical_line_id(lid) in accepted_map
            ]
            if remaining_lines:
                block.line_ids = remaining_ids
                block.text = "\n".join(
                    t for t in (
                        normalize_reading_text(ln.text).strip() for ln in remaining_lines
                    ) if t
                )
                block.bbox = BBox.union_all([ln.bbox for ln in remaining_lines])
                # list_items cannot be safely reconstructed without a line→item
                # mapping; clear them to avoid stale references.
                block.list_items = []
        else:
            # Every line in this block was already owned elsewhere.
            block.line_ids = []
            block.suppressed = True
            block.suppression_reason = "all_lines_deduplicated"

        claims_resolved += len(losing_lids)

    return claims_detected, claims_resolved


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
    def _block_max_accepted_idx(block: PageContentBlock) -> int:
        indices = [
            accepted_order.get(_canonical_line_id(lid), -1)
            for lid in block.line_ids
        ]
        return max(indices) if indices else -1

    for fallback in fallback_blocks:
        fallback_lid = (
            _canonical_line_id(fallback.line_ids[0]) if fallback.line_ids else ""
        )
        fallback_idx = accepted_order.get(fallback_lid, len(accepted_order))

        insert_pos = 0
        for i, block in enumerate(blocks):
            if _block_max_accepted_idx(block) < fallback_idx:
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
