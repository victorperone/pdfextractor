from dataclasses import FrozenInstanceError, replace
import math

import pytest

from structured_pdf_text.document import (
    LayoutRegion,
    OcrProvenance,
    OcrToken,
    RegionDecision,
    RegionKind,
    RegionQuality,
    SourceKind,
    TableCell,
)
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.ocr.contracts import OCRToken
from structured_pdf_text.ocr.pipeline.authorization import AuthorizationRegistry, ResultReceiptLedger
from structured_pdf_text.ocr.pipeline.contracts import (
    EffectiveIdentity, EngineSelection, IntegrationProposal, LocalFailure, OCRPipelineResult,
    PageGeometry, ProposalKind, ResourceAllocation, TargetRef, TokenEvidence, WorkScope, WorkStatus,
)
from structured_pdf_text.ocr.pipeline.converters import from_domain, from_raw, to_domain, to_raw
from structured_pdf_text.ocr.pipeline.geometry import RasterFrame, affine
from structured_pdf_text.ocr.pipeline.integrator import (
    CellTarget,
    FigureTarget,
    ProposalIntegrator,
    RegionTarget,
    TokenTarget,
)
from structured_pdf_text.fusion.integrator import IntegrationRejected
from ocr_pipeline_helpers import make_work


def evidence(auth, **kwargs):
    return TokenEvidence("token-1", auth.authorization_id, "attempt-1", "R$ -1.234,56", BBox(20, 20, 40, 30), **kwargs)


def result_for(request, **kwargs):
    return OCRPipelineResult(request.request_id, request.scope, request.selection, EffectiveIdentity(request.selection),
                             WorkStatus.OK, tuple(a.authorization_id for a in request.authorizations), **kwargs)


def request_for_target(kind, target_id, *, page_bbox=None, goal="region"):
    box = page_bbox or BBox(0, 0, 200, 100)
    scope = WorkScope("extraction-1", "document-1", 0, PageGeometry(box),
                      TargetRef(kind, target_id, 1), "evidence-v1")
    registry = AuthorizationRegistry(scope.extraction_id, scope.document_id)
    auth = registry.issue(scope, (box,))
    from structured_pdf_text.ocr.pipeline.contracts import OCRWorkRequest, WorkGoal
    return OCRWorkRequest("request-typed", scope, EngineSelection("custom"), (auth,), goal=WorkGoal(goal)), auth


def test_snapshots_are_deep_copies_and_reject_domain_objects():
    original = {"native": {"ids": ["id-1"]}}
    _, request, _ = make_work(native_context=original)
    original["native"]["ids"].append("id-2")
    assert request.native_context["native"]["ids"] == ("id-1",)
    with pytest.raises(TypeError): request.native_context["new"] = 1
    with pytest.raises(FrozenInstanceError): request.request_id = "different"
    with pytest.raises(ValueError): make_work(native_context={"object": object()})


@pytest.mark.parametrize("box", [BBox(0, 0, 0, 1), BBox(0, 0, 1, 0), BBox(math.nan, 0, 1, 1), BBox(0, 0, math.inf, 1)])
def test_invalid_boxes_rejected_at_new_boundary(box):
    with pytest.raises(ValueError): make_work(regions=(box,))


@pytest.mark.parametrize("changes", [{"contract_version": 2}, {"contract_version": True}, {"parent_request_id": "request-1"}, {"resource_allocation": {"max_attempts": 5}}])
def test_invalid_request_versions_and_mutable_inputs(changes):
    with pytest.raises(ValueError): make_work(**changes)


def test_authorization_and_target_revision_are_bound_to_request():
    _, request, auth = make_work()
    with pytest.raises(ValueError): replace(request, scope=replace(request.scope, page_index=1))
    with pytest.raises(ValueError): replace(request, scope=replace(request.scope, target=replace(request.scope.target, revision=2)))
    with pytest.raises(ValueError): replace(request, authorizations=(auth, auth))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("confidence,scale", [(0.83, "0..1"), (83, "0..100"), (None, "unknown"), (17, "unknown")])
