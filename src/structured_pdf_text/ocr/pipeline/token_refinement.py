"""Engine-owned acceptance over authorized immutable refinement evidence."""
from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
from structured_pdf_text.fusion.integrator import IntegrationRejected


def refine_footnote(facade, target, *, base_scale, quality_policy, policy):
    old = [token for token, evidence_id in zip(target.tokens, target.evidence_ids)
           if evidence_id in target.selected_evidence_ids]
    try:
        try:
            reread = facade.recognize_page(
                None, facade.coordinator.page_index, target.bbox,
                quality_policy=quality_policy, render_scale=base_scale * policy.footnote_scale_factor,
                selection_stage="footnote", decision_reason="core_selected_small_footnote",
                integration_targets=(target,), defer_integration=True, mandatory=False,
            )
        except FatalExtractionError:
            raise
        except Exception as exc:
            raise_if_resource_exhausted(exc, page_index=facade.coordinator.page_index, stage="footnote_pipeline")
            return False
        reread = policy.deduplicate([token for token in reread if token.text.strip()])
        if not policy.accept_footnote(old, reread):
            return False
        try:
            return bool(facade.integrate_selected_tokens(reread, target))
        except IntegrationRejected:
            return False
    finally:
        facade.discard_selected_tokens(target)


def refine_critical(facade, target, *, base_scale, quality_policy, data_type, page_image, policy):
    """Keep original candidate evidence until typed composition commits it once."""
    from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementGoal, RegionRefinementRequest

    original = next(token for token, value in zip(target.tokens, target.evidence_ids)
                    if value in target.selected_evidence_ids)
    hypotheses = []
    try:
        try:
            kwargs = dict(
                quality_policy=quality_policy, render_scale=base_scale * policy.critical_render_scale_factor,
                integration_targets=(target,), defer_integration=True, mandatory=False,
            )
            hypotheses.append(facade.recognize_page(
                None, facade.coordinator.page_index, target.bbox, selection_stage="critical_data",
                decision_reason=f"core_selected_critical_data:{data_type}", **kwargs,
            ))
            if facade.capabilities.get("direct_recognition", False):
                hypotheses.append(facade.recognize_direct(None, facade.coordinator.page_index, target.bbox, **kwargs))
        except FatalExtractionError:
            raise
        except Exception as exc:
            raise_if_resource_exhausted(exc, stage="critical_data_pipeline")
            hypotheses = []
        if not any(hypotheses):
            profile = facade.recovery_policy
            from .policies import LEGACY_COMPATIBILITY_RECOVERY_PROFILE

            profile = profile or LEGACY_COMPATIBILITY_RECOVERY_PROFILE
            # This common geometric executor may inspect the core page raster;
            # the engine still receives only leases from authorized services.
            result = OcrRegionRefiner(facade).refine(
                page_image, facade.coordinator.page_index, facade.coordinator.page_bbox,
                RegionRefinementRequest(
                    bbox=target.bbox,
                    scale_factors=profile.scale_factors("critical_data", quality_policy=quality_policy),
                    rotations=(0.,), quality_variants=quality_policy != "baseline",
                    quality_policy=quality_policy, goal=RegionRefinementGoal.NUMERIC,
                    page_rotation=facade.coordinator.rotation,
                    quality_reasons=(f"critical_data:{data_type}",), integration_targets=(target,),
                ),
            )
            hypotheses.append(result.tokens)
        chosen = policy.select_critical(hypotheses, original, data_type)
        if not chosen:
            return False
        try:
            return bool(facade.integrate_selected_tokens(chosen, target))
        except IntegrationRejected:
            return False
    finally:
        facade.discard_selected_tokens(target)
