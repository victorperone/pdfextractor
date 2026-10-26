"""EasyOCR composition; candidate generation stays inside its backend."""
from dataclasses import replace
import structured_pdf_text.ocr.factory as factory
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection
from structured_pdf_text.ocr.registry import get_entry
from .policy import ADMISSION_POLICY, CELL_POLICY, FIGURE_SEGMENTATION_POLICY, FUSION_POLICY, INTEGRATION_POLICY, RASTER_BOUNDARY_POLICY, RECOVERY_POLICY, TOKEN_REFINEMENT_POLICY


class EasyOCRPipeline(AuthorizedBackendPipeline):
    recovery_policy = RECOVERY_POLICY
    integration_policy = INTEGRATION_POLICY
    cell_policy = CELL_POLICY
    token_refinement_policy = TOKEN_REFINEMENT_POLICY
    fusion_policy = FUSION_POLICY
    figure_segmentation_policy = FIGURE_SEGMENTATION_POLICY
    raster_boundary_policy = RASTER_BOUNDARY_POLICY
    admission_policy = ADMISSION_POLICY

    def _identity(self):
        identity = super()._identity()
        return replace(identity, extra={**dict(identity.extra),
            "raster_boundary_policy": self.raster_boundary_policy.mode,
            "figure_admission": {
                "policy_id": self.admission_policy.policy_id,
                "lone_glyph_minimum_confidence": self.admission_policy.lone_glyph_minimum_confidence,
                "lone_glyph_minimum_height": self.admission_policy.lone_glyph_minimum_height,
                "ring_glyphs": self.admission_policy.ring_glyphs,
                "ring_minimum_aspect": self.admission_policy.ring_minimum_aspect,
            },
            "candidate_fusion": self.fusion_policy.__class__.__name__,
            "table_refinement": self.token_refinement_policy.identity()})

    def __init__(self, resolved, backend):
        entry = get_entry(resolved.engine)
        super().__init__(
            backend,
            selection=EngineSelection(resolved.engine, resolved.provider),
            render_scale=resolved.settings.render_scale,
            capabilities=entry.capabilities,
            settings=resolved.settings,
        )


def build_pipeline(resolved):
    return EasyOCRPipeline(resolved, factory.build_ocr_backend(replace(resolved.config, ocr_settings=resolved.settings)))