def test_raw_token_roundtrip_uses_actual_raster_dimensions_and_physical_rotation(rotation, confidence, scale):
    frame = RasterFrame(BBox(11.5, 23.25, 151.75, 83.5), 421, 181, rotation)
    raw = OCRToken("Ação - R$ 1.234,56", ((10, 20), (100, 20), (100, 40), (10, 40)), (10, 20, 100, 40), confidence, scale, "line", "fifth")
    _, _, auth = make_work()
    token = from_raw(raw, frame, token_id="token-1", authorization_id=auth.authorization_id, attempt_id="attempt-1")
    actual = to_raw(token, frame)
    assert actual.text == raw.text
    assert actual.confidence_native == raw.confidence_native
    assert actual.confidence_scale == raw.confidence_scale
    assert actual.source_engine == raw.source_engine
    assert actual.bbox_px == pytest.approx(raw.bbox_px)
    for a, b in zip(actual.polygon_px, raw.polygon_px): assert a == pytest.approx(b)
    if scale == "unknown": assert token.confidence_normalized is None


def test_domain_token_roundtrip_does_not_fabricate_native_confidence():
    _, _, auth = make_work()
    raw = OcrToken("R$ -12,34", BBox(20, 20, 40, 30), .81, "pt-BR", SourceKind.OCR_REGION,
                   90, "region-origin", (Point(20,20),Point(40,20),Point(40,30),Point(20,30)), "word",
                   OcrProvenance("easyocr", "craft", preprocessing=("clahe",), scale=3))
    token = from_domain(raw, token_id="token-1", authorization_id=auth.authorization_id, attempt_id="attempt-1")
    assert token.confidence_native is None
    assert token.confidence_scale == "unknown"
    assert token.normalization == "domain_0..1"
    assert to_domain(token) == raw


@pytest.mark.parametrize("changes", [
    {"confidence_native": math.nan}, {"confidence_normalized": .8},
    {"confidence_native": 80, "confidence_scale": "0..100", "confidence_normalized": .8, "normalization": "identity"},
    {"confidence_native": 101, "confidence_scale": "0..100"},
    {"polygon": (Point(20,20), Point(30,math.inf), Point(40,30))},
])
def test_bad_confidence_and_polygon_are_rejected(changes):
    _, _, auth = make_work()
    with pytest.raises(ValueError): evidence(auth, **changes)


def test_result_identity_scope_and_proposal_lineage_are_checked():
    _, request, auth = make_work()
    token = evidence(auth)
    proposal = IntegrationProposal("proposal-1", auth.authorization_id, request.scope.target, ProposalKind.COMPLEMENT, (token.token_id,), "observed")
    result = result_for(request, tokens=(token,), proposals=(proposal,))
    result.validate_for(request)
    for scope in (replace(request.scope, document_id="another"), replace(request.scope, evidence_version="old"), replace(request.scope, page_index=1)):
        with pytest.raises(ValueError): replace(result, scope=scope).validate_for(request)
    for changes in ({"request_id": "another"}, {"selection": EngineSelection("other")}):
        with pytest.raises(ValueError): result.validate_for(replace(request, **changes))
    with pytest.raises(ValueError): replace(result, proposals=(proposal, proposal)).validate_for(request)
    with pytest.raises(ValueError): replace(result, proposals=(replace(proposal, target=replace(proposal.target, revision=2)),)).validate_for(request)
    with pytest.raises(ValueError): replace(result, identity=EffectiveIdentity(EngineSelection("other")))
    ledger = ResultReceiptLedger()
    assert ledger.record(request, result)
    assert not ledger.record(request, result)
    with pytest.raises(ValueError): ledger.record(request, replace(result, tokens=(replace(token, text="different"),)))


def test_proposal_integrator_receipt_covers_full_proposal_and_rejects_unbound_mutation():
    _, request, auth = make_work()
    token = replace(evidence(auth), text="Total 42")
    proposal = IntegrationProposal(
        "proposal-1", auth.authorization_id, request.scope.target,
        ProposalKind.COMPLEMENT, (token.token_id,), "authorized-complement",
    )
    result = result_for(request, tokens=(token,), proposals=(proposal,))
    integrator = ProposalIntegrator()

    integrated = integrator.apply(request, result)
    assert len(integrated) == 1
    assert integrator.apply(request, result) == ()

    altered_proposal = replace(proposal, proposal_id="proposal-2")
    with pytest.raises(ValueError, match="different proposals"):
        integrator.apply(request, replace(result, proposals=(altered_proposal,)))

    replacement = replace(proposal, kind=ProposalKind.REPLACE, replaces_evidence_ids=("native-1",))
    with pytest.raises(ValueError, match="target binding"):
        ProposalIntegrator().apply(request, replace(result, proposals=(replacement,)))


