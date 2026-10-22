"""EasyOCR-owned recovery schedule, matching the pre-migration path."""
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy

# Kept local so future EasyOCR tuning cannot alter another engine. Values
# capture the common legacy schedule; changing them is a separate experiment.
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

INTEGRATION_POLICY = ConservativeIntegrationPolicy("easyocr-integration-v1")

CELL_POLICY = CellRecoveryPolicy()

TOKEN_REFINEMENT_POLICY = TokenRefinementPolicy()
FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()

FUSION_POLICY = CandidateFusionPolicy(
    detector_families=("craft_greedy", "craft_beam", "dbnet"),
    decoder_markers=(("wordbeam", "wordbeamsearch"), ("beam", "beamsearch")),
)
