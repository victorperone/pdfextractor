"""Core extraction pipeline: PdfTextExtractor and page-level OCR orchestration.

Provides :class:`PdfTextExtractor`, which coordinates native PDF text
extraction, layout analysis, table detection, and OCR-based recovery into a
unified :class:`~structured_pdf_text.document.StructuredDocument`.
"""
from __future__ import annotations

import inspect
import math
import re
import time
from uuid import uuid4
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .assemble.document import assemble_document
from .assemble.page import assemble_page
from .config import (
    ExtractionMode,
    ExtractorConfig,
    OcrQualityThresholds,
    effective_ocr_quality_policy,
    effective_ocr_quality_thresholds,
)
from .document import (
    Baseline,
    PageLink,
    ComplexityReason,
    DocumentMetadata,
    EvidenceRef,
    LayoutRegion,
    OcrToken,
    PageDiagnostics,
    PageStrategy,
    RegionDecision,
    RegionKind,
    SourceKind,
    StructuredDocument,
    StructuredPage,
    TableMethod,
    TextLine,
    TextToken,
    WritingDirection,
)
from .errors import (
    ExtractionCancelled,
    FatalExtractionError,
    raise_if_resource_exhausted,
)
from .evidence.complexity import ComplexityAnalyzer
from .memory import process_memory_snapshot
from .evidence.decision import assess_region_recovery
from .fusion.conservation import (
    _collect_token_critical_fingerprint,
    _critical_surface_preserved,
    _emit_conservation_warning,
    _extract_protected_semantic_tokens,
    _extract_signed_numeric_critical_values,
    _extract_structural_critical_values,
    _refinement_conserves_content,
    _similarity_key,
)
from .fusion.integrator import (
    IntegrationRejected,
    NativeOCRIntegrator,
    best_figure_region as _best_figure_region,
    cell_evidence_snapshot,
    merge_ocr_region_tokens_into_table_cells as _merge_regions_into_table_cells,
    ocr_tokens_snapshot,
    replace_tokens_in_box as _replace_tokens_in_box,
)
from .fusion.token_fusion import fuse_native_and_ocr
from .layout.engine import NativeHeuristicLayoutEngine
from .layout.regions import full_page_text_region, regions_from_predictions
from .layout.ocr_aware import reconstruct_ocr_layout
from .native.pdfium_source import PdfiumNativeEvidenceSource
from .ocr.engine import OcrEngine
from .ocr.candidate_fusion import recognize_page_with_tiles
from .ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult
from .ocr.reconstruct import reconstruct_ocr_lines
from .ocr.pipeline.contracts import WorkGoal
from .ocr.pipeline.coordinator import OCRPageFacade, PageCoordinator
from .ocr.pipeline.diagnostics import LegacyDiagnosticsProjector
from .ocr.pipeline.policies import LEGACY_COMPATIBILITY_RECOVERY_PROFILE
from .ocr.pipeline.cancellation import CancellationToken
from .ocr.critical_data import CriticalDataRefiner
from .ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementGoal,
    RegionRefinementRequest,
)
from .ocr.quality import assess_ocr_quality
from .tables.detector import detect_tables_native
from .tables.visual import detect_visual_table, detect_visual_table_in_region
from .tables.validation import (
    build_table_construction_diagnostics,
    validate_table_geometry,
)
from .tables.text_tracks import assess_borderless_region
from .tables.text_join import join_table_tokens
from .tables.grid_repair import repair_missing_dividers
from .tables.value_surfaces import harmonize_currency_markers, harmonize_value_surfaces
from .tables.ocr_lines import _rebuild_table_ocr_lines, _merge_short_table_fragments
from .native.reading_frame import to_upright_frame, upside_down
from .text.line_detector import lines_to_text, reconstruct_native_lines, spacing_diagnostics
from .visibility import (
    characters_inside_page,
    characters_occluded,
    characters_without_ink,
    detect_opaque_occlusion_boxes,
)
from .geometry import BBox


