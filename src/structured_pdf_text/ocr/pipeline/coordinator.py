"""Core-side planning and compatibility facade for authorized OCR work."""
from __future__ import annotations

from dataclasses import dataclass, replace
from threading import RLock
from uuid import uuid4

from structured_pdf_text.geometry import BBox

from .authorization import AuthorizationRegistry, ResultReceiptLedger
from .contracts import (
    Authorization, EngineSelection, OCRWorkRequest, PageGeometry, ResourceAllocation,
    TargetRef, WorkGoal, WorkScope, WorkStatus, contains,
)
from .integrator import CellTarget, FigureRefinementTarget, FigureTarget, ProposalIntegrator, RegionTarget, TokenTarget
from .table_integration import VisualTableTarget
from .services import AuthorizedOCRServices


@dataclass(frozen=True, slots=True)
class PlannedOCRWork:
    request: OCRWorkRequest
    authorizations: tuple[Authorization, ...]


class OCRWorkPlanner:
    """Turn a core decision into immutable, engine-specific work requests."""

    def __init__(self, extraction_id: str, document_id: str, registry: AuthorizationRegistry,
                 selection: EngineSelection, *, evidence_version: str = "native-v1"):
        self.extraction_id = extraction_id
        self.document_id = document_id
        self.registry = registry
        self.selection = selection
        self.evidence_version = evidence_version

    def plan(self, *, page_index: int, page_bbox: BBox, rotation: int, regions: tuple[BBox, ...],
             goal: WorkGoal, quality_policy, mandatory: bool = True, reasons: tuple[str, ...] = (),
             allocation: ResourceAllocation | None = None, selection_stage: str = "page",
             render_scale: float = 1.0, preprocessing_rotation: float = 0.0,
             target_ref: TargetRef | None = None, native_context=None) -> PlannedOCRWork:
        scope = WorkScope(
            self.extraction_id,
            self.document_id,
            page_index,
            PageGeometry(page_bbox, rotation),
            target_ref or TargetRef(
                "page" if goal is WorkGoal.PAGE else selection_stage,
                f"page-{page_index}" if goal is WorkGoal.PAGE else (
                    f"page-{page_index}:{selection_stage}:"
                    + ",".join(f"{value:.6f}" for value in (
                        regions[0].x0, regions[0].y0, regions[0].x1, regions[0].y1,
                    ))
                ),
                0,
            ),
            self.evidence_version,
        )
        auth = self.registry.issue(scope, regions)
        request = OCRWorkRequest(
            uuid4().hex,
            scope,
            self.selection,
            (auth,),
            goal=goal,
            quality_policy=quality_policy,
            render_scale=render_scale,
            preprocessing_rotation=preprocessing_rotation,
            resource_allocation=allocation or ResourceAllocation(
                max_live_raster_bytes=256 * 1024 * 1024,
                max_raster_pixels=100_000_000,
                max_work_pixels=400_000_000,
                max_attempts=256,
                max_rasters=1024,
            ),
            decision_reasons=reasons,
            mandatory=mandatory,
            selection_stage=selection_stage,
            native_context=native_context or {},
        )
        return PlannedOCRWork(request, (auth,))


