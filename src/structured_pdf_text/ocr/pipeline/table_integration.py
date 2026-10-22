"""Core-owned table draft validation; no recognition or engine calibration."""
from copy import deepcopy
from dataclasses import dataclass, field, fields
import math
from types import SimpleNamespace

from structured_pdf_text.document import OcrToken, StructuredTable
from structured_pdf_text.fusion.conservation import _refinement_conserves_content
from structured_pdf_text.fusion.integrator import IntegrationRejected, cell_evidence_snapshot, ocr_tokens_snapshot, prepare_cell_evidence
from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
from structured_pdf_text.tables.ocr_lines import _rebuild_table_ocr_lines
from structured_pdf_text.tables.text_join import join_table_tokens

from .authorization import union_contains
from .contracts import ProposalKind, TargetRef, WorkGoal, contains


def table_snapshot(table):
    cells = []
    for cell in table.cells:
        draft = deepcopy(cell)
        prepare_cell_evidence(draft, table_id=table.table_id, page_index=0)
        cells.append((cell_evidence_snapshot(draft), ocr_tokens_snapshot(
            [token for token in cell.tokens if isinstance(token, OcrToken)])))
    return (table.table_id, table.column_count, table.row_count, table.confidence, table.method,
            tuple((part.page_index, part.bbox, part.row_start, part.row_end) for part in table.page_fragments),
            tuple(cells), table.header_rows, table.continued_from_previous_page, table.continues_to_next_page)


@dataclass(slots=True)
class VisualTableTarget:
    target: TargetRef
    bbox: BBox
    table: StructuredTable
    native_lines: tuple
    visible_target: bool = False
    candidate_table: StructuredTable | None = None
    ocr_lines: list = field(default_factory=list)
    ocr_tokens: list = field(default_factory=list)
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        if (not isinstance(self.target, TargetRef) or self.target.kind != "visual_table"
                or not isinstance(self.table, StructuredTable) or self.target.target_id != self.table.table_id
                or self.visible_target is not True):
            raise ValueError("Visual table refinement requires a visible core-owned table")
        self.native_lines = tuple(self.native_lines)
        current = self.snapshot()
        if self.expected_snapshot is None:
            self.expected_snapshot = current
        elif self.expected_snapshot != current:
            raise ValueError("Visual table binding was created from stale evidence")
        self.binding_snapshot = self.metadata()

    def metadata(self):
        return (self.target, self.bbox, self.visible_target, id(self.table),
                table_snapshot(self.candidate_table) if self.candidate_table is not None else None)

    def snapshot(self):
        from .integrator import _line_snapshot

        return (table_snapshot(self.table), _line_snapshot(self.native_lines),
                _line_snapshot(self.ocr_lines), ocr_tokens_snapshot(self.ocr_tokens))

    def stage_candidate(self, table):
        if self.snapshot() != self.expected_snapshot or self.metadata() != self.binding_snapshot:
            raise IntegrationRejected("Table evidence changed before its draft was prepared")
        if not isinstance(table, StructuredTable):
            raise IntegrationRejected("A visual table draft must be a structured table")
        self.candidate_table = deepcopy(table)
        self.binding_snapshot = self.metadata()


