"""PaddleOCR-owned recovery schedule, matching the pre-migration path."""
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureShapeAdmissionPolicy, RasterBoundaryPolicy
from .raster import LEGACY_RASTER_PREPARATION

# Paddle can evolve this profile independently after its own equivalence gate.
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

INTEGRATION_POLICY = ConservativeIntegrationPolicy("paddle-integration-v1")

CELL_POLICY = CellRecoveryPolicy()

TOKEN_REFINEMENT_POLICY = TokenRefinementPolicy()
FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()
# Paddle detectors extend boxes onto the white partial-pixel border of a masked
# crop (the historical crop held real pixels there); see ADR 0002.
RASTER_BOUNDARY_POLICY = RasterBoundaryPolicy(confine_masked_border=True)

# ADR 0002: Paddle restores the pre-decoupling crop/resize/rotate pixel path.
RASTER_PREPARATION = LEGACY_RASTER_PREPARATION

FUSION_POLICY = CandidateFusionPolicy()

# PP-OCRv6 reads chart circles, markers and textless emblems as one huge glyph
# ("O", "é", "0"). Same v6 calibration RapidOCR validated for these models.
ADMISSION_POLICY = FigureShapeAdmissionPolicy("paddle-figure-admission-v1", "paddle")