class PdfTextExtractor:
    def __init__(
        self,
        config: ExtractorConfig | None = None,
        layout_engine: Any | None = None,
        ocr_engine: OcrEngine | None = None,
        *,
        ocr_pipeline: Any | None = None,
    ) -> None:
        if ocr_engine is not None and ocr_pipeline is not None:
            raise ValueError("Pass either ocr_engine or ocr_pipeline, never both")
        self.config = config or ExtractorConfig()
        self.complexity_analyzer = ComplexityAnalyzer()
        self.layout_engine = layout_engine or NativeHeuristicLayoutEngine()
        self.ocr_engine = None
        from .ocr.pipeline.adapters import adapt_injection
        self._ocr_pipeline = adapt_injection(
            pipeline=ocr_pipeline, ocr_engine=ocr_engine, config=self.config,
        )
        self._provided_pipeline = ocr_pipeline is not None
        self._ocr_factory_pending = self._ocr_pipeline is None and _ocr_enabled(self.config)
        self._page_coordinator: PageCoordinator | None = None
        self._extraction_id = uuid4().hex
        self._document_id = uuid4().hex
        self._closed = False
        self._active_cancellation_token: CancellationToken | None = None

    def _ensure_ocr_engine(self) -> None:
        if self._closed:
            raise RuntimeError("PdfTextExtractor is closed")
        if self._ocr_pipeline is not None:
            return
        if self._ocr_factory_pending:
            from .ocr.factory import build_ocr_pipeline
            self._ocr_pipeline = build_ocr_pipeline(self.config)
            self._ocr_factory_pending = False

    def _ensure_page_ocr(self, source, native_page, page_index: int) -> None:
        if self._active_cancellation_token is not None:
            self._active_cancellation_token.check()
        self._ensure_ocr_engine()
        if self._ocr_pipeline is None:
            return
        if self._page_coordinator is not None:
            same_page = self._page_coordinator.page_index == page_index
            if not same_page:
                self._page_coordinator.close()
                self._page_coordinator = None
        if self._page_coordinator is None:
            descriptor = self._ocr_pipeline.descriptor
            reset_diagnostics = getattr(self._ocr_pipeline, "reset_page_diagnostics", None)
            if callable(reset_diagnostics):
                reset_diagnostics()
            reset_cancellation = getattr(self._ocr_pipeline, "reset_cancellation", None)
            if callable(reset_cancellation):
                reset_cancellation()
            self._page_coordinator = PageCoordinator(
                self._ocr_pipeline,
                source,
                extraction_id=self._extraction_id,
                document_id=self._document_id,
                selection=descriptor.selection,
                page_index=page_index,
                page_bbox=native_page.bbox,
                rotation=native_page.objects.rotation,
                quality_policy=effective_ocr_quality_policy(self.config),
                security_limits=self.config.security_limits,
                cancellation_token=self._active_cancellation_token,
            )
        self.ocr_engine = OCRPageFacade(self._page_coordinator)

    def _close_page_coordinator(self) -> None:
        if self._page_coordinator is not None:
            self._page_coordinator.close()
            self._page_coordinator = None

    def close(self) -> None:
        """Release resources held by the OCR backend (e.g. the Paddle subprocess)."""
        if self._closed:
            return
        cancel = getattr(self._ocr_pipeline, "cancel", None)
        if callable(cancel):
            cancel()
        self._close_page_coordinator()
        close_target = self._ocr_pipeline
        if close_target is not None and hasattr(close_target, "close"):
            close_target.close()
        self.ocr_engine = None
        self._ocr_pipeline = None
        self._ocr_factory_pending = False
        self._closed = True

    def __enter__(self) -> "PdfTextExtractor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def extract(
        self,
        path: str | Path,
        password: str | None = None,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> StructuredDocument:
        """Extract a document and classify resource failures at the API boundary."""
        if cancellation_token is not None and not isinstance(cancellation_token, CancellationToken):
            raise TypeError("cancellation_token must be a CancellationToken or None")
        token = cancellation_token or CancellationToken()
        self._active_cancellation_token = token
        try:
            return self._extract_impl(
                path,
                password=password,
                progress_callback=progress_callback,
                cancellation_token=token,
            )
        except FatalExtractionError:
            raise
        except Exception as exc:
            raise_if_resource_exhausted(
                exc,
                stage="document_extract",
                details=_process_memory_snapshot(),
            )
            raise
        finally:
            self._close_page_coordinator()
            self._active_cancellation_token = None

    def _extract_impl(
        self,
        path: str | Path,
        password: str | None = None,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        cancellation_token: CancellationToken,
    ) -> StructuredDocument:
        if self._closed:
            raise RuntimeError("PdfTextExtractor is closed")
        cancellation_token.check()
        self._close_page_coordinator()
        self._extraction_id = uuid4().hex
        self._document_id = uuid4().hex
        pages = []
        document_start = time.perf_counter()
        memory_start = _process_memory_snapshot()
        document_warnings: list[str] = []
        source = PdfiumNativeEvidenceSource(path, config=self.config, password=password)
        open_start = time.perf_counter()
        with source:
            open_pdf_ms = (time.perf_counter() - open_start) * 1000
            context = source.open() if getattr(source, "_context", None) is None else source._context
            if context is None:
                raise RuntimeError("source.open() did not return a DocumentContext")
            page_indices = _selected_page_indices(self.config.page_indices, context.page_count)
            for page_index in page_indices:
                cancellation_token.check()
                start = time.perf_counter()
                if progress_callback is not None:
                    progress_callback(len(pages) + 1, len(page_indices))
                page_memory_start = _process_memory_snapshot()
                ocr_render_scale = self.config.effective_ocr_render_scale()
                source_metrics_start = source.metrics_snapshot()
                timings: dict[str, float] = {}
                native_start = time.perf_counter()
                try:
                    native_page = source.extract_page(page_index)
                except FatalExtractionError:
                    raise
                except Exception as exc:
                    raise_if_resource_exhausted(
                        exc,
                        page_index=page_index,
                        stage="native_extract",
                        details=_process_memory_snapshot(),
                    )
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    pages.append(_failed_page(page_index, exc, elapsed_ms))
                    continue
                cancellation_token.check()
                timings["native_extract_ms"] = (time.perf_counter() - native_start) * 1000
                warnings: list[str] = []
                partial_reasons: list[str] = []
                render_limit_diagnostics: dict[str, dict[str, Any]] = {}

                def safe_render_scale(stage: str, requested_scale: float) -> float:
                    effective = _safe_complexity_scale(
                        native_page.bbox.width,
                        native_page.bbox.height,
                        self.config.security_limits.max_render_pixels,
                        requested_scale,
                        self.config.security_limits.max_render_bytes,
                    )
                    if effective < requested_scale:
                        render_limit_diagnostics[stage] = {
                            "reason": "max_render_pixels",
                            "maximum_pixels": self.config.security_limits.max_render_pixels,
                            "maximum_rgb_bytes": self.config.security_limits.max_render_bytes,
                            "requested_scale": requested_scale,
                            "effective_scale": effective,
                            "resulting_width_pixels": math.ceil(native_page.bbox.width * effective),
                            "resulting_height_pixels": math.ceil(native_page.bbox.height * effective),
                        }
                    return effective

                rendered_page = None
                render_start = time.perf_counter()
                if (
                    self.config.normalized_mode() not in (ExtractionMode.NATIVE, ExtractionMode.FAST)
                    and self.config.enable_complexity_render
                ):
                    try:
                        rendered_page = source.render_page(
                            page_index,
                            scale=safe_render_scale("complexity", self.config.complexity_render_scale),
                        )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="complexity_render",
                            details=_process_memory_snapshot(),
                        )
                        warnings.append(f"Complexity render unavailable: {type(exc).__name__}: {exc}")
                timings["render_lowres_ms"] = (time.perf_counter() - render_start) * 1000
                complexity_start = time.perf_counter()
                complexity = self.complexity_analyzer.analyze(native_page, rendered_page)
                timings["complexity_ms"] = (time.perf_counter() - complexity_start) * 1000
                visibility_start = time.perf_counter()

                # Exclude glyphs outside the visible page and characters covered
                # by solid, visually uniform objects. The pixel check rejects
                # banners/cells that contain readable reversed text.
                visible_characters, outside_page_character_count = characters_inside_page(
                    native_page.characters,
                    native_page.bbox,
                )
                opaque_occlusion_boxes: list[BBox] = []
                redacted_character_count = 0

                inkless_character_count = 0

                if self.config.enable_experimental_occlusion_redaction and rendered_page is not None:
                    # Paint-order check per glyph, in the modes that render:
                    # covered, same-color, clipped or invisible glyphs leave no
                    # ink, while text drawn over a solid fill remains visible.
                    hidden_glyphs = source.hidden_glyphs(
                        page_index,
                        visible_characters,
                        native_page.bbox,
                        native_page.objects.rotation,
                    ) if visible_characters else frozenset()
                    if hidden_glyphs is not None:
                        visible_characters, inkless_character_count = characters_without_ink(
                            visible_characters,
                            hidden_glyphs,
                        )
                        redacted_character_count = inkless_character_count
                    else:
                        # Pixel heuristic for PDFium builds that cannot attribute
                        # glyphs to text objects (it mistakes light text on
                        # light fills for redaction, hence only a fallback).
                        opaque_occlusion_boxes = detect_opaque_occlusion_boxes(
                            native_page,
                            rendered_page,
                        )
                        visible_characters, redacted_character_count = characters_occluded(
                            visible_characters,
                            opaque_occlusion_boxes,
                        )

                timings["visibility_ms"] = (
                    time.perf_counter() - visibility_start
                ) * 1000

                # The PDF textpage contains the original native text,
                # including characters that were just suppressed. Never
                # reconcile against that unfiltered textpage; after any
                # suppression, rebuild it from the kept characters (their
                # concatenation is the textpage text), so spacing recovery
                # still works but rejected glyphs cannot come back.
                textpage_reconciliation_disabled_for_redaction = (
                    (redacted_character_count > 0 or outside_page_character_count > 0)
                )

                # Every later consumer (layout, tables, figures, fusion) sees
                # only visible glyphs; rejected evidence cannot be resurrected.
                # The unfiltered evidence stays available as diagnostics.
                source_native_page = native_page
                if textpage_reconciliation_disabled_for_redaction:
                    kept = {character.char_index for character in visible_characters}
                    native_page = replace(
                        native_page,
                        characters=tuple(visible_characters),
                        extracted_text="".join(
                            character.text for character in source_native_page.characters
                            if character.char_index in kept or character.text.isspace()
                        ),
                    )
                textpage_for_reconciliation = native_page.extracted_text

                # Content drawn upside down (shown upright by /Rotate 180) is
                # analysed in the frame where its glyphs stand upright, so
                # layout and reading order do not run from the last line up.
                reading_frame_turned = upside_down(visible_characters)
                if reading_frame_turned:
                    native_page, visible_characters = to_upright_frame(
                        native_page, tuple(visible_characters),
                    )

                reconstruct_start = time.perf_counter()
                native_lines = reconstruct_native_lines(
                    tuple(visible_characters),
                    textpage_for_reconciliation,
                )
                timings["native_reconstruct_ms"] = (
                    time.perf_counter() - reconstruct_start
                ) * 1000

                # Layout and local quality evidence must exist before OCR is
                # selected. This is the order defined by the MVP pipeline.
                layout_start = time.perf_counter()
                regions: list[LayoutRegion] = []
                warnings.extend(
                    _layout_regions_if_requested(
                        config=self.config,
                        complexity=complexity,
                        page=native_page,
                        lines=native_lines,
                        image=rendered_page,
                        layout_engine=self.layout_engine,
                        output=regions,
                    )
                )
                timings["layout_ms"] = (time.perf_counter() - layout_start) * 1000
                if not regions:
                    regions = [
                        full_page_text_region(page_index, native_page.bbox, native_lines, complexity)
                    ]
                recovery_plan = assess_region_recovery(
                    regions,
                    complexity,
                    rendered_page,
                    native_page.bbox,
                    native_page.objects.rotation,
                )
                region_quality_facts = {
                    region.region_id: {
                        "kind": region.kind.value,
                        "decision": region.quality.decision.value,
                        "reasons": list(region.quality.reasons),
                        "confidence": region.quality.confidence,
                    }
                    for region in regions
                }

                ocr_tokens: list[OcrToken] = []
                page_ocr_failed = False
                blank_page_ocr = False
                ocr_lines: list[TextLine] = []
                unmatched_ocr_lines: list[TextLine] = []
                unmatched_ocr_tokens: list[OcrToken] = []
                fusion = None
                local_fusion_decisions: list[dict[str, Any]] = []
                ocr_image = None
                ocr_passes_total = None
                ocr_batches_total = None
                ocr_table_tokens = 0
                ocr_figure_tokens = 0
                table_validation_facts: list[dict[str, Any]] = []
                table_construction_facts: list[dict[str, Any]] = []
                table_source_provenance: list[dict[str, Any]] = []
                table_prefix_facts = _table_prefix_diagnostics(regions)
                ocr_region_stats: dict[str, dict[str, Any]] = {}
                ocr_targeted_stats: dict[str, dict[str, Any]] = {}
                ocr_targeted_passes = 0
                ocr_targeted_batches = 0
                ocr_attempt_errors: list[str] = []
                ocr_tile_stats: dict[str, int] = {"tiles": 0, "tile_tokens": 0, "fusion_conflicts": 0}
                embedded_image_tokens = 0
                mode = self.config.normalized_mode()
                ocr_available_by_mode = _ocr_enabled(self.config)
                promotion_reasons = list(recovery_plan.reasons)
                raster_primary = _is_raster_primary_candidate(complexity)
                if raster_primary and "raster_primary_candidate" not in promotion_reasons:
                    promotion_reasons.append("raster_primary_candidate")
                page_ocr_requested = mode == ExtractionMode.OCR or (
                    ocr_available_by_mode
                    and (recovery_plan.promote_page_ocr or raster_primary)
                )
                if (
                    page_ocr_requested
                    and mode != ExtractionMode.OCR
                    and ComplexityReason.INVISIBLE_TEXT in complexity.reasons
                    and not native_page.objects.images
                    and native_lines
                ):
                    # The visible native stream has already been recovered;
                    # a second OCR pass would reintroduce duplicate text on
                    # pages whose visible native stream was already recovered.
                    page_ocr_requested = False
                    promotion_reasons.append("page_ocr_suppressed_visible_native_stream")
                selected_regions = [
                    region
                    for region in regions
                    if region.region_id in recovery_plan.region_ids
                    and region.quality.decision
                    in {RegionDecision.MERGE_OCR, RegionDecision.OCR_REGION}
                ]
                region_ocr_requested = bool(
                    ocr_available_by_mode and selected_regions and not page_ocr_requested
                )
                ocr_requested = page_ocr_requested or region_ocr_requested
                # R71: compute the exact list of unresolved images up-front so
                # the decision and execution paths cannot diverge.
                _figures_pending_ocr = (
                    _figures_requiring_ocr(native_page, selected_regions)
                    if ocr_available_by_mode and native_page.objects.images and not page_ocr_requested
                    else []
                )
                figure_ocr_requested = bool(_figures_pending_ocr)
                if ocr_requested or figure_ocr_requested:
                    try:
                        self._ensure_page_ocr(source, native_page, page_index)
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(exc, page_index=page_index, stage="ocr_backend_init", details=_process_memory_snapshot())
                        warnings.append(f"OCR backend unavailable: {type(exc).__name__}: {exc}")
                        partial_reasons.append("ocr_backend_unavailable")
                        page_ocr_requested = region_ocr_requested = figure_ocr_requested = False
                        ocr_requested = False
                figure_ocr_bindings: list[tuple[BBox, list[TextLine], list[OcrToken]]] = []
                if ocr_requested:
                    ocr_image = rendered_page
                    ocr_render_start = time.perf_counter()
                    try:
                        if (
                            ocr_image is None
                            or ocr_render_scale > self.config.complexity_render_scale
                        ):
                            ocr_image = source.render_page(
                                page_index,
                                scale=safe_render_scale("ocr", ocr_render_scale),
                            )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="ocr_render",
                            details=_process_memory_snapshot(),
                        )
                        timings["render_ocr_ms"] = (
                            time.perf_counter() - ocr_render_start
                        ) * 1000
                        warnings.append(
                            f"OCR render unavailable: {type(exc).__name__}: {exc}"
                            + (
                                f"; regions={[region.region_id for region in selected_regions]}"
                                if region_ocr_requested else ""
                            )
                        )
                        if page_ocr_requested or mode == ExtractionMode.OCR:
                            partial_reasons.append("page_ocr_unavailable")
                        elif region_ocr_requested:
                            partial_reasons.append("ocr_region_recovery_unavailable")
                    else:
                        timings["render_ocr_ms"] = (
                            time.perf_counter() - ocr_render_start
                        ) * 1000
                        if self.ocr_engine is None:
                            warnings.append("OCR requested but no OCR engine was configured")
                            if mode == ExtractionMode.OCR or page_ocr_requested:
                                partial_reasons.append("page_ocr_unavailable")
                        else:
                            ocr_start = time.perf_counter()
                            try:
                                if page_ocr_requested:
                                    recognize_page = self.ocr_engine.recognize_page
                                    quality_policy = effective_ocr_quality_policy(self.config).value
                                    # Compatibility for user-injected legacy engines is
                                    # selected by signature, never by catching an internal TypeError.
                                    try:
                                        parameters = inspect.signature(recognize_page).parameters
                                    except (TypeError, ValueError):
                                        parameters = {}
                                    accepts_quality_policy = (
                                        "quality_policy" in parameters
                                        or any(
                                            p.kind is inspect.Parameter.VAR_KEYWORD
                                            for p in parameters.values()
                                        )
                                    )
                                    accepts_page_rotation = "page_rotation" in parameters
                                    page_kwargs = (
                                        {"quality_policy": quality_policy}
                                        if accepts_quality_policy
                                        else {}
                                    )
                                    if accepts_page_rotation:
                                        page_kwargs["page_rotation"] = native_page.objects.rotation
                                    ocr_tokens = recognize_page(
                                        ocr_image,
                                        page_index,
                                        native_page.bbox,
                                        **page_kwargs,
                                    )
                                    if self.config.ocr_tiling and _backend_supports(self.ocr_engine, "recognition"):
                                        ocr_tokens, ocr_tile_stats = recognize_page_with_tiles(
                                            self.ocr_engine,
                                            ocr_image,
                                            page_index,
                                            native_page.bbox,
                                            ocr_tokens,
                                            quality_policy=quality_policy,
                                            rows=self.config.ocr_tile_rows,
                                            columns=self.config.ocr_tile_columns,
                                            overlap=self.config.ocr_tile_overlap,
                                            page_rotation=native_page.objects.rotation,
                                        )
                                    operation_metrics = self.ocr_engine.operation_metrics
                                    ocr_passes_total = operation_metrics.get("passes")
                                    ocr_batches_total = operation_metrics.get("batches")
                                    ocr_attempt_errors = list(operation_metrics.get("attempt_errors", []))
                                else:
                                    (
                                        ocr_tokens,
                                        ocr_passes_total,
                                        ocr_batches_total,
                                        ocr_region_stats,
                                    ) = _recover_selected_regions(
                                        engine=self.ocr_engine,
                                        page_image=ocr_image,
                                        page_index=page_index,
                                        page_bbox=native_page.bbox,
                                        regions=selected_regions,
                                        quality_variants=self.config.ocr_quality_variants,
                                        quality_policy=effective_ocr_quality_policy(self.config).value,
                                        page_rotation=native_page.objects.rotation,
                                    )
                                timings["ocr_ms"] = (
                                    time.perf_counter() - ocr_start
                                ) * 1000
                            except FatalExtractionError:
                                raise
                            except Exception as exc:
                                raise_if_resource_exhausted(
                                    exc,
                                    page_index=page_index,
                                    stage="page_ocr",
                                    details=_process_memory_snapshot(),
                                )
                                timings["ocr_ms"] = (
                                    time.perf_counter() - ocr_start
                                ) * 1000
                                page_ocr_failed = True
                                warnings.append(
                                    "OCR backend failed before producing a usable result: "
                                    f"{type(exc).__name__}: {exc}"
                                )
                                if mode == ExtractionMode.OCR or page_ocr_requested:
                                    partial_reasons.append("page_ocr_unavailable")
                                elif region_ocr_requested:
                                    partial_reasons.append("ocr_region_recovery_unavailable")

                            if region_ocr_requested:
                                for region in selected_regions:
                                    stat = ocr_region_stats.get(region.region_id, {})
                                    if stat.get("ocr_failed"):
                                        warnings.append(
                                            "OCR region recovery produced no usable tokens "
                                            f"for {region.region_id}"
                                        )
                                        partial_reasons.append("ocr_region_recovery_unavailable")
                            for attempt_error in ocr_attempt_errors:
                                warnings.append(
                                    f"OCR optional attempt unavailable: {attempt_error}"
                                )

                if figure_ocr_requested and ocr_image is None:
                    ocr_image = rendered_page
                    if (
                        ocr_image is None
                        or ocr_render_scale > self.config.complexity_render_scale
                    ):
                        try:
                            ocr_image = source.render_page(
                                page_index,
                            scale=safe_render_scale("ocr", ocr_render_scale),
                            )
                        except FatalExtractionError:
                            raise
                        except Exception as exc:
                            raise_if_resource_exhausted(
                                exc,
                                page_index=page_index,
                                stage="figure_ocr_render",
                                details=_process_memory_snapshot(),
                            )
                            warnings.append(
                                f"Figure OCR render unavailable: {type(exc).__name__}: {exc}"
                            )
                            if figure_ocr_requested:
                                partial_reasons.append("figure_ocr_unavailable")

                if figure_ocr_requested and self.ocr_engine is not None and ocr_image is not None:
                    figure_start = time.perf_counter()
                    figure_refinements = _refine_figure_ocr(
                        engine=self.ocr_engine,
                        page_image=ocr_image,
                        page=native_page,
                        page_index=page_index,
                        native_lines=native_lines,
                        quality_policy=effective_ocr_quality_policy(self.config).value,
                        page_rotation=native_page.objects.rotation,
                        warnings=warnings,
                        images=_figures_pending_ocr,
                        integration_tokens=ocr_tokens,
                        integration_regions=regions,
                    )
                    if any(item.startswith("figure_ocr_failed:") for item in warnings):
                        partial_reasons.append("figure_ocr_unavailable")
                    if figure_refinements:
                        figure_integrator = NativeOCRIntegrator()
                        for (
                            box,
                            all_lines,
                            all_tokens,
                            figure_lines,
                            figure_tokens,
                            passes,
                            batches,
                        ) in figure_refinements:
                            if isinstance(self.ocr_engine, OCRPageFacade):
                                figure_applied = bool(all_tokens)
                            else:
                                ocr_tokens, figure_applied = figure_integrator.apply_figure_refinement(
                                    tokens=ocr_tokens,
                                    box=box,
                                    replacement_tokens=all_tokens,
                                    regions=regions,
                                    figure_lines=figure_lines,
                                    figure_tokens=figure_tokens,
                                )
                            if figure_applied:
                                figure_ocr_bindings.append((box, figure_lines, figure_tokens))
                                ocr_figure_tokens += len(all_tokens)
                            ocr_passes_total = (ocr_passes_total or 0) + passes
                            ocr_batches_total = (ocr_batches_total or 0) + batches
                        ocr_tokens = _deduplicate_region_ocr_tokens(ocr_tokens)
                        timings["ocr_figure_ms"] = (
                            time.perf_counter() - figure_start
                        ) * 1000

                if (
                    page_ocr_requested
                    and self.ocr_engine is not None
                    and ocr_image is not None
                    and not ocr_tokens
                    and not page_ocr_failed
                ):
                    blank_page_ocr = _is_visually_blank(ocr_image)
                    warnings.append(
                        "OCR produced no tokens for a visually blank page"
                        if blank_page_ocr else
                        "OCR completed successfully but produced no usable tokens for an OCR-primary page"
                    )
                    if not blank_page_ocr:
                        partial_reasons.append("ocr_no_text")

                if region_ocr_requested and not isinstance(self.ocr_engine, OCRPageFacade):
                    for region in selected_regions:
                        region.ocr_tokens = [
                            token for token in ocr_tokens if _line_in_box(token, region.bbox)
                        ]

                if page_ocr_requested and ocr_tokens and ocr_image is not None:
                    if self.config.max_quality:
                        try:
                            original_candidate = _recognize_dominant_embedded_image(
                                source=source,
                                engine=self.ocr_engine,
                                page=native_page,
                                page_image=ocr_image,
                                page_index=page_index,
                                quality_policy=effective_ocr_quality_policy(self.config).value,
                            )
                            if original_candidate:
                                embedded_image_tokens = len(original_candidate)
                                score = self.ocr_engine.token_refinement_policy.token_score
                                ocr_tokens = list(OcrCandidateFusionEngine(self.ocr_engine.fusion_policy).fuse([
                                    OcrCandidateResult("full-page", "full-page", tuple(ocr_tokens), score(ocr_tokens), self.ocr_engine.identity.engine),
                                    OcrCandidateResult("embedded-image", "embedded-image", tuple(original_candidate), score(original_candidate), self.ocr_engine.identity.engine),
                                ]).tokens)
                        except FatalExtractionError:
                            raise
                        except Exception as exc:
                            raise_if_resource_exhausted(
                                exc, page_index=page_index, stage="embedded_image_ocr",
                                details=_process_memory_snapshot(),
                            )
                            warnings.append(f"Embedded image OCR candidate unavailable: {type(exc).__name__}: {exc}")
                    weak_start = time.perf_counter()
                    try:
                        (
                            ocr_tokens,
                            ocr_targeted_passes,
                            ocr_targeted_batches,
                            ocr_targeted_stats,
                        ) = _recover_weak_ocr_regions(
                            engine=self.ocr_engine,
                            page_image=ocr_image,
                            page_index=page_index,
                            page_bbox=native_page.bbox,
                            tokens=ocr_tokens,
                            lines=reconstruct_ocr_lines(
                                ocr_tokens,
                                page_index,
                                native_page.bbox,
                                page_rotation=native_page.objects.rotation,
                            ),
                            quality_policy=effective_ocr_quality_policy(self.config).value,
                            thresholds=effective_ocr_quality_thresholds(self.config),
                            page_rotation=native_page.objects.rotation,
                            enforce_policy=True,
                        )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="ocr_targeted_refinement",
                            details=_process_memory_snapshot(),
                        )
                        warnings.append(
                            f"Targeted OCR recovery unavailable: {type(exc).__name__}: {exc}"
                        )
                    timings["ocr_targeted_refinement_ms"] = (
                        time.perf_counter() - weak_start
                    ) * 1000

                critical_data_refinements = 0
                footnote_refinements = 0
                if (
                    self.config.enable_critical_data_refinement
                    and page_ocr_requested
                    and ocr_tokens
                    and ocr_image is not None
                    and self.ocr_engine is not None
                ):
                    critical_start = time.perf_counter()
                    ocr_tokens, critical_data_refinements = _refine_critical_data_tokens(
                        engine=self.ocr_engine,
                        page_image=ocr_image,
                        page_index=page_index,
                        page_bbox=native_page.bbox,
                        tokens=ocr_tokens,
                        lines=[
                            *native_lines,
                            *reconstruct_ocr_lines(ocr_tokens, page_index, native_page.bbox, page_rotation=native_page.objects.rotation),
                        ],
                        quality_policy=effective_ocr_quality_policy(self.config).value,
                        page_rotation=native_page.objects.rotation,
                        region_renderer=lambda bbox, scale: source.render_region(
                            page_index, bbox, scale, native_page.bbox, native_page.objects.rotation
                        ),
                        base_scale=ocr_render_scale,
                    )
                    timings["critical_data_refinement_ms"] = (
                        time.perf_counter() - critical_start
                    ) * 1000

                if (
                    (self.config.max_quality or effective_ocr_quality_policy(self.config).value == "exhaustive")
                    and page_ocr_requested
                    and ocr_tokens
                    and ocr_image is not None
                    and self.ocr_engine is not None
                ):
                    footnote_start = time.perf_counter()
                    ocr_tokens, footnote_refinements = _refine_small_footnote_tokens(
                        engine=self.ocr_engine,
                        page_index=page_index,
                        page_bbox=native_page.bbox,
                        tokens=ocr_tokens,
                        quality_policy=effective_ocr_quality_policy(self.config).value,
                        region_renderer=lambda bbox, scale: source.render_region(
                            page_index, bbox, scale, native_page.bbox, native_page.objects.rotation
                        ),
                        base_scale=ocr_render_scale,
                        page_rotation=native_page.objects.rotation,
                    )
                    timings["ocr_footnote_refinement_ms"] = (
                        time.perf_counter() - footnote_start
                    ) * 1000

                # D3: resync region.ocr_tokens after all refinements so that
                # downstream table-cell merge and layout always see final tokens.
                if region_ocr_requested and ocr_tokens:
                    for region in selected_regions:
                        region.ocr_tokens = [
                            token for token in ocr_tokens if _line_in_box(token, region.bbox)
                        ]

                # Recompute alignment after refinements so diagnostics and
                # supplemental text describe the final OCR evidence.
                if ocr_tokens:
                    ocr_lines = reconstruct_ocr_lines(
                        ocr_tokens,
                        page_index,
                        native_page.bbox,
                        page_rotation=native_page.objects.rotation,
                    )
                    fusion_start = time.perf_counter()
                    fusion = fuse_native_and_ocr(native_lines, ocr_tokens)
                    local_fusion_decisions = _local_fusion_decisions(
                        regions, ocr_tokens
                    )
                    unmatched_ocr_tokens = list(fusion.unmatched_ocr_tokens)
                    unmatched_ocr_lines = reconstruct_ocr_lines(
                        unmatched_ocr_tokens,
                        page_index,
                        native_page.bbox,
                        page_rotation=native_page.objects.rotation,
                    )
                    timings["fusion_ms"] = (
                        time.perf_counter() - fusion_start
                    ) * 1000

                # H1: page-level OCR must not replace strong native text.
                # On hybrid pages (native + raster image), the unmatched OCR
                # tokens are incorporated by the hybrid assembly path instead.
                use_ocr_as_primary = _should_use_page_ocr_as_primary(
                    native_lines=native_lines,
                    ocr_lines=ocr_lines,
                    page_ocr_requested=page_ocr_requested,
                    mode=mode,
                )
                if use_ocr_as_primary:
                    ocr_region = full_page_text_region(
                        page_index,
                        native_page.bbox,
                        [],
                        complexity,
                    )
                    ocr_region.region_id = f"page-{page_index + 1}:ocr-primary"
                    ocr_region.ocr_lines = list(ocr_lines)
                    ocr_region.ocr_tokens = ocr_tokens
                    regions = [ocr_region]
                table_start = time.perf_counter()
                try:
                    tables = detect_tables_native(
                        native_page,
                        regions,
                        # The filtered view's textpage holds visible glyphs only.
                        allow_textpage_recovery=True,
                    ) if self.config.enable_tables else []
                    _clear_occluded_table_cells(tables, opaque_occlusion_boxes)
                except FatalExtractionError:
                    raise
                except Exception as exc:
                    raise_if_resource_exhausted(
                        exc,
                        page_index=page_index,
                        stage="native_table_detection",
                        details=_process_memory_snapshot(),
                    )
                    tables = []
                    warnings.append(
                        f"Native table detection unavailable: {type(exc).__name__}: {exc}"
                    )
                    partial_reasons.append("native_table_detection_unavailable")
                table_ocr_overrides: dict[str, tuple[list[Any], list[OcrToken]]] = {}
                # VQ-09: Run visual detection even when native tables exist, but only
                # in image areas not already covered by a native table bbox.  This
                # allows hybrid pages (e.g. V5-P019/P024) to have both a native-ruled
                # table and a separate raster table on the same page.
                # Thin ruling lines disappear in the complexity thumbnail.
                # Reuse the OCR raster already rendered for authorized work;
                # this neither schedules OCR nor renders a native-only page.
                table_structure_image = ocr_image if ocr_image is not None else rendered_page
                _should_run_visual = (
                    self.config.enable_tables
                    and table_structure_image is not None
                    and _has_uncovered_image_area(native_page, tables)
                )
                # VQ-22: detect multiple visual tables — one attempt per uncovered
                # image region. The full-page call is preserved as the first attempt
                # (covers single-table pages and pages with one raster table);
                # subsequent attempts crop the page image to each image bbox that is
                # not already covered by a native or previously detected visual table.
                _table_structure_engine = (
                    self.ocr_engine.table_structure_engine
                    if isinstance(self.ocr_engine, OCRPageFacade) else None
                )
                _visual_candidates: list = []  # StructuredTable results
                if _should_run_visual:
                    structure_tokens = _table_structure_tokens(
                        ocr_lines, regions, page_index, native_page.bbox,
                        native_page.objects.rotation,
                    )
                    try:
                        visual_table = detect_visual_table(
                            native_page,
                            table_structure_image,
                            tokens=structure_tokens,
                            structure_engine=_table_structure_engine,
                            page_rotation=native_page.objects.rotation,
                        )
                        if visual_table is not None:
                            _visual_candidates.append(visual_table)
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="visual_table_detection",
                            details=_process_memory_snapshot(),
                        )
                        warnings.append(
                            f"Visual table detection unavailable: {type(exc).__name__}: {exc}"
                        )
                        if self.config.enable_tables or mode in {ExtractionMode.BALANCED, ExtractionMode.OCR}:
                            partial_reasons.append("visual_table_detection_unavailable")

                # VQ-22: for each image region not yet explained by any detected table,
                # try a crop-based visual detection to find additional raster tables.
                if _should_run_visual and table_structure_image is not None:
                    all_accepted_bboxes = [
                        f.bbox
                        for t in tables + _visual_candidates
                        for f in t.page_fragments
                        if f.page_index == page_index and f.bbox is not None
                    ]
                    for img_obj in native_page.objects.images:
                        img_box = img_obj.bbox
                        if img_box is None:
                            continue
                        # Skip if already explained by an existing table.
                        if any(img_box.overlap_ratio(ab) >= 0.50 for ab in all_accepted_bboxes):
                            continue
                        # Discover structure in the composed raster with its
                        # actual crop frame; OCR still uses authorized services.
                        try:
                            extra_id = f"page-{page_index + 1}:visual-grid-{len(_visual_candidates) + 1}"
                            extra = detect_visual_table_in_region(
                                native_page,
                                table_structure_image,
                                img_box,
                                table_id=extra_id,
                                tokens=structure_tokens,
                                structure_engine=_table_structure_engine,
                            )
                            if extra is not None:
                                _visual_candidates.append(extra)
                                all_accepted_bboxes.append(
                                    extra.page_fragments[0].bbox
                                    if extra.page_fragments else None
                                )
                        except FatalExtractionError:
                            raise
                        except Exception as exc:
                            raise_if_resource_exhausted(
                                exc, page_index=page_index, stage="visual_table_region_detection",
                                details=_process_memory_snapshot(),
                            )
                            warnings.append(
                                f"Visual table region detection unavailable: {type(exc).__name__}: {exc}"
                            )
                            continue

                if _should_run_visual:
                    from .tables.ocr_tracks import detect_ocr_track_tables

                    try:
                        _visual_candidates.extend(detect_ocr_track_tables(
                            native_page, regions, table_structure_image,
                            tables + _visual_candidates,
                            structure_engine=_table_structure_engine,
                        ))
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(exc, page_index=page_index, stage="ocr_table_structure")
                        warnings.append(f"OCR table structure unavailable: {type(exc).__name__}: {exc}")
                        partial_reasons.append("ocr_table_structure_unavailable")

                for visual_table in _visual_candidates:
                    # Discard if the detected visual grid overlaps substantially
                    # with an already-accepted native table — it is the same table.
                    visual_bbox = (
                        visual_table.page_fragments[0].bbox
                        if visual_table.page_fragments
                        else None
                    )
                    keep = True
                    if visual_bbox is not None:
                        for t in tables:
                            native_bbox = next(
                                (f.bbox for f in t.page_fragments if f.page_index == page_index and f.bbox),
                                None,
                            )
                            if native_bbox and visual_bbox.overlap_ratio(native_bbox) >= 0.5:
                                keep = False
                                break
                    if not keep:
                        continue
                    try:
                        refined = _refine_visual_table_ocr(
                            engine=self.ocr_engine,
                            page_image=table_structure_image,
                            page=native_page,
                            page_index=page_index,
                            table=visual_table,
                            native_lines=native_lines,
                            quality_policy=effective_ocr_quality_policy(self.config).value,
                            page_rotation=native_page.objects.rotation,
                        )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="visual_table_ocr_refinement",
                            details=_process_memory_snapshot(),
                        )
                        refined = None
                        warnings.append(
                            f"Visual table OCR refinement unavailable: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    if refined is not None:
                        (
                            visual_table,
                            refined_lines,
                            refined_tokens,
                            table_passes,
                            table_batches,
                        ) = refined
                        if refined_lines:
                            table_ocr_overrides[visual_table.table_id] = (
                                refined_lines,
                                refined_tokens,
                            )
                        ocr_table_tokens = len(refined_tokens)
                        ocr_passes_total = (ocr_passes_total or 0) + table_passes
                        ocr_batches_total = (ocr_batches_total or 0) + table_batches
                    tables = tables + [visual_table]
                empty_native_grids = [table for table in tables
                                      if table.method != TableMethod.VISUAL_MODEL
                                      and not any(cell.text.strip() for cell in table.cells)]
                consumed_table_ocr = _merge_ocr_region_tokens_into_table_cells(
                    tables=tables,
                    regions=regions,
                    page_index=page_index,
                )
                if consumed_table_ocr:
                    if table_structure_image is not None and empty_native_grids:
                        from .ocr.image_views import canonicalize_page_image
                        from .tables.headers import infer_visual_header_rows

                        canonical = canonicalize_page_image(table_structure_image, native_page.objects.rotation)
                        try:
                            for table in empty_native_grids:
                                table.header_rows = infer_visual_header_rows(table.cells, canonical, native_page.bbox)
                        finally:
                            if canonical is not table_structure_image and hasattr(canonical, "close"):
                                canonical.close()
                    unmatched_ocr_tokens = [
                        token
                        for token in unmatched_ocr_tokens
                        if id(token) not in consumed_table_ocr
                    ]
                    unmatched_ocr_lines = reconstruct_ocr_lines(
                        unmatched_ocr_tokens,
                        page_index,
                        native_page.bbox,
                        page_rotation=native_page.objects.rotation,
                    )
                    ocr_table_tokens += len(consumed_table_ocr)
                table_cell_refinements = 0
                _automatic_cells = (
                    isinstance(self.ocr_engine, OCRPageFacade)
                    and getattr(self.ocr_engine.cell_recovery_policy, "automatic_recovery", False)
                    and effective_ocr_quality_policy(self.config).value != "baseline"
                )
                if (self.config.enable_table_cell_ocr or _automatic_cells) and _ocr_enabled(self.config) and tables:
                    try:
                        self._ensure_page_ocr(source, native_page, page_index)
                        cell_image = ocr_image if ocr_image is not None else rendered_page
                        if cell_image is None and not isinstance(self.ocr_engine, OCRPageFacade):
                            cell_image = source.render_page(
                                page_index,
                                scale=safe_render_scale("table_cell_ocr", ocr_render_scale),
                            )
                        if self.ocr_engine is not None and (
                            isinstance(self.ocr_engine, OCRPageFacade) or cell_image is not None
                        ):
                            table_cell_refinements = _refine_table_cells_ocr(
                                engine=self.ocr_engine,
                                page_image=cell_image,
                                page_index=page_index,
                                page_bbox=native_page.bbox,
                                tables=tables,
                                quality_policy=effective_ocr_quality_policy(self.config).value,
                                region_renderer=lambda bbox, scale: source.render_region(
                                    page_index, bbox, scale, native_page.bbox, native_page.objects.rotation
                                ),
                                base_scale=ocr_render_scale,
                                page_rotation=native_page.objects.rotation,
                                automatic=not self.config.enable_table_cell_ocr,
                            )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        warnings.append(f"Table cell OCR refinement unavailable: {type(exc).__name__}: {exc}")
                        raise_if_resource_exhausted(exc, page_index=page_index, stage="table_cell_ocr", details=_process_memory_snapshot())
                        partial_reasons.append("table_cell_ocr_unavailable")
                try:
                    (
                        tables,
                        table_validation_facts,
                        table_construction_facts,
                        table_source_provenance,
                    ) = _validate_detected_tables(
                        tables=tables,
                        regions=regions,
                        extra_lines=ocr_lines,
                        table_ocr_overrides=table_ocr_overrides,
                        warnings=warnings,
                    )
                    if use_ocr_as_primary and any(table.method == TableMethod.VISUAL_MODEL for table in tables):
                        # In OCR-primary mode the page region would otherwise
                        # bypass table-aware reading order entirely.
                        regions = []
                        _append_hybrid_ocr_regions(
                            regions=regions,
                            tables=tables,
                            unmatched_lines=ocr_lines,
                            unmatched_tokens=ocr_tokens,
                            page_index=page_index,
                            page_bbox=native_page.bbox,
                            complexity=complexity,
                            table_ocr_overrides=table_ocr_overrides,
                            figure_ocr_bindings=figure_ocr_bindings,
                            preserve_cell_refinements=_automatic_cells,
                        )
                    elif not use_ocr_as_primary and (unmatched_ocr_lines or figure_ocr_bindings):
                        _append_hybrid_ocr_regions(
                            regions=regions,
                            tables=tables,
                            unmatched_lines=unmatched_ocr_lines,
                            unmatched_tokens=unmatched_ocr_tokens,
                            page_index=page_index,
                            page_bbox=native_page.bbox,
                            complexity=complexity,
                            table_ocr_overrides=table_ocr_overrides,
                            figure_ocr_bindings=figure_ocr_bindings,
                            preserve_cell_refinements=_automatic_cells,
                        )
                    if ocr_tokens:
                        _line_order_policy = (
                            self.ocr_engine.line_order_policy
                            if isinstance(self.ocr_engine, OCRPageFacade) else None
                        )
                        regions = reconstruct_ocr_layout(
                            regions, native_page.bbox, line_order_policy=_line_order_policy,
                        )
                except FatalExtractionError:
                    raise
                except Exception as exc:
                    raise_if_resource_exhausted(
                        exc, page_index=page_index, stage="layout_assembly",
                        details=_process_memory_snapshot(),
                    )
                    warnings.append(f"Layout assembly failed: {type(exc).__name__}: {exc}")
                    partial_reasons.append("layout_assembly_failed")
                timings["table_ms"] = (time.perf_counter() - table_start) * 1000
                elapsed_ms = (time.perf_counter() - start) * 1000
                page_memory_end = _process_memory_snapshot()
                source_metrics_end = source.metrics_snapshot()
                actual_strategy = complexity.recommended_strategy
                if page_ocr_requested:
                    actual_strategy = PageStrategy.OCR_CANDIDATE
                elif (region_ocr_requested or figure_ocr_requested) and ocr_tokens:
                    actual_strategy = PageStrategy.MIXED
                pipeline_work = (
                    self._page_coordinator.diagnostics()
                    if self._page_coordinator is not None
                    and self._page_coordinator.page_index == page_index
                    else None
                )
                work_summary = pipeline_work["summary"] if pipeline_work is not None else None
                cell_tokens_added = work_summary["cell_tokens_added"] if work_summary else 0
                ocr_any_requested = bool(
                    ocr_requested or figure_ocr_requested
                    or (work_summary and work_summary["requested"])
                )
                if cell_tokens_added and not page_ocr_requested:
                    actual_strategy = PageStrategy.MIXED
                ocr_backend_diagnostics = (
                    self.ocr_engine.consume_page_diagnostics()
                    if self.ocr_engine is not None
                    and hasattr(self.ocr_engine, "consume_page_diagnostics")
                    else {
                        "fallback_count": 0,
                        "fallback_rate": 0.0,
                        "fallback_reasons": [],
                    }
                )
                if not ocr_any_requested:
                    ocr_outcome = "not_requested"
                    ocr_degraded = False
                    ocr_degraded_reasons: list[str] = []
                elif page_ocr_requested and blank_page_ocr:
                    ocr_outcome = "blank_page"
                    ocr_degraded = False
                    ocr_degraded_reasons = []
                elif _is_successful_empty_optional_figure_ocr(
                    page_ocr_requested=page_ocr_requested,
                    region_ocr_requested=region_ocr_requested,
                    figure_ocr_requested=figure_ocr_requested,
                    ocr_tokens=ocr_tokens,
                    region_ocr_stats=ocr_region_stats,
                    warnings=warnings,
                    partial_reasons=partial_reasons,
                ):
                    # A figure is an optional text source.  A clean empty read
                    # must not turn an otherwise good native page into a
                    # degraded OCR result.
                    ocr_outcome = "no_text"
                    ocr_degraded = False
                    ocr_degraded_reasons = []
                elif ocr_tokens:
                    fallback_count = ocr_backend_diagnostics.get("fallback_count", 0)
                    if fallback_count:
                        ocr_outcome = "recovered"
                        ocr_degraded = True
                        ocr_degraded_reasons = [
                            "engine_fallback: "
                            + "; ".join(ocr_backend_diagnostics.get("fallback_reasons", []))
                        ]
                    else:
                        ocr_outcome = "success"
                        ocr_degraded = False
                        ocr_degraded_reasons = []
                else:
                    ocr_outcome = "degraded"
                    ocr_degraded = True
                    ocr_degraded_reasons = [
                        "no_usable_tokens"
                        if self.ocr_engine is not None and ocr_image is not None
                        else "ocr_unavailable"
                    ]
                if work_summary and work_summary["requested"]:
                    if not (ocr_requested or figure_ocr_requested):
                        ocr_outcome = work_summary["outcome"]
                        ocr_degraded = work_summary["degraded"]
                        ocr_degraded_reasons = list(work_summary["reasons"])
                    elif work_summary["degraded"]:
                        ocr_degraded = True
                        ocr_degraded_reasons.extend(work_summary["reasons"])
                        if ocr_outcome == "success":
                            ocr_outcome = "recovered"
                    if work_summary["mandatory_failures"]:
                        partial_reasons.append("ocr_mandatory_work_failed")
                        warnings.append("Mandatory OCR work failed; see ocr_pipeline_work.summary")
                diagnostics = PageDiagnostics(
                    page_index=page_index,
                    strategy=actual_strategy,
                    reasons=sorted(complexity.reasons, key=lambda item: item.value),
                    native_chars=len(source_native_page.characters),
                    native_text_length=len(source_native_page.extracted_text or ""),
                    ocr_tokens_added=len(ocr_tokens) + cell_tokens_added,
                    conflicts=len(fusion.conflicts) if fusion else 0,
                    tables=len(tables),
                    processing_time_ms=elapsed_ms,
                    warnings=warnings,
                    facts={
                        "native_text_score": complexity.native_text_score,
                        **spacing_diagnostics(native_lines),
                        "layout_regions": len(regions),
                        "experimental_occlusion_redaction_enabled": (
                            self.config.enable_experimental_occlusion_redaction
                        ),
                        "textpage_reconciliation_disabled_for_redaction": (
                            textpage_reconciliation_disabled_for_redaction
                        ),
                        "opaque_occlusion_boxes": [
                            _bbox_to_dict(box) for box in opaque_occlusion_boxes
                        ],
                        "redacted_native_characters": redacted_character_count,
                        "inkless_native_characters": inkless_character_count,
                        "reading_frame_turned_half": reading_frame_turned,
                        "outside_page_native_characters": outside_page_character_count,
                        "layout_engine": type(self.layout_engine).__name__ if len(regions) > 0 else None,
                        "ocr_requested": ocr_any_requested,
                        "ocr_outcome": ocr_outcome,
                        "ocr_degraded": ocr_degraded,
                        "ocr_degraded_reasons": ocr_degraded_reasons,
                        "ocr_optional_empty": ocr_outcome == "no_text",
                        "ocr_attempt_errors": ocr_attempt_errors,
                        "ocr_quality_policy": effective_ocr_quality_policy(self.config).value,
                        "ocr_baseline_quality": ocr_backend_diagnostics.get("baseline_quality"),
                        "ocr_image_profile": ocr_backend_diagnostics.get("image_profile"),
                        "ocr_fallback_used": bool(ocr_backend_diagnostics.get("fallback_count", 0)),
                        "ocr_fallback_reason": "; ".join(ocr_backend_diagnostics.get("fallback_reasons", [])) or None,
                        # Historical serialized names are projected at the
                        # compatibility boundary, outside core orchestration.
                        **LegacyDiagnosticsProjector.public_compatibility_aliases(ocr_backend_diagnostics),
                        **ocr_backend_diagnostics,
                        "ocr_recovery_triggered": bool(ocr_backend_diagnostics.get("recovery_triggered", False)),
                        "ocr_recovery_reasons": list(ocr_backend_diagnostics.get("recovery_reasons", [])),
                        "ocr_selected_variant": ocr_backend_diagnostics.get("selected_variant"),
                        "ocr_variants_attempted": list(ocr_backend_diagnostics.get("variants_attempted", [])),
                        "ocr_variant_scores": dict(ocr_backend_diagnostics.get("variant_scores", {})),
                        "ocr_variants_succeeded": list(ocr_backend_diagnostics.get("variants_succeeded", [])),
                        "ocr_variants_failed": list(ocr_attempt_errors),
                        "ocr_candidate_count": ocr_backend_diagnostics.get("candidate_count", 0),
                        "ocr_candidate_metrics": dict(ocr_backend_diagnostics.get("candidate_metrics", {})),
                        "ocr_candidate_quality": {
                            name: float(candidate.get("quality_score", 0.0))
                            for name, candidate in ocr_backend_diagnostics.get("candidate_metrics", {}).items()
                        },
                        "ocr_candidate_coverage": {
                            name: float(candidate.get("spatial_coverage_score", 0.0))
                            for name, candidate in ocr_backend_diagnostics.get("candidate_metrics", {}).items()
                        },
                        "ocr_candidate_line_clusters": {
                            name: int(candidate.get("line_cluster_count", 0))
                            for name, candidate in ocr_backend_diagnostics.get("candidate_metrics", {}).items()
                        },
                        "ocr_fusion_replacements_attempted": ocr_backend_diagnostics.get("fusion_replacements_attempted", 0),
                        "ocr_fusion_replacements_accepted": ocr_backend_diagnostics.get("fusion_replacements_accepted", 0),
                        "ocr_fusion_replacements_rolled_back": ocr_backend_diagnostics.get("fusion_replacements_rolled_back", 0),
                        "ocr_fusion_lost_clusters": ocr_backend_diagnostics.get("fusion_lost_clusters", 0),
                        "ocr_fusion_conflict_clusters": ocr_backend_diagnostics.get("fusion_conflict_clusters", 0),
                        # Compatibility alias for the historical diagnostic name.
                        "ocr_fusion_duplicate_clusters": ocr_backend_diagnostics.get("fusion_duplicate_clusters", 0),
                        "ocr_consensus_replacements": ocr_backend_diagnostics.get("consensus_replacements", 0),
                        "ocr_consensus_insertions": ocr_backend_diagnostics.get("consensus_insertions", 0),
                        "ocr_orientation_selected": ocr_backend_diagnostics.get("orientation_selected"),
                        "ocr_orientation_attempts": list(ocr_backend_diagnostics.get("orientation_attempts") or []),
                        "ocr_enhancement_orientation": ocr_backend_diagnostics.get("enhancement_orientation"),
                        "ocr_targeted_refinement_regions": [
                            region_id for region_id, stat in {
                                **ocr_region_stats,
                                **ocr_targeted_stats,
                            }.items()
                            if stat.get("attempts", 0) > 0
                        ],
                        "ocr_targeted_refinement_passes": sum(
                            int(stat.get("ocr_passes", 0)) for stat in ocr_region_stats.values()
                        ) + ocr_targeted_passes,
                        "ocr_targeted_refinement_batches": sum(
                            int(stat.get("ocr_batches", 0)) for stat in ocr_region_stats.values()
                        ) + ocr_targeted_batches,
                        "ocr_targeted_refinement_stats": ocr_targeted_stats,
                        "ocr_resource_exhausted_count": sum(
                            int(stat.get("resource_exhausted_attempts", 0))
                            for stat in ocr_region_stats.values()
                        ),
                        "ocr_resource_exhausted_regions": [
                            region_id for region_id, stat in ocr_region_stats.items()
                            if int(stat.get("resource_exhausted_attempts", 0)) > 0
                        ],
                        "ocr_early_stop": bool(ocr_backend_diagnostics.get("early_stop", False)),
                        "page_ocr_requested": page_ocr_requested,
                        "region_ocr_requested": region_ocr_requested,
                        "ocr_candidate_region_ids": list(recovery_plan.region_ids),
                        "ocr_region_ids": [
                            region.region_id for region in selected_regions
                        ] if region_ocr_requested else [],
                        "ocr_promotion_reasons": promotion_reasons,
                        "bad_region_ratio": recovery_plan.bad_region_ratio,
                        "bad_area_ratio": recovery_plan.bad_area_ratio,
                        "region_quality": region_quality_facts,
                        "ocr_region_stats": ocr_region_stats,
                        "memory": {
                            **page_memory_end,
                            "peak_rss_growth_bytes": (
                                max(0, page_memory_end["peak_rss_bytes"] - page_memory_start["peak_rss_bytes"])
                                if page_memory_end["peak_rss_bytes"] is not None
                                and page_memory_start["peak_rss_bytes"] is not None
                                else None
                            ),
                        },
                        "native_source_calls": _counter_delta(
                            source_metrics_start,
                            source_metrics_end,
                        ),
                        "ocr_engine": type(self.ocr_engine).__name__ if self.ocr_engine is not None else None,
                        "ocr_pipeline_work": pipeline_work,
                        "ocr_tile_stats": ocr_tile_stats,
                        "critical_data_refinements": critical_data_refinements,
                        "footnote_refinements": footnote_refinements,
                        "embedded_image_candidate_tokens": embedded_image_tokens,
                        "table_cell_refinements": table_cell_refinements,
                        "ocr_capabilities": _backend_capability_facts(self.ocr_engine),
                        "ocr_passes": ocr_passes_total,
                        "ocr_batches": ocr_batches_total,
                        "deskew_angle_deg": (
                            ocr_backend_diagnostics.get("deskew_angle", 0.0)
                            if page_ocr_requested and self.ocr_engine is not None
                            else None
                        ),
                        "ocr_table_tokens": ocr_table_tokens,
                        "ocr_figure_tokens": ocr_figure_tokens,
                        "ocr_matched_tokens": fusion.matched_ocr_tokens if fusion else 0,
                        "ocr_unmatched_tokens": len(fusion.unmatched_ocr_tokens) if fusion else 0,
                        "ocr_conflicts": len(fusion.conflicts) if fusion else 0,
                        "native_ocr_local_decisions": local_fusion_decisions,
                        "ocr_supplemental_lines": len(unmatched_ocr_lines),
                        "ocr_rotations": sorted({token.rotation for token in ocr_tokens}),
                        "table_count": len(tables),
                        "table_methods": [table.method.value for table in tables],
                        "table_confidences": [table.confidence for table in tables],
                        "table_geometry_valid": [item["valid"] for item in table_validation_facts],
                        "table_row_monotonicity": [item["row_monotonicity"] for item in table_validation_facts],
                        "table_column_monotonicity": [item["column_monotonicity"] for item in table_validation_facts],
                        "table_source_row_monotonicity": [
                            item["source_row_assignment_monotonicity"]
                            for item in table_validation_facts
                        ],
                        "table_source_col_monotonicity": [
                            item["source_column_assignment_monotonicity"]
                            for item in table_validation_facts
                        ],
                        "table_source_assignment_conflicts": [
                            item["source_assignment_conflicts"]
                            for item in table_validation_facts
                        ],
                        "table_cell_coverage": [item["token_coverage"] for item in table_validation_facts],
                        "table_token_coverage": [item["token_coverage"] for item in table_validation_facts],
                        "table_empty_cell_ratio": [item["empty_cell_ratio"] for item in table_validation_facts],
                        "table_source_provenance": table_source_provenance,
                        **table_prefix_facts,
                        "table_construction_diagnostics": table_construction_facts,
                        "table_geometry_validation": table_validation_facts,
                        "timings_ms": timings,
                        "render_limit_reductions": render_limit_diagnostics,
                        "partial": bool(partial_reasons),
                        "partial_reasons": sorted(set(partial_reasons)),
                        **complexity.facts,
                    },
                )
                # Last: earlier stages rebuild cell text from OCR lines.
                _currency_markers = (
                    isinstance(self.ocr_engine, OCRPageFacade)
                    and self.ocr_engine.currency_marker_repair
                )
                for table in tables:
                    harmonize_value_surfaces(table, currency_markers=_currency_markers)
                if _currency_markers:
                    harmonize_currency_markers(regions, tables)
                pages.append(
                    assemble_page(
                        page_index=page_index,
                        page_bbox=native_page.bbox,
                        regions=regions,
                        tables=tables,
                        diagnostics=diagnostics,
                        # Raw output reflects reconstructed native evidence
                        # after page-boundary and visual-occlusion filtering.
                        raw_text=lines_to_text(native_lines),
                        native_evidence=(
                            source_native_page if self.config.retain_native_evidence else None
                        ),
                        page_rotation=native_page.objects.rotation,
                        links=[
                            PageLink(annotation.bbox, annotation.link_uri)
                            for annotation in native_page.objects.annotations
                            if annotation.subtype == "link"
                            and annotation.link_uri
                            and annotation.bbox is not None
                        ],
                        rule_boxes=_rule_boxes(native_page),
                        stroke_boxes=_stroke_boxes(native_page),
                    )
                )
            metadata = DocumentMetadata(
                source_path=str(Path(path)),
                page_count=context.page_count,
                pdfium_version=context.pdfium_version,
            )
        self._close_page_coordinator()
        assemble_start = time.perf_counter()
        document = assemble_document(
            pages=pages,
            metadata=metadata,
            merge_cross_page_tables=self.config.merge_cross_page_tables,
            preserve_headers_footers=self.config.preserve_headers_footers,
            document_warnings=document_warnings,
        )
        document.diagnostics.facts["assemble_ms"] = (time.perf_counter() - assemble_start) * 1000
        document.diagnostics.facts["total_ms"] = (time.perf_counter() - document_start) * 1000
        memory_end = _process_memory_snapshot()
        document.diagnostics.facts["open_pdf_ms"] = open_pdf_ms
        document.diagnostics.facts["memory"] = {
            **memory_end,
            "peak_rss_growth_bytes": (
                max(0, memory_end["peak_rss_bytes"] - memory_start["peak_rss_bytes"])
                if memory_end["peak_rss_bytes"] is not None
                and memory_start["peak_rss_bytes"] is not None
                else None
            ),
        }
        document.diagnostics.facts["native_source_calls"] = source.metrics_snapshot()
        document.diagnostics.facts["num_threads"] = _resolve_num_threads(self.config.num_threads)
        identity_source = self.ocr_engine or self._ocr_pipeline
        ident = getattr(identity_source, "identity", None)
        if ident is not None:
            document.diagnostics.facts["ocr_profile"] = ident.profile
            document.diagnostics.facts["ocr_engine_identity"] = {
                "engine": ident.engine if hasattr(ident, "engine") else ident.selection.engine,
                "runtime": ident.runtime,
                "language": ident.language,
                "device": ident.device,
                "package_versions": ident.package_versions,
            }
        elif identity_source is not None:
            document.diagnostics.facts["ocr_profile"] = "unknown-injected-engine"
        return document


def _process_memory_snapshot() -> dict[str, Any]:
    """Return process RSS metrics with explicit units and collection source."""
    return process_memory_snapshot()


def _resolve_num_threads(num_threads: int) -> int:
    """Resolve the effective number of threads for the OCR engine.

    0  → auto: uses os.cpu_count() with fallback 2
    -1 → do not configure (let PaddlePaddle decide)
    n  → use exactly n (minimum 1)
    """
    import os
    if num_threads == -1:
        return -1
    if num_threads == 0:
        return max(2, os.cpu_count() or 2)
    return max(1, num_threads)


def _counter_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {
        key: max(0, after.get(key, 0) - before.get(key, 0))
        for key in sorted(set(before) | set(after))
    }


def _ocr_enabled(config: ExtractorConfig) -> bool:
    """Return whether the selected mode permits OCR work."""
    mode = config.normalized_mode()
    if mode in (ExtractionMode.NATIVE, ExtractionMode.FAST):
        return False
    if mode == ExtractionMode.OCR:
        return True
    return config.enable_ocr is not False


def _backend_supports(engine: Any, capability: str) -> bool:
    """Read declared OCR capabilities while keeping legacy engines usable."""
    if engine is None:
        return False
    capabilities = getattr(engine, "capabilities", None)
    if capabilities is None:
        return True
    if isinstance(capabilities, dict):
        return bool(capabilities.get(capability, False))
    return bool(getattr(capabilities, capability, False))


def _regional_scale_factors(
    engine: Any,
    stage: str,
    *,
    quality_policy: str | None,
    reasons: tuple[str, ...] = (),
    area_ratio: float = 1.0,
    fallback: tuple[float, ...] = (1.0,),
) -> tuple[float, ...]:
    """Resolve scales from pipeline policy, never from the engine's name."""
    policy = getattr(engine, "recovery_policy", None) or LEGACY_COMPATIBILITY_RECOVERY_PROFILE
    choose = getattr(policy, "scale_factors", None)
    if callable(choose):
        try:
            values = tuple(choose(
                stage, quality_policy=quality_policy, reasons=reasons,
                area_ratio=area_ratio,
            ))
        except (TypeError, ValueError):
            values = ()
        if values and all(
            isinstance(value, (int, float)) and math.isfinite(value) and value > 0
            for value in values
        ):
            return values
        # A custom/legacy object may expose an unrelated dynamic attribute
        # named recovery_policy. Preserve injected-engine compatibility from
        # an explicit profile value rather than inferring from engine identity.
    compatibility = LEGACY_COMPATIBILITY_RECOVERY_PROFILE.scale_factors(
        stage, quality_policy=quality_policy, reasons=reasons, area_ratio=area_ratio,
    )
    return tuple(compatibility) if compatibility else tuple(fallback)


def _backend_capability_facts(engine: Any) -> dict[str, bool]:
    names = (
        "polygons", "direct_recognition", "detector_profiles", "decoder_profiles",
        "orientation_search", "multiple_detectors", "word_beam_search", "native_confidence",
    )
    return {name: _backend_supports(engine, name) for name in names}


def _local_fusion_decisions(
    regions: list[LayoutRegion], ocr_tokens: list[OcrToken]
) -> list[dict[str, Any]]:
    """Record region-local source authority and conflict scores."""
    decisions: list[dict[str, Any]] = []
    for region in regions:
        if region.quality.decision not in {RegionDecision.MERGE_OCR, RegionDecision.OCR_REGION}:
            continue
        local_tokens = [token for token in ocr_tokens if _line_in_box(token, region.bbox)]
        if not local_tokens:
            continue
        result = fuse_native_and_ocr(
            region.native_lines,
            local_tokens,
            ocr_authoritative=region.quality.decision == RegionDecision.OCR_REGION,
        )
        decisions.append({
            "region_id": region.region_id,
            "decision": region.quality.decision.value,
            "chosen_source": "ocr" if region.quality.decision == RegionDecision.OCR_REGION else "native_preferred",
            "matched": result.matched_ocr_tokens,
            "conflicts": [
                {
                    "chosen": item.chosen,
                    "alternatives": item.alternatives,
                    "reason": item.reason,
                    "chosen_source": item.chosen_source,
                    "alternative_sources": item.alternative_sources,
                    "chosen_score": item.chosen_score,
                    "alternative_scores": item.alternative_scores,
                }
                for item in result.conflicts
            ],
        })
    return decisions


def _ocr_token_score(tokens: list[OcrToken]) -> float:
    if not tokens:
        return -math.inf
    confidences = [token.confidence for token in tokens if token.confidence is not None]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.5
    character_count = sum(len(token.text.strip()) for token in tokens)
    return mean_confidence + min(character_count, 400) / 4000


def _recognize_dominant_embedded_image(
    *, source: Any, engine: Any, page: Any, page_image: Any,
    page_index: int, quality_policy: str,
) -> list[OcrToken]:
    """Use an original embedded bitmap when it materially exceeds raster detail."""
    if engine is None or not page.objects.images or page.bbox.area <= 0:
        return []
    candidates = [
        image for image in page.objects.images
        if image.bbox is not None and image.bbox.area / page.bbox.area >= 0.55
        and image.pixel_width and image.pixel_height
    ]
    if not candidates:
        return []
    image = max(candidates, key=lambda item: item.bbox.area)
    scale = 3.0
    try:
        scale = float(engine.identity.extra.get("render_scale", scale))
    except Exception:
        pass
    if image.pixel_width < image.bbox.width * scale * 1.15 or image.pixel_height < image.bbox.height * scale * 1.15:
        return []
    # Only request the visible PDF composition through the authorized facade.
    # The embedded source bitmap may include pixels hidden by clipping or masks.
    image_bbox = image.bbox
    tokens = _call_page_recognition(engine.recognize_page, page_image, page_index, image_bbox, quality_policy)
    return [replace(token, provenance="embedded_image_original") for token in tokens]


def _call_page_recognition(
    method: Any,
    image: Any,
    page_index: int,
    bbox: BBox,
    quality_policy: str,
    page_rotation: int = 0,
) -> list[OcrToken]:
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_policy = "quality_policy" in parameters or any(
        item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
    )
    kwargs = {"quality_policy": quality_policy} if accepts_policy else {}
    if "page_rotation" in parameters:
        kwargs["page_rotation"] = page_rotation
    return method(image, page_index, bbox, **kwargs)


def _is_successful_empty_optional_figure_ocr(
    *,
    page_ocr_requested: bool,
    region_ocr_requested: bool,
    figure_ocr_requested: bool,
    ocr_tokens: list[OcrToken],
    region_ocr_stats: dict[str, dict[str, Any]],
    warnings: list[str],
    partial_reasons: list[str],
) -> bool:
    """Recognize a clean empty result when only optional figures were read."""
    optional_regions = (
        not region_ocr_requested
        or bool(region_ocr_stats)
        and all(
            item.get("kind") == "figure"
            and item.get("ocr_required") is False
            and item.get("ocr_failed") is False
            and item.get("tokens", 0) == 0
            for item in region_ocr_stats.values()
        )
    )
    return (
        (figure_ocr_requested or region_ocr_requested)
        and not page_ocr_requested
        and optional_regions
        and not ocr_tokens
        and not any(item.startswith("figure_ocr_failed:") for item in warnings)
        and "figure_ocr_unavailable" not in partial_reasons
    )


def _recover_selected_regions(
    engine: Any,
    page_image: Any,
    page_index: int,
    page_bbox: BBox,
    regions: list[LayoutRegion],
    quality_variants: bool,
    quality_policy: str | None = None,
    page_rotation: int = 0,
) -> tuple[list[OcrToken], int, int, dict[str, dict[str, Any]]]:
    """Run targeted OCR recovery on regions flagged by the quality gate.

    For each region the function constructs a :class:`RegionRefinementRequest`
    and delegates to :class:`OcrRegionRefiner`. The selected pipeline supplies
    its own scale schedule; the common refiner only executes it. The
    ``quality_reasons`` field is forwarded verbatim for diagnostics.

    The RGB budget gate in :func:`plan_ocr_scales` may further reduce the
    effective scale list; blocked variants are never rendered or sent to OCR.

    Returns:
        A 4-tuple of ``(tokens, total_passes, total_batches, per_region_stats)``.
        *tokens* is de-duplicated by position across all recovered regions.
        *per_region_stats* maps ``region_id`` to a dict with token counts,
        attempt counts, errors and selected scale/rotation for diagnostics.
    """
    refiner = OcrRegionRefiner(engine)
    # Baseline must not silently expand into quality variants. Scale schedules
    # come from the selected pipeline's local recovery policy.
    _is_baseline_policy = str(quality_policy or "").lower() == "baseline"
    requests = []
    target_bindings: dict[str, Any] = {}
    target_factory = getattr(engine, "make_region_target", None)
    for region in regions:
        reasons = tuple(region.quality.reasons)
        binding = None
        if callable(target_factory) and not region.ocr_tokens:
            binding = target_factory(region)
            target_bindings[region.region_id] = binding
        requests.append(RegionRefinementRequest(
            bbox=region.bbox,
            scale_factors=_regional_scale_factors(
                engine, "region", quality_policy=quality_policy,
                reasons=reasons,
            ),
            quality_variants=False if _is_baseline_policy else quality_variants,
            quality_policy=quality_policy,
            goal=RegionRefinementGoal.TEXT,
            page_rotation=page_rotation,
            quality_reasons=reasons,
            integration_targets=(binding,) if binding is not None else (),
        ))
    results = refiner.refine_many(
        page_image,
        page_index,
        page_bbox,
        requests,
    )
    tokens: list[OcrToken] = []
    passes = 0
    batches = 0
    stats: dict[str, dict[str, Any]] = {}
    for region, result in zip(regions, results):
        region_tokens = list(result.tokens)
        binding = target_bindings.get(region.region_id)
        integrate_selected = getattr(engine, "integrate_selected_tokens", None)
        if binding is not None and callable(integrate_selected):
            region_tokens = list(integrate_selected(region_tokens, binding))
        region.ocr_tokens = region_tokens
        region.ocr_lines = reconstruct_ocr_lines(
            region_tokens, page_index, page_bbox, page_rotation=page_rotation
        )
        tokens.extend(region_tokens)
        passes += result.ocr_passes
        batches += result.ocr_batches
        all_errors = [a.error for a in result.attempts if a.error is not None]
        is_figure = region.kind.value == "figure"
        ocr_empty = result.status == "no_text" and not region_tokens
        # A figure with no tokens and no errors is an optional OCR region (logo,
        # signature, decorative element) — returning no text is a valid outcome.
        # Only treat no_text as a failure for non-figure regions or when there
        # were actual runtime errors alongside the empty result.
        ocr_required = not is_figure or bool(all_errors)
        ocr_failed = (
            result.status in {"budget_blocked", "invalid_region", "runtime_error", "timeout", "resource_exhausted"}
            or (ocr_empty and ocr_required)
        )
        # Count attempts that raised ResourceExhaustedExtractionError, regardless
        # of whether recovery via tiling ultimately succeeded.  This keeps OOM
        # events visible in telemetry even when the final status is "ok".
        resource_exhausted_attempts = sum(
            1
            for attempt in result.attempts
            if attempt.error and "ResourceExhausted" in attempt.error
        )
        stats[region.region_id] = {
            "kind": region.kind.value,
            "tokens": len(region_tokens),
            "attempts": len(result.attempts),
            "attempt_errors": len(all_errors),
            "ocr_empty": ocr_empty,
            "ocr_required": ocr_required,
            "ocr_failed": ocr_failed,
            "attempt_error_messages": all_errors if all_errors else None,
            "status": result.status,
            "reason_code": result.reason_code,
            "selected_scale_factor": result.selected_scale_factor,
            "selected_rotation": result.selected_rotation,
            "ocr_passes": result.ocr_passes,
            "ocr_batches": result.ocr_batches,
            "resource_exhausted_attempts": resource_exhausted_attempts,
            "resource_exhausted": resource_exhausted_attempts > 0,
        }
    return _deduplicate_region_ocr_tokens(tokens), passes, batches, stats


def _deduplicate_refinement_tokens(tokens: list[OcrToken]) -> list[OcrToken]:
    """Remove spatially and textually duplicate tokens from a reread result.

    When EasyOCR rerenders at high scale it can return the same text span
    twice (e.g. "NOTA ABC" and "NOTA ABC" at nearly the same position).
    Only the higher-confidence copy of each duplicate group is retained.
    """
    if len(tokens) <= 1:
        return tokens
    from unicodedata import normalize
    def _norm(text: str) -> str:
        return " ".join(normalize("NFKC", text).casefold().split())
    kept: list[OcrToken] = []
    for token in tokens:
        norm = _norm(token.text)
        replaced = False
        duplicate = False
        for idx, existing in enumerate(kept):
            if _norm(existing.text) != norm:
                continue
            if token.bbox.iou(existing.bbox) < 0.50:
                continue
            duplicate = True
            if (token.confidence or 0.0) > (existing.confidence or 0.0):
                kept[idx] = token
                replaced = True
            break
        if not duplicate:
            kept.append(token)
    return kept


def _refine_small_footnote_tokens(
    *, engine: Any, page_index: int, page_bbox: BBox, tokens: list[OcrToken],
    quality_policy: str, region_renderer: Any, base_scale: float,
    page_rotation: int = 0,
) -> tuple[list[OcrToken], int]:
    """Rerender tiny bottom-page OCR lines directly from PDFium in max quality."""
    if not tokens or page_bbox.height <= 0 or not callable(region_renderer):
        return tokens, 0
    lines = reconstruct_ocr_lines(tokens, page_index, page_bbox, page_rotation=page_rotation)
    heights = sorted(line.bbox.height for line in lines if line.bbox.height > 0)
    if len(heights) < 3:
        return tokens, 0
    median_height = heights[len(heights) // 2]
    candidates = [
        line for line in lines
        if line.bbox.y1 >= page_bbox.y0 + page_bbox.height * 0.72
        and (
            line.bbox.height <= median_height * 0.88
            or bool(re.match(r"^(?:\d+|[*†‡])\s*", line.text.strip()))
        )
    ]
    refined_count = 0
    output = list(tokens)
    token_integrator = NativeOCRIntegrator()
    for line_index, line in enumerate(candidates, start=1):
        box = line.bbox.expand(max(3.0, line.bbox.height * 0.5)).intersection(page_bbox)
        if box is None or box.area <= 0:
            continue
        if isinstance(engine, OCRPageFacade):
            from structured_pdf_text.ocr.pipeline.contracts import WorkGoal

            indices = tuple(index for index, token in enumerate(output)
                            if box.x0 <= token.bbox.cx <= box.x1 and box.y0 <= token.bbox.cy <= box.y1)
            if not indices:
                continue
            try:
                target = engine.make_token_target(
                    output, box, target_id=f"footnote:p{page_index}:{line_index}", indices=indices, goal=WorkGoal.NOTE,
                )
            except ValueError:
                # Overlap alone cannot authorize deletion of the part of an
                # existing token that lies outside this optional note crop.
                continue
            refined_count += int(engine.refine_footnote(target, base_scale=base_scale, quality_policy=quality_policy))
            continue
        try:
            image = region_renderer(box, base_scale * 2.0)
            reread = _call_page_recognition(
                engine.recognize_page, image, page_index, box, quality_policy,
                page_rotation=page_rotation,
            )
        except FatalExtractionError:
            raise
        except Exception as exc:
            raise_if_resource_exhausted(exc, page_index=page_index, stage="footnote_reread")
            continue
        reread = [token for token in reread if token.text.strip()]
        if not reread:
            continue
        # R65: deduplicate the reread result before evaluating it.
        # High-scale renders can return the same span twice (e.g. "NOTA ABC"
        # twice), which inflates char count and bypasses conservation checks.
        reread = _deduplicate_refinement_tokens(reread)
        old_indices = [
            index for index, token in enumerate(output)
            if box.x0 <= token.bbox.cx <= box.x1 and box.y0 <= token.bbox.cy <= box.y1
        ]
        old_tokens = [output[index] for index in old_indices]
        old_text = " ".join(token.text.strip() for token in old_tokens)
        new_text = " ".join(token.text.strip() for token in reread)
        old_score = _ocr_token_score(old_tokens)
        new_score = _ocr_token_score(reread)
        old_chars = len(re.sub(r"\s+", "", old_text))
        new_chars = len(re.sub(r"\s+", "", new_text))
        # A rerender can win on better confidence or recover at least 20% more
        # non-whitespace characters without a confidence drop over 0.10.
        if not (
            new_score > old_score + 0.05
            or (new_chars >= old_chars * 1.2 and new_score >= old_score - 0.10)
        ):
            continue
        try:
            output, applied = token_integrator.apply_token_replacement(
                request_id=f"footnote:p{page_index}:{line_index}",
                page_index=page_index,
                target_id=f"page-{page_index}:ocr-tokens",
                tokens=output,
                expected_snapshot=ocr_tokens_snapshot(output),
                box=box,
                selection_indices=tuple(old_indices),
                replacement=[
                    replace(token, provenance="footnote_high_scale")
                    for token in reread
                ],
            )
        except IntegrationRejected:
            continue
        refined_count += int(applied)
    return output, refined_count


_CRITICAL_LABEL_RE = re.compile(
    r"\b(CPF(?:/CNPJ)?|CNPJ|VALOR|TOTAL|SUBTOTAL|DESCONTO|PRE[CÇ]O|DATA|EMISS[AÃ]O|VENCIMENTO|AL[IÍ]QUOTA|PERCENTUAL|QUANTIDADE|PROCESSO|N[ÚU]MERO DO PROCESSO|CEP|C[ÓO]DIGO POSTAL|HORA|HOR[AÁ]RIO|TIME|NF|NFE|NOTA FISCAL|N[ÚU]MERO DA NOTA|INVOICE|PEDIDO)\b\s*:?[ ]*",
    re.IGNORECASE,
)




def _refine_critical_data_tokens(
    *, engine: Any, page_image: Any, page_index: int, page_bbox: BBox,
    tokens: list[OcrToken], lines: list[TextLine], quality_policy: str,
    page_rotation: int = 0, region_renderer: Any = None, base_scale: float = 3.0,
    page_warnings: list[str] | None = None,
) -> tuple[list[OcrToken], int]:
    """Rerun contextual critical fields and select only OCR-produced text."""
    refiner = CriticalDataRefiner()
    contexts: list[tuple[TextLine, list[str]]] = []
    for line in lines:
        labels = [match.group(1) for match in _CRITICAL_LABEL_RE.finditer(line.text)]
        if labels:
            contexts.append((line, labels))
    if not contexts:
        return tokens, 0

    # VQ-06: snapshot before refinement for drift detection
    before_fingerprint = _collect_token_critical_fingerprint(tokens) if page_warnings is not None else None

    output = list(tokens)
    refinements = 0
    for index, token in enumerate(tokens):
        if not any(character.isdigit() for character in token.text):
            continue
        nearby_labels: list[str] = []
        for label_line, labels in contexts:
            vertical_distance = max(
                0.0, label_line.bbox.y0 - token.bbox.y1,
                token.bbox.y0 - label_line.bbox.y1,
            )
            horizontal_distance = max(
                0.0, label_line.bbox.x0 - token.bbox.x1,
                token.bbox.x0 - label_line.bbox.x1,
            )
            tolerance = max(36.0, token.bbox.height * 3.0)
            if vertical_distance <= tolerance and horizontal_distance <= max(160.0, tolerance * 3):
                nearby_labels.extend(labels)
        data_type = refiner.detect_type_from_context(nearby_labels)
        if isinstance(engine, OCRPageFacade):
            needs_refinement = engine.token_refinement_policy.needs_critical(token, data_type)
        else:
            needs_refinement = data_type is not None and refiner.score_token(token, data_type) < .98
        if not needs_refinement:
            continue
        box = token.bbox.expand(max(2.0, token.bbox.height * 0.35)).intersection(page_bbox)
        if box is None or box.area <= 0:
            continue
        if isinstance(engine, OCRPageFacade):
            from structured_pdf_text.ocr.pipeline.contracts import WorkGoal

            try:
                target = engine.make_token_target(
                    output, box, target_id=f"critical:p{page_index}:{index}", indices=(index,), goal=WorkGoal.CRITICAL_DATA,
                )
            except ValueError:
                continue
            refinements += int(engine.refine_critical(
                target, base_scale=base_scale, quality_policy=quality_policy,
                data_type=data_type, page_image=page_image,
            ))
            continue
        refined_tokens: list[OcrToken] = []
        hypotheses: list[list[OcrToken]] = []
        if callable(region_renderer):
            try:
                region_image = region_renderer(box, base_scale * 1.75)
                from structured_pdf_text.ocr.image_views import canonicalize_page_image
                region_image = canonicalize_page_image(region_image, page_rotation)
                hypotheses.append(_call_page_recognition(
                    engine.recognize_page, region_image, page_index, box, quality_policy,
                    page_rotation=0,
                ))
                if _backend_supports(engine, "direct_recognition") and callable(getattr(engine, "recognize_direct", None)):
                    hypotheses.append(engine.recognize_direct(
                        region_image, page_index, box, quality_policy=quality_policy
                    ))
            except FatalExtractionError:
                raise
            except Exception as exc:
                raise_if_resource_exhausted(exc, page_index=page_index, stage="critical_data_refinement")
                hypotheses = []
        if not any(hypothesis for hypothesis in hypotheses):
            result = OcrRegionRefiner(engine).refine(
                page_image, page_index, page_bbox,
                RegionRefinementRequest(
                    bbox=box,
                    scale_factors=_regional_scale_factors(
                        engine, "critical_data", quality_policy=quality_policy,
                    ),
                    rotations=(0.0,),
                    quality_variants=quality_policy != "baseline",
                    quality_policy=quality_policy,
                    goal=RegionRefinementGoal.NUMERIC,
                    page_rotation=page_rotation,
                    quality_reasons=(f"critical_data:{data_type}",),
                ),
            )
            hypotheses.append(list(result.tokens))
        hypotheses = [hypothesis for hypothesis in hypotheses if hypothesis]
        if hypotheses:
            def hypothesis_score(items: list[OcrToken]) -> float:
                text = " ".join(item.text.strip() for item in items if item.text.strip())
                candidate = replace(token, text=text)
                return refiner.score_token(candidate, data_type) + sum(item.confidence or 0.0 for item in items) / max(len(items), 1) * 0.01
            refined_tokens = max(hypotheses, key=hypothesis_score)
        if not refined_tokens:
            continue
        observed_text = " ".join(item.text.strip() for item in refined_tokens if item.text.strip())
        if not observed_text:
            continue
        candidate = replace(
            token,
            text=observed_text,
            bbox=BBox.union_all([item.bbox for item in refined_tokens]),
            confidence=max((item.confidence or 0.0) for item in refined_tokens),
            provenance="critical_data_refinement",
            polygon=None,
        )
        if refiner.score_token(candidate, data_type) > refiner.score_token(token, data_type):
            # VQ-02: block substitutions that alter sign, leading zeros, or
            # separators even when the format score improves.
            if not _critical_surface_preserved(token.text, observed_text):
                continue
            output[index] = candidate
            refinements += 1
    # VQ-06: emit warning for any critical value lost during this refinement
    if page_warnings is not None and before_fingerprint is not None:
        after_fingerprint = _collect_token_critical_fingerprint(output)
        _emit_conservation_warning(
            "critical_data_refinement", before_fingerprint, after_fingerprint,
            page_index, page_warnings,
        )

    return output, refinements


def _refine_table_cells_ocr(
    *, engine: Any, page_image: Any, page_index: int, page_bbox: BBox,
    tables: list[Any], quality_policy: str, region_renderer: Any = None,
    base_scale: float = 3.0, page_rotation: int = 0, automatic: bool = False,
) -> int:
    """Target weak cells using both detector and direct-recognition paths."""
    if isinstance(engine, OCRPageFacade):
        refinements = 0
        if not automatic:
            # Preserve the explicit legacy path, including reading the current
            # header after earlier cells have been refined.
            threshold = engine.cell_recovery_policy.minimum_existing_confidence
            for table in tables:
                header_rows = table.header_rows if table.header_rows is not None else (
                    (min((cell.row for cell in table.cells), default=0),) if table.cells else ()
                )
                for cell in table.cells:
                    if cell.bbox is None or (cell.text.strip() and cell.confidence >= threshold):
                        continue
                    header_text = next((item.text for item in table.cells
                                        if item.col == cell.col and item.row in header_rows and item.text.strip()), "")
                    refinements += int(engine.refine_cell(
                        table_id=table.table_id, page_index=page_index, cell=cell,
                        quality_policy=quality_policy, base_scale=base_scale, header_text=header_text,
                    ))
            return refinements
        policy = engine.cell_recovery_policy
        selected = []
        for table in tables:
            header_rows = table.header_rows if table.header_rows is not None else (
                (min((cell.row for cell in table.cells), default=0),) if table.cells else ()
            )
            for cell in table.cells:
                if cell.bbox is None:
                    continue
                header_text = policy.header_context(table, cell, header_rows)
                if automatic:
                    # Engine policy may schedule only already-observed raster
                    # tables; native extraction remains outside this opt-in.
                    priority = policy.automatic_priority(cell, header_text, is_header=cell.row in header_rows)
                    if priority is None or table.method.value != "visual_model":
                        continue
                else:
                    if cell.text.strip() and cell.confidence >= policy.minimum_existing_confidence:
                        continue
                    priority = 0
                selected.append((priority, table, cell, header_text))
        selected.sort(key=lambda value: value[0])
        if automatic:
            selected = selected[:policy.maximum_automatic_cells]
        for _, table, cell, header_text in selected:
            refinements += int(engine.refine_cell(
                table_id=table.table_id, page_index=page_index, cell=cell,
                quality_policy=quality_policy, base_scale=base_scale, header_text=header_text,
            ))
        return refinements

    import numpy as np
    from PIL import Image
    from .tables.cell_ocr import crop_cell_image
    from .tables.text_join import join_table_tokens
    from structured_pdf_text.ocr.image_views import canonicalize_page_image

    refiner = CriticalDataRefiner()
    integrator = NativeOCRIntegrator(join_table_tokens)
    page_array = np.asarray(page_image)
    capabilities = getattr(engine, "capabilities", None)
    direct_supported = bool(getattr(capabilities, "direct_recognition", False))
    direct = getattr(engine, "recognize_direct", None)
    refinements = 0
    for table in tables:
        header_rows = table.header_rows if table.header_rows is not None else ((min((cell.row for cell in table.cells), default=0),) if table.cells else ())
        for cell in table.cells:
            if cell.bbox is None or (cell.text.strip() and cell.confidence >= 0.70):
                continue
            cell_snapshot = cell_evidence_snapshot(cell)
            crop = crop_cell_image(
                page_array, cell.bbox, page_bbox, pad_px=3,
                page_rotation=page_rotation,
            )
            if crop is None or not crop.size:
                continue
            if callable(region_renderer):
                try:
                    crop = np.asarray(region_renderer(cell.bbox, base_scale * 1.75))
                except FatalExtractionError:
                    raise
                except Exception as exc:
                    raise_if_resource_exhausted(exc, page_index=page_index, stage="table_cell_render")
                    pass
            crop = canonicalize_page_image(crop, page_rotation)
            cell_header = next((
                item.text for item in table.cells
                if item.col == cell.col and item.row in header_rows and item.text.strip()
            ), "")
            data_type = refiner.detect_type_from_context([cell_header]) if cell_header else None
            candidates: list[list[OcrToken]] = []
            try:
                candidates.append(_call_page_recognition(
                    engine.recognize_page, crop, page_index, cell.bbox, quality_policy,
                    page_rotation=0,
                ))
            except FatalExtractionError:
                raise
            except Exception as exc:
                raise_if_resource_exhausted(exc, page_index=page_index, stage="table_cell_render")
                pass
            if direct_supported and callable(direct):
                try:
                    candidates.append(direct(crop, page_index, cell.bbox, quality_policy=quality_policy))
                except FatalExtractionError:
                    raise
                except Exception as exc:
                    raise_if_resource_exhausted(exc, page_index=page_index, stage="table_cell_direct_ocr")
                    pass
            # A direct render from the existing crop still provides a useful
            # recognizer hypothesis when the detector misses very small text.
            try:
                enlarged = Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2), Image.Resampling.LANCZOS)
                candidates.append(_call_page_recognition(
                    engine.recognize_page, enlarged, page_index, cell.bbox, quality_policy,
                    page_rotation=0,
                ))
            except FatalExtractionError:
                raise
            except Exception as exc:
                raise_if_resource_exhausted(exc, page_index=page_index, stage="table_cell_ocr")
                pass
            candidates = [candidate for candidate in candidates if candidate]
            if not candidates:
                continue
            candidate_texts = [" ".join(token.text.strip() for token in group if token.text.strip()) for group in candidates]
            if data_type:
                best_index = max(
                    range(len(candidates)),
                    key=lambda idx: (
                        refiner.score_token(replace(candidates[idx][0], text=candidate_texts[idx]), data_type),
                        sum(token.confidence or 0.0 for token in candidates[idx]) / len(candidates[idx]),
                    ),
                )
            else:
                best_index = max(
                    range(len(candidates)),
                    key=lambda idx: sum(token.confidence or 0.0 for token in candidates[idx]) / len(candidates[idx]),
                )
            chosen = candidates[best_index]
            text = candidate_texts[best_index]
            if not text:
                continue

            # VQ-03: for cells that appear empty, require stronger geometric
            # and confidence evidence before inserting text.  This prevents
            # column-border artefacts (e.g. a detected "1") from filling
            # legitimately empty cells.
            if not cell.text.strip():
                avg_conf = sum(t.confidence or 0.0 for t in chosen) / len(chosen)
                if avg_conf < 0.75:
                    continue
                if len(text.strip()) < 2:
                    continue
                # OCR tokens must overlap the cell interior, not just a border.
                tokens_inside = [
                    t for t in chosen
                    if not t.text.isspace()
                    and t.bbox.overlap_ratio(cell.bbox) >= 0.30
                ]
                if not tokens_inside:
                    continue

            old_score = refiner.score_token(
                replace(chosen[0], text=cell.text), data_type
            ) if data_type and cell.text.strip() else 0.0
            new_score = refiner.score_token(replace(chosen[0], text=text), data_type) if data_type else 1.0
            if cell.text.strip() and new_score < old_score:
                continue

            try:
                applied = integrator.apply_cell_replacement(
                    request_id=f"cell-ocr:p{page_index}:{table.table_id}:{cell.row}:{cell.col}",
                    page_index=page_index,
                    table_id=table.table_id,
                    cell=cell,
                    expected_snapshot=cell_snapshot,
                    evidence=tuple(chosen),
                    proposed_text=text,
                )
            except IntegrationRejected:
                continue
            refinements += int(applied)
    return refinements


