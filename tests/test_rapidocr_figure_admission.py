from dataclasses import replace
from types import SimpleNamespace

from PIL import Image
import pytest

from structured_pdf_text import SecurityLimits
from structured_pdf_text.document import LayoutRegion, OcrToken, RegionDecision, RegionKind, RegionQuality, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.pipeline.contracts import CandidateEvidence, EngineSelection, WorkGoal, WorkStatus
from structured_pdf_text.ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from structured_pdf_text.ocr.pipeline.converters import to_domain
from structured_pdf_text.ocr.pipelines.rapidocr.pipeline import RapidOCRPipeline
from structured_pdf_text.ocr.pipelines.rapidocr.policy import ADMISSION_POLICY, RapidOCRFigureAdmissionPolicy
from ocr_pipeline_helpers import make_work
from test_ocr_pipeline_contracts import evidence, result_for
from test_ocr_pipeline_services import SpySource


@pytest.mark.parametrize("goal,confidence,box,rejected", [
    (WorkGoal.FIGURE, .61, BBox(0, 0, 200, 100), True),
    (WorkGoal.FIGURE, .61, BBox(10, 10, 80, 30), False),
    (WorkGoal.FIGURE, .95, BBox(0, 0, 200, 100), False),
    (WorkGoal.FIGURE, .70, BBox(0, 0, 200, 100), False),
    (WorkGoal.FIGURE, None, BBox(0, 0, 200, 100), False),
    (WorkGoal.CELL, .61, BBox(0, 0, 200, 100), False),
    (WorkGoal.CRITICAL_DATA, .61, BBox(0, 0, 200, 100), False),
    (WorkGoal.PAGE, .61, BBox(0, 0, 200, 100), False),
])
def test_admission_uses_authorized_goal_confidence_and_geometry(goal, confidence, box, rejected):
    _, request, auth = make_work(goal=goal)
    token = evidence(auth, confidence_normalized=confidence,
                     normalization="domain_0..1" if confidence is not None else None)
    token = replace(token, bbox=box)
    assert bool(ADMISSION_POLICY.rejection_reason(token, request)) is rejected


def test_scope_coverage_uses_union_area_without_double_counting_or_convex_gaps():
    box = BBox(0, 0, 100, 100)
    for regions, rejected in (
        ((box, BBox(50, 0, 100, 100)), True),
        ((box, BBox(150, 0, 200, 100)), False),
    ):
        _, request, auth = make_work(goal=WorkGoal.FIGURE, regions=regions)
        token = replace(evidence(auth, confidence_normalized=.6, normalization="domain_0..1"), bbox=box)
        assert bool(ADMISSION_POLICY.rejection_reason(token, request)) is rejected


@pytest.mark.parametrize("value", [-.1, 1.1, float("nan"), float("inf"), True])
def test_invalid_local_calibration_is_rejected(value):
    with pytest.raises(ValueError):
        RapidOCRFigureAdmissionPolicy(minimum_confidence=value)


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_raw_evidence_is_preserved_but_only_admitted_proposals_reach_figure(deferred, mixed):
    page = BBox(0, 0, 200, 100)
    box = BBox(20, 20, 180, 80)
    class Engine:
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            tokens = [OcrToken("uncertain graphic", BBox(0, 0, image.width, image.height), .61,
                               "pt-BR", SourceKind.OCR_REGION)]
            if mixed:
                tokens.append(OcrToken("R$ -42,90 AB-009", BBox(10, 10, 100, 25), .95,
                                       "pt-BR", SourceKind.OCR_REGION))
            return tokens
        def close(self):
            pass
    pipeline = RapidOCRPipeline(SimpleNamespace(engine="rapidocr", provider="onnxruntime",
                                settings=SimpleNamespace(render_scale=1.)), Engine())
    coordinator = PageCoordinator(pipeline, SpySource(), extraction_id="admission", document_id="doc",
                                  selection=pipeline.descriptor.selection, page_index=0, page_bbox=page,
                                  rotation=0, quality_policy="baseline", security_limits=SecurityLimits())
    facade = OCRPageFacade(coordinator)
    region = LayoutRegion("figure", RegionKind.FIGURE, box, .9, [], [], RegionQuality(RegionDecision.OCR_REGION))
    target = facade.make_region_target(region)
    try:
        with Image.new("RGB", (200, 100)) as image:
            selected = facade.recognize_page(image, 0, integration_targets=(target,), defer_integration=deferred)
        assert [t.text for t in selected] == (["R$ -42,90 AB-009"] if mixed else [])
        raw = facade.last_result
        assert len(raw.tokens) == 1 + mixed
        assert raw.status is WorkStatus.OK and not raw.failures
        assert raw.counters["rejected_tokens"] == 1 and raw.counters["admitted_tokens"] == int(mixed)
        assert any(not c.selected and c.reason == "rapidocr_uncertain_figure_spanning_detection" for c in raw.candidates)
        rejected_ids = {i for c in raw.candidates if not c.selected for i in c.token_ids}
        assert not any(i in rejected_ids for p in raw.proposals for i in p.evidence_ids)
        assert raw.engine_diagnostics["rapidocr"]["admission_decisions"][0]["text"] == "uncertain graphic"
        if deferred:
            with pytest.raises(ValueError, match="did not come from a deferred"):
                facade.integrate_selected_tokens([to_domain(raw.tokens[0])], target)
            if selected:
                facade.integrate_selected_tokens(selected, target)
        assert [t.text for t in region.ocr_tokens] == (["R$ -42,90 AB-009"] if mixed else [])
        assert coordinator.work_summary()["outcome"] == ("success" if mixed else "no_text")
        assert not coordinator._deferred_tokens
        assert facade.identity.extra["figure_admission"]["minimum_confidence"] == .70
    finally:
        facade.close()


