"""Core-owned, atomic integration of validated OCR proposals."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from hashlib import sha256
from threading import RLock

from structured_pdf_text.document import LayoutRegion, RegionKind, TableCell, TextToken
from structured_pdf_text.geometry import BBox
from structured_pdf_text.fusion.integrator import (
    IntegrationRejected,
    NativeOCRIntegrator,
    cell_evidence_snapshot,
    ocr_tokens_snapshot,
)
from structured_pdf_text.ocr.pipeline.converters import to_domain
from structured_pdf_text.tables.text_join import join_table_tokens

from .authorization import union_contains
from .table_integration import VisualTableTarget, commit_visual_table, stage_visual_table
from .contracts import (
    OCRPipelineResult,
    OCRWorkRequest,
    ProposalKind,
    TargetRef,
    WorkGoal,
    contains,
)


def _line_snapshot(lines) -> tuple:
    return tuple(
        (
            line.text,
            (line.bbox.x0, line.bbox.y0, line.bbox.x1, line.bbox.y1),
            None if line.baseline is None else (line.baseline.y, line.baseline.angle),
            line.direction.value,
            line.native_order_min,
            line.native_order_max,
            line.line_id,
            line.text_override,
            line.join_next_without_space,
            line.ghost_punctuation_candidate,
            line.merged_source_line_ids,
            line.gap_mode,
            line.order_mode,
            tuple(
                (
                    token.text,
                    (token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1),
                    token.confidence,
                    token.evidence_id,
                    tuple((source.source.value, source.page_index, source.element_id) for source in token.sources),
                    token.owner_id,
                    token.provenance,
                    token.derived_from_ids,
                    token.transformation_type,
                    token.decision_reason,
                )
                for token in line.tokens
            ),
        )
        for line in lines
    )


def _region_snapshot(region: LayoutRegion) -> tuple:
    return (
        region.region_id,
        region.kind.value,
        (region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1),
        region.layout_confidence,
        _line_snapshot(region.native_lines),
        _line_snapshot(region.ocr_lines),
        region.quality.decision.value,
        tuple(region.quality.reasons),
        region.quality.confidence,
        region.heading_level,
        region.semantic_role,
        region.edge_role,
        ocr_tokens_snapshot(region.ocr_tokens),
    )


def _binding_metadata(binding) -> tuple:
    if isinstance(binding, VisualTableTarget):
        return binding.metadata()
    if isinstance(binding, FigureRefinementTarget):
        return (binding.target, binding.bbox, tuple(binding.evidence_ids), binding.selected_evidence_ids,
                binding.visible_target, id(binding.region) if binding.region is not None else None)
    if isinstance(binding, TokenTarget):
        return (binding.target, binding.bbox, binding.goal, tuple(binding.evidence_ids),
                tuple(binding.selected_evidence_ids), frozenset(binding.visible_evidence_ids), binding.visible_target,
                tuple(sorted(binding.lineage.items())))
    if isinstance(binding, RegionTarget):
        return (binding.target, tuple(binding.evidence_ids), frozenset(binding.visible_evidence_ids), binding.visible_target)
    if isinstance(binding, FigureTarget):
        return (binding.target, tuple(binding.evidence_ids), binding.visible_target)
    if isinstance(binding, CellTarget):
        return (binding.target, binding.table_id, binding.page_index,
                frozenset(binding.visible_evidence_ids), binding.visible_target, binding.allow_single_digit)
    raise ValueError("Unsupported core integration target binding")


def _reject_duplicates(tokens, *, existing=()) -> None:
    """Reject spatially duplicated evidence; candidate ranking stays local."""
    accepted = list(existing)
    for token in tokens:
        normalized = " ".join(token.text.casefold().split())
        for previous in accepted:
            if " ".join(previous.text.casefold().split()) != normalized:
                continue
            if token.bbox.iou(previous.bbox) >= 0.20:
                raise IntegrationRejected("OCR proposal contains spatially duplicated evidence")
            intersection = token.bbox.intersection(previous.bbox)
            min_area = min(token.bbox.area, previous.bbox.area)
            if intersection is not None and min_area > 0 and intersection.area / min_area >= 0.45:
                raise IntegrationRejected("OCR proposal contains spatially duplicated evidence")
        accepted.append(token)


@dataclass(slots=True)
class FigureRefinementTarget:
    """One atomic binding for the page token stream and its owning figure."""

    target: TargetRef
    bbox: BBox
    tokens: list
    evidence_ids: list[str]
    selected_evidence_ids: tuple[str, ...]
    region: LayoutRegion | None
    native_lines: tuple
    visible_target: bool = False
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.target, TargetRef) or self.target.kind != "figure_refinement" or self.visible_target is not True:
            raise ValueError("Dedicated figure refinement requires a visible core-owned binding")
        if self.region is not None and self.region.kind is not RegionKind.FIGURE:
            raise ValueError("Figure refinement cannot change a non-figure region")
        self.evidence_ids = list(self.evidence_ids)
        self.selected_evidence_ids = tuple(self.selected_evidence_ids)
        self.native_lines = tuple(self.native_lines)
        if len(set(self.evidence_ids)) != len(self.tokens) or len(self.evidence_ids) != len(self.tokens) or not all(self.evidence_ids):
            raise ValueError("Figure stream requires unique ordered evidence IDs")
        selected = set(self.selected_evidence_ids)
        expected = {value for token, value in zip(self.tokens, self.evidence_ids)
                    if token.bbox.overlap_ratio(self.bbox) >= .25 or self.bbox.overlap_ratio(token.bbox) >= .25}
        if len(selected) != len(self.selected_evidence_ids) or selected != expected:
            raise ValueError("Figure binding must identify exactly its prior stream evidence")
        if any(not contains(self.bbox, token.bbox) for token, value in zip(self.tokens, self.evidence_ids) if value in selected):
            raise ValueError("Figure replacement cannot delete evidence outside its authorized crop")
        actual = self.snapshot()
        if self.expected_snapshot is None:
            self.expected_snapshot = actual
        elif self.expected_snapshot != actual:
            raise ValueError("Figure refinement binding is stale")
        self.binding_snapshot = _binding_metadata(self)

    def snapshot(self):
        return (ocr_tokens_snapshot(self.tokens), _region_snapshot(self.region) if self.region is not None else None,
                _line_snapshot(self.native_lines))


@dataclass(slots=True)
class TokenTarget:
    """Core-owned page token collection with a finite, visible replacement scope."""

    target: TargetRef
    bbox: BBox
    tokens: list
    evidence_ids: list[str]
    selected_evidence_ids: tuple[str, ...]
    visible_evidence_ids: set[str]
    goal: WorkGoal = WorkGoal.REGION
    visible_target: bool = False
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)
    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.target, TargetRef) or self.target.kind != "ocr_tokens":
            raise ValueError("Token replacement requires a core-owned OCR token target")
        if self.visible_target is not True or self.goal not in (WorkGoal.REGION, WorkGoal.NOTE, WorkGoal.CRITICAL_DATA):
            raise ValueError("Token target requires a visible core-approved refinement goal")
        self.evidence_ids = list(self.evidence_ids)
        self.selected_evidence_ids = tuple(self.selected_evidence_ids)
        self.visible_evidence_ids = set(self.visible_evidence_ids)
        known = set(self.evidence_ids)
        if len(known) != len(self.tokens) or len(self.evidence_ids) != len(self.tokens) or not all(self.evidence_ids):
            raise ValueError("Token target requires stable, unique evidence IDs")
        selected = set(self.selected_evidence_ids)
        if not selected or len(selected) != len(self.selected_evidence_ids) or not selected <= self.visible_evidence_ids <= known:
            raise ValueError("Replacement must select visible evidence belonging to its target")
        if any(not contains(self.bbox, token.bbox) for token, evidence_id in zip(self.tokens, self.evidence_ids)
               if evidence_id in selected):
            raise ValueError("Selected OCR evidence extends outside its authorized target")
        actual = ocr_tokens_snapshot(self.tokens)
        if self.expected_snapshot is None:
            self.expected_snapshot = actual
        elif self.expected_snapshot != actual:
            raise ValueError("Token binding was created from a stale target snapshot")
        self.binding_snapshot = _binding_metadata(self)


@dataclass(slots=True)
class RegionTarget:
    """Core-issued binding for replacing visible OCR evidence in one region."""

    target: TargetRef
    region: LayoutRegion
    evidence_ids: list[str]
    visible_evidence_ids: set[str]
    visible_target: bool = False
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.target, TargetRef) or not isinstance(self.region, LayoutRegion):
            raise ValueError("Region proposals require a core-owned LayoutRegion binding")
        if self.target.kind != "region" or self.target.target_id != self.region.region_id:
            raise ValueError("Region binding does not match its target reference")
        if self.visible_target is not True:
            raise ValueError("Region replacement requires a visible, core-approved target")
        self.evidence_ids = list(self.evidence_ids)
        self.visible_evidence_ids = set(self.visible_evidence_ids)
        if len(self.evidence_ids) != len(self.region.ocr_tokens) or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Region evidence IDs must uniquely identify its ordered OCR tokens")
        if not self.visible_evidence_ids <= set(self.evidence_ids):
            raise ValueError("Visible region evidence must belong to the bound target")
        actual = _region_snapshot(self.region)
        if self.expected_snapshot is None:
            self.expected_snapshot = actual
        elif self.expected_snapshot != actual:
            raise ValueError("Region binding was created from a stale target snapshot")
        self.binding_snapshot = _binding_metadata(self)


@dataclass(slots=True)
class FigureTarget:
    """Core-issued binding for associating OCR evidence with a figure."""

    target: TargetRef
    region: LayoutRegion
    evidence_ids: list[str]
    visible_target: bool = False
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.target, TargetRef) or not isinstance(self.region, LayoutRegion):
            raise ValueError("Figure proposals require a core-owned LayoutRegion binding")
        if self.target.kind != "figure" or self.target.target_id != self.region.region_id:
            raise ValueError("Figure binding does not match its target reference")
        if self.region.kind is not RegionKind.FIGURE:
            raise ValueError("Figure association target must be a figure region")
        if self.visible_target is not True:
            raise ValueError("Figure association requires a visible, core-approved target")
        self.evidence_ids = list(self.evidence_ids)
        if len(self.evidence_ids) != len(self.region.ocr_tokens) or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Figure evidence IDs must uniquely identify its ordered OCR tokens")
        actual = _region_snapshot(self.region)
        if self.expected_snapshot is None:
            self.expected_snapshot = actual
        elif self.expected_snapshot != actual:
            raise ValueError("Figure binding was created from a stale target snapshot")
        self.binding_snapshot = _binding_metadata(self)


@dataclass(slots=True)
class CellTarget:
    """Core-issued binding for correcting one table cell."""

    target: TargetRef
    table_id: str
    page_index: int
    cell: TableCell
    visible_evidence_ids: set[str]
    visible_target: bool = False
    expected_snapshot: tuple | None = None
    binding_snapshot: tuple | None = field(init=False, default=None, repr=False)
    allow_single_digit: bool = False

    def __post_init__(self):
        if not isinstance(self.target, TargetRef) or not isinstance(self.cell, TableCell):
            raise ValueError("Cell proposals require a core-owned TableCell binding")
        if self.target.kind != "cell" or not self.table_id:
            raise ValueError("Cell binding requires a table and cell target reference")
        if self.visible_target is not True:
            raise ValueError("Cell correction requires a visible, core-approved target")
        if type(self.allow_single_digit) is not bool:
            raise ValueError("Single-digit cell context must be explicitly approved by the core")
        self.visible_evidence_ids = set(self.visible_evidence_ids)
        actual = cell_evidence_snapshot(self.cell)
        if self.expected_snapshot is None:
            self.expected_snapshot = actual
        elif self.expected_snapshot != actual:
            raise ValueError("Cell binding was created from a stale target snapshot")
        known = {token.evidence_id for token in self.cell.tokens if token.evidence_id}
        if len(known) != len(self.cell.tokens) or (self.cell.text.strip() and not known):
            raise ValueError("Cell correction requires stable IDs for all existing cell evidence")
        if not self.visible_evidence_ids <= known:
            raise ValueError("Visible cell evidence must belong to the bound target")
        self.binding_snapshot = _binding_metadata(self)


@dataclass(frozen=True, slots=True)
class PriorityRecommendation:
    target: TargetRef
    authorization_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class _Receipt:
    proposals: tuple[tuple, ...]
    target_snapshots: tuple[tuple[object, tuple], ...]


class ProposalIntegrator:
    """Validate and atomically apply typed proposals to core-owned targets.

    Pipelines only return immutable evidence and proposals. Mutable domain
    targets enter through explicit bindings created by the coordinator. All
    proposed mutations are staged and validated before a target is changed.
    """

    def __init__(self):
        self._lock = RLock()
        self._receipts: dict[tuple[str, str], _Receipt] = {}
        self._selection_receipts: dict[tuple, tuple] = {}
        self._priorities: dict[tuple[str, str], tuple[PriorityRecommendation, ...]] = {}

    @property
    def priority_recommendations(self) -> tuple[PriorityRecommendation, ...]:
        with self._lock:
            return tuple(item for batch in self._priorities.values() for item in batch)

    @staticmethod
    def _proposal_receipt(result: OCRPipelineResult) -> tuple[tuple, ...]:
        return tuple(
            (
                proposal.proposal_id,
                proposal.authorization_id,
                proposal.target,
                proposal.kind,
                proposal.evidence_ids,
                proposal.replaces_evidence_ids,
                proposal.reason,
                proposal.composition,
            )
            for proposal in result.proposals
        )

    @staticmethod
    def _binding_snapshot(binding) -> tuple:
        if isinstance(binding, VisualTableTarget):
            return binding.snapshot(), _binding_metadata(binding)
        if isinstance(binding, FigureRefinementTarget):
            return binding.snapshot(), _binding_metadata(binding)
        if isinstance(binding, TokenTarget):
            return ocr_tokens_snapshot(binding.tokens), _binding_metadata(binding)
        if isinstance(binding, (RegionTarget, FigureTarget)):
            return _region_snapshot(binding.region), _binding_metadata(binding)
        if isinstance(binding, CellTarget):
            return cell_evidence_snapshot(binding.cell), _binding_metadata(binding)
        raise ValueError("Unsupported core integration target binding")

    @staticmethod
    def _binding_target(binding) -> TargetRef:
        return binding.target

    @staticmethod
    def _stage_token_selection(selections, binding, evidence_order=()):
        """Combine authorized tile evidence without fabricating a wider request."""
        if not selections:
            raise IntegrationRejected("Token selection has no replacement proposals")
        evidence, new_ids, authorized_regions = [], [], []
        selected = set(binding.selected_evidence_ids)
        for request, proposal, tokens in selections:
            if request.scope.target != binding.target or request.goal != binding.goal:
                raise IntegrationRejected("Token selection does not match its core binding")
            if proposal.kind is not ProposalKind.REPLACE or set(proposal.replaces_evidence_ids) != selected:
                raise IntegrationRejected("Token replacement must identify exactly the selected prior evidence")
            auth = next(auth for auth in request.authorizations if auth.authorization_id == proposal.authorization_id)
            authorized_regions.extend(auth.regions)
            if any(not union_contains(auth.regions, token.bbox) or not contains(binding.bbox, token.bbox) for token in tokens):
                raise IntegrationRejected("Replacement evidence extends outside its authorization or target")
            evidence.extend(tokens)
            new_ids.extend(proposal.evidence_ids)
        if not union_contains(tuple(authorized_regions), binding.bbox):
            raise IntegrationRejected("Selected requests do not cover the replacement target")
        if not evidence or len(set(new_ids)) != len(new_ids):
            raise IntegrationRejected("Token replacement requires unique non-empty evidence")
        if evidence_order:
            if len(evidence_order) != len(new_ids) or set(evidence_order) != set(new_ids):
                raise IntegrationRejected("Selected evidence order does not identify exactly its source evidence")
            by_id = dict(zip(new_ids, evidence))
            evidence = [by_id[value] for value in evidence_order]
            new_ids = list(evidence_order)
        indices = tuple(index for index, value in enumerate(binding.evidence_ids) if value in selected)
        outside = [token for index, token in enumerate(binding.tokens) if index not in indices]
        _reject_duplicates(evidence, existing=outside)
        compositions = {proposal.composition for _request, proposal, _tokens in selections}
        if len(compositions) != 1:
            raise IntegrationRejected("Selected requests disagree on evidence composition")
        joined = compositions == {"join_critical"}
        if binding.goal is WorkGoal.CRITICAL_DATA and not joined:
            raise IntegrationRejected("Critical token refinement must preserve its single output position")
        lineage = dict(binding.lineage)
        if joined:
            if binding.goal is not WorkGoal.CRITICAL_DATA or len(indices) != 1:
                raise IntegrationRejected("Critical composition requires one selected critical token")
            original = binding.tokens[indices[0]]
            text = " ".join(token.text.strip() for token in evidence if token.text.strip())
            from structured_pdf_text.fusion.conservation import _critical_surface_preserved

            if not _critical_surface_preserved(original.text, text):
                raise IntegrationRejected("Critical composition changes a protected value surface")
            raw_ids = tuple(new_ids)
            provenance = evidence[0].ocr_provenance
            evidence = [replace(
                original, text=text, bbox=BBox.union_all([token.bbox for token in evidence]),
                confidence=max(token.confidence or 0.0 for token in evidence),
                provenance="critical_data_refinement", polygon=None,
                ocr_provenance=replace(provenance, refinement_kind="critical_data_refinement") if provenance else None,
            )]
            derived_id = "joined:" + sha256("\0".join(raw_ids).encode()).hexdigest()
            new_ids = [derived_id]
            lineage[derived_id] = (*binding.selected_evidence_ids, *raw_ids)
            _reject_duplicates(evidence, existing=outside)
        staged, changed = NativeOCRIntegrator().apply_token_replacement(
            request_id=selections[0][0].request_id,
            page_index=selections[0][0].scope.page_index,
            target_id=binding.target.target_id, tokens=binding.tokens,
            expected_snapshot=binding.expected_snapshot, box=binding.bbox,
            replacement=evidence, selection_indices=indices,
            preserve_position=joined,
        )
        if not changed:
            raise IntegrationRejected("Replacement proposal produced no target change")
        ids = list(binding.evidence_ids) if joined else [value for value in binding.evidence_ids if value not in selected] + new_ids
        if joined:
            ids[indices[0]] = new_ids[0]
        return staged, ids, evidence, lineage

    @staticmethod
    def _stage_figure_selection(selections, binding, evidence_order=()):
        evidence, new_ids, regions = [], [], []
        for request, proposal, tokens in selections:
            if (request.goal is not WorkGoal.FIGURE or request.scope.target != binding.target
                    or proposal.kind is not ProposalKind.ASSOCIATE_FIGURE or proposal.composition != "preserve"
                    or set(proposal.replaces_evidence_ids) != set(binding.selected_evidence_ids)):
                raise IntegrationRejected("Figure proposal does not match its core-owned refinement binding")
            auth = next(auth for auth in request.authorizations if auth.authorization_id == proposal.authorization_id)
            regions.extend(auth.regions)
            if any(not union_contains(auth.regions, token.bbox) or not contains(binding.bbox, token.bbox) for token in tokens):
                raise IntegrationRejected("Figure refinement evidence extends outside its authorization or target")
            evidence.extend(tokens)
            new_ids.extend(proposal.evidence_ids)
        if not selections or not evidence or len(new_ids) != len(set(new_ids)) or not union_contains(tuple(regions), binding.bbox):
            raise IntegrationRejected("Figure refinement requires unique evidence covering its authorized target")
        if evidence_order:
            if len(evidence_order) != len(new_ids) or set(evidence_order) != set(new_ids):
                raise IntegrationRejected("Figure selection order does not match its evidence")
            by_id = dict(zip(new_ids, evidence))
            evidence = [by_id[value] for value in evidence_order]
            new_ids = list(evidence_order)
        outside = [token for token, value in zip(binding.tokens, binding.evidence_ids) if value not in binding.selected_evidence_ids]
        _reject_duplicates(evidence, existing=outside)
        from structured_pdf_text.fusion.token_fusion import fuse_native_and_ocr
        from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

        request = selections[0][0]
        fusion = fuse_native_and_ocr(list(binding.native_lines), evidence)
        figure_tokens = list(fusion.unmatched_ocr_tokens)
        figure_lines = reconstruct_ocr_lines(figure_tokens, request.scope.page_index,
                                             request.scope.page_geometry.bbox,
                                             page_rotation=request.scope.page_geometry.rotation)
        draft_region = deepcopy(binding.region)
        staged, changed = NativeOCRIntegrator().apply_figure_refinement(
            tokens=binding.tokens, box=binding.bbox, replacement_tokens=evidence,
            regions=[draft_region] if draft_region is not None else [],
            figure_lines=figure_lines, figure_tokens=figure_tokens,
        )
        if not changed:
            raise IntegrationRejected("Figure refinement does not conserve the prior stream or changes no evidence")
        ids = [value for value in binding.evidence_ids if value not in binding.selected_evidence_ids] + new_ids
        return staged, ids, figure_lines, figure_tokens, evidence

    def apply_selected(self, selections, *, target, evidence_order=()):
        """Apply selected candidates/tiles to one target in a single commit.

        Each source result retains its original request and authorizations.
        Token targets can compose authorized tiles into one replacement;
        figure/table targets stage their stream and structure together. A
        failed tile or draft cannot leave a partly changed target.
        """
        selections = tuple(selections)
        evidence_order = tuple(evidence_order)
        if not selections:
            return ()
        first_request = selections[0][0]
        if any(request.scope != first_request.scope or request.selection != first_request.selection
               for request, _result in selections):
            raise IntegrationRejected("Selected candidates have incompatible work scopes")
        if target.target != first_request.scope.target:
            raise IntegrationRejected("Selected evidence does not match its bound target")
        request_ids = tuple(request.request_id for request, _result in selections)
        if len(set(request_ids)) != len(request_ids):
            raise IntegrationRejected("Selected candidates repeat an OCR request")
        if not isinstance(target, TokenTarget) and len(selections) > 1 and any(
            proposal.kind not in (ProposalKind.COMPLEMENT, ProposalKind.ASSOCIATE_FIGURE)
            for _request, result in selections for proposal in result.proposals
        ):
            raise IntegrationRejected("Replacement candidates cannot combine separate requests")
        key = first_request.scope.extraction_id, target.target, request_ids
        with self._lock:
            before = self._binding_snapshot(target)
            receipt = self._selection_receipts.get(key)
            if receipt is not None:
                expected_selection, expected_after = receipt
                if (selections, evidence_order) != expected_selection:
                    raise IntegrationRejected("Selected requests cannot apply different evidence twice")
                if before != expected_after:
                    raise IntegrationRejected("Target changed after selected evidence was applied")
                return ()
            if (_binding_metadata(target) != target.binding_snapshot
                    or before[0] != target.expected_snapshot):
                raise IntegrationRejected("Target changed after its authorized snapshot")
            if isinstance(target, VisualTableTarget):
                selected_proposals = []
                for request, result in selections:
                    result.validate_for(request)
                    by_id = {token.token_id: token for token in result.tokens}
                    selected_proposals.extend((request, proposal, [to_domain(by_id[value]) for value in proposal.evidence_ids])
                                              for proposal in result.proposals)
                staged = stage_visual_table(selected_proposals, target, evidence_order)
                if self._binding_snapshot(target) != before:
                    raise IntegrationRejected("Table target changed while refinement was staged")
                commit_visual_table(target, staged)
                self._selection_receipts[key] = (selections, evidence_order), self._binding_snapshot(target)
                return tuple(staged[3])
            if isinstance(target, FigureRefinementTarget):
                selected_proposals = []
                for request, result in selections:
                    result.validate_for(request)
                    by_id = {token.token_id: token for token in result.tokens}
                    selected_proposals.extend((request, proposal, [to_domain(by_id[value]) for value in proposal.evidence_ids])
                                              for proposal in result.proposals)
                tokens, ids, lines, figure_tokens, accepted = self._stage_figure_selection(selected_proposals, target, evidence_order)
                if self._binding_snapshot(target) != before:
                    raise IntegrationRejected("Figure target changed while refinement was staged")
                target.tokens[:] = tokens
                target.evidence_ids[:] = ids
                if target.region is not None:
                    target.region.ocr_lines = lines
                    target.region.ocr_tokens = figure_tokens
                self._selection_receipts[key] = (selections, evidence_order), self._binding_snapshot(target)
                return tuple(accepted)
            if isinstance(target, TokenTarget):
                selected_proposals = []
                for request, result in selections:
                    result.validate_for(request)
                    by_id = {token.token_id: token for token in result.tokens}
                    for proposal in result.proposals:
                        selected_proposals.append((request, proposal, [to_domain(by_id[value]) for value in proposal.evidence_ids]))
                tokens, ids, accepted, lineage = self._stage_token_selection(selected_proposals, target, evidence_order)
                if self._binding_snapshot(target) != before:
                    raise IntegrationRejected("Target changed while token selection was staged")
                target.tokens[:] = tokens
                target.evidence_ids[:] = ids
                target.lineage = lineage
                self._selection_receipts[key] = (selections, evidence_order), self._binding_snapshot(target)
                return tuple(accepted)
            draft = deepcopy(target)
            staging = ProposalIntegrator()
            accepted = []
            for request, result in selections:
                accepted.extend(staging.apply(request, result, targets=(draft,)))
                draft.expected_snapshot = self._binding_snapshot(draft)[0]
                draft.binding_snapshot = _binding_metadata(draft)
            if self._binding_snapshot(target) != before:
                raise IntegrationRejected("Target changed while selected evidence was staged")
            if isinstance(target, (RegionTarget, FigureTarget)):
                target.region.ocr_tokens = draft.region.ocr_tokens
                target.region.ocr_lines = draft.region.ocr_lines
                target.evidence_ids[:] = draft.evidence_ids
            else:
                target.cell.tokens = draft.cell.tokens
                target.cell.text = draft.cell.text
                target.cell.confidence = draft.cell.confidence
            self._selection_receipts[key] = (selections, evidence_order), self._binding_snapshot(target)
            self._priorities.update(staging._priorities)
            return tuple(accepted)

    def apply(self, request: OCRWorkRequest, result: OCRPipelineResult, *, targets=()):
        """Apply a validated result and return the evidence accepted for fusion.

        ``targets`` contains only mutable targets selected by the core. A
        replacement/cell/figure proposal without a matching binding is
        rejected. Priority proposals are returned as immutable recommendations
        and never start OCR by themselves.
        """
        result.validate_for(request)
        bindings = tuple(targets.values()) if hasattr(targets, "values") else tuple(targets)
        by_ref = {}
        for binding in bindings:
            target = self._binding_target(binding)
            if target in by_ref:
                raise ValueError("Duplicate integration target binding")
            by_ref[target] = binding

        key = request.scope.extraction_id, request.request_id
        proposal_receipt = self._proposal_receipt(result)
        with self._lock:
            previous = self._receipts.get(key)
            if previous is not None:
                if previous.proposals != proposal_receipt:
                    raise ValueError("A request ID cannot integrate different proposals twice")
                for target, expected_after in previous.target_snapshots:
                    binding = by_ref.get(target)
                    if binding is None or self._binding_snapshot(binding) != expected_after:
                        raise IntegrationRejected("Integrated target changed after proposal application")
                return ()

            token_by_id = {token.token_id: token for token in result.tokens}
            authorization_by_id = {item.authorization_id: item for item in request.authorizations}
            staged_tokens = []
            referenced: set[str] = set()
            staged_mutations = []
            staged_target_snapshots = []
            priorities = []
            touched_targets = set()

            for proposal in result.proposals:
                if proposal.authorization_id not in result.authorization_ids:
                    raise ValueError("Proposal authorization is outside the validated result")
                if proposal.kind is ProposalKind.PRIORITIZE_OCR:
                    if proposal.evidence_ids or proposal.replaces_evidence_ids:
                        raise ValueError("An OCR priority recommendation cannot carry replacement evidence")
                    priorities.append(PriorityRecommendation(proposal.target, proposal.authorization_id, proposal.reason))
                    continue

                if not proposal.evidence_ids:
                    raise ValueError(f"Proposal {proposal.kind.value!r} must reference new evidence")
                overlap = referenced.intersection(proposal.evidence_ids)
                if overlap:
                    raise ValueError("Evidence cannot be integrated by multiple proposals")
                referenced.update(proposal.evidence_ids)
                evidence = [to_domain(token_by_id[evidence_id]) for evidence_id in proposal.evidence_ids]
                binding = by_ref.get(proposal.target)
                if isinstance(binding, VisualTableTarget):
                    if proposal.target in touched_targets:
                        raise IntegrationRejected("A result cannot mutate one table through multiple proposals")
                    touched_targets.add(proposal.target)
                    before = self._binding_snapshot(binding)
                    if _binding_metadata(binding) != binding.binding_snapshot or before[0] != binding.expected_snapshot:
                        raise IntegrationRejected("Table target changed after its authorized snapshot")
                    staged = stage_visual_table(((request, proposal, evidence),), binding)
                    staged_tokens.extend(staged[3])
                    staged_mutations.append((binding, staged))
                    staged_target_snapshots.append((proposal.target, before))
                    continue

                if proposal.kind is ProposalKind.COMPLEMENT:
                    if proposal.replaces_evidence_ids:
                        raise ValueError("A complement proposal cannot replace evidence")
                    binding = by_ref.get(proposal.target)
                    if binding is not None:
                        if not isinstance(binding, RegionTarget):
                            raise ValueError("A target-bound complement requires a text region target")
                        if proposal.target in touched_targets:
                            raise ValueError("A result cannot mutate one target through multiple proposals")
                        touched_targets.add(proposal.target)
                        before = self._binding_snapshot(binding)
                        if (_binding_metadata(binding) != binding.binding_snapshot
                                or before[0] != binding.expected_snapshot):
                            raise IntegrationRejected("Target changed after its authorized snapshot")
                        auth = authorization_by_id[proposal.authorization_id]
                        if any(not union_contains(auth.regions, token.bbox) for token in evidence):
                            raise IntegrationRejected("Complement evidence extends outside its authorization")
                        if any(not contains(binding.region.bbox, token.bbox) for token in evidence):
                            raise IntegrationRejected("Complement evidence lies outside its region target")
                        _reject_duplicates(evidence, existing=binding.region.ocr_tokens)
                        combined_tokens = [*binding.region.ocr_tokens, *evidence]
                        from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

                        combined_lines = reconstruct_ocr_lines(
                            combined_tokens,
                            request.scope.page_index,
                            request.scope.page_geometry.bbox,
                            page_rotation=request.scope.page_geometry.rotation,
                        )
                        staged_mutations.append((
                            binding, combined_tokens, combined_lines,
                            [*binding.evidence_ids, *proposal.evidence_ids],
                        ))
                        staged_target_snapshots.append((proposal.target, before))
                    staged_tokens.extend(evidence)
                    continue

                binding = by_ref.get(proposal.target)
                if binding is None:
                    raise ValueError(f"No core-owned target binding for {proposal.kind.value!r}")
                if proposal.target in touched_targets:
                    raise ValueError("A result cannot mutate one target through multiple proposals")
                touched_targets.add(proposal.target)
                before = self._binding_snapshot(binding)
                if (_binding_metadata(binding) != binding.binding_snapshot
                        or before[0] != binding.expected_snapshot):
                    raise IntegrationRejected("Target changed after its authorized snapshot")

                if proposal.kind is ProposalKind.REPLACE:
                    if proposal.composition != "preserve" and not isinstance(binding, TokenTarget):
                        raise IntegrationRejected("Critical composition requires a core-owned token stream")
                    if isinstance(binding, TokenTarget):
                        staged, ids, accepted, lineage = self._stage_token_selection(((request, proposal, evidence),), binding)
                        staged_tokens.extend(accepted)
                        staged_mutations.append((binding, staged, ids, lineage))
                        staged_target_snapshots.append((proposal.target, before))
                        continue
                    if not isinstance(binding, RegionTarget):
                        raise ValueError("Token replacement requires a region target")
                    auth = authorization_by_id[proposal.authorization_id]
                    if not union_contains(auth.regions, binding.region.bbox):
                        raise IntegrationRejected("Region target extends outside its authorization")
                    if not proposal.replaces_evidence_ids:
                        raise ValueError("Token replacement must identify the evidence it replaces")
                    indices_by_id = {evidence_id: index for index, evidence_id in enumerate(binding.evidence_ids)}
                    if any(evidence_id not in indices_by_id for evidence_id in proposal.replaces_evidence_ids):
                        raise IntegrationRejected("Replacement references unknown target evidence")
                    if not set(proposal.replaces_evidence_ids) <= binding.visible_evidence_ids:
                        raise IntegrationRejected("Replacement references hidden or untrusted evidence")
                    indices = tuple(indices_by_id[value] for value in proposal.replaces_evidence_ids)
                    if any(not contains(binding.region.bbox, token.bbox) for token in evidence):
                        raise IntegrationRejected("Replacement evidence lies outside its region target")
                    selected_indices = set(indices)
                    outside = [
                        token for index, token in enumerate(binding.region.ocr_tokens)
                        if index not in selected_indices
                    ]
                    _reject_duplicates(evidence, existing=outside)
                    staged_region_tokens, changed = NativeOCRIntegrator().apply_token_replacement(
                        request_id=request.request_id,
                        page_index=request.scope.page_index,
                        target_id=binding.region.region_id,
                        tokens=list(binding.region.ocr_tokens),
                        expected_snapshot=ocr_tokens_snapshot(binding.region.ocr_tokens),
                        box=binding.region.bbox,
                        replacement=evidence,
                        selection_indices=indices,
                    )
                    if not changed:
                        raise IntegrationRejected("Replacement proposal produced no target change")
                    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

                    staged_lines = reconstruct_ocr_lines(
                        staged_region_tokens,
                        request.scope.page_index,
                        request.scope.page_geometry.bbox,
                        page_rotation=request.scope.page_geometry.rotation,
                    )
                    selected = set(proposal.replaces_evidence_ids)
                    staged_ids = [value for value in binding.evidence_ids if value not in selected]
                    staged_ids.extend(proposal.evidence_ids)
                    if len(staged_ids) != len(staged_region_tokens):
                        raise IntegrationRejected("Replacement lineage does not match staged region evidence")
                    staged_tokens.extend(evidence)
                    staged_mutations.append((binding, staged_region_tokens, staged_lines, staged_ids))

                elif proposal.kind is ProposalKind.CORRECT_CELL:
                    if not isinstance(binding, CellTarget):
                        raise ValueError("Cell correction requires a cell target")
                    auth = authorization_by_id[proposal.authorization_id]
                    if binding.page_index != request.scope.page_index:
                        raise IntegrationRejected("Cell target belongs to a different page")
                    if binding.cell.bbox is None or not union_contains(auth.regions, binding.cell.bbox):
                        raise IntegrationRejected("Cell target extends outside its authorization")
                    current_ids = {token.evidence_id for token in binding.cell.tokens if token.evidence_id}
                    if set(proposal.replaces_evidence_ids) != current_ids:
                        if current_ids or proposal.replaces_evidence_ids:
                            raise IntegrationRejected("Cell correction must identify all current cell evidence")
                    if not current_ids <= binding.visible_evidence_ids:
                        raise IntegrationRejected("Cell correction references hidden or untrusted evidence")
                    if binding.cell.bbox is None or any(not contains(binding.cell.bbox, token.bbox) for token in evidence):
                        raise IntegrationRejected("Cell correction evidence lies outside its target cell")
                    _reject_duplicates(evidence)
                    draft = [
                        TextToken(token.text, token.bbox, [], max(0.0, min(1.0, token.confidence or 0.0)), token.text)
                        for token in evidence
                    ]
                    proposed_cell_text = join_table_tokens(draft)
                    if not binding.cell.text.strip():
                        average_confidence = (
                            sum(token.confidence or 0.0 for token in evidence) / len(evidence)
                            if evidence else 0.0
                        )
                        single_digit = (
                            binding.allow_single_digit and len(evidence) == 1
                            and len(proposed_cell_text.strip()) == 1
                            and proposed_cell_text.strip() in "0123456789"
                            and average_confidence >= .95
                            and evidence[0].bbox.overlap_ratio(binding.cell.bbox) >= .80
                        )
                        if (len(proposed_cell_text.strip()) < 2 and not single_digit) or average_confidence < 0.75:
                            raise IntegrationRejected("Empty-cell insertion lacks strong OCR evidence")
                    staged_cell = deepcopy(binding.cell)
                    helper = NativeOCRIntegrator(join_table_tokens)
                    helper.apply_cell_replacement(
                        request_id=request.request_id,
                        page_index=request.scope.page_index,
                        table_id=binding.table_id,
                        cell=staged_cell,
                        expected_snapshot=binding.expected_snapshot,
                        evidence=tuple(evidence),
                        proposed_text=proposed_cell_text,
                        source_evidence_ids=proposal.evidence_ids,
                        decision_reason=proposal.reason,
                    )
                    staged_tokens.extend(evidence)
                    staged_mutations.append((binding, staged_cell))

                elif proposal.kind is ProposalKind.ASSOCIATE_FIGURE:
                    if isinstance(binding, FigureRefinementTarget):
                        staged, ids, lines, figure_tokens, accepted = self._stage_figure_selection(((request, proposal, evidence),), binding)
                        staged_tokens.extend(accepted)
                        staged_mutations.append((binding, staged, ids, lines, figure_tokens))
                        staged_target_snapshots.append((proposal.target, before))
                        continue
                    if not isinstance(binding, FigureTarget):
                        raise ValueError("Figure association requires a figure target")
                    auth = authorization_by_id[proposal.authorization_id]
                    if any(not union_contains(auth.regions, token.bbox) for token in evidence):
                        raise IntegrationRejected("Figure evidence extends outside its authorization")
                    if proposal.replaces_evidence_ids:
                        raise ValueError("Figure association cannot erase existing figure evidence")
                    if len(set(proposal.evidence_ids) & set(binding.evidence_ids)):
                        raise IntegrationRejected("Figure evidence is already associated with this target")
                    if any(not contains(binding.region.bbox, token.bbox) for token in evidence):
                        raise IntegrationRejected("Figure evidence lies outside its target")
                    _reject_duplicates(evidence, existing=binding.region.ocr_tokens)
                    combined_tokens = [*binding.region.ocr_tokens, *evidence]
                    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

                    combined_lines = reconstruct_ocr_lines(
                        combined_tokens,
                        request.scope.page_index,
                        request.scope.page_geometry.bbox,
                        page_rotation=request.scope.page_geometry.rotation,
                    )
                    staged_tokens.extend(evidence)
                    staged_mutations.append((binding, combined_tokens, combined_lines,
                                             [*binding.evidence_ids, *proposal.evidence_ids]))

                else:  # pragma: no cover - Enum exhaustiveness guard
                    raise ValueError(f"Unsupported proposal kind {proposal.kind.value!r}")

                staged_target_snapshots.append((proposal.target, before))

            if referenced != set(token_by_id) - result.unselected_token_ids:
                raise ValueError("Every candidate token must be authorized by exactly one integration proposal")

            # Optimistic concurrency check for every target is performed again
            # immediately before the non-failing assignments below.
            for binding, *_ in staged_mutations:
                before = self._binding_snapshot(binding)
                if (_binding_metadata(binding) != binding.binding_snapshot
                        or before[0] != binding.expected_snapshot):
                    raise IntegrationRejected("Target changed while proposals were being staged")

            for mutation in staged_mutations:
                binding = mutation[0]
                if isinstance(binding, VisualTableTarget):
                    commit_visual_table(binding, mutation[1])
                elif isinstance(binding, FigureRefinementTarget):
                    _, tokens, evidence_ids, lines, figure_tokens = mutation
                    binding.tokens[:] = tokens
                    binding.evidence_ids[:] = evidence_ids
                    if binding.region is not None:
                        binding.region.ocr_lines = lines
                        binding.region.ocr_tokens = figure_tokens
                elif isinstance(binding, TokenTarget):
                    _, tokens, evidence_ids, lineage = mutation
                    binding.tokens[:] = tokens
                    binding.evidence_ids[:] = evidence_ids
                    binding.lineage = lineage
                elif isinstance(binding, RegionTarget):
                    _, tokens, lines, evidence_ids = mutation
                    binding.region.ocr_tokens = list(tokens)
                    binding.region.ocr_lines = list(lines)
                    binding.evidence_ids[:] = evidence_ids
                elif isinstance(binding, FigureTarget):
                    _, tokens, lines, evidence_ids = mutation
                    binding.region.ocr_tokens = list(tokens)
                    binding.region.ocr_lines = list(lines)
                    binding.evidence_ids[:] = evidence_ids
                else:
                    _, cell = mutation
                    binding.cell.tokens = cell.tokens
                    binding.cell.text = cell.text
                    binding.cell.confidence = cell.confidence

            after_snapshots = []
            for target, _ in staged_target_snapshots:
                after_snapshots.append((target, self._binding_snapshot(by_ref[target])))
            self._receipts[key] = _Receipt(proposal_receipt, tuple(after_snapshots))
            if priorities:
                self._priorities[key] = tuple(priorities)
            return tuple(staged_tokens)