# ---------------------------------------------------------------------------


def _recover_weak_ocr_regions(
    engine: Any,
    page_image: Any,
    page_index: int,
    page_bbox: BBox,
    tokens: list[OcrToken],
    lines: list[TextLine],
    quality_policy: str | None,
    thresholds: OcrQualityThresholds,
    page_rotation: int = 0,
    enforce_policy: bool = False,
) -> tuple[list[OcrToken], int, int, dict[str, dict[str, Any]]]:
    """Refine OCR lines that scored poorly after page-level candidate selection.

    Identifies weak lines by running :func:`assess_ocr_quality` on each line's
    tokens.  Lines whose quality assessment recommends recovery or whose
    suspicious-token ratio exceeds 10 % are grouped spatially into compact
    crops and re-processed at higher scale via :class:`OcrRegionRefiner`.

    When ``enforce_policy`` is ``True`` and the resolved policy is
    ``"baseline"``, the function is a no-op and returns the original *tokens*
    unchanged — baseline policy must not trigger targeted quality recovery.

    Returns:
        A 4-tuple of ``(tokens, total_passes, total_batches, per_region_stats)``
        in the same format as :func:`_recover_selected_regions`.
    """
    # Baseline is intentionally a single normal OCR path plus any orientation
    # needed for a valid reading. Targeted quality recovery belongs to
    # adaptive/exhaustive and must not be smuggled into baseline by the API.
    if enforce_policy and str(quality_policy or "").lower() == "baseline":
        return tokens, 0, 0, {}
    if engine is None or not lines or page_bbox.area <= 0:
        return tokens, 0, 0, {}
    weak_lines: list[tuple[TextLine, tuple[str, ...]]] = []
    for line in lines:
        assessment = assess_ocr_quality(line.tokens, thresholds=thresholds)
        if assessment.recovery_recommended or assessment.suspicious_token_ratio > 0.10:
            weak_lines.append((line, assessment.reasons))
    if not weak_lines:
        return tokens, 0, 0, {}

    groups: list[list[tuple[TextLine, tuple[str, ...]]]] = []
    for item in weak_lines:
        line = item[0]
        if not groups:
            groups.append([item])
            continue
        previous = groups[-1][-1][0]
        gap = line.bbox.y0 - previous.bbox.y1
        horizontal_gap = max(
            0.0,
            previous.bbox.x0 - line.bbox.x1,
            line.bbox.x0 - previous.bbox.x1,
        )
        tolerance = max(previous.bbox.height, line.bbox.height, 4.0) * 1.6
        if gap <= tolerance and horizontal_gap <= max(12.0, tolerance):
            groups[-1].append(item)
        else:
            groups.append([item])

    refiner = OcrRegionRefiner(engine)
    refined_tokens = list(tokens)
    token_integrator = NativeOCRIntegrator()
    from structured_pdf_text.ocr.pipeline.policies import LEGACY_TOKEN_REFINEMENT_POLICY

    acceptance_policy = (engine.token_refinement_policy if isinstance(engine, OCRPageFacade)
                         else LEGACY_TOKEN_REFINEMENT_POLICY)
    passes = 0
    batches = 0
    stats: dict[str, dict[str, Any]] = {}
    for index, group in enumerate(groups, start=1):
        box = BBox.union_all([line.bbox for line, _ in group]).expand(4.0).intersection(page_bbox)
        if box is None or box.area <= 0:
            continue
        area_ratio = box.area / max(page_bbox.area, 1.0)
        scales = _regional_scale_factors(
            engine, "weak_line", quality_policy=quality_policy,
            area_ratio=area_ratio,
        )
        target = None
        if isinstance(engine, OCRPageFacade):
            indices = tuple(position for position, token in enumerate(refined_tokens) if _line_in_box(token, box))
            if not indices:
                continue
            try:
                target = engine.make_token_target(
                    refined_tokens, box, target_id=f"weak-region:p{page_index}:{index}", indices=indices,
                )
            except ValueError:
                stats[f"weak-region-{index}"] = {
                    "bbox": box, "line_count": len(group), "accepted": False,
                    "reason": "existing_evidence_extends_outside_authorized_crop",
                    "attempts": 0, "ocr_passes": 0, "ocr_batches": 0,
                }
                continue
        request = RegionRefinementRequest(
            bbox=box,
            scale_factors=scales,
            rotations=(0.0,),
            quality_variants=True,
            quality_policy=quality_policy,
            goal=RegionRefinementGoal.TEXT,
            page_rotation=page_rotation,
            integration_targets=(target,) if target is not None else (),
        )
        try:
            result = refiner.refine(page_image, page_index, page_bbox, request)
        except BaseException:
            if target is not None:
                engine.discard_selected_tokens(target)
            raise
        passes += result.ocr_passes
        batches += result.ocr_batches
        old_tokens = [token for token in refined_tokens if _line_in_box(token, box)]
        new_tokens = list(result.tokens)
        accepted, old_quality, new_quality = acceptance_policy.accept_weak_line(old_tokens, new_tokens, thresholds)
        # R67: quality score alone cannot justify discarding most of the prior
        # content. A short result with high confidence must still retain at
        # least 60% of the previous non-whitespace character count.
        if target is not None:
            try:
                if accepted:
                    accepted = bool(engine.integrate_selected_tokens(new_tokens, target))
            except IntegrationRejected:
                accepted = False
            finally:
                engine.discard_selected_tokens(target)
        elif accepted:
            try:
                refined_tokens, applied = token_integrator.apply_token_replacement(
                    request_id=f"weak-region:p{page_index}:{index}",
                    page_index=page_index,
                    target_id=f"page-{page_index}:ocr-tokens",
                    tokens=refined_tokens,
                    expected_snapshot=ocr_tokens_snapshot(refined_tokens),
                    box=box,
                    replacement=new_tokens,
                )
                accepted = applied
            except IntegrationRejected:
                accepted = False
        stats[f"weak-region-{index}"] = {
            "bbox": box,
            "line_count": len(group),
            "reasons": sorted({reason for _, reasons in group for reason in reasons}),
            "attempts": len(result.attempts),
            "ocr_passes": result.ocr_passes,
            "ocr_batches": result.ocr_batches,
            "selected_scale_factor": result.selected_scale_factor,
            "old_score": old_quality.score,
            "new_score": new_quality.score,
            "accepted": accepted,
        }
    return _deduplicate_region_ocr_tokens(refined_tokens), passes, batches, stats


