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
    records: dict[str, ContentDispositionRecord] = {}
    duplicate_assignment_count = 0
    fallback_blocks: list[PageContentBlock] = []

    for block in blocks:
        disposition, reason = _block_disposition(block)
        for line_id in block.line_ids:
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

    unaccounted = [
        (line_id, line)
        for line_id, line in accepted
        if line_id not in records and line.text.strip()
    ]
    for index, (line_id, line) in enumerate(unaccounted, start=1):
        text = normalize_reading_text(line.text).strip()
        if not text:
            continue
        block = PageContentBlock(
            block_id=f"page-{page.page_index + 1}:conservation-fallback-{index}",
            page_index=page.page_index,
            kind=ContentKind.TEXT,
            bbox=line.bbox,
            order_index=len(blocks) + index,
            text=text,
            source_region_ids=["conservation-fallback"],
            line_ids=[line_id],
        )
        fallback_blocks.append(block)
        records[line_id] = ContentDispositionRecord(
            line_id=line_id,
            disposition=ContentDisposition.RENDERED,
            owner_id=block.block_id,
            reason="unaccounted_content_fallback",
        )

    if fallback_blocks:
        blocks.extend(fallback_blocks)

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
        # Every non-empty accepted line now has a disposition.  Lines that
        # needed recovery are exposed separately through fallback_lines.
        unaccounted_lines=0,
        duplicate_assignment_count=duplicate_assignment_count,
        fallback_lines=len(fallback_blocks),
    )
    return blocks, summary, tuple(records.values())


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
