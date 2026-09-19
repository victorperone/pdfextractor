from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "native_text_fidelity"))

from structured_pdf_text.document import (
    Baseline,
    ContentDisposition,
    ContentKind,
    EvidenceRef,
    LayoutRegion,
    PageContentBlock,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    StructuredPage,
    TextLine,
    TextToken,
    WritingDirection,
)
from structured_pdf_text.assemble.conservation import record_content_conservation
from structured_pdf_text.geometry import BBox
from a2_conservation import (
    ConservationCategory,
    audit_page_conservation,
)


def _line(text: str, line_id: str, y: float = 10.0, merged=()) -> TextLine:
    bbox = BBox(0, y, 100, y + 10)
    token = TextToken(text, bbox, [EvidenceRef(SourceKind.NATIVE_PDF, 0, line_id)], 1.0, text)
    return TextLine(
        tokens=[token], bbox=bbox, baseline=Baseline(y + 10),
        direction=WritingDirection.LEFT_TO_RIGHT, native_order_min=None,
        native_order_max=None, line_id=line_id, merged_source_line_ids=tuple(merged),
    )


def _page(lines: list[TextLine]) -> StructuredPage:
    region = LayoutRegion(
        region_id="region-1", kind=RegionKind.TEXT, bbox=BBox(0, 0, 200, 200),
        layout_confidence=1.0, native_lines=lines, ocr_tokens=[],
        quality=RegionQuality(RegionDecision.KEEP_NATIVE),
    )
    return StructuredPage(
        page_index=0, bbox=BBox(0, 0, 200, 200), regions=[region], tables=[],
        raw_text="", reading_text="",
        diagnostics=PageDiagnostics(0, PageStrategy.NATIVE, [], 0, 0),
    )


def _block(block_id: str, line_ids: list[str], *, suppressed=False, reason=None) -> PageContentBlock:
    return PageContentBlock(
        block_id=block_id, page_index=0, kind=ContentKind.TEXT,
        bbox=BBox(0, 0, 100, 100), order_index=0, line_ids=line_ids,
        suppressed=suppressed, suppression_reason=reason,
    )


def test_a2_audits_exactly_once_ownership_without_mutating_blocks():
    page = _page([_line("A", "line-a")])
    block = _block("block-a", ["line-a"])
    before = list(block.line_ids)

    summary, findings = audit_page_conservation(page, [block])

    assert summary.auditable
    assert summary.visible_owned_once == 1
    assert findings[0].category is ConservationCategory.OWNED_ONCE
    assert block.line_ids == before


def test_a2_reports_duplicate_visible_owners_and_orphan_claims():
    page = _page([_line("A", "line-a")])
    blocks = [_block("block-a", ["line-a"]), _block("block-b", ["line-a", "missing"])]

    summary, findings = audit_page_conservation(page, blocks)
    categories = [finding.category for finding in findings]

    assert not summary.auditable
    assert summary.duplicate_owners == 1
    assert summary.orphan_claims == 1
    assert ConservationCategory.DUPLICATE_OWNER in categories
    assert ConservationCategory.ORPHAN_CLAIM in categories


def test_a2_requires_suppression_and_transformation_to_be_explicit():
    source = _line("2", "source-line", y=5)
    target = _line("E²", "target-line", y=10, merged=("source-line",))
    page = _page([target, source])
    suppressed = _block("decorative", ["target-line"], suppressed=True, reason="decorative")
    records = [
        {"line_id": "target-line", "disposition": ContentDisposition.SUPPRESSED_DECORATIVE.value, "owner_id": "decorative"},
        {"line_id": "source-line", "disposition": ContentDisposition.DEDUPLICATED.value, "owner_id": "decorative", "target_line_id": "target-line"},
    ]

    summary, findings = audit_page_conservation(page, [suppressed], records)
    categories = [finding.category for finding in findings]

    assert summary.auditable
    assert ConservationCategory.EXPLICITLY_SUPPRESSED in categories
    assert ConservationCategory.TRANSFORMED_SOURCE in categories


def test_a2_distinguishes_unaccounted_nonblank_content_from_blank_lines():
    page = _page([_line("não contabilizada", "line-u"), _line("   ", "line-blank", y=30)])

    summary, findings = audit_page_conservation(page, [])
    by_id = {finding.line_id: finding for finding in findings}

    assert not summary.auditable
    assert by_id["line-u"].category is ConservationCategory.UNACCOUNTED
    assert by_id["line-blank"].category is ConservationCategory.BLANK_NOT_ASSESSABLE


def test_a2_reaudits_the_production_ledger_after_duplicate_resolution():
    page = _page([_line("A", "line-a")])
    blocks = [_block("first", ["line-a"]), _block("second", ["line-a"])]

    resolved_blocks, production_summary, records = record_content_conservation(page, blocks)
    audit_summary, findings = audit_page_conservation(page, resolved_blocks, records)

    assert production_summary.duplicate_claims_detected == 1
    assert production_summary.duplicate_claims_resolved == 1
    assert audit_summary.auditable
    assert audit_summary.visible_owned_once == 1
    assert all(finding.category is not ConservationCategory.DUPLICATE_OWNER for finding in findings)


def test_a2_reaudits_the_production_fallback_as_visible_ownership():
    page = _page([_line("fallback", "line-u")])

    fallback_blocks, production_summary, records = record_content_conservation(page, [])
    audit_summary, findings = audit_page_conservation(page, fallback_blocks, records)

    assert production_summary.fallback_lines == 1
    assert audit_summary.auditable
    assert findings[0].category is ConservationCategory.OWNED_ONCE