def _deduplicate_region_ocr_tokens(tokens: list[OcrToken]) -> list[OcrToken]:
    """Remove the same OCR hypothesis emitted by overlapping layout crops."""
    output: list[OcrToken] = []
    for token in sorted(tokens, key=lambda item: (item.bbox.y0, item.bbox.x0)):
        normalized = " ".join(token.text.casefold().split())
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(output)
                if normalized == " ".join(existing.text.casefold().split())
                and _same_ocr_hypothesis_position(token, existing)
            ),
            None,
        )
        if duplicate_index is None:
            output.append(token)
            continue
        existing = output[duplicate_index]
        if (token.confidence or 0.0) > (existing.confidence or 0.0):
            output[duplicate_index] = token
    return sorted(output, key=lambda item: (item.bbox.y0, item.bbox.x0))


def _same_ocr_hypothesis_position(first: OcrToken, second: OcrToken) -> bool:
    if first.bbox.iou(second.bbox) >= 0.20:
        return True
    intersection = first.bbox.intersection(second.bbox)
    if intersection is not None and (
        intersection.area / max(first.bbox.area, 1.0) >= 0.45
        or intersection.area / max(second.bbox.area, 1.0) >= 0.45
    ):
        return True
    return (
        abs(first.bbox.cx - second.bbox.cx)
        <= max(first.bbox.width, second.bbox.width, 1.0) * 0.35
        and abs(first.bbox.cy - second.bbox.cy)
        <= max(first.bbox.height, second.bbox.height, 1.0) * 0.35
    )