def stage_visual_table(selections, target, evidence_order=()):
    from .integrator import _reject_duplicates

    evidence, ids, authorized = [], [], []
    for request, proposal, tokens in selections:
        if (request.goal is not WorkGoal.TABLE or request.scope.target != target.target
                or proposal.kind is not ProposalKind.COMPLEMENT or proposal.composition != "preserve"
                or proposal.replaces_evidence_ids):
            raise IntegrationRejected("Visual table proposal does not match its core binding")
        auth = next(auth for auth in request.authorizations if auth.authorization_id == proposal.authorization_id)
        authorized.extend(auth.regions)
        if any(not union_contains(auth.regions, token.bbox) or not contains(target.bbox, token.bbox) for token in tokens):
            raise IntegrationRejected("Visual table evidence extends outside its authorization or target")
        evidence.extend(tokens)
        ids.extend(proposal.evidence_ids)
    if (not selections or not evidence or len(set(ids)) != len(ids)
            or not union_contains(tuple(authorized), target.bbox) or target.candidate_table is None):
        raise IntegrationRejected("Visual table selection requires unique scoped evidence and a prepared core draft")
    if evidence_order:
        if len(evidence_order) != len(ids) or set(evidence_order) != set(ids):
            raise IntegrationRejected("Visual table selection order does not match its evidence")
        by_id = dict(zip(ids, evidence))
        evidence = [by_id[value] for value in evidence_order]
        ids = list(evidence_order)
    _reject_duplicates(evidence)
    table = deepcopy(target.candidate_table)
    page = selections[0][0].scope.page_index
    if table.table_id != target.table.table_id:
        raise IntegrationRejected("Visual table draft changes table ownership")
    if any(part.page_index != page or part.bbox is None or not contains(target.bbox, part.bbox) for part in table.page_fragments):
        raise IntegrationRejected("Visual table draft extends outside its authorized page or region")
    if not table.page_fragments or type(table.row_count) is not int or type(table.column_count) is not int or min(table.row_count, table.column_count) < 1:
        raise IntegrationRejected("Visual table draft has invalid grid dimensions")
    if not math.isfinite(table.confidence) or not 0. <= table.confidence <= 1.:
        raise IntegrationRejected("Visual table draft confidence is invalid")
    source = {(token.text, token.bbox): value for token, value in zip(evidence, ids)}
    allowed = set(source) | {(token.text, token.bbox) for cell in target.table.cells for token in cell.tokens}
    used = set()
    for cell in table.cells:
        if (cell.bbox is None or not contains(target.bbox, cell.bbox)
                or any(type(value) is not int for value in (cell.row, cell.col, cell.rowspan, cell.colspan))
                or min(cell.row, cell.col) < 0 or min(cell.rowspan, cell.colspan) < 1
                or cell.row + cell.rowspan > table.row_count or cell.col + cell.colspan > table.column_count):
            raise IntegrationRejected("Visual table draft contains invalid cell geometry or indices")
        if not math.isfinite(cell.confidence) or not 0. <= cell.confidence <= 1.:
            raise IntegrationRejected("Visual table cell confidence is invalid")
        if cell.text != join_table_tokens(cell.tokens):
            raise IntegrationRejected("Visual table cell text lacks matching token evidence")
        for token in cell.tokens:
            key = token.text, token.bbox
            if key not in allowed or not contains(target.bbox, token.bbox):
                raise IntegrationRejected("Visual table draft contains unobserved token evidence")
            if not (cell.bbox.x0 <= token.bbox.cx <= cell.bbox.x1 and cell.bbox.y0 <= token.bbox.cy <= cell.bbox.y1):
                raise IntegrationRejected("Visual table token is assigned to a different cell")
            if key in source:
                if key in used:
                    raise IntegrationRejected("Visual table draft assigns one OCR observation to multiple cells")
                used.add(key)
    for index, cell in enumerate(table.cells):
        if any(cell.row < other.row + other.rowspan and other.row < cell.row + cell.rowspan
               and cell.col < other.col + other.colspan and other.col < cell.col + cell.colspan
               for other in table.cells[index + 1:]):
            raise IntegrationRejected("Visual table draft has overlapping grid ownership")
    fusion = fuse_native_and_ocr(list(target.native_lines), evidence)
    tokens = list(fusion.unmatched_ocr_tokens)
    geometry = selections[0][0].scope.page_geometry
    lines = reconstruct_ocr_lines(tokens, page, geometry.bbox, page_rotation=geometry.rotation)
    for line in lines:
        for token in line.tokens:
            value = source.get((token.text, token.bbox))
            if value is not None:
                token.evidence_id = value
                token.derived_from_ids = (value,)
                token.transformation_type = "visual_table_ocr"
                token.decision_reason = "core_authorized_visual_table_refinement"
    lines = _rebuild_table_ocr_lines(table, lines, page, bind_cell_evidence=True)
    for old in target.table.cells:
        if not old.text.strip():
            continue
        matching = [cell for cell in table.cells if old.bbox is not None and cell.bbox is not None
                    and (old.bbox.overlap_ratio(cell.bbox) >= .25 or cell.bbox.overlap_ratio(old.bbox) >= .25)]
        if not _refinement_conserves_content([SimpleNamespace(text=old.text)], [SimpleNamespace(text=cell.text) for cell in matching]):
            raise IntegrationRejected("Visual table refinement loses prior cell content or a protected value")
    for cell in table.cells:
        prepare_cell_evidence(cell, table_id=table.table_id, page_index=page)
        for token in cell.tokens:
            token.owner_id = f"table-cell:{table.table_id}:{cell.row}:{cell.col}"
    return table, lines, tokens, evidence


def commit_visual_table(target, staged):
    table, lines, tokens, _evidence = staged
    for item in fields(StructuredTable):
        setattr(target.table, item.name, getattr(table, item.name))
    target.ocr_lines = lines
    target.ocr_tokens = tokens
