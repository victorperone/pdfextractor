"""Independent conservation-IR audit for the A2 increment.

The production ledger decides ownership and may rebuild blocks.  This module
does not call that decision code; it inspects the resulting page, blocks and
ledger records and checks whether the result is auditable.  It is therefore
safe to use as a second evaluator for exactly-once ownership.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from structured_pdf_text.document import ContentDisposition, PageContentBlock, StructuredPage


class ConservationCategory(str, Enum):
    OWNED_ONCE = "owned_once"
    EXPLICITLY_SUPPRESSED = "explicitly_suppressed"
    TRANSFORMED_SOURCE = "transformed_source"
    BLANK_NOT_ASSESSABLE = "blank_not_assessable"
    DUPLICATE_OWNER = "duplicate_owner"
    UNACCOUNTED = "unaccounted"
    ORPHAN_CLAIM = "orphan_claim"
    LEDGER_OWNER_MISMATCH = "ledger_owner_mismatch"


@dataclass(frozen=True, slots=True)
class ConservationFinding:
    page_index: int
    line_id: str
    category: ConservationCategory
    owner_ids: tuple[str, ...] = ()
    disposition: str | None = None
    target_line_id: str | None = None
    text: str = ""
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConservationAuditSummary:
    page_index: int
    accepted_lines: int
    visible_owned_once: int
    explicitly_suppressed: int
    transformed_sources: int
    blank_not_assessable: int
    duplicate_owners: int
    unaccounted: int
    orphan_claims: int
    ledger_owner_mismatches: int

    @property
    def auditable(self) -> bool:
        return not any(
            (
                self.duplicate_owners,
                self.unaccounted,
                self.orphan_claims,
                self.ledger_owner_mismatches,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "accepted_lines": self.accepted_lines,
            "visible_owned_once": self.visible_owned_once,
            "explicitly_suppressed": self.explicitly_suppressed,
            "transformed_sources": self.transformed_sources,
            "blank_not_assessable": self.blank_not_assessable,
            "duplicate_owners": self.duplicate_owners,
            "unaccounted": self.unaccounted,
            "orphan_claims": self.orphan_claims,
            "ledger_owner_mismatches": self.ledger_owner_mismatches,
            "auditable": self.auditable,
        }


def audit_page_conservation(
    page: StructuredPage,
    blocks: Sequence[PageContentBlock] | None = None,
    ledger_records: Iterable[Any] = (),
) -> tuple[ConservationAuditSummary, tuple[ConservationFinding, ...]]:
    """Audit exactly-once ownership without changing the page or its blocks.

    ``ledger_records`` accepts the production ``ContentDispositionRecord``
    objects or JSON-like mappings with the same field names.  Suppression and
    transformation are explicit evidence; absence of an owner is never
    silently treated as success.
    """

    block_list = list(page.content_blocks if blocks is None else blocks)
    accepted: dict[str, tuple[str, Any]] = {}
    transformed: dict[str, tuple[str, str]] = {}
    for region in page.regions:
        for line in [*region.native_lines, *region.ocr_lines]:
            raw_id = line.line_id or f"line:{id(line)}"
            line_id = _canonical(raw_id)
            accepted.setdefault(line_id, (raw_id, line))
            for source_id in line.merged_source_line_ids:
                source = _canonical(source_id)
                if source != line_id:
                    transformed[source] = (line_id, "script_merge")

    records = {_field(record, "line_id"): record for record in ledger_records}
    claims: dict[str, list[PageContentBlock]] = {}
    findings: list[ConservationFinding] = []
    for block in block_list:
        for raw_id in block.line_ids:
            line_id = _canonical(raw_id)
            claims.setdefault(line_id, []).append(block)

    for line_id, (raw_id, line) in accepted.items():
        # A transformed source is accounted for through its target and its
        # explicit DEDUPLICATED record, not as a second visible line.
        if line_id in transformed:
            continue
        claimants = claims.get(line_id, [])
        visible = [block for block in claimants if not block.suppressed]
        suppressed = [block for block in claimants if block.suppressed]
        record = records.get(line_id)
        disposition = _field(record, "disposition") if record is not None else None
        disposition = disposition.value if isinstance(disposition, ContentDisposition) else disposition
        if not line.text.strip():
            category = ConservationCategory.BLANK_NOT_ASSESSABLE
            reasons = ("line_has_no_non_whitespace_text",)
        elif len(visible) > 1:
            category = ConservationCategory.DUPLICATE_OWNER
            reasons = ("more_than_one_visible_block_claims_canonical_line",)
        elif len(visible) == 1:
            owner_ids = (visible[0].block_id,)
            expected_owner = _field(record, "owner_id") if record is not None else None
            if expected_owner is not None and expected_owner != owner_ids[0]:
                category = ConservationCategory.LEDGER_OWNER_MISMATCH
                reasons = ("ledger_owner_differs_from_visible_block",)
            else:
                category = ConservationCategory.OWNED_ONCE
                reasons = ()
        elif suppressed:
            category = ConservationCategory.EXPLICITLY_SUPPRESSED
            reasons = (suppressed[0].suppression_reason or "suppressed_block",)
        else:
            category = ConservationCategory.UNACCOUNTED
            reasons = ("non_blank_accepted_line_has_no_block_owner",)
        findings.append(
            ConservationFinding(
                page.page_index,
                line_id,
                category,
                tuple(block.block_id for block in claimants),
                disposition,
                _field(record, "target_line_id") if record is not None else None,
                line.text,
                reasons,
            )
        )

    for source_id, (target_id, reason) in transformed.items():
        record = records.get(source_id)
        disposition = _field(record, "disposition") if record is not None else None
        disposition = disposition.value if isinstance(disposition, ContentDisposition) else disposition
        if disposition == ContentDisposition.DEDUPLICATED.value or disposition == ContentDisposition.DEDUPLICATED:
            category = ConservationCategory.TRANSFORMED_SOURCE
            reasons = (reason,)
        else:
            category = ConservationCategory.LEDGER_OWNER_MISMATCH
            reasons = ("transformed_source_missing_deduplicated_ledger_disposition",)
        findings.append(
            ConservationFinding(
                page.page_index,
                source_id,
                category,
                (),
                disposition,
                target_id,
                "",
                reasons,
            )
        )

    for line_id, claimants in claims.items():
        if line_id in accepted or line_id in transformed:
            continue
        findings.append(
            ConservationFinding(
                page.page_index,
                line_id,
                ConservationCategory.ORPHAN_CLAIM,
                tuple(block.block_id for block in claimants),
                reasons=("block_claim_does_not_resolve_to_page_line",),
            )
        )

    counts = Counter(finding.category for finding in findings)
    summary = ConservationAuditSummary(
        page_index=page.page_index,
        accepted_lines=len(accepted),
        visible_owned_once=counts[ConservationCategory.OWNED_ONCE],
        explicitly_suppressed=counts[ConservationCategory.EXPLICITLY_SUPPRESSED],
        transformed_sources=counts[ConservationCategory.TRANSFORMED_SOURCE],
        blank_not_assessable=counts[ConservationCategory.BLANK_NOT_ASSESSABLE],
        duplicate_owners=counts[ConservationCategory.DUPLICATE_OWNER],
        unaccounted=counts[ConservationCategory.UNACCOUNTED],
        orphan_claims=counts[ConservationCategory.ORPHAN_CLAIM],
        ledger_owner_mismatches=counts[ConservationCategory.LEDGER_OWNER_MISMATCH],
    )
    return summary, tuple(findings)


def _canonical(line_id: str) -> str:
    return line_id.split(":lane-", 1)[0]


def _field(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