def _safe_complexity_scale(
    page_width: float,
    page_height: float,
    max_pixels: int,
    requested_scale: float,
    max_bytes: int = 256 * 1024 * 1024,
) -> float:
    """Limit raster dimensions by both pixels and estimated RGB allocation."""
    scale = max(0.0, float(requested_scale))
    if page_width <= 0 or page_height <= 0 or max_pixels <= 0 or scale == 0:
        return scale

    def raster_pixels(candidate: float) -> int:
        return math.ceil(page_width * candidate) * math.ceil(page_height * candidate)

    max_rgb_pixels = max(1, max_bytes // 3)
    max_pixels = min(max_pixels, max_rgb_pixels)
    if raster_pixels(scale) <= max_pixels:
        return scale

    scale = min(scale, math.sqrt(max_pixels / (page_width * page_height)))
    # PDFium rounds each side upward. Find the largest representable scale
    # whose actual integer raster dimensions satisfy the configured cap.
    low, high = 0.0, scale
    for _ in range(64):
        candidate = (low + high) / 2.0
        if raster_pixels(candidate) <= max_pixels:
            low = candidate
        else:
            high = candidate
    return low


def _is_visually_blank(image: Any) -> bool:
    """Conservatively identify an almost uniform white raster page."""
    try:
        from PIL import Image
        if not isinstance(image, Image.Image):
            image = Image.fromarray(image)
        gray = image.convert("L")
        low, high = gray.resize((min(64, gray.width), min(64, gray.height))).getextrema()
        return low >= 250 and high - low <= 3
    except MemoryError:
        raise
    except Exception:
        return False


def _selected_page_indices(page_indices: tuple[int, ...] | None, page_count: int) -> list[int]:
    if page_indices is None:
        return list(range(page_count))
    selected = sorted(set(page_indices))
    if any(index < 0 or index >= page_count for index in selected):
        raise ValueError(
            f"page_indices must be zero-based values within [0, {page_count}); got {page_indices}"
        )
    return selected


def _should_use_page_ocr_as_primary(
    *,
    native_lines: list[Any],
    ocr_lines: list[Any],
    page_ocr_requested: bool,
    mode: Any,
) -> bool:
    """Return True only when OCR should replace native text as the primary source.

    OCR taking ownership of the whole page discards all native regions.  This is
    correct for pure-raster pages (no useful native text) and for forced OCR
    mode, but it is *wrong* for hybrid pages where native text is strong.

    On a hybrid page the correct policy is:
      - native regions stay as primary;
      - unmatched OCR tokens (from the image/raster portion) are appended as
        supplemental coverage by the hybrid assembly path (line ~980).

    Criteria for OCR becoming primary:
    1. Forced OCR mode (user explicitly chose OCR as source) — always True.
    2. No native lines at all — OCR must be primary.
    3. Native is negligible (<20 non-WS chars) — OCR can take over.
    4. VQ-08: Native chars ≥ 20 but all text appears to be invalid/garbled
       (high ratio of replacement characters U+FFFD or non-printable chars)
       — treat as effectively empty so OCR can take over.

    In all other cases native is considered "strong" and stays primary.
    """
    if not page_ocr_requested or not ocr_lines:
        return False
    if mode == ExtractionMode.OCR:
        return True
    if not native_lines:
        return True
    native_chars = sum(
        len(re.sub(r"\s+", "", line.text)) for line in native_lines
    )
    if native_chars < 20:
        return True
    # VQ-08: Count is not enough — check for garbled/invalid native text.
    # If the native layer consists mostly of replacement characters or
    # private-use codepoints, it is effectively absent.
    all_native_text = "".join(line.text for line in native_lines)
    total_visible = len(all_native_text.replace(" ", "").replace("\n", ""))
    if total_visible > 0:
        garbled = sum(
            1 for ch in all_native_text
            if ch == "�" or (ord(ch) < 32 and ch not in "\t\n\r") or 0xe000 <= ord(ch) <= 0xf8ff
        )
        if garbled / total_visible >= 0.40:
            return True
    return False


def _is_raster_primary_candidate(complexity: Any) -> bool:
    """Prefer visible OCR over a short layer on an almost-raster page.

    The native-length bound is the quality gate here. Requiring the generic
    ``SPARSE_TEXT`` reason as well created a dead interval: that reason is
    emitted below 80 characters while this promotion intentionally allows up
    to 200 characters of native residue on a raster-dominant page.
    """
    facts = complexity.facts
    image_coverage = float(facts.get("image_coverage") or 0.0)
    largest_image = float(facts.get("largest_image_coverage") or 0.0)
    native_length = int(facts.get("useful_text_length") or 0)
    return (
        image_coverage >= 0.75
        and largest_image >= 0.75
        and native_length <= 200
    )


def _failed_page(page_index: int, exc: Exception, elapsed_ms: float) -> StructuredPage:
    warning = f"Native extraction unavailable: {type(exc).__name__}: {exc}"
    diagnostics = PageDiagnostics(
        page_index=page_index,
        strategy=PageStrategy.NATIVE,
        reasons=[ComplexityReason.NO_TEXT],
        native_chars=0,
        native_text_length=0,
        processing_time_ms=elapsed_ms,
        warnings=[warning],
        facts={"failed": True, "timings_ms": {"native_extract_ms": elapsed_ms}},
    )
    return StructuredPage(
        page_index=page_index,
        bbox=BBox(0.0, 0.0, 0.0, 0.0),
        regions=[],
        tables=[],
        raw_text="",
        reading_text="",
        diagnostics=diagnostics,
        native_evidence=None,
    )


def _append_hybrid_ocr_regions(
    regions: list[LayoutRegion],
    tables: list[Any],
    unmatched_lines: list[Any],
    unmatched_tokens: list[Any],
    page_index: int,
    page_bbox: BBox,
    complexity: Any,
    table_ocr_overrides: dict[str, tuple[list[Any], list[OcrToken]]] | None = None,
    figure_ocr_bindings: list[tuple[BBox, list[TextLine], list[OcrToken]]] | None = None,
    preserve_cell_refinements: bool = False,
) -> None:
    """Attach unmatched OCR to tables/remaining body without duplicating regions."""
    remaining = list(unmatched_lines)
    for table in tables:
        if table.method != TableMethod.VISUAL_MODEL or not table.page_fragments:
            continue
        boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
        if not boxes:
            continue
        table_bbox = BBox.union_all(boxes)
        table_lines = [line for line in remaining if _line_in_box(line, table_bbox)]
        table_tokens = [token for token in unmatched_tokens if _line_in_box(token, table_bbox)]
        override = (table_ocr_overrides or {}).get(table.table_id)
        if override is not None:
            table_lines, table_tokens = override
        # The axis-aligned box of a skewed grid also covers text just above
        # or below it; a line none of whose glyphs lies in a cell stays prose.
        outside = [line for line in table_lines if not _line_in_table_cells(line, table)]
        if outside:
            table_lines = [line for line in table_lines if _line_in_table_cells(line, table)]
            outside_ids = {id(line) for line in outside}
            remaining = [line for line in remaining if id(line) not in outside_ids] + outside
            table_tokens = [token for token in table_tokens
                            if _point_in_cells(table, token.bbox.cx, token.bbox.cy)]
        if not table_lines:
            continue
        table_lines = _rebuild_table_ocr_lines(
            table, table_lines, page_index,
            preserve_cell_refinements=preserve_cell_refinements,
        )
        table_region = full_page_text_region(page_index, table_bbox, table_lines, complexity)
        table_region.region_id = f"{table.table_id}:ocr"
        table_region.kind = RegionKind.TABLE
        table_region.layout_confidence = table.confidence
        table_region.ocr_tokens = table_tokens
        regions.append(table_region)
        remaining = [line for line in remaining
                     if not (_line_in_box(line, table_bbox) and _line_in_table_cells(line, table))]

    # OCR from a region already classified as a figure must never fall through
    # into the page's body-text supplement. A figure refinement can be rejected
    # by the conservation gate, while its initial region OCR remains useful as
    # diagnostic evidence on the figure itself.
    figure_boxes: list[BBox] = [
        region.bbox for region in regions if region.kind == RegionKind.FIGURE
    ]
    for index, (figure_bbox, figure_lines, figure_tokens) in enumerate(
        figure_ocr_bindings or (),
        start=1,
    ):
        if not any(existing.iou(figure_bbox) >= 0.25 for existing in figure_boxes):
            figure_boxes.append(figure_bbox)
        existing = _best_figure_region(regions, figure_bbox)
        if existing is None:
            existing = full_page_text_region(page_index, figure_bbox, [], complexity)
            existing.region_id = f"page-{page_index + 1}:figure-{index}:ocr"
            existing.kind = RegionKind.FIGURE
            existing.layout_confidence = 0.85
            regions.append(existing)
        existing.ocr_lines = list(figure_lines)
        existing.ocr_tokens = list(figure_tokens)
        remaining = [line for line in remaining if not _line_in_box(line, figure_bbox)]

    visual_tables = [table for table in tables
                     if table.method == TableMethod.VISUAL_MODEL and table.page_fragments]
    remaining = [
        line
        for line in remaining
        if not any(_line_in_box(line, box) for box in figure_boxes)
        and not any(_line_in_table_cells(line, table) for table in visual_tables)
    ]
    if remaining:
        supplemental = full_page_text_region(page_index, page_bbox, [], complexity)
        supplemental.region_id = f"page-{page_index + 1}:ocr-supplement"
        supplemental.ocr_lines = list(remaining)
        supplemental.ocr_tokens = [
            token
            for token in unmatched_tokens
            if not any(_line_in_box(token, box) for box in figure_boxes)
            and not any(_point_in_cells(table, token.bbox.cx, token.bbox.cy) for table in visual_tables)
        ]
        regions.append(supplemental)




def _image_union_coverage(image_bbox: BBox, regions: list[LayoutRegion]) -> float:
    """Fraction of image_bbox covered by the union of all region intersections.

    Uses coordinate-compression sweep to avoid double-counting overlapping regions.
    """
    if image_bbox.area <= 0:
        return 1.0
    intersections: list[BBox] = []
    for region in regions:
        inter = region.bbox.intersection(image_bbox)
        if inter is not None and inter.area > 0:
            intersections.append(inter)
    if not intersections:
        return 0.0
    # Coordinate-compression sweep over the intersection rectangles.
    xs = sorted({r.x0 for r in intersections} | {r.x1 for r in intersections})
    ys = sorted({r.y0 for r in intersections} | {r.y1 for r in intersections})
    union_area = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx = (xs[i] + xs[i + 1]) / 2
            cy = (ys[j] + ys[j + 1]) / 2
            if any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in intersections):
                union_area += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
    return union_area / image_bbox.area