def test_region_replacement_is_core_bound_conservative_idempotent_and_rejects_hidden_evidence():
    request, auth = request_for_target("region", "region-target")
    original = OcrToken("Total 42", BBox(20, 20, 60, 30), .7, "pt-BR", SourceKind.OCR_REGION)
    region = LayoutRegion(
        "region-target", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    binding = RegionTarget(request.scope.target, region, ["ocr-old"], {"ocr-old"}, visible_target=True)
    token = replace(evidence(auth), text="Total 42")
    proposal = IntegrationProposal(
        "replace-1", auth.authorization_id, request.scope.target, ProposalKind.REPLACE,
        (token.token_id,), "same value with stronger recognition", ("ocr-old",),
    )
    result = result_for(request, tokens=(token,), proposals=(proposal,))
    integrator = ProposalIntegrator()
    assert integrator.apply(request, result, targets=(binding,)) == (to_domain(token),)
    assert region.ocr_tokens[0].text == token.text
    assert binding.evidence_ids == [token.token_id]
    assert integrator.apply(request, result, targets=(binding,)) == ()

    hidden_region = LayoutRegion(
        "region-target", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    hidden = RegionTarget(request.scope.target, hidden_region, ["ocr-old"], set(), visible_target=True)
    with pytest.raises(ValueError, match="hidden or untrusted"):
        ProposalIntegrator().apply(request, result, targets=(hidden,))
    assert hidden_region.ocr_tokens == [original]

    stale_region = LayoutRegion(
        "region-target", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    stale = RegionTarget(request.scope.target, stale_region, ["ocr-old"], {"ocr-old"}, visible_target=True)
    stale_region.bbox = BBox(0, 10, 100, 80)
    with pytest.raises(IntegrationRejected, match="authorized snapshot"):
        ProposalIntegrator().apply(request, result, targets=(stale,))
    assert stale_region.ocr_tokens == [original]

    remapped_region = LayoutRegion(
        "region-target", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    remapped = RegionTarget(request.scope.target, remapped_region, ["ocr-old"],
                            {"ocr-old"}, visible_target=True)
    remapped.evidence_ids[0] = "different-owner-evidence"
    with pytest.raises(IntegrationRejected, match="authorized snapshot"):
        ProposalIntegrator().apply(request, result, targets=(remapped,))
    assert remapped_region.ocr_tokens == [original]

    duplicate = replace(token, token_id="token-duplicate")
    duplicate_proposal = replace(proposal, proposal_id="replace-duplicate",
                                 evidence_ids=(token.token_id, duplicate.token_id))
    duplicate_region = LayoutRegion(
        "region-target", RegionKind.TEXT, BBox(10, 10, 100, 80), .9, [], [original],
        RegionQuality(RegionDecision.OCR_REGION),
    )
    duplicate_binding = RegionTarget(request.scope.target, duplicate_region, ["ocr-old"],
                                     {"ocr-old"}, visible_target=True)
    duplicate_result = result_for(request, tokens=(token, duplicate), proposals=(duplicate_proposal,))
    with pytest.raises(IntegrationRejected, match="duplicated evidence"):
        ProposalIntegrator().apply(request, duplicate_result, targets=(duplicate_binding,))
    assert duplicate_region.ocr_tokens == [original]


def test_selected_tile_results_preserve_authorizations_and_commit_atomically():
    request, _auth = request_for_target("region", "tile-target")
    registry = AuthorizationRegistry(request.scope.extraction_id, request.scope.document_id)
    left = registry.issue(request.scope, (BBox(0, 0, 100, 100),))
    right = registry.issue(request.scope, (BBox(100, 0, 200, 100),))
    selections = []
    for index, (auth, box) in enumerate(((left, BBox(20, 20, 40, 30)), (right, BBox(120, 20, 140, 30)))):
        tile = replace(request, request_id=f"tile-{index}", authorizations=(auth,))
        token = replace(evidence(auth), token_id=f"evidence-{index}", text=f"part-{index}", bbox=box)
        proposal = IntegrationProposal(
            f"proposal-{index}", auth.authorization_id, tile.scope.target,
            ProposalKind.COMPLEMENT, (token.token_id,), "selected tile",
        )
        selections.append((tile, result_for(tile, tokens=(token,), proposals=(proposal,))))

    def binding():
        region = LayoutRegion(
            "tile-target", RegionKind.TEXT, request.scope.page_geometry.bbox,
            .9, [], [], RegionQuality(RegionDecision.OCR_REGION),
        )
        return RegionTarget(request.scope.target, region, [], set(), visible_target=True)

    target = binding()
    integrator = ProposalIntegrator()
    assert len(integrator.apply_selected(selections, target=target)) == 2
    assert [token.text for token in target.region.ocr_tokens] == ["part-0", "part-1"]
    assert integrator.apply_selected(selections, target=target) == ()

    failed_target = binding()
    bad_request, bad_result = selections[-1]
    bad_token = replace(bad_result.tokens[0], bbox=BBox(80, 20, 95, 30))
    invalid = [selections[0], (bad_request, replace(bad_result, tokens=(bad_token,)))]
    with pytest.raises(IntegrationRejected, match="outside its authorization"):
        ProposalIntegrator().apply_selected(invalid, target=failed_target)
    assert failed_target.region.ocr_tokens == []
    assert failed_target.region.ocr_lines == []


def test_page_token_replacement_combines_authorized_tiles_atomically_and_rejects_hidden_or_stale_evidence():
    request, _ = request_for_target("ocr_tokens", "weak-line")
    registry = AuthorizationRegistry(request.scope.extraction_id, request.scope.document_id)
    old = [OcrToken("part-0 part-1", BBox(20, 20, 140, 30), .4, "pt-BR", SourceKind.OCR_PAGE)]
    outside = OcrToken("part-0", BBox(10, 80, 40, 90), .9, "pt-BR", SourceKind.OCR_PAGE)
    def binding():
        return TokenTarget(request.scope.target, BBox(0, 0, 200, 50), [*old, outside],
                           ["old", "outside"], ("old",), {"old"}, visible_target=True)
    selections = []
    for index, region in enumerate((BBox(0, 0, 100, 50), BBox(100, 0, 200, 50))):
        auth = registry.issue(request.scope, (region,))
        tile = replace(request, request_id=f"tile-{index}", authorizations=(auth,))
        token = replace(evidence(auth), token_id=f"tile-token-{index}", text=f"part-{index}",
                        bbox=BBox(20 + index*100, 20, 40 + index*100, 30))
        proposal = IntegrationProposal(f"replace-{index}", auth.authorization_id, request.scope.target,
                                       ProposalKind.REPLACE, (token.token_id,), "selected tiles", ("old",))
        selections.append((tile, result_for(tile, tokens=(token,), proposals=(proposal,))))
    target = binding()
    integrator = ProposalIntegrator()
    assert len(integrator.apply_selected(selections, target=target)) == 2
    assert [token.text for token in target.tokens] == ["part-0", "part-0", "part-1"]
    assert target.tokens[0] is outside
    assert target.evidence_ids == ["outside", "tile-token-0", "tile-token-1"]
    assert integrator.apply_selected(selections, target=target) == ()
    for invalidation in ("missing_tile", "stale", "hidden", "wrong_selection"):
        rejected = binding()
        invalid = selections
        if invalidation == "missing_tile":
            invalid = selections[:1]
        elif invalidation == "stale":
            rejected.tokens.append(outside)
        elif invalidation == "hidden":
            rejected.visible_evidence_ids.clear()
        else:
            req, result = selections[1]
            invalid = [selections[0], (req, replace(result, proposals=(replace(result.proposals[0], replaces_evidence_ids=("outside",)),)))]
        prior = tuple(rejected.tokens)
        with pytest.raises(IntegrationRejected):
            ProposalIntegrator().apply_selected(invalid, target=rejected)
        assert tuple(rejected.tokens) == prior


def test_critical_composition_preserves_order_position_lineage_and_replay_across_source_requests():
    request, _ = request_for_target("ocr_tokens", "critical-field", goal="critical_data")
    registry = AuthorizationRegistry(request.scope.extraction_id, request.scope.document_id)
    selections = []
    for index, (region, text, box) in enumerate((
        (BBox(0, 0, 100, 100), "R$", BBox(20, 20, 40, 30)),
        (BBox(100, 0, 200, 100), "12,34", BBox(120, 20, 160, 30)),
    )):
        auth = registry.issue(request.scope, (region,))
        tile = replace(request, request_id=f"critical-{index}", authorizations=(auth,))
        token = replace(evidence(auth), token_id=f"critical-evidence-{index}", text=text, bbox=box)
        proposal = IntegrationProposal(f"critical-proposal-{index}", auth.authorization_id, tile.scope.target,
                                       ProposalKind.REPLACE, (token.token_id,), "selected critical tile", ("old",),
                                       composition="join_critical")
        selections.append((tile, result_for(tile, tokens=(token,), proposals=(proposal,))))
    outside = OcrToken("fora", BBox(5, 80, 20, 90), .99, "pt-BR", SourceKind.OCR_PAGE)
    original = OcrToken("12,34", BBox(120, 20, 160, 30), .4, "pt-BR", SourceKind.OCR_PAGE)
    def binding():
        return TokenTarget(request.scope.target, request.scope.page_geometry.bbox, [outside, original],
                           ["outside", "old"], ("old",), {"old"}, goal=request.goal, visible_target=True)
    target = binding()
    order = ("critical-evidence-0", "critical-evidence-1")
    integrator = ProposalIntegrator()
    accepted = integrator.apply_selected(tuple(reversed(selections)), target=target, evidence_order=order)
    assert len(accepted) == 1 and target.tokens[0] is outside
    assert target.tokens[1].text == "R$ 12,34"
    assert target.lineage[target.evidence_ids[1]] == ("old", *order)
    assert integrator.apply_selected(tuple(reversed(selections)), target=target, evidence_order=order) == ()
    with pytest.raises(IntegrationRejected, match="different evidence"):
        integrator.apply_selected(tuple(reversed(selections)), target=target, evidence_order=tuple(reversed(order)))
    incomplete = binding()
    with pytest.raises(IntegrationRejected, match="cover"):
        ProposalIntegrator().apply_selected(selections[1:], target=incomplete)
    assert incomplete.tokens == [outside, original] and not incomplete.lineage


def test_cell_correction_preserves_conservation_lineage_and_is_atomic_on_rejection():
    request, auth = request_for_target("cell", "cell-target", goal="cell")
    from structured_pdf_text.document import EvidenceRef, TextToken

    old = TextToken("Valor 42", BBox(20, 20, 60, 30), [EvidenceRef(SourceKind.NATIVE_PDF, 0, "native-1")],
                    .8, "Valor 42", evidence_id="native-1")
    cell = TableCell(0, 0, 1, 1, BBox(10, 10, 100, 80), "Valor 42", [old], .8)
    binding = CellTarget(request.scope.target, "table-a", 0, cell, {"native-1"}, visible_target=True)
    good = replace(evidence(auth), text="Valor 42")
    proposal = IntegrationProposal("cell-1", auth.authorization_id, request.scope.target,
                                   ProposalKind.CORRECT_CELL, (good.token_id,), "verified cell correction",
                                   ("native-1",))
    result = result_for(request, tokens=(good,), proposals=(proposal,))
    assert len(ProposalIntegrator().apply(request, result, targets=(binding,))) == 1
    assert cell.text == good.text
    assert cell.tokens[0].derived_from_ids == ("native-1", good.token_id)
    assert cell.tokens[0].decision_reason == "verified cell correction"

    bad_cell = TableCell(0, 0, 1, 1, BBox(10, 10, 100, 80), "Não aprovado - R$ 42",
                         [TextToken("Não aprovado - R$ 42", BBox(20, 20, 60, 30),
                                    [EvidenceRef(SourceKind.NATIVE_PDF, 0, "native-1")], .8,
                                    "Não aprovado - R$ 42", evidence_id="native-1")], .8)
    bad_binding = CellTarget(request.scope.target, "table-a", 0, bad_cell, {"native-1"}, visible_target=True)
    erased = replace(good, text="Aprovado 42")
    bad_proposal = replace(proposal, evidence_ids=(erased.token_id,), proposal_id="cell-erase")
    before = bad_cell.text, tuple(bad_cell.tokens)
    with pytest.raises(IntegrationRejected, match="conserve"):
        ProposalIntegrator().apply(request, result_for(request, tokens=(erased,), proposals=(bad_proposal,)),
                                   targets=(bad_binding,))
    assert (bad_cell.text, tuple(bad_cell.tokens)) == before


def test_figure_association_and_priority_recommendation_do_not_overwrite_or_launch_work():
    request, auth = request_for_target("figure", "figure-target", goal="region")
    region = LayoutRegion(
        "figure-target", RegionKind.FIGURE, BBox(10, 10, 100, 80), .9, [], [],
        RegionQuality(RegionDecision.MERGE_OCR),
    )
    binding = FigureTarget(request.scope.target, region, [], visible_target=True)
    token = evidence(auth)
    proposal = IntegrationProposal("figure-1", auth.authorization_id, request.scope.target,
                                   ProposalKind.ASSOCIATE_FIGURE, (token.token_id,), "text belongs to this figure")
    integrator = ProposalIntegrator()
    assert integrator.apply(request, result_for(request, tokens=(token,), proposals=(proposal,)),
                            targets=(binding,)) == (to_domain(token),)
    assert region.ocr_tokens == [to_domain(token)]
    assert region.ocr_lines

    priority = IntegrationProposal("priority-1", auth.authorization_id, request.scope.target,
                                   ProposalKind.PRIORITIZE_OCR, (), "low-confidence recognized region")
    priority_request = replace(request, request_id="request-priority")
    assert integrator.apply(priority_request, result_for(priority_request, proposals=(priority,))) == ()
    assert integrator.priority_recommendations[-1].target == request.scope.target


def test_local_failure_cannot_be_hidden_behind_success():
    _, request, _ = make_work()
    failure = LocalFailure(WorkStatus.RUNTIME_ERROR, "optional attempt failed", mandatory=False)
    with pytest.raises(ValueError): result_for(request, failures=(failure,))
    result = replace(result_for(request), status=WorkStatus.PARTIAL, failures=(failure,))
    assert result.failures == (failure,)


def test_allocations_have_no_deadline_and_cannot_be_mutated_between_instances():
    nested = {"threads": 2, "decoder": ["beamsearch"]}
    a = EffectiveIdentity(EngineSelection("easyocr"), extra=nested)
    b = EffectiveIdentity(EngineSelection("paddle"), extra=nested)
    nested["decoder"].append("other")
    assert a.extra["decoder"] == b.extra["decoder"] == ("beamsearch",)
    assert all("second" not in name and "timeout" not in name for name in ResourceAllocation.__dataclass_fields__)
    with pytest.raises(ValueError): ResourceAllocation(max_attempts=True)


def test_composed_affine_roundtrip_and_invalid_inverse():
    frame = RasterFrame(BBox(11, 19, 111, 69), 333, 167, 270)
    child = frame.derived(20, 30, affine((2, .2, 17, -.1, 3, 5)))
    for p in (Point(0,0),Point(7.5,11.25),Point(20,30)):
        actual = child.page_point_to_pixel(child.point_to_page(p))
        assert (actual.x,actual.y) == pytest.approx((p.x,p.y))
    with pytest.raises(ValueError): affine((0,0,0,0,0,0))


def test_pipeline_artifact_projection_is_finite_and_uses_its_own_version():
    import json
    from structured_pdf_text.ocr.pipeline.serialization import to_payload
    _, request, auth = make_work(native_context={"evidence_ids":["native-1"]})
    result = result_for(request,tokens=(evidence(auth),),engine_diagnostics={"custom":{"candidates":["one"]}})
    request_payload = to_payload(request)
    result_payload = to_payload(result)
    assert request_payload["schema_version"] == result_payload["schema_version"] == "ocr-pipeline-1.0"
    assert request_payload["kind"] == "work_request"
    assert request_payload["native_context"] == {"evidence_ids":["native-1"]}
    assert result_payload["tokens"][0]["bbox"]["x0"] == 20
    assert "_items" not in json.dumps(result_payload,allow_nan=False)