class PageCoordinator:
    """Execute one authorized request and integrate its validated evidence."""

    def __init__(self, pipeline, source, *, extraction_id: str, document_id: str,
                 selection: EngineSelection, page_index: int, page_bbox: BBox, rotation: int,
                 quality_policy, security_limits, allocation: ResourceAllocation | None = None,
                 cancellation_token=None):
        self.pipeline = pipeline
        self.page_index = page_index
        self.page_bbox = page_bbox
        self.selection = selection
        self.quality_policy = quality_policy
        self.registry = AuthorizationRegistry(extraction_id, document_id)
        self.planner = OCRWorkPlanner(extraction_id, document_id, self.registry, selection)
        self.services = AuthorizedOCRServices(
            source, self.registry, selection, limits=security_limits,
            allocation=allocation or ResourceAllocation(
                max_live_raster_bytes=256 * 1024 * 1024,
                max_raster_pixels=100_000_000,
                max_work_pixels=400_000_000,
                max_attempts=256,
                max_rasters=1024,
            ),
            cancellation_token=cancellation_token,
        )
        self.rotation = rotation
        self.ledger = ResultReceiptLedger()
        self.integrator = ProposalIntegrator()
        self.results = []
        self._lock = RLock()
        self._closed = False
        self.last_result = None
        self.last_tokens = ()
        # Keep the actual domain object alive: Python object IDs alone can be
        # reused after a filtered-out candidate is released.
        self._deferred_tokens: dict[int, tuple] = {}
        self.calls = 0
        self.cancellation_token = cancellation_token
        if cancellation_token is not None:
            cancel = getattr(pipeline, "cancel", None)
            if callable(cancel):
                cancellation_token.add_callback(cancel)

    def recognize(self, bbox: BBox | None = None, *, goal: WorkGoal = WorkGoal.REGION,
                   quality_policy=None, mandatory: bool = True, reason: str = "core_authorized_work",
                   recognition_method: str = "page", render_scale: float | None = None,
                   preprocessing_rotation: float = 0.0, integration_targets=(),
                   defer_integration: bool = False):
        with self._lock:
            if self._closed:
                raise RuntimeError("OCR page coordinator is closed")
            box = bbox or self.page_bbox
            policy = self.quality_policy if quality_policy is None else quality_policy
            integration_targets = tuple(integration_targets)
            if defer_integration and not integration_targets:
                raise ValueError("Deferred integration requires a core-owned target binding")
            target_refs = {target.target for target in integration_targets}
            if len(target_refs) > 1:
                raise ValueError("One OCR request can bind one mutable domain target")
            target_ref = next(iter(target_refs), None)
            target_context = {}
            if integration_targets and target_ref is None:
                raise ValueError("Integration targets must carry an immutable target reference")
            if integration_targets:
                binding = integration_targets[0]
                if isinstance(binding, RegionTarget):
                    bound_box, bound_goal = binding.region.bbox, WorkGoal.REGION
                elif isinstance(binding, FigureTarget):
                    bound_box, bound_goal = binding.region.bbox, WorkGoal.FIGURE
                elif isinstance(binding, CellTarget):
                    bound_box, bound_goal = binding.cell.bbox, WorkGoal.CELL
                    target_context = {"replacement_evidence_ids": tuple(
                        token.evidence_id for token in binding.cell.tokens
                    )}
                elif isinstance(binding, TokenTarget):
                    bound_box, bound_goal = binding.bbox, binding.goal
                    target_context = {"replacement_evidence_ids": binding.selected_evidence_ids}
                    mandatory = False
                elif isinstance(binding, FigureRefinementTarget):
                    bound_box, bound_goal = binding.bbox, WorkGoal.FIGURE
                    target_context = {"replacement_evidence_ids": binding.selected_evidence_ids, "dedicated_refinement": True}
                    mandatory = False
                elif isinstance(binding, VisualTableTarget):
                    bound_box, bound_goal = binding.bbox, WorkGoal.TABLE
                    target_context = {"visual_table_refinement": True}
                    mandatory = False
                else:
                    raise ValueError("Unsupported core integration target binding")
                authorized_tile = (
                    defer_integration and isinstance(binding, (RegionTarget, FigureTarget, TokenTarget, FigureRefinementTarget, VisualTableTarget))
                    and bbox is not None and bound_box is not None and contains(bound_box, bbox)
                )
                if bound_box is None or (bbox is not None and bbox != bound_box and not authorized_tile):
                    raise ValueError("Typed proposals must authorize exactly their bound target geometry")
                if goal not in (WorkGoal.REGION, bound_goal):
                    raise ValueError("OCR work goal does not match its integration target")
                box, goal = bbox if authorized_tile else bound_box, bound_goal
            planned = self.planner.plan(
                page_index=self.page_index,
                page_bbox=self.page_bbox,
                rotation=self.rotation,
                regions=(box,),
                goal=goal,
                quality_policy=policy,
                mandatory=mandatory,
                reasons=(reason,),
                selection_stage=recognition_method,
                render_scale=(getattr(self.pipeline, "render_scale", 1.0)
                              if render_scale is None else render_scale),
                preprocessing_rotation=preprocessing_rotation,
                target_ref=target_ref,
                native_context=target_context,
            )
            result = self.pipeline.execute(planned.request, self.services)
            self.services.validate_result(planned.request, result)
            first_receipt = self.ledger.record(planned.request, result)
            self.last_result = result
            try:
                if defer_integration:
                    if not first_receipt:
                        raise ValueError("Deferred OCR candidates cannot reuse a request ID")
                    from .converters import to_domain

                    proposed_ids = {value for proposal in result.proposals for value in proposal.evidence_ids}
                    proposed_evidence = tuple(token for token in result.tokens if token.token_id in proposed_ids)
                    self.last_tokens = tuple(to_domain(token) for token in proposed_evidence)
                    for domain_token, evidence in zip(self.last_tokens, proposed_evidence):
                        self._deferred_tokens[id(domain_token)] = (
                            planned.request, result, evidence.token_id, domain_token, integration_targets,
                        )
                else:
                    self.last_tokens = (
                        self.integrator.apply(planned.request, result, targets=integration_targets)
                        if first_receipt else ()
                    )
            except ValueError as exc:
                self.last_tokens = ()
                self.results.append({
                    "request_id": result.request_id,
                    "goal": planned.request.goal.value,
                    "selection_stage": planned.request.selection_stage,
                    "target_id": planned.request.scope.target.target_id,
                    "status": "integration_rejected",
                    "mandatory": planned.request.mandatory,
                    "token_count": len(result.tokens),
                    "integrated_token_count": 0,
                    "proposal_count": len(result.proposals),
                    "integration_failures": [{
                        "reason": str(exc),
                        "proposal_ids": [proposal.proposal_id for proposal in result.proposals],
                    }],
                })
                raise
            self.results.append({
                "request_id": result.request_id,
                "goal": planned.request.goal.value,
                "selection_stage": planned.request.selection_stage,
                "target_id": planned.request.scope.target.target_id,
                "render_scale": planned.request.render_scale,
                "preprocessing_rotation": planned.request.preprocessing_rotation,
                "status": result.status.value,
                "mandatory": planned.request.mandatory,
                "token_count": len(result.tokens),
                "integrated_token_count": 0 if defer_integration else len(self.last_tokens),
                "deferred_integration": defer_integration,
                "proposal_count": len(result.proposals),
                "integration_decisions": [
                    {
                        "proposal_id": proposal.proposal_id,
                        "kind": proposal.kind.value,
                        "composition": proposal.composition,
                        "authorization_id": proposal.authorization_id,
                        "outcome": (
                            "recommended" if proposal.kind.value == "prioritize_ocr"
                            else "staged" if defer_integration else "accepted"
                        ),
                        "reason": proposal.reason,
                    }
                    for proposal in result.proposals
                ],
                "failures": [
                    {"status": failure.status.value, "reason": failure.reason, "mandatory": failure.mandatory}
                    for failure in result.failures
                ],
                "counters": dict(result.counters),
                "timings_ms": dict(result.timings_ms),
                "engine_diagnostics": _plain(dict(result.engine_diagnostics)),
                "priority_recommendations": [
                    {"target_kind": item.target.kind, "target_id": item.target.target_id,
                     "target_revision": item.target.revision, "reason": item.reason}
                    for item in self.integrator.priority_recommendations
                    if item.authorization_id in result.authorization_ids
                ],
            })
            self.calls += result.counters.get("legacy_calls", 1)
            mandatory_failures = [failure for failure in result.failures if failure.mandatory]
            if mandatory_failures:
                raise RuntimeError(mandatory_failures[0].reason)
            if result.status not in (WorkStatus.OK, WorkStatus.NO_TEXT, WorkStatus.PARTIAL):
                raise RuntimeError(f"OCR work ended with status {result.status.value}")
            return list(self.last_tokens)

    def integrate_selected_tokens(self, tokens, target):
        """Commit a selected candidate or selected tiles without reauthorizing pixels."""
        selected = tuple(tokens)
        with self._lock:
            if self._closed:
                raise RuntimeError("OCR page coordinator is closed")
            if not selected:
                self._discard_deferred_target(target.target)
                return ()
            if len({id(token) for token in selected}) != len(selected):
                raise ValueError("Selected OCR evidence contains a repeated occurrence")
            references = [self._deferred_tokens.get(id(token)) for token in selected]
            if any(reference is None or reference[3] is not token
                   for token, reference in zip(selected, references)):
                raise ValueError("Selected OCR evidence did not come from a deferred pipeline result")
            if any(not any(binding is target for binding in reference[4]) for reference in references):
                raise ValueError("Selected OCR evidence belongs to a different core target binding")
            if any(target.target != reference[0].scope.target for reference in references):
                raise ValueError("Selected candidate target differs from its authorization")
            groups = {}
            for reference in references:
                request, result, evidence_id = reference[:3]
                group = groups.setdefault(request.request_id, (request, result, set()))
                group[2].add(evidence_id)
            selections = []
            for request, result, selected_ids in groups.values():
                selected_evidence = tuple(
                    evidence for evidence in result.tokens if evidence.token_id in selected_ids
                )
                if len(selected_evidence) != len(selected_ids):
                    raise ValueError("Selected OCR evidence is missing from its pipeline result")
                selected_proposals = []
                for proposal in result.proposals:
                    evidence_ids = tuple(i for i in proposal.evidence_ids if i in selected_ids)
                    if evidence_ids:
                        selected_proposals.append(replace(proposal, evidence_ids=evidence_ids))
                    elif proposal.kind.value == "prioritize_ocr":
                        selected_proposals.append(proposal)
                if not selected_proposals:
                    raise ValueError("Selected OCR candidate has no integration proposal")
                selections.append((request, replace(
                    result, tokens=selected_evidence, proposals=tuple(selected_proposals), candidates=(),
                )))
            self.services.check_cancelled()
            try:
                accepted = self.integrator.apply_selected(
                    selections, target=target, evidence_order=tuple(reference[2] for reference in references),
                )
            except ValueError as exc:
                for entry in self.results:
                    if entry.get("request_id") in groups:
                        entry["integration_failures"] = [{"reason": str(exc)}]
                        entry["status"] = "integration_rejected"
                self._discard_deferred_target(target.target)
                raise
            for entry in self.results:
                if entry.get("request_id") in groups:
                    entry["integrated_token_count"] = len(groups[entry["request_id"]][2])
                    entry["deferred_integration"] = False
                    if isinstance(target, TokenTarget) and target.lineage:
                        entry["output_evidence_lineage"] = [
                            {"evidence_id": value, "derived_from_ids": list(sources)}
                            for value, sources in target.lineage.items()
                            if set(groups[entry["request_id"]][2]).intersection(sources)
                        ]
                    for decision in entry.get("integration_decisions", ()):
                        if decision.get("outcome") == "staged":
                            decision["outcome"] = "accepted"
            self._discard_deferred_target(target.target)
            return accepted

    def _discard_deferred_target(self, target_ref):
        request_ids = set()
        for token_id, reference in tuple(self._deferred_tokens.items()):
            if reference[0].scope.target == target_ref:
                request_ids.add(reference[0].request_id)
                self._deferred_tokens.pop(token_id, None)
        for entry in self.results:
            if entry.get("request_id") in request_ids:
                for decision in entry.get("integration_decisions", ()):
                    if decision.get("outcome") == "staged":
                        decision["outcome"] = "not_selected"

    def diagnostics(self):
        return {
            "selection": {"engine": self.selection.engine, "provider": self.selection.provider},
            "requests": list(self.results),
            "resource_observations": self.services.observations(),
            "service_events": len(self.services.events),
            "summary": self.work_summary(),
            "priority_recommendations": [
                {"target_kind": item.target.kind, "target_id": item.target.target_id,
                 "target_revision": item.target.revision, "reason": item.reason}
                for item in self.integrator.priority_recommendations
            ],
        }

    def work_summary(self):
        """Aggregate all stages, including cells discovered after initial OCR."""
        groups = {}
        cell_tokens = 0
        for entry in self.results:
            group = groups.setdefault((entry["goal"], entry["target_id"]), {
                "useful": False, "mandatory": False, "errors": [],
            })
            integrated = entry.get("integrated_token_count", 0)
            group["useful"] |= bool(integrated)
            group["mandatory"] |= bool(entry.get("mandatory", True))
            if entry["goal"] == WorkGoal.CELL.value:
                cell_tokens += integrated
            status = entry["status"]
            # A conservative proposal rejection is a domain decision; runtime
            # failures remain separate even when another candidate succeeds.
            if status not in ("ok", "no_text", "integration_rejected"):
                group["errors"].append(status)
            group["errors"].extend(failure["status"] for failure in entry.get("failures", ()))
        useful = any(group["useful"] for group in groups.values())
        errors = sorted({error for group in groups.values() for error in group["errors"]})
        mandatory_failures = [
            {"goal": key[0], "target_id": key[1], "statuses": sorted(set(group["errors"]))}
            for key, group in groups.items()
            if group["mandatory"] and group["errors"] and not group["useful"]
        ]
        outcome = (
            "not_requested" if not groups else
            "recovered" if errors and useful else "degraded" if errors else
            "success" if useful else "no_text"
        )
        return {
            "requested": bool(groups), "outcome": outcome, "degraded": bool(errors),
            "reasons": errors, "mandatory_failures": mandatory_failures,
            "cell_tokens_added": cell_tokens,
        }

    def reset_engine_diagnostics(self):
        reset = getattr(self.pipeline, "reset_page_diagnostics", None)
        if callable(reset):
            reset()

    def consume_engine_diagnostics(self):
        consume = getattr(self.pipeline, "consume_page_diagnostics", None)
        if callable(consume):
            values = consume()
            return dict(values) if values else {}
        return {}

    def operation_metrics(self):
        read = getattr(self.pipeline, "operation_metrics", None)
        metrics = dict(read()) if callable(read) else {}
        if not metrics.get("passes") and self.last_result is not None:
            metrics["passes"] = self.last_result.counters.get("legacy_calls", 0)
        if not metrics.get("attempt_errors") and self.last_result is not None:
            metrics["attempt_errors"] = [failure.reason for failure in self.last_result.failures]
        metrics.setdefault("batches", 0)
        metrics.setdefault("attempt_errors", [])
        return metrics

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._deferred_tokens.clear()
                self.services.close()