def _figures_requiring_ocr(
    page: NativePageEvidence,
    selected_regions: list[LayoutRegion],
) -> list[Any]:
    """Return image objects whose content is not yet covered by selected regions.

    R71: returns the exact list that _refine_figure_ocr should process, so the
    decision and execution paths cannot diverge.
    R72: coverage uses union(intersections) / image_bbox.area to avoid false
    positives from tiny regions sitting wholly inside a large image.
    """
    result = []
    for image in page.objects.images:
        image_bbox = image.bbox
        if image_bbox is None:
            continue
        # R72: use union coverage (image as denominator)
        if _image_union_coverage(image_bbox, selected_regions) < 0.80:
            result.append(image)
    return result


def _validate_detected_tables(
    *,
    tables: list[Any],
    regions: list[LayoutRegion],
    extra_lines: list[TextLine],
    table_ocr_overrides: dict[str, tuple[list[Any], list[OcrToken]]],
    warnings: list[str],
) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate table candidates and retain rejected content as ordinary text."""
    valid_tables: list[Any] = []
    validation_facts: list[dict[str, Any]] = []
    construction_facts: list[dict[str, Any]] = []
    provenance_facts: list[dict[str, Any]] = []
    for original in tables:
        source_lines = _source_lines_for_table(
            original,
            regions,
            extra_lines,
            table_ocr_overrides,
        )
        direction = _table_writing_direction(source_lines)
        # An OCR table may have lost a divider in the scan; the repaired grid
        # is kept only when it passes the same validation.
        table = repair_missing_dividers(original)
        validation = validate_table_geometry(
            table,
            writing_direction=direction,
            source_lines=source_lines,
        )
        if table is not original and not validation.valid:
            table = original
            validation = validate_table_geometry(
                table,
                writing_direction=direction,
                source_lines=source_lines,
            )
        construction = build_table_construction_diagnostics(
            table,
            source_lines=source_lines,
            writing_direction=direction,
            region_bbox=_table_region_bbox(table, regions),
        )
        validation_facts.append(asdict(validation))
        construction_data = asdict(construction)
        construction_facts.append(construction_data)
        provenance_facts.append(
            {
                "candidate_id": table.table_id,
                "sources": list(construction.token_sources),
            }
        )
        if validation.valid:
            valid_tables.append(table)
        else:
            warnings.append("table_structure_uncertain")
    return valid_tables, validation_facts, construction_facts, provenance_facts



def _merge_ocr_region_tokens_into_table_cells(
    *, tables: list[Any], regions: list[LayoutRegion], page_index: int,
) -> set[int]:
    """Compatibility adapter to the shared core-owned cell integrator."""
    return _merge_regions_into_table_cells(
        tables=tables, regions=regions, page_index=page_index,
        join_tokens=join_table_tokens,
    )

def _table_prefix_diagnostics(regions: list[LayoutRegion]) -> dict[str, Any]:
    assessments = [
        assessment
        for region in regions
        if region.kind in {
            RegionKind.TEXT,
            RegionKind.LIST,
            RegionKind.UNKNOWN,
            RegionKind.TABLE,
        }
        for assessment in assess_borderless_region(region)
    ]
    return {
        "table_prefix_candidate_count": sum(item.prefix_candidate_count for item in assessments),
        "table_prefix_accepted_count": sum(item.prefix_accepted_count for item in assessments),
        "table_prefix_rejected_count": sum(item.prefix_rejected_count for item in assessments),
        "table_prefix_reasons": sorted({reason for item in assessments for reason in item.prefix_reasons}),
    }


def _source_lines_for_table(
    table: Any,
    regions: list[LayoutRegion],
    extra_lines: list[TextLine],
    overrides: dict[str, tuple[list[Any], list[OcrToken]]],
) -> list[TextLine]:
    boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    if not boxes:
        return []
    override = overrides.get(table.table_id)
    candidates: list[TextLine] = []
    if override is not None:
        candidates.extend(override[0])
    for region in regions:
        if any(_line_in_box(region, box) for box in boxes):
            candidates.extend(region.native_lines)
            candidates.extend(region.ocr_lines)
    candidates.extend(extra_lines)
    output: list[TextLine] = []
    seen: set[int] = set()
    for line in candidates:
        if id(line) in seen:
            continue
        if any(_line_in_box(line, box) for box in boxes):
            output.append(line)
            seen.add(id(line))
    return output


def _table_region_bbox(table: Any, regions: list[LayoutRegion]) -> BBox | None:
    boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    for region in regions:
        if boxes and any(_line_in_box(region, box) for box in boxes):
            return region.bbox
    return BBox.union_all(boxes) if boxes else None


def _table_writing_direction(lines: list[TextLine]) -> WritingDirection:
    directions = {line.direction for line in lines}
    if WritingDirection.RIGHT_TO_LEFT in directions:
        return WritingDirection.RIGHT_TO_LEFT
    if WritingDirection.TOP_TO_BOTTOM in directions:
        return WritingDirection.TOP_TO_BOTTOM
    return WritingDirection.LEFT_TO_RIGHT


def _point_in_cells(table: Any, x: float, y: float) -> bool:
    from .tables.cell_geometry import contains_point

    return any(
        contains_point(cell, x, y) if getattr(cell, "polygon", None) is not None
        else (cell.bbox is not None and cell.bbox.x0 <= x <= cell.bbox.x1 and cell.bbox.y0 <= y <= cell.bbox.y1)
        for cell in table.cells
    )


def _line_in_table_cells(line: Any, table: Any) -> bool:
    """Most of the line's visible glyph centers fall inside the table's cells."""
    tokens = [token for token in getattr(line, "tokens", ()) if token.text.strip()]
    if not tokens:
        return False
    inside = sum(_point_in_cells(table, token.bbox.cx, token.bbox.cy) for token in tokens)
    return inside >= 0.5 * len(tokens)


def _line_in_box(value: Any, box: BBox) -> bool:
    bbox = value.bbox
    return bbox.overlap_ratio(box) >= 0.25 or box.overlap_ratio(bbox) >= 0.25


def _refine_visual_table_ocr(
    engine: Any,
    page_image: Any,
    page: Any,
    page_index: int,
    table: Any,
    native_lines: list[TextLine],
    quality_policy: str = "baseline",
    page_rotation: int = 0,
) -> tuple[Any, list[TextLine], list[OcrToken], int, int] | None:
    """Run a quality-first OCR pass over a detected visual table crop."""
    if (
        engine is None
        or page_image is None
        or not table.page_fragments
        or str(quality_policy).lower() == "baseline"
    ):
        return None
    boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
    if not boxes:
        return None
    table_bbox = BBox.union_all(boxes)
    target = None
    if isinstance(engine, OCRPageFacade):
        try:
            target = engine.make_visual_table_target(table, table_bbox, native_lines=native_lines)
        except ValueError:
            return None
    try:
        result = OcrRegionRefiner(engine).refine(
            page_image,
            page_index,
            page.bbox,
            RegionRefinementRequest(
                bbox=table_bbox,
                quality_variants=True,
                quality_policy=quality_policy,
                goal=RegionRefinementGoal.TEXT,
                page_rotation=page_rotation,
                integration_targets=(target,) if target is not None else (),
            ),
        )
    except BaseException:
        if target is not None:
            engine.discard_selected_tokens(target)
        raise
    if target is not None:
        try:
            if not result.tokens:
                return None
            refined_table = detect_visual_table_in_region(
                page=page, image=page_image, region=table_bbox, table_id=table.table_id,
                tokens=list(result.tokens),
                structure_engine=engine.table_structure_engine,
            )
            candidate = _preserve_visual_grid(table, refined_table)
            if candidate is table:
                candidate = _project_reading_onto_grid(table, result.tokens)
            prepared = engine.token_refinement_policy.prepare_visual_table(table, candidate)
            if not engine.token_refinement_policy.accept_visual_table(table, prepared):
                return None
            target.stage_candidate(prepared, preserve_cell_evidence=prepared is not candidate)
            engine.integrate_selected_tokens(result.tokens, target)
            return table, target.ocr_lines, target.ocr_tokens, result.ocr_passes, result.ocr_batches
        except IntegrationRejected:
            return None
        finally:
            engine.discard_selected_tokens(target)
    refined_tokens = [
        replace(token, provenance="visual_table_refinement")
        for token in result.tokens
    ]
    if not refined_tokens:
        return None
    refined_table = detect_visual_table_in_region(
        page=page,
        image=page_image,
        region=table_bbox,
        table_id=table.table_id,
        tokens=refined_tokens,
    )
    refined_table = _preserve_visual_grid(table, refined_table)
    refined_fusion = fuse_native_and_ocr(native_lines, refined_tokens)
    unmatched = list(refined_fusion.unmatched_ocr_tokens)
    lines = reconstruct_ocr_lines(unmatched, page_index, page.bbox, page_rotation=page_rotation)
    lines = _rebuild_table_ocr_lines(refined_table, lines, page_index)
    return refined_table, lines, unmatched, result.ocr_passes, result.ocr_batches


def _table_structure_tokens(ocr_lines, regions, page_index, page_bbox, page_rotation=0):
    """Collect already integrated OCR, including recovery local to a figure.

    Regional OCR does not necessarily populate the page-wide ``ocr_lines``.
    Structure discovery must see that accepted evidence without scheduling a
    new pass or reading an embedded image behind the composed page.
    """
    lines = list(ocr_lines)
    for region in regions:
        # Lines may predate critical-data refinement. The final accepted token
        # view is authoritative, including an empty view after rejection.
        lines.extend(reconstruct_ocr_lines(
            region.ocr_tokens, page_index, page_bbox, page_rotation=page_rotation,
        ))
    tokens = []
    seen = set()
    by_text = {}
    for line in lines:
        for token in line.tokens:
            key = token.evidence_id or (token.text, token.bbox)
            if key in seen:
                continue
            seen.add(key)
            # The same accepted observation can also be represented by its
            # regional line. Keep distinct text/locations, including conflicts.
            if any(box.iou(token.bbox) >= .9 for box in by_text.get(token.text, ())):
                continue
            tokens.append(token)
            by_text.setdefault(token.text, []).append(token.bbox)
    return tokens


def _preserve_visual_grid(original, candidate):
    """Text-only refinement cannot discard already observed separators.

    The same structure raster is sampled in a tighter crop; rounding or crop
    thresholds can make a thin rule disappear there. Keep the finer observed
    grid and let the core reassign fresh OCR evidence to its existing cells.
    """
    if (candidate is None or candidate.row_count < original.row_count
            or candidate.column_count < original.column_count
            or len(candidate.cells) < len(original.cells)):
        return original
    return candidate


def _project_reading_onto_grid(table, tokens):
    """The kept grid with each cell holding the reread tokens it contains.

    The core reassigns the reread evidence to the kept grid; projecting it
    first lets the engine policy compare each cell's two readings before
    staging. Unchanged cells stage exactly as before.
    """
    from copy import deepcopy
    from .tables.cell_geometry import contains_point

    projected = deepcopy(table)
    for cell in projected.cells:
        if cell.bbox is None:
            continue
        cell.tokens = [token for token in tokens if contains_point(cell, token.bbox.cx, token.bbox.cy)]
        cell.text = join_table_tokens(cell.tokens)
        cell.confidence = (sum(token.confidence or 0. for token in cell.tokens) / len(cell.tokens)
                           if cell.tokens else cell.confidence)
    return projected


def _refine_figure_ocr(
    engine: Any,
    page_image: Any,
    page: Any,
    page_index: int,
    native_lines: list[TextLine],
    quality_policy: str = "baseline",
    page_rotation: int = 0,
    warnings: list[str] | None = None,
    images: list[Any] | None = None,
    integration_tokens: list[OcrToken] | None = None,
    integration_regions: list[LayoutRegion] | None = None,
) -> list[
    tuple[
        BBox,
        list[TextLine],
        list[OcrToken],
        list[TextLine],
        list[OcrToken],
        int,
        int,
    ]
]:
    """OCR embedded figures as generic text-bearing image regions.

    The extractor deliberately does not infer axes, labels, series, arrows, or
    chart structure. Any text returned is preserved with its observed geometry.

    When ``warnings`` is provided, geometric rejections (figure outside the
    rendered page area, zero width/height) are appended there so the caller can
    expose them via ``PageDiagnostics`` without surfacing PIL/image internals.
    An invalid figure is skipped; other figures on the same page are still
    processed.

    R71: when ``images`` is provided, only those image objects are processed —
    the decision (which images need OCR) and execution (which images are OCRed)
    cannot diverge.  When ``None``, falls back to all images on the page.
    """
    import math

    refinements: list[
        tuple[
            BBox,
            list[TextLine],
            list[OcrToken],
            list[TextLine],
            list[OcrToken],
            int,
            int,
        ]
    ] = []
    refiner = OcrRegionRefiner(engine)
    images_to_process = images if images is not None else page.objects.images
    if isinstance(engine, OCRPageFacade) and (integration_tokens is None or integration_regions is None):
        raise ValueError("Production figure refinement requires the core page token stream and region bindings")
    for image in images_to_process:
        figure_bbox = image.bbox
        if figure_bbox is None or figure_bbox.height <= 0 or figure_bbox.width <= 0:
            if warnings is not None and figure_bbox is not None:
                warnings.append(
                    f"figure_ocr_skipped:page={page_index + 1}"
                    f":reason=zero_dimensions"
                    f":bbox=({figure_bbox.x0:.1f},{figure_bbox.y0:.1f}"
                    f",{figure_bbox.x1:.1f},{figure_bbox.y1:.1f})"
                )
            continue

        # Guard: non-finite coordinates indicate a corrupt or synthetic bbox.
        for coord in (figure_bbox.x0, figure_bbox.y0, figure_bbox.x1, figure_bbox.y1):
            if not math.isfinite(coord):
                if warnings is not None:
                    warnings.append(
                        f"figure_ocr_skipped:page={page_index + 1}"
                        f":reason=non_finite_coordinates"
                        f":bbox=({figure_bbox.x0},{figure_bbox.y0}"
                        f",{figure_bbox.x1},{figure_bbox.y1})"
                    )
                break
        else:
            # Check whether the figure has any area within the rendered page.
            # _subfigure_boxes returns [] when the crop is None (no overlap).
            if isinstance(engine, OCRPageFacade):
                visible_figure = figure_bbox.intersection(page.bbox)
                ocr_boxes = [] if visible_figure is None else _subfigure_boxes(
                    page_image, page.bbox, visible_figure, page_rotation,
                    policy=engine.figure_segmentation_policy,
                )
            else:
                ocr_boxes = _subfigure_boxes(page_image, page.bbox, figure_bbox, page_rotation)
            if not ocr_boxes:
                if warnings is not None:
                    warnings.append(
                        f"figure_ocr_skipped:page={page_index + 1}"
                        f":reason=no_visible_area_in_page"
                        f":figure_bbox=({figure_bbox.x0:.1f},{figure_bbox.y0:.1f}"
                        f",{figure_bbox.x1:.1f},{figure_bbox.y1:.1f})"
                        f":page_bbox=({page.bbox.x0:.1f},{page.bbox.y0:.1f}"
                        f",{page.bbox.x1:.1f},{page.bbox.y1:.1f})"
                    )
            for ocr_box in ocr_boxes:
                target = None
                if isinstance(engine, OCRPageFacade):
                    visible_box = ocr_box.intersection(page.bbox)
                    if visible_box is None or visible_box.area <= 0:
                        continue
                    ocr_box = visible_box
                    try:
                        target = engine.make_figure_refinement_target(
                            integration_tokens, ocr_box, regions=integration_regions, native_lines=native_lines,
                        )
                    except ValueError:
                        continue
                try:
                    result = refiner.refine(
                        page_image,
                        page_index,
                        page.bbox,
                        RegionRefinementRequest(
                            bbox=ocr_box,
                            quality_variants=str(quality_policy).lower() != "baseline",
                            quality_policy=quality_policy,
                            goal=RegionRefinementGoal.TEXT,
                            page_rotation=page_rotation,
                            integration_targets=(target,) if target is not None else (),
                        ),
                    )
                    if target is not None:
                        try:
                            selected = list(engine.integrate_selected_tokens(result.tokens, target)) if result.tokens else []
                        except IntegrationRejected:
                            selected = []
                    else:
                        selected = [replace(token, provenance="figure_ocr") for token in result.tokens]
                finally:
                    if target is not None:
                        engine.discard_selected_tokens(target)
                if not result.tokens and result.status not in {"ok", "no_text"} and warnings is not None:
                    warnings.append(f"figure_ocr_failed:region={ocr_box}:status={result.status}:reason={result.reason_code}")
                figure_tokens = selected
                if not figure_tokens:
                    if target is not None and result.tokens:
                        refinements.append((ocr_box, [], [], [], [], result.ocr_passes, result.ocr_batches))
                    continue
                all_lines = reconstruct_ocr_lines(figure_tokens, page_index, page.bbox, page_rotation=page_rotation)
                figure_fusion = fuse_native_and_ocr(native_lines, figure_tokens)
                unmatched_tokens = list(figure_fusion.unmatched_ocr_tokens)
                unmatched_lines = reconstruct_ocr_lines(unmatched_tokens, page_index, page.bbox, page_rotation=page_rotation)
                refinements.append(
                    (
                        ocr_box,
                        all_lines,
                        figure_tokens,
                        unmatched_lines,
                        unmatched_tokens,
                        result.ocr_passes,
                        result.ocr_batches,
                    )
                )
    return refinements


def _subfigure_boxes(image: Any, page_bbox: BBox, figure_bbox: BBox, page_rotation: int = 0, *, policy=None) -> list[BBox]:
    """Find well-separated horizontal subfigures using rendered ink gaps.

    Returns an empty list when the figure has no rendereable area within the
    page (i.e. ``_crop_page_image`` returns ``None``).  Returns
    ``[figure_bbox]`` as a single undivided box when the crop exists but is too
    small to split or splitting finds no significant gaps.
    """
    crop = _crop_page_image(image, page_bbox, figure_bbox, page_rotation)
    if crop is None:
        # Figure is completely outside the rendered page area; no subfigures.
        return []
    from .ocr.pipeline.policies import LEGACY_FIGURE_SEGMENTATION_POLICY

    width, _ = _image_size(crop)
    visual_figure = figure_bbox.rotate_to_visual(page_rotation, page_bbox.width, page_bbox.height)
    try:
        segments = (policy or LEGACY_FIGURE_SEGMENTATION_POLICY).segments(
            crop, aspect=visual_figure.width / max(visual_figure.height, 1.0),
        )
    finally:
        close = getattr(crop, "close", None)
        if callable(close):
            close()
    # A policy may narrow this core-selected figure, never extend its scope.
    previous_end = 0
    for left, right in segments:
        if type(left) is not int or type(right) is not int or not (previous_end <= left < right <= width):
            raise ValueError("Figure segmentation exceeds its selected crop or overlaps another segment")
        previous_end = right
    if segments == ((0, width),):
        return [figure_bbox]
    scale_x = visual_figure.width / max(width, 1)
    output: list[BBox] = []
    for left, right in segments:
        visual_box = BBox(visual_figure.x0 + left * scale_x, visual_figure.y0,
                          visual_figure.x0 + right * scale_x, visual_figure.y1)
        rotation = page_rotation % 360
        if rotation == 90:
            box = BBox(visual_box.y0, page_bbox.height - visual_box.x1, visual_box.y1, page_bbox.height - visual_box.x0)
        elif rotation == 180:
            box = BBox(page_bbox.width - visual_box.x1, page_bbox.height - visual_box.y1,
                       page_bbox.width - visual_box.x0, page_bbox.height - visual_box.y0)
        elif rotation == 270:
            box = BBox(page_bbox.width - visual_box.y1, visual_box.x0, page_bbox.width - visual_box.y0, visual_box.x1)
        else:
            box = visual_box
        output.append(box)
    return output


def _has_uncovered_image_area(native_page: Any, tables: list[Any]) -> bool:
    """Return True when the page has at least one image bbox not substantially
    covered by an already-detected native table.

    VQ-09: Visual table detection should run even when native tables exist,
    provided there is an image region that the native detector did not claim.
    This enables hybrid pages (e.g. V5-P019/P024) to produce both a native-
    ruled table and a raster table.
    """
    image_boxes = [
        item.bbox for item in native_page.objects.images if item.bbox is not None
    ]
    if not image_boxes:
        return False
    if not tables:
        return True
    table_bboxes: list[Any] = []
    for t in tables:
        for frag in getattr(t, "page_fragments", []):
            if getattr(frag, "bbox", None) is not None:
                table_bboxes.append(frag.bbox)
    if not table_bboxes:
        return True
    for img_bbox in image_boxes:
        max_overlap = max(img_bbox.overlap_ratio(tb) for tb in table_bboxes)
        if max_overlap < 0.5:
            return True
    return False




def _crop_page_image(image: Any, page_bbox: BBox, region_bbox: BBox, page_rotation: int = 0) -> Any | None:
    """Crop a rendered page using PDF-point coordinates at any render scale.

    Returns the cropped image when the region has a positive rendereable area,
    or ``None`` when:

    - ``page_bbox`` has zero or negative dimensions (invalid geometry),
    - ``region_bbox`` contains non-finite coordinates,
    - the intersection of ``region_bbox`` with ``page_bbox`` is empty (figure
      is completely outside the rendered page area), or
    - the intersection maps to zero pixels after scaling and rounding.

    A ``None`` return signals a legitimately absent rendereable area and must
    **not** be treated as a fatal error by callers.  A genuine transformation
    inconsistency that would make a visible figure disappear is distinguished
    from an empty intersection: when ``page_bbox`` itself is valid and
    ``region_bbox`` is finite but produces no intersection, the figure is simply
    outside the rendered area.  When ``page_bbox`` is degenerate the caller
    should treat the entire page as un-renderable.
    """
    from .ocr.recovery import crop_page_region
    visible = region_bbox.intersection(page_bbox)
    if visible is None:
        return None
    try:
        return crop_page_region(image, page_bbox, visible, page_rotation)
    except (ValueError, TypeError, IndexError):
        return None

def _image_size(image: Any) -> tuple[int, int]:
    if hasattr(image, "size") and isinstance(image.size, tuple):
        return int(image.size[0]), int(image.size[1])
    return int(image.shape[1]), int(image.shape[0])


def _layout_regions_if_requested(
    config: ExtractorConfig,
    complexity,
    page,
    lines,
    image,
    layout_engine,
    output: list[LayoutRegion],
) -> list[str]:
    mode = config.normalized_mode()
    # OCR mode needs layout regions just as much as BALANCED — without regions
    # the recovery gate has nothing to select, falling back to whole-page OCR.
    requested = config.enable_layout or mode in {ExtractionMode.BALANCED, ExtractionMode.OCR}
    if mode == ExtractionMode.FAST:
        requested = requested or complexity.layout_needed
    if not requested:
        return []
    try:
        if hasattr(layout_engine, "detect_page"):
            predictions = layout_engine.detect_page(page, lines, image)
        elif image is not None:
            predictions = layout_engine.detect(image)
        else:
            return ["Layout skipped: no page image available"]
        output.extend(regions_from_predictions(page, lines, predictions, complexity))
        return []
    except FatalExtractionError:
        raise
    except Exception as exc:
        raise_if_resource_exhausted(
            exc,
            page_index=page.page_index,
            stage="layout",
            details=_process_memory_snapshot(),
        )
        return [f"Layout detection unavailable: {type(exc).__name__}: {exc}"]


def _rule_boxes(page: Any) -> list[BBox]:
    """Vector-path boxes that can mark quotes: thin vertical bars and frames."""
    boxes: list[BBox] = []
    for path in page.objects.paths:
        box = path.bbox
        if box is None or box.area <= 0 or box.area > page.bbox.area * 0.5:
            continue
        bar = box.width <= 8.0 and box.height >= 8.0 and box.height >= 3.0 * box.width
        frame = box.width >= 60.0 and box.height >= 12.0
        if bar or frame:
            boxes.append(box)
    return boxes


def _stroke_boxes(page: Any) -> list[BBox]:
    """Thin horizontal strokes that can underline or strike through text."""
    return [
        path.bbox for path in page.objects.paths
        if path.bbox is not None and 0 < path.bbox.height <= 2.5
        and 4.0 <= path.bbox.width <= page.bbox.width * 0.6
    ]


def _clear_occluded_table_cells(tables: list[Any], boxes: list[BBox]) -> None:
    """Remove only cell tokens whose own glyph bounds are occluded."""
    if not boxes:
        return
    for table in tables:
        for cell in table.cells:
            if cell.bbox is None:
                continue
            surviving = [
                token for token in cell.tokens
                if not any(token.bbox.overlap_ratio(box) >= 0.35 for box in boxes)
            ]
            if len(surviving) != len(cell.tokens):
                cell.tokens[:] = surviving
                cell.text = join_table_tokens(surviving)


def _bbox_to_dict(box: BBox) -> dict[str, float]:
    return {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1}
