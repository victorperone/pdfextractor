"""RapidOCR composition; ONNX Runtime and OpenVINO stay provider-local."""
from dataclasses import replace
import structured_pdf_text.ocr.factory as factory
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection
from structured_pdf_text.ocr.registry import get_entry
from .policy import CELL_POLICY, FIGURE_SEGMENTATION_POLICY, FUSION_POLICY, INTEGRATION_POLICY, RASTER_BOUNDARY_POLICY, RECOVERY_POLICY, TOKEN_REFINEMENT_POLICY


class RapidOCRPipeline(AuthorizedBackendPipeline):
    recovery_policy = RECOVERY_POLICY
    integration_policy = INTEGRATION_POLICY
    cell_policy = CELL_POLICY
    token_refinement_policy = TOKEN_REFINEMENT_POLICY
    fusion_policy = FUSION_POLICY
    figure_segmentation_policy = FIGURE_SEGMENTATION_POLICY
    raster_boundary_policy = RASTER_BOUNDARY_POLICY

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
    return RapidOCRPipeline(resolved, factory.build_ocr_backend(replace(resolved.config, ocr_settings=resolved.settings)))
