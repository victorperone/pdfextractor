"""PaddleOCR-owned recovery schedule, matching the pre-migration path."""
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureShapeAdmissionPolicy, IsolatedGlyphAdmissionPolicy, RasterBoundaryPolicy
from structured_pdf_text.ocr.pipeline.text_columns import TextColumnPolicy
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
FIGURE_ADMISSION_POLICY = FigureShapeAdmissionPolicy("paddle-figure-admission-v1", "paddle")
# On pages without text (V10 seals, emblems, charts) PP-OCR reads the shapes
# as lone glyphs: "O", "Q", "9", "典", "新场督局" (001-P1-02).
ADMISSION_POLICY = IsolatedGlyphAdmissionPolicy("paddle-admission-v2", "paddle", base=FIGURE_ADMISSION_POLICY)

# PP-OCR detects one box per column line; the shared row grouping joined the
# rows of neighbouring columns on raster multi-column pages (V10, 001-P1-01).
TEXT_COLUMN_POLICY = TextColumnPolicy("paddle-text-columns-v1", enabled=True)
