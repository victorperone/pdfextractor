"""Core-owned, conservative application of OCR evidence to document targets."""
from __future__ import annotations

import math
from dataclasses import replace
from hashlib import sha256
from threading import RLock
from types import SimpleNamespace

from structured_pdf_text.document import (
    EvidenceRef,
    OcrToken,
    RegionKind,
    SourceKind,
    TableCell,
    TableMethod,
    TextToken,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.fusion.conservation import _refinement_conserves_content


class IntegrationRejected(ValueError):
    """A proposal was validly rejected by a core invariant."""


def prepare_cell_evidence(cell: TableCell, *, table_id: str, page_index: int) -> None:
    """Give existing cell evidence stable IDs without changing shared native tokens.

    Visual detectors may still supply OcrToken objects. Normalize them at the
    domain integration boundary, preserving their actual source and lineage.
    """
    prepared = []
    for index, token in enumerate(cell.tokens):
        if isinstance(token, OcrToken):
            token = TextToken(
                token.text, token.bbox,
                [EvidenceRef(token.source, page_index, f"table-cell-source:{table_id}:{cell.row}:{cell.col}:{index}")],
                max(0.0, min(1.0, token.confidence or 0.0)), token.text,
                provenance=token.provenance, rotation=token.rotation,
                ocr_provenance=token.ocr_provenance,
                evidence_id=token.evidence_id,
            )
        if not token.evidence_id:
            fingerprint = sha256(repr((
                token.text, token.bbox,
                tuple((source.source.value, source.page_index, source.element_id) for source in token.sources),
            )).encode("utf-8")).hexdigest()[:16]
            token = replace(
                token,
                evidence_id=f"cell-source:p{page_index}:{table_id}:{cell.row}:{cell.col}:{index}:{fingerprint}",
            )
        prepared.append(token)
    cell.tokens = prepared


def cell_evidence_snapshot(cell: TableCell) -> tuple:
    """Return a stable optimistic-concurrency snapshot for a table cell."""
    return (
        cell.row,
        cell.col,
        cell.rowspan,
        cell.colspan,
        None if cell.bbox is None else (
            cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1,
        ),
        cell.text,
        cell.confidence,
        tuple(
            (
                token.evidence_id,
                token.text,
                (token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1),
                token.confidence,
                tuple((source.source.value, source.page_index, source.element_id) for source in token.sources),
                token.owner_id,
                token.provenance,
                token.rotation,
                token.derived_from_ids,
                token.transformation_type,
                token.decision_reason,
                token.normalized_text,
                tuple(sorted(getattr(flag, "value", str(flag)) for flag in token.flags)),
                token.font_name,
                token.font_size,
                token.font_weight,
                token.fill_color,
                token.stroke_color,
                token.text_render_mode,
                token.ocr_provenance,
            )
            for token in cell.tokens
        ),
    )


def ocr_tokens_snapshot(tokens: tuple[OcrToken, ...] | list[OcrToken]) -> tuple:
    """Snapshot ordered OCR evidence for optimistic replacement checks.

    Position is part of the snapshot, so identical repeated tokens remain
    distinct evidence occurrences. Invalid numeric evidence is rejected before
    an integration can mutate the target.
    """
    result = []
    for token in tokens:
        values = (token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1)
        if any(not math.isfinite(value) for value in values):
            raise IntegrationRejected("OCR evidence geometry is not finite")
        if token.confidence is not None and not math.isfinite(token.confidence):
            raise IntegrationRejected("OCR evidence confidence is not finite")
        polygon = None if token.polygon is None else tuple((point.x, point.y) for point in token.polygon)
        result.append((
            token.text, values, token.confidence, token.language, token.source.value,
            token.rotation, token.provenance, polygon, token.level, token.ocr_provenance,
            token.evidence_id,
        ))
    return tuple(result)


class NativeOCRIntegrator:
    """Stage and validate OCR replacements before mutating domain targets.

    Recognition and engine-specific ranking stay with their pipeline. This
    integrator applies shared conservation, checks the target snapshot, stages
    all fallible work before commit, preserves cell evidence lineage, and makes
    repeated identical requests idempotent.
    """

    def __init__(self, join_tokens=None):
        self._join_tokens = join_tokens
        self._lock = RLock()
        self._receipts: dict[tuple, tuple] = {}

    def apply_cell_replacement(
        self,
        *,
        request_id: str,
        page_index: int,
        table_id: str,
        cell: TableCell,
        expected_snapshot: tuple,
        evidence: tuple[OcrToken, ...],
        proposed_text: str,
        source_evidence_ids: tuple[str, ...] = (),
        decision_reason: str = "cell_confidence_below_threshold",
    ) -> bool:
        """Apply one validated cell proposal; return False for an identical replay."""
        key = page_index, table_id, cell.row, cell.col, request_id
        proposal = tuple(
            (token.text, token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1,
             token.confidence, token.rotation)
            for token in evidence
        ), proposed_text

        with self._lock:
            receipt = self._receipts.get(key)
            current_snapshot = cell_evidence_snapshot(cell)
            if receipt is not None:
                expected_before, recorded_proposal, expected_after = receipt
                if recorded_proposal != proposal:
                    raise IntegrationRejected("A cell request ID cannot apply different evidence twice")
                if current_snapshot == expected_after:
                    return False
                if current_snapshot != expected_before:
                    raise IntegrationRejected("Cell evidence changed after its OCR proposal was applied")

            if current_snapshot != expected_snapshot:
                raise IntegrationRejected("Cell evidence is stale; re-evaluate the OCR proposal")
            if not callable(self._join_tokens):
                raise IntegrationRejected("Cell integration requires the domain token joiner")
            if not evidence:
                raise IntegrationRejected("A cell replacement requires OCR evidence")
            if source_evidence_ids and (
                len(source_evidence_ids) != len(evidence)
                or len(set(source_evidence_ids)) != len(source_evidence_ids)
                or any(not isinstance(value, str) or not value for value in source_evidence_ids)
            ):
                raise IntegrationRejected("Cell OCR evidence IDs do not match the proposed tokens")
            if cell.text.strip() and not _refinement_conserves_content(
                [SimpleNamespace(text=cell.text)],
                [SimpleNamespace(text=proposed_text)],
            ):
                raise IntegrationRejected("Cell replacement does not conserve existing content")

            original_ids = tuple(dict.fromkeys(
                value for token in cell.tokens
                for value in (token.evidence_id, *token.derived_from_ids) if value
            ))
            source_ids = tuple(source_evidence_ids)
            previous_owners = {token.owner_id for token in cell.tokens if token.owner_id}
            owner_id = next(iter(previous_owners)) if len(previous_owners) == 1 else (
                f"table-cell:{table_id}:{cell.row}:{cell.col}"
            )
            staged_tokens = [
                TextToken(
                    text=token.text,
                    bbox=token.bbox,
                    sources=[EvidenceRef(
                        SourceKind.OCR_REGION,
                        page_index,
                        f"table-cell:{table_id}:{cell.row}:{cell.col}",
                    )],
                    confidence=max(0.0, min(1.0, token.confidence or 0.0)),
                    normalized_text=token.text,
                    provenance="table_cell_ocr",
                    rotation=token.rotation,
                    evidence_id=f"ocr:p{page_index}:cell:{table_id}:{cell.row}:{cell.col}:idx{index}",
                    derived_from_ids=tuple(dict.fromkeys((
                        *original_ids,
                        *((source_ids[index],) if source_ids else ()),
                    ))),
                    transformation_type="table_cell_ocr",
                    decision_reason=decision_reason,
                    owner_id=owner_id,
                    ocr_provenance=token.ocr_provenance,
                )
                for index, token in enumerate(evidence)
            ]
            staged_text = self._join_tokens(staged_tokens)
            staged_confidence = max(
                (token.confidence for token in evidence if token.confidence is not None),
                default=cell.confidence,
            )
            after_snapshot = cell_evidence_snapshot(TableCell(
                cell.row,
                cell.col,
                cell.rowspan,
                cell.colspan,
                cell.bbox,
                staged_text,
                staged_tokens,
                staged_confidence,
            ))

            # All fallible work is complete. Keep the previous cell untouched
            # if validation, token construction, or text joining fails.
            cell.tokens = staged_tokens
            cell.text = staged_text
            cell.confidence = staged_confidence
            self._receipts[key] = (expected_snapshot, proposal, after_snapshot)
            return True

    def apply_token_replacement(
        self,
        *,
        request_id: str,
        page_index: int,
        target_id: str,
        tokens: list[OcrToken],
        expected_snapshot: tuple,
        box: BBox,
        replacement: list[OcrToken],
        selection_indices: tuple[int, ...] | None = None,
        preserve_position: bool = False,
    ) -> tuple[list[OcrToken], bool]:
        """Replace OCR tokens atomically with conservation, revision and replay checks."""
        if not request_id or not target_id:
            raise IntegrationRejected("Token replacement requires request and target IDs")
        key = page_index, target_id, request_id
        proposal = (
            (box.x0, box.y0, box.x1, box.y1),
            ocr_tokens_snapshot(replacement),
            selection_indices,
            preserve_position,
        )
        with self._lock:
            current_snapshot = ocr_tokens_snapshot(tokens)
            receipt = self._receipts.get(("tokens", *key))
            if receipt is not None:
                expected_before, recorded_proposal, expected_after, committed_tokens = receipt
                if recorded_proposal != proposal:
                    raise IntegrationRejected("A token request ID cannot apply different evidence twice")
                if current_snapshot == expected_after:
                    return list(committed_tokens), False
                if current_snapshot != expected_before:
                    raise IntegrationRejected("OCR token evidence changed after its proposal was applied")
            if current_snapshot != expected_snapshot:
                raise IntegrationRejected("OCR token evidence is stale; re-evaluate the proposal")

            if selection_indices is None:
                selected = {index for index, token in enumerate(tokens) if _line_in_box(token, box)}
            else:
                if (len(set(selection_indices)) != len(selection_indices)
                        or any(isinstance(index, bool) or not isinstance(index, int)
                               or index < 0 or index >= len(tokens) for index in selection_indices)):
                    raise IntegrationRejected("Token replacement references an invalid evidence position")
                selected = set(selection_indices)
            inside = [token for index, token in enumerate(tokens) if index in selected]
            if type(preserve_position) is not bool or (preserve_position and (len(selected) != 1 or len(replacement) != 1)):
                raise IntegrationRejected("In-place replacement requires one selected token and one replacement")
            if inside and not replacement:
                raise IntegrationRejected("Token replacement cannot erase existing OCR evidence")
            if inside and replacement and not _refinement_conserves_content(inside, replacement):
                raise IntegrationRejected("Token replacement does not conserve existing content")
            if preserve_position:
                staged = list(tokens)
                staged[next(iter(selected))] = replacement[0]
            elif selection_indices is None:
                staged = replace_tokens_in_box(tokens, box, replacement)
            else:
                staged = [token for index, token in enumerate(tokens) if index not in selected]
                staged.extend(replacement)
            if tuple(staged) == tuple(tokens):
                return list(tokens), False
            after_snapshot = ocr_tokens_snapshot(staged)
            self._receipts[("tokens", *key)] = (
                expected_snapshot, proposal, after_snapshot, tuple(staged),
            )
            return staged, True

    def apply_figure_refinement(
        self,
        *,
        tokens: list[OcrToken],
        box: BBox,
        replacement_tokens: list[OcrToken],
        regions,
        figure_lines,
        figure_tokens,
    ) -> tuple[list[OcrToken], bool]:
        """Stage figure token replacement and region association as one commit.

        A rejected token replacement cannot still overwrite the figure region's
        prior OCR evidence. All list construction happens before either target
        is changed.
        """
        with self._lock:
            staged_tokens = replace_tokens_in_box(tokens, box, replacement_tokens)
            if tuple(staged_tokens) == tuple(tokens):
                return list(tokens), False

            region = best_figure_region(regions, box)
            staged_lines = list(figure_lines)
            staged_figure_tokens = list(figure_tokens)
            if region is not None:
                # Dataclass targets accept these assignments without fallible
                # conversions; both collections have already been staged.
                region.ocr_lines = staged_lines
                region.ocr_tokens = staged_figure_tokens
            return staged_tokens, True


def replace_tokens_in_box(
    tokens: list[OcrToken],
    box: BBox,
    replacement: list[OcrToken],
    *,
    conservative: bool = True,
) -> list[OcrToken]:
    """Replace OCR evidence in a region without silently deleting content."""
    inside = [token for token in tokens if _line_in_box(token, box)]
    outside = [token for token in tokens if not _line_in_box(token, box)]
    if conservative and inside and not replacement:
        return tokens
    if conservative and inside and replacement and not _refinement_conserves_content(inside, replacement):
        return tokens
    return outside + replacement


def best_figure_region(regions, figure_bbox: BBox):
    """Find the existing figure region with the strongest geometric match."""
    candidates = [
        region for region in regions
        if region.kind == RegionKind.FIGURE and region.bbox.iou(figure_bbox) >= 0.25
    ]
    return max(candidates, key=lambda region: region.bbox.iou(figure_bbox), default=None)


def bind_figure_ocr_to_regions(regions, figure_bbox, figure_lines, figure_tokens) -> None:
    """Associate validated figure OCR with the best current figure region."""
    region = best_figure_region(regions, figure_bbox)
    if region is None:
        return
    region.ocr_lines = list(figure_lines)
    region.ocr_tokens = list(figure_tokens)


def merge_ocr_region_tokens_into_table_cells(
    *, tables, regions, page_index: int, join_tokens,
) -> set[int]:
    """Stage and attach OCR from image regions contained in native table cells."""
    consumed: set[int] = set()
    staged: dict[int, tuple[TableCell, list[TextToken]]] = {}
    for table in tables:
        if table.method == TableMethod.VISUAL_MODEL:
            continue
        cells = [cell for cell in table.cells if cell.bbox is not None]
        if not cells:
            continue
        for region in regions:
            if not region.ocr_tokens or region.kind == RegionKind.TABLE:
                continue
            matching_cells = [
                cell for cell in cells
                if region.bbox.overlap_ratio(cell.bbox) >= 0.80
                and cell.bbox.overlap_ratio(region.bbox) >= 0.75
            ]
            if not matching_cells:
                continue
            for token in region.ocr_tokens:
                cell = next((
                    candidate for candidate in matching_cells
                    if candidate.bbox.x0 <= token.bbox.cx <= candidate.bbox.x1
                    and candidate.bbox.y0 <= token.bbox.cy <= candidate.bbox.y1
                ), None)
                if cell is None:
                    continue
                key = id(cell)
                current_tokens = staged.get(key, (cell, list(cell.tokens)))[1]
                if any(
                    existing.text == token.text and existing.bbox.iou(token.bbox) >= 0.80
                    for existing in current_tokens
                ):
                    consumed.add(id(token))
                    continue
                current_tokens.append(TextToken(
                    text=token.text,
                    bbox=token.bbox,
                    sources=[EvidenceRef(
                        token.source, page_index,
                        f"ocr-table:{table.table_id}:{cell.row}:{cell.col}",
                    )],
                    confidence=max(0.0, min(1.0, token.confidence or 0.0)),
                    normalized_text=token.text,
                    provenance=token.provenance or "table_cell_ocr",
                    rotation=token.rotation,
                    evidence_id=token.evidence_id,
                    ocr_provenance=token.ocr_provenance,
                ))
                staged[key] = cell, current_tokens
                consumed.add(id(token))

    # Compute every joined value before committing any target mutation.
    ready = [
        (cell, cell_tokens, join_tokens(cell_tokens))
        for cell, cell_tokens in staged.values()
    ]
    for cell, cell_tokens, text in ready:
        cell.tokens = cell_tokens
        cell.text = text
    return consumed


def _line_in_box(value, box: BBox) -> bool:
    bbox = value.bbox
    return bbox.overlap_ratio(box) >= 0.25 or box.overlap_ratio(bbox) >= 0.25