def test_unselected_candidate_cannot_be_sneaked_into_a_proposal():
    from structured_pdf_text.ocr.pipeline.contracts import IntegrationProposal, ProposalKind

    _, request, auth = make_work()
    token = evidence(auth)
    result = result_for(request, tokens=(token,),
                        candidates=(CandidateEvidence("rejected", token.attempt_id, (token.token_id,), False, "local_policy"),),
                        proposals=(IntegrationProposal("proposal", auth.authorization_id, request.scope.target,
                                   ProposalKind.COMPLEMENT, (token.token_id,), "invalid_selection"),))
    with pytest.raises(ValueError, match="unselected candidate"):
        result.validate_for(request)


def test_rapidocr_admission_tuning_does_not_change_other_engines():
    from structured_pdf_text.ocr.pipelines.easyocr.pipeline import EasyOCRPipeline
    from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
    from structured_pdf_text.ocr.pipelines.tesseract.pipeline import TesseractPipeline

    _, request, auth = make_work(goal=WorkGoal.FIGURE)
    token = replace(evidence(auth, confidence_normalized=.61, normalization="domain_0..1"), bbox=auth.regions[0])
    assert ADMISSION_POLICY.rejection_reason(token, request)
    assert replace(ADMISSION_POLICY, minimum_confidence=.5).rejection_reason(token, request) is None
    assert all(p.admission_policy.rejection_reason(token, request) is None
               for p in (EasyOCRPipeline, PaddlePipeline, TesseractPipeline))


def test_quality_abstention_does_not_hide_invalid_geometry_or_make_a_runtime_failure_clean():
    from structured_pdf_text.ocr.pipeline.services import AuthorizedOCRServices

    registry, request, _ = make_work(selection=EngineSelection("rapidocr", "onnxruntime"), goal=WorkGoal.FIGURE)
    class Engine:
        def recognize_page(self, image, *args, **kwargs):
            return [OcrToken("outside", BBox(-100, -100, image.width, image.height), .6, "pt-BR", SourceKind.OCR_REGION),
                    OcrToken("uncertain graphic", BBox(0, 0, image.width, image.height), .6, "pt-BR", SourceKind.OCR_REGION)]
        def close(self):
            pass
    pipeline = RapidOCRPipeline(SimpleNamespace(engine="rapidocr", provider="onnxruntime",
                                settings=SimpleNamespace(render_scale=1.)), Engine())
    try:
        with AuthorizedOCRServices(SpySource(), registry, request.selection) as services:
            result = pipeline.execute(request, services)
            assert result.status is WorkStatus.PARTIAL
            assert len(result.failures) == 1 and result.failures[0].status is WorkStatus.INVALID_REGION
            assert result.counters["rejected_tokens"] == 1 and not result.proposals
            assert len(result.tokens) == 1 and result.tokens[0].text == "uncertain graphic"
    finally:
        pipeline.close()