class OCRPageFacade:
    """Compatibility surface that routes legacy core calls through a coordinator.

    Passed image objects are deliberately ignored: authorized pixels are
    rendered by OCRServices from the source boundary for each requested bbox.
    """

    def __init__(self, coordinator: PageCoordinator):
        self.coordinator = coordinator

    @property
    def identity(self):
        result = self.coordinator.last_result
        identity = result.identity if result is not None else getattr(self.coordinator.pipeline, "identity", None)
        if identity is None or hasattr(identity, "engine"):
            return identity
        # Keep the established OCRBackendIdentity surface for evaluation and
        # downstream callers while the pipeline contract stores selection and
        # effective runtime identity separately.
        from structured_pdf_text.ocr.contracts import OCRBackendIdentity

        selection = identity.selection
        # EffectiveIdentity snapshots nested diagnostic metadata in FrozenMap;
        # project recursively when crossing back to the mutable legacy API.
        extra = _plain(dict(identity.extra))
        if selection.provider is not None:
            extra.setdefault("provider", selection.provider)
        return OCRBackendIdentity(
            engine=selection.engine,
            runtime=identity.runtime,
            profile=identity.profile,
            language=identity.language,
            device=identity.device,
            package_versions=dict(identity.package_versions),
            artifact_hashes=dict(identity.artifact_hashes),
            extra=extra,
        )

    @property
    def capabilities(self):
        descriptor = self.coordinator.pipeline.descriptor
        declared = set(descriptor.declared_capabilities)
        available = set(
            descriptor.declared_capabilities
            if descriptor.available_capabilities is None
            else descriptor.available_capabilities
        )
        names = declared | {"recognition"}
        result = {name: name in available for name in names}
        result["recognition"] = "page" in available
        return result

    @property
    def last_result(self):
        return self.coordinator.last_result

    @property
    def operation_metrics(self):
        return self.coordinator.operation_metrics()

    @property
    def render_scale(self):
        return getattr(self.coordinator.pipeline, "render_scale", 1.0)

    @property
    def settings(self):
        return getattr(self.coordinator.pipeline, "settings", None)

    @property
    def recovery_policy(self):
        return getattr(self.coordinator.pipeline, "recovery_policy", None)

    @property
    def canonical_page_output(self):
        """Recognitions are mapped by the authorized raster frame to PDF points."""
        return True

    def consume_page_diagnostics(self):
        return self.coordinator.consume_engine_diagnostics()

    def make_region_target(self, region):
        """Create the semantic core-owned binding for a selected region."""
        evidence_ids = [
            f"{region.region_id}:ocr:{index}"
            for index, _token in enumerate(region.ocr_tokens)
        ]
        if region.kind.value == "figure":
            return FigureTarget(
                TargetRef("figure", region.region_id, 0),
                region,
                evidence_ids,
                visible_target=True,
            )
        return RegionTarget(
            TargetRef("region", region.region_id, 0),
            region,
            evidence_ids,
            set(evidence_ids),
            visible_target=True,
        )

    def integrate_selected_tokens(self, tokens, target):
        return self.coordinator.integrate_selected_tokens(tokens, target)

    def discard_selected_tokens(self, target):
        with self.coordinator._lock:
            self.coordinator._discard_deferred_target(target.target)

    def make_cell_target(self, table_id, page_index, cell, *, allow_single_digit=False):
        if page_index != self.coordinator.page_index:
            raise ValueError("Cell target belongs to a different page")
        from structured_pdf_text.fusion.integrator import prepare_cell_evidence

        prepare_cell_evidence(cell, table_id=table_id, page_index=page_index)
        evidence_ids = {token.evidence_id for token in cell.tokens if token.evidence_id}
        return CellTarget(
            TargetRef("cell", f"{table_id}:cell:{cell.row}:{cell.col}", 0),
            table_id, page_index, cell, evidence_ids, visible_target=True,
            allow_single_digit=allow_single_digit,
        )

    def make_token_target(self, tokens, bbox, *, target_id, indices, goal=WorkGoal.REGION):
        indices = tuple(indices)
        if len(set(indices)) != len(indices) or any(type(index) is not int or not 0 <= index < len(tokens) for index in indices):
            raise ValueError("Token target must select valid, unique evidence positions")
        ids = [f"{target_id}:existing:{index}" for index in range(len(tokens))]
        selected = tuple(ids[index] for index in indices)
        return TokenTarget(
            TargetRef("ocr_tokens", target_id, 0), bbox, tokens, ids,
            selected, set(selected), goal=goal, visible_target=True,
        )

    def make_figure_refinement_target(self, tokens, bbox, *, regions, native_lines):
        from structured_pdf_text.fusion.integrator import best_figure_region

        if not contains(self.coordinator.page_bbox, bbox):
            raise ValueError("Figure refinement target lies outside the visible page")
        region = best_figure_region(regions, bbox)
        target_id = "figure-refinement:" + ":".join(str(value) for value in (bbox.x0, bbox.y0, bbox.x1, bbox.y1))
        ids = [f"{target_id}:existing:{index}" for index in range(len(tokens))]
        selected = tuple(value for token, value in zip(tokens, ids)
                         if token.bbox.overlap_ratio(bbox) >= .25 or bbox.overlap_ratio(token.bbox) >= .25)
        return FigureRefinementTarget(
            TargetRef("figure_refinement", target_id, 0), bbox, tokens, ids, selected,
            region, tuple(native_lines), visible_target=True,
        )

    def make_visual_table_target(self, table, bbox, *, native_lines):
        if not contains(self.coordinator.page_bbox, bbox) or any(part.page_index != self.coordinator.page_index for part in table.page_fragments):
            raise ValueError("Visual table refinement must belong to the authorized page")
        return VisualTableTarget(TargetRef("visual_table", table.table_id, 0), bbox, table,
                                 tuple(native_lines), visible_target=True)

    @property
    def token_refinement_policy(self):
        from .policies import LEGACY_TOKEN_REFINEMENT_POLICY

        return getattr(self.coordinator.pipeline, "token_refinement_policy", LEGACY_TOKEN_REFINEMENT_POLICY)

    @property
    def fusion_policy(self):
        from .candidate_policy import LEGACY_FUSION_POLICY

        return getattr(self.coordinator.pipeline, "fusion_policy", LEGACY_FUSION_POLICY)

    @property
    def line_order_policy(self):
        return getattr(self.coordinator.pipeline, "line_order_policy", None)

    @property
    def table_structure_engine(self):
        return getattr(self.coordinator.pipeline, "table_structure_engine", None)

    @property
    def currency_marker_repair(self):
        return bool(getattr(self.coordinator.pipeline, "currency_marker_repair", False))

    @property
    def figure_segmentation_policy(self):
        from .policies import LEGACY_FIGURE_SEGMENTATION_POLICY

        return getattr(self.coordinator.pipeline, "figure_segmentation_policy", LEGACY_FIGURE_SEGMENTATION_POLICY)

    def refine_footnote(self, target, *, base_scale, quality_policy):
        from .token_refinement import refine_footnote

        return refine_footnote(self, target, base_scale=base_scale, quality_policy=quality_policy,
                               policy=self.token_refinement_policy)

    def refine_critical(self, target, *, base_scale, quality_policy, data_type, page_image):
        from .token_refinement import refine_critical

        return refine_critical(self, target, base_scale=base_scale, quality_policy=quality_policy,
                               data_type=data_type, page_image=page_image, policy=self.token_refinement_policy)

    @property
    def cell_recovery_policy(self):
        from .policies import LEGACY_CELL_POLICY

        return getattr(self.coordinator.pipeline, "cell_policy", LEGACY_CELL_POLICY)

    def refine_cell(self, *, table_id, page_index, cell, quality_policy, base_scale, header_text):
        from .cell_recovery import refine_cell

        approves = getattr(self.cell_recovery_policy, "allows_single_digit", None)
        target = self.make_cell_target(
            table_id, page_index, cell,
            allow_single_digit=bool(approves(header_text)) if callable(approves) else False,
        )
        return refine_cell(
            self, target, quality_policy=quality_policy, base_scale=base_scale,
            header_text=header_text, policy=self.cell_recovery_policy,
        )

    def recognize_page(self, image, page_index, page_bbox=None, *, quality_policy=None,
                       quality_variants=None, page_rotation=None, render_scale=None,
                       preprocessing_rotation=0.0, target_bbox=None,
                       selection_stage="page", decision_reason="core_page_or_region_decision",
                       integration_targets=(), defer_integration=False, mandatory=True):
        if page_index != self.coordinator.page_index:
            raise ValueError("OCR request page does not match the active page coordinator")
        selected_bbox = (
            target_bbox if target_bbox is not None
            else None if integration_targets
            else page_bbox
        )
        tokens = self.coordinator.recognize(
            selected_bbox,
            goal=(WorkGoal.REGION if integration_targets else
                  WorkGoal.PAGE if selected_bbox == self.coordinator.page_bbox else WorkGoal.REGION),
            quality_policy=quality_policy,
            reason=decision_reason,
            recognition_method=selection_stage,
            render_scale=render_scale,
            preprocessing_rotation=preprocessing_rotation,
            integration_targets=integration_targets,
            defer_integration=defer_integration,
            mandatory=mandatory,
        )
        return tokens

    def recognize_direct(self, image, page_index, page_bbox=None, **kwargs):
        if page_index != self.coordinator.page_index:
            raise ValueError("OCR request page does not match the active page coordinator")
        tokens = self.coordinator.recognize(
            page_bbox, goal=WorkGoal.REGION, quality_policy=kwargs.get("quality_policy"),
            reason="core_direct_recognition_decision", recognition_method="direct",
            render_scale=kwargs.get("render_scale"),
            preprocessing_rotation=kwargs.get("preprocessing_rotation", 0.0),
            integration_targets=kwargs.get("integration_targets", ()),
            defer_integration=kwargs.get("defer_integration", False),
            mandatory=kwargs.get("mandatory", True),
        )
        return tokens

    def recognize_region(self, image, page_index, region_bbox, *, page_bbox=None,
                         integration_targets=()):
        if page_index != self.coordinator.page_index:
            raise ValueError("OCR request page does not match the active page coordinator")
        tokens = self.coordinator.recognize(
            region_bbox, goal=WorkGoal.REGION, reason="core_region_recognition_decision",
            recognition_method="region", integration_targets=integration_targets,
        )
        return tokens

    def close(self):
        self.coordinator.close()


def _plain(value):
    if hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value
