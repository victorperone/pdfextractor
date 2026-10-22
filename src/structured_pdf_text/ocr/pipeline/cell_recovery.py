"""Authorized cell attempts with engine-local ranking and core-owned commit."""
from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
from structured_pdf_text.fusion.integrator import IntegrationRejected


def refine_cell(facade, target, *, quality_policy, base_scale, header_text, policy):
    """Keep unselected attempts out of the table and preserve their diagnostics."""
    page_index = target.page_index
    scale = base_scale * policy.render_scale_factor
    candidates = []
    attempts = [(False, scale)]
    if facade.capabilities.get("direct_recognition", False):
        attempts.append((True, scale))
    attempts.append((False, scale * policy.enlarged_scale_factor))
    try:
        for direct, attempt_scale in attempts:
            try:
                kwargs = dict(
                    quality_policy=quality_policy, render_scale=attempt_scale,
                    integration_targets=(target,), defer_integration=True,
                    mandatory=False,
                )
                if direct:
                    tokens = facade.recognize_direct(None, page_index, target.cell.bbox, **kwargs)
                else:
                    tokens = facade.recognize_page(
                        None, page_index, target.cell.bbox,
                        selection_stage="cell", decision_reason="weak_table_cell", **kwargs,
                    )
                candidates.append(tokens)
            except FatalExtractionError:
                raise
            except Exception as exc:
                raise_if_resource_exhausted(exc, page_index=page_index, stage="cell_pipeline_attempt")
                # Each failed request is already recorded by the coordinator;
                # a later useful candidate must not erase that failure.
        chosen = policy.select(
            candidates, current_text=target.cell.text, cell_bbox=target.cell.bbox,
            header_text=header_text,
        )
        if not chosen:
            return False
        try:
            return bool(facade.integrate_selected_tokens(chosen, target))
        except IntegrationRejected:
            return False
    finally:
        facade.discard_selected_tokens(target)
