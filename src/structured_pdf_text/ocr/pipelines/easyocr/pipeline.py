"""EasyOCR composition; candidate generation stays inside its backend."""
from dataclasses import replace
import structured_pdf_text.ocr.factory as factory
from structured_pdf_text.ocr.pipeline.adapters import AuthorizedBackendPipeline
from structured_pdf_text.ocr.pipeline.contracts import EngineSelection
from structured_pdf_text.ocr.registry import get_entry
from .structure import EasyOCRTableStructureEngine
from .layout import prepare_layout_lines
from .marks import SELECTION_MARK_POLICY
from .decorations import WATERMARK_POLICY
from .policy import ADMISSION_POLICY, CELL_POLICY, FIGURE_SEGMENTATION_POLICY, FUSION_POLICY, INTEGRATION_POLICY, RASTER_BOUNDARY_POLICY, RECOVERY_POLICY, TOKEN_REFINEMENT_POLICY


class EasyOCRPipeline(AuthorizedBackendPipeline):
    table_structure_engine = EasyOCRTableStructureEngine()
    line_order_policy = staticmethod(prepare_layout_lines)
    recovery_policy = RECOVERY_POLICY
    integration_policy = INTEGRATION_POLICY
    cell_policy = CELL_POLICY
    token_refinement_policy = TOKEN_REFINEMENT_POLICY
    fusion_policy = FUSION_POLICY
    figure_segmentation_policy = FIGURE_SEGMENTATION_POLICY
    raster_boundary_policy = RASTER_BOUNDARY_POLICY
    admission_policy = ADMISSION_POLICY

    @property
    def currency_marker_repair(self):
        # ``Rs`` before a pt-BR amount is ``R$`` misread only in Portuguese
        # documents; elsewhere it may be the rupee abbreviation.
        language = getattr(self.settings, "language", None) or ""
        return language.casefold().startswith("pt")

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
            "raster_table_structure": self.table_structure_engine.__class__.__name__,
            "rotated_prose_columns": True,
            "currency_marker_repair": self.currency_marker_repair,
            "numeric_verification": {"maximum_fields_per_raster": 8, "minimum_confidence": .80,
                                     "matching_direct_reads": 2, "preserve_amount_and_explicit_sign": True},
            "direct_content_crop": "edge_rules_and_blank_margins",
            "case_majority": {"minimum_votes": 3, "absolute_majority": True},
            "selection_marks": {**SELECTION_MARK_POLICY.identity(), "requires_aligned_empty_neighbour": True},
            "decorative_watermarks": WATERMARK_POLICY.identity(),
            "automatic_cell_recovery": {
                "enabled": self.cell_policy.automatic_recovery,
                "maximum_cells": self.cell_policy.maximum_automatic_cells,
            },
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
