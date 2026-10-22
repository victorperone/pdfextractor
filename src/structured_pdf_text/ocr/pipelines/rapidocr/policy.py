"""RapidOCR-owned recovery schedule, matching the pre-migration path."""
from dataclasses import dataclass
import math
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.quality import _union_area
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import RasterBoundaryPolicy

# Both RapidOCR providers share policy; their resolved settings stay separate.
CANDIDATE_POLICY = RegionalCandidatePolicy()

RECOVERY_POLICY = RegionalRecoveryProfile(
    candidates=CANDIDATE_POLICY,
    weak_region_markers=("small", "sparse", "missing", "damaged"),
    weak_region_scales=(1.0, 1.5, 2.0),
    ordinary_region_scales=(1.0,),
    critical_data_scales=(1.5, 2.0, 3.0),
    weak_line_area_threshold=0.12,
    weak_line_small_scales=(1.5, 2.0, 3.0),
    weak_line_large_scales=(1.5, 2.0),
)

INTEGRATION_POLICY = ConservativeIntegrationPolicy("rapidocr-integration-v1")

CELL_POLICY = CellRecoveryPolicy()

TOKEN_REFINEMENT_POLICY = TokenRefinementPolicy()
FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()
RASTER_BOUNDARY_POLICY = RasterBoundaryPolicy("trim")

FUSION_POLICY = CandidateFusionPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCRFigureAdmissionPolicy:
    """Abstain on uncertain detections that span almost an entire figure.

    Small labels and high-confidence text remain eligible. This is local v6
    calibration, not a corpus/page rule or a change to shared conservation.
    """

    policy_id: str = "rapidocr-figure-admission-v1"
    minimum_confidence: float = .70
    minimum_scope_coverage: float = .85

    def __post_init__(self):
        for value in (self.minimum_confidence, self.minimum_scope_coverage):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Figure admission calibration must be finite in [0, 1]")

    def rejection_reason(self, token, request):
        if request.goal is not WorkGoal.FIGURE or token.confidence_normalized is None or token.confidence_normalized >= self.minimum_confidence:
            return None
        authorization = next(a for a in request.authorizations if a.authorization_id == token.authorization_id)
        # Use actual authorized rectangles, not a convex envelope that could
        # include unobserved gaps. The service already validated the token.
        if token.bbox.area / _union_area(list(authorization.regions)) >= self.minimum_scope_coverage:
            return "rapidocr_uncertain_figure_spanning_detection"
        return None


ADMISSION_POLICY = RapidOCRFigureAdmissionPolicy()
