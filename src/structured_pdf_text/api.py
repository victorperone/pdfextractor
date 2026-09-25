from __future__ import annotations

import math
import time
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
)
from .document import (
    Baseline,
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
    FatalExtractionError,
    raise_if_resource_exhausted,
)
from .evidence.complexity import ComplexityAnalyzer
from .memory import process_memory_snapshot
from .ocr.models import get_profile
from .evidence.decision import assess_region_recovery
from .fusion.token_fusion import fuse_native_and_ocr
from .layout.engine import NativeHeuristicLayoutEngine
from .layout.regions import full_page_text_region, regions_from_predictions
from .native.pdfium_source import PdfiumNativeEvidenceSource
from .ocr.engine import OcrEngine
from .ocr.reconstruct import reconstruct_ocr_lines
from .ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementGoal,
    RegionRefinementRequest,
)
from .ocr.quality import assess_ocr_quality
from .tables.detector import detect_tables_native
from .tables.visual import detect_visual_table
from .tables.validation import (
    build_table_construction_diagnostics,
    validate_table_geometry,
)
from .tables.text_tracks import assess_borderless_region
from .tables.text_join import join_table_tokens
from .text.line_detector import lines_to_text, reconstruct_native_lines, spacing_diagnostics
from .visibility import characters_occluded, detect_opaque_occlusion_boxes
from .geometry import BBox


class PdfTextExtractor:
    def __init__(
        self,
        config: ExtractorConfig | None = None,
        layout_engine: Any | None = None,
        ocr_engine: OcrEngine | None = None,
    ) -> None:
        self.config = config or ExtractorConfig()
        self.complexity_analyzer = ComplexityAnalyzer()
        self.layout_engine = layout_engine or NativeHeuristicLayoutEngine()
        if ocr_engine is not None:
            self.ocr_engine = ocr_engine
        elif _ocr_enabled(self.config):
            get_profile(self.config.language)
            from .ocr.paddle import PaddleOcrEngine

            self.ocr_engine = PaddleOcrEngine(
                language=self.config.language,
                num_threads=_resolve_num_threads(self.config.num_threads),
                ocr_batch_size=self.config.ocr_batch_size,
                quality_variants=self.config.ocr_quality_variants,
                quality_policy=effective_ocr_quality_policy(self.config).value,
                quality_thresholds=self.config.ocr_quality_thresholds,
            )
        else:
            self.ocr_engine = None

    def extract(
        self,
        path: str | Path,
        password: str | None = None,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> StructuredDocument:
        """Extract a document and classify resource failures at the API boundary."""
        try:
            return self._extract_impl(
                path,
                password=password,
                progress_callback=progress_callback,
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

    def _extract_impl(
        self,
        path: str | Path,
        password: str | None = None,
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> StructuredDocument:
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
                start = time.perf_counter()
                if progress_callback is not None:
                    progress_callback(len(pages) + 1, len(page_indices))
                page_memory_start = _process_memory_snapshot()
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
                    )
                    if effective < requested_scale:
                        render_limit_diagnostics[stage] = {
                            "reason": "max_render_pixels",
                            "maximum_pixels": self.config.security_limits.max_render_pixels,
                            "requested_scale": requested_scale,
                            "effective_scale": effective,
                            "resulting_width_pixels": math.ceil(native_page.bbox.width * effective),
                            "resulting_height_pixels": math.ceil(native_page.bbox.height * effective),
                        }
                    return effective

                rendered_page = None
                render_start = time.perf_counter()
                if self.config.enable_complexity_render:
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

                # Occlusion/redaction detection is intentionally experimental
                # and disabled by default. A dark opaque region can be
                # legitimate document design (for example reversed text,
                # dark table cells, banners or labels), so it must not remove
                # native characters unless explicitly enabled.
                opaque_occlusion_boxes: list[BBox] = []
                visible_characters = list(native_page.characters)
                redacted_character_count = 0

                if self.config.enable_experimental_occlusion_redaction:
                    opaque_occlusion_boxes = detect_opaque_occlusion_boxes(
                        native_page,
                        rendered_page,
                    )
                    visible_characters, redacted_character_count = characters_occluded(
                        native_page.characters,
                        opaque_occlusion_boxes,
                    )

                timings["visibility_ms"] = (
                    time.perf_counter() - visibility_start
                ) * 1000

                # The PDF textpage contains the original native text,
                # including characters that experimental occlusion detection
                # may just have removed. Never reconcile against that
                # unfiltered textpage after any character was suppressed.
                textpage_reconciliation_disabled_for_redaction = (
                    self.config.enable_experimental_occlusion_redaction
                    and redacted_character_count > 0
                )

                textpage_for_reconciliation = (
                    None
                    if textpage_reconciliation_disabled_for_redaction
                    else native_page.extracted_text
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
                ocr_lines: list[TextLine] = []
                unmatched_ocr_lines: list[TextLine] = []
                unmatched_ocr_tokens: list[OcrToken] = []
                fusion = None
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
                figure_ocr_requested = bool(
                    ocr_available_by_mode
                    and self.ocr_engine is not None
                    and native_page.objects.images
                    and not page_ocr_requested
                    and _figures_need_ocr(native_page, selected_regions)
                )
                figure_ocr_bindings: list[tuple[BBox, list[TextLine], list[OcrToken]]] = []
                if ocr_requested:
                    ocr_image = rendered_page
                    ocr_render_start = time.perf_counter()
                    try:
                        if (
                            ocr_image is None
                            or self.config.ocr_render_scale > self.config.complexity_render_scale
                        ):
                            ocr_image = source.render_page(
                                page_index,
                                scale=safe_render_scale("ocr", self.config.ocr_render_scale),
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
                        )
                        if mode == ExtractionMode.OCR or page_ocr_requested:
                            partial_reasons.append("page_ocr_unavailable")
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
                                    try:
                                        ocr_tokens = self.ocr_engine.recognize_page(
                                            ocr_image,
                                            page_index,
                                            native_page.bbox,
                                            quality_policy=effective_ocr_quality_policy(self.config).value,
                                        )
                                    except TypeError:
                                        # Preserve compatibility with early injected
                                        # engines that implement the two-argument API.
                                        ocr_tokens = self.ocr_engine.recognize_page(
                                            ocr_image,
                                            page_index,
                                        )
                                    ocr_passes_total = getattr(
                                        self.ocr_engine, "last_pass_count", None
                                    )
                                    ocr_batches_total = getattr(
                                        self.ocr_engine, "last_batch_count", None
                                    )
                                    ocr_attempt_errors = list(
                                        getattr(self.ocr_engine, "last_attempt_errors", [])
                                    )
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
                                warnings.append(
                                    f"OCR unavailable: {type(exc).__name__}: {exc}"
                                )
                                if mode == ExtractionMode.OCR or page_ocr_requested:
                                    partial_reasons.append("page_ocr_unavailable")

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
                        or self.config.ocr_render_scale > self.config.complexity_render_scale
                    ):
                        try:
                            ocr_image = source.render_page(
                                page_index,
                                scale=safe_render_scale("ocr", self.config.ocr_render_scale),
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
                        warnings=warnings,
                    )
                    if figure_refinements:
                        for (
                            box,
                            all_lines,
                            all_tokens,
                            figure_lines,
                            figure_tokens,
                            passes,
                            batches,
                        ) in figure_refinements:
                            ocr_tokens = _replace_tokens_in_box(ocr_tokens, box, all_tokens)
                            figure_ocr_bindings.append((box, figure_lines, figure_tokens))
                            _bind_figure_ocr_to_regions(
                                regions,
                                box,
                                figure_lines,
                                figure_tokens,
                            )
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
                ):
                    warnings.append(
                        "OCR completed but produced no usable tokens for an OCR-primary page"
                    )

                if region_ocr_requested:
                    for region in selected_regions:
                        region.ocr_tokens = [
                            token for token in ocr_tokens if _line_in_box(token, region.bbox)
                        ]

                if page_ocr_requested and ocr_tokens and ocr_image is not None:
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
                            ),
                            quality_policy=effective_ocr_quality_policy(self.config).value,
                            thresholds=self.config.ocr_quality_thresholds,
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

                # Recompute alignment after refinements so diagnostics and
                # supplemental text describe the final OCR evidence.
                if ocr_tokens:
                    ocr_lines = reconstruct_ocr_lines(
                        ocr_tokens,
                        page_index,
                        native_page.bbox,
                    )
                    fusion_start = time.perf_counter()
                    fusion = fuse_native_and_ocr(native_lines, ocr_tokens)
                    unmatched_ocr_tokens = list(fusion.unmatched_ocr_tokens)
                    unmatched_ocr_lines = reconstruct_ocr_lines(
                        unmatched_ocr_tokens,
                        page_index,
                        native_page.bbox,
                    )
                    timings["fusion_ms"] = (
                        time.perf_counter() - fusion_start
                    ) * 1000

                use_ocr_as_primary = bool(ocr_lines) and page_ocr_requested
                if use_ocr_as_primary:
                    ocr_region = full_page_text_region(
                        page_index,
                        native_page.bbox,
                        ocr_lines,
                        complexity,
                    )
                    ocr_region.region_id = f"page-{page_index + 1}:ocr-primary"
                    ocr_region.ocr_tokens = ocr_tokens
                    regions = [ocr_region]
                table_start = time.perf_counter()
                try:
                    tables = detect_tables_native(native_page, regions)
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
                visual_table = None
                if not tables and rendered_page is not None and (
                    self.config.enable_tables
                    or complexity.layout_needed
                    or mode in {ExtractionMode.BALANCED, ExtractionMode.OCR}
                ):
                    try:
                        visual_table = detect_visual_table(
                            native_page,
                            rendered_page,
                            tokens=[token for line in ocr_lines for token in line.tokens],
                        )
                    except FatalExtractionError:
                        raise
                    except Exception as exc:
                        raise_if_resource_exhausted(
                            exc,
                            page_index=page_index,
                            stage="visual_table_detection",
                            details=_process_memory_snapshot(),
                        )
                        visual_table = None
                        warnings.append(
                            f"Visual table detection unavailable: {type(exc).__name__}: {exc}"
                        )
                        if self.config.enable_tables or mode in {ExtractionMode.BALANCED, ExtractionMode.OCR}:
                            partial_reasons.append("visual_table_detection_unavailable")
                    if visual_table is not None:
                        try:
                            refined = _refine_visual_table_ocr(
                                engine=self.ocr_engine,
                                page_image=ocr_image,
                                page=native_page,
                                page_index=page_index,
                                table=visual_table,
                                native_lines=native_lines,
                                quality_policy=effective_ocr_quality_policy(self.config).value,
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
                        tables = [visual_table]
                consumed_table_ocr = _merge_ocr_region_tokens_into_table_cells(
                    tables=tables,
                    regions=regions,
                    page_index=page_index,
                )
                if consumed_table_ocr:
                    unmatched_ocr_tokens = [
                        token
                        for token in unmatched_ocr_tokens
                        if id(token) not in consumed_table_ocr
                    ]
                    unmatched_ocr_lines = reconstruct_ocr_lines(
                        unmatched_ocr_tokens,
                        page_index,
                        native_page.bbox,
                    )
                    ocr_table_tokens += len(consumed_table_ocr)
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
                    )
                timings["table_ms"] = (time.perf_counter() - table_start) * 1000
                elapsed_ms = (time.perf_counter() - start) * 1000
                page_memory_end = _process_memory_snapshot()
                source_metrics_end = source.metrics_snapshot()
                actual_strategy = complexity.recommended_strategy
                if page_ocr_requested:
                    actual_strategy = PageStrategy.OCR_CANDIDATE
                elif (region_ocr_requested or figure_ocr_requested) and ocr_tokens:
                    actual_strategy = PageStrategy.MIXED
                ocr_any_requested = ocr_requested or figure_ocr_requested
                if not ocr_any_requested:
                    ocr_outcome = "not_requested"
                    ocr_degraded = False
                    ocr_degraded_reasons: list[str] = []
                elif ocr_tokens:
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
                ocr_diag_engine = self.ocr_engine if ocr_any_requested else None
                diagnostics = PageDiagnostics(
                    page_index=page_index,
                    strategy=actual_strategy,
                    reasons=sorted(complexity.reasons, key=lambda item: item.value),
                    native_chars=len(native_page.characters),
                    native_text_length=len(native_page.extracted_text or ""),
                    ocr_tokens_added=len(ocr_tokens),
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
                        "layout_engine": type(self.layout_engine).__name__ if len(regions) > 0 else None,
                        "ocr_requested": ocr_any_requested,
                        "ocr_outcome": ocr_outcome,
                        "ocr_degraded": ocr_degraded,
                        "ocr_degraded_reasons": ocr_degraded_reasons,
                        "ocr_attempt_errors": ocr_attempt_errors,
                        "ocr_quality_policy": effective_ocr_quality_policy(self.config).value,
                        "ocr_baseline_quality": _quality_to_dict(getattr(ocr_diag_engine, "last_baseline_quality", None)),
                        "ocr_image_profile": _image_profile_to_dict(getattr(ocr_diag_engine, "last_image_profile", None)),
                        "ocr_recovery_triggered": bool(getattr(ocr_diag_engine, "last_recovery_triggered", False)),
                        "ocr_recovery_reasons": list(getattr(ocr_diag_engine, "last_recovery_reasons", [])),
                        "ocr_selected_variant": getattr(ocr_diag_engine, "last_selected_variant", None),
                        "ocr_variants_attempted": list(getattr(ocr_diag_engine, "last_variants_attempted", [])),
                        "ocr_variant_scores": dict(getattr(ocr_diag_engine, "last_variant_scores", {})),
                        "ocr_variants_succeeded": list(getattr(ocr_diag_engine, "last_variants_succeeded", [])),
                        "ocr_variants_failed": list(ocr_attempt_errors),
                        "ocr_candidate_count": getattr(ocr_diag_engine, "last_candidate_count", 0),
                        "ocr_candidate_metrics": dict(getattr(ocr_diag_engine, "last_candidate_metrics", {})),
                        "ocr_candidate_quality": {
                            name: float(candidate.get("quality_score", 0.0))
                            for name, candidate in getattr(ocr_diag_engine, "last_candidate_metrics", {}).items()
                        },
                        "ocr_candidate_coverage": {
                            name: float(candidate.get("spatial_coverage_score", 0.0))
                            for name, candidate in getattr(ocr_diag_engine, "last_candidate_metrics", {}).items()
                        },
                        "ocr_candidate_line_clusters": {
                            name: int(candidate.get("line_cluster_count", 0))
                            for name, candidate in getattr(ocr_diag_engine, "last_candidate_metrics", {}).items()
                        },
                        "ocr_fusion_replacements_attempted": getattr(ocr_diag_engine, "last_fusion_replacements_attempted", 0),
                        "ocr_fusion_replacements_accepted": getattr(ocr_diag_engine, "last_fusion_replacements_accepted", 0),
                        "ocr_fusion_replacements_rolled_back": getattr(ocr_diag_engine, "last_fusion_replacements_rolled_back", 0),
                        "ocr_fusion_lost_clusters": getattr(ocr_diag_engine, "last_fusion_lost_clusters", 0),
                        "ocr_fusion_conflict_clusters": getattr(ocr_diag_engine, "last_fusion_conflict_clusters", 0),
                        # Compatibility alias for the historical diagnostic name.
                        "ocr_fusion_duplicate_clusters": getattr(ocr_diag_engine, "last_fusion_duplicate_clusters", 0),
                        "ocr_consensus_replacements": getattr(ocr_diag_engine, "last_consensus_replacements", 0),
                        "ocr_consensus_insertions": getattr(ocr_diag_engine, "last_consensus_insertions", 0),
                        "ocr_orientation_selected": getattr(ocr_diag_engine, "last_orientation_selected", None),
                        "ocr_orientation_attempts": list(getattr(ocr_diag_engine, "last_orientation_attempts", [])),
                        "ocr_enhancement_orientation": getattr(ocr_diag_engine, "last_enhancement_orientation", None),
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
                        "ocr_early_stop": bool(getattr(ocr_diag_engine, "last_early_stop", False)),
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
                        "ocr_passes": ocr_passes_total,
                        "ocr_batches": ocr_batches_total,
                        "deskew_angle_deg": (
                            getattr(self.ocr_engine, "last_deskew_angle", 0.0)
                            if page_ocr_requested and self.ocr_engine is not None
                            else None
                        ),
                        "ocr_table_tokens": ocr_table_tokens,
                        "ocr_figure_tokens": ocr_figure_tokens,
                        "ocr_matched_tokens": fusion.matched_ocr_tokens if fusion else 0,
                        "ocr_unmatched_tokens": len(fusion.unmatched_ocr_tokens) if fusion else 0,
                        "ocr_conflicts": len(fusion.conflicts) if fusion else 0,
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
                pages.append(
                    assemble_page(
                        page_index=page_index,
                        page_bbox=native_page.bbox,
                        regions=regions,
                        tables=tables,
                        diagnostics=diagnostics,
                        # Raw output reflects the reconstructed native
                        # evidence. Experimental visual occlusion filtering,
                        # when enabled, has already been applied before
                        # native_lines were built.
                        raw_text=lines_to_text(native_lines),
                        native_evidence=(
                            native_page if self.config.retain_native_evidence else None
                        ),
                    )
                )
            metadata = DocumentMetadata(
                source_path=str(Path(path)),
                page_count=context.page_count,
                pdfium_version=context.pdfium_version,
            )
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
        if self.ocr_engine is not None:
            profile = get_profile(self.config.language)
            document.diagnostics.facts["ocr_profile"] = self.config.language
            document.diagnostics.facts["ocr_models"] = {
                "detection": profile.detection,
                "recognition": profile.recognition,
            }
        return document


def _process_memory_snapshot() -> dict[str, Any]:
    """Return process RSS metrics with explicit units and collection source."""
    return process_memory_snapshot()


def _quality_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    names = (
        "score", "sufficient", "recovery_recommended", "character_count",
        "token_count", "char_weighted_confidence", "median_confidence",
        "lower_quartile_confidence", "low_confidence_character_ratio",
        "horizontal_token_ratio", "printable_character_ratio",
        "alphanumeric_character_ratio", "suspicious_token_ratio",
        "detection_count", "recognition_count", "recognition_yield",
        "orientation_incoherent", "reasons",
    )
    return {name: list(getattr(value, name)) if name == "reasons" else getattr(value, name) for name in names}


def _image_profile_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    names = (
        "available", "contrast_span", "grayscale_stddev", "sharpness_score", "noise_score",
        "median_token_height_px", "low_contrast", "likely_blurred_or_small",
        "likely_noisy",
    )
    return {name: getattr(value, name) for name in names}


def _resolve_num_threads(num_threads: int) -> int:
    """Resolve o número efetivo de threads para o motor OCR.

    0  → auto: usa os.cpu_count() com fallback 2
    -1 → não configurar (deixar PaddlePaddle decidir)
    n  → usar exatamente n (mínimo 1)
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
    return config.enable_ocr or config.normalized_mode() in {
        ExtractionMode.BALANCED,
        ExtractionMode.OCR,
    }


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
    and delegates to :class:`OcrRegionRefiner`.  Regions whose quality reasons
    include ``"small"``, ``"sparse"``, ``"missing"`` or ``"damaged"`` receive
    scale factors ``(1.0, 1.5, 2.0)``; all others use only ``(1.0,)``.  The
    ``quality_reasons`` field is forwarded verbatim to the refiner so they
    appear in the ``REGION_SELECTED`` debug log entry.

    The RGB budget gate in :func:`plan_ocr_scales` may further reduce the
    effective scale list; blocked variants are never created or sent to Paddle.

    Returns:
        A 4-tuple of ``(tokens, total_passes, total_batches, per_region_stats)``.
        *tokens* is de-duplicated by position across all recovered regions.
        *per_region_stats* maps ``region_id`` to a dict with token counts,
        attempt counts, errors and selected scale/rotation for diagnostics.
    """
    refiner = OcrRegionRefiner(engine)
    requests = [
        RegionRefinementRequest(
            bbox=region.bbox,
            scale_factors=(1.0, 1.5, 2.0) if any(
                marker in " ".join(region.quality.reasons).lower()
                for marker in ("small", "sparse", "missing", "damaged")
            ) else (1.0,),
            quality_variants=quality_variants,
            quality_policy=quality_policy,
            goal=RegionRefinementGoal.TEXT,
            page_rotation=page_rotation,
            quality_reasons=tuple(region.quality.reasons),
        )
        for region in regions
    ]
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
        region.ocr_tokens = region_tokens
        tokens.extend(region_tokens)
        passes += result.ocr_passes
        batches += result.ocr_batches
        all_errors = [a.error for a in result.attempts if a.error is not None]
        ocr_failed = bool(result.attempts) and not region_tokens
        stats[region.region_id] = {
            "kind": region.kind.value,
            "tokens": len(region_tokens),
            "attempts": len(result.attempts),
            "attempt_errors": len(all_errors),
            "ocr_failed": ocr_failed,
            "attempt_error_messages": all_errors if all_errors else None,
            "selected_scale_factor": result.selected_scale_factor,
            "selected_rotation": result.selected_rotation,
            "ocr_passes": result.ocr_passes,
            "ocr_batches": result.ocr_batches,
        }
    return _deduplicate_region_ocr_tokens(tokens), passes, batches, stats


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
    passes = 0
    batches = 0
    stats: dict[str, dict[str, Any]] = {}
    for index, group in enumerate(groups, start=1):
        box = BBox.union_all([line.bbox for line, _ in group]).expand(4.0).intersection(page_bbox)
        if box is None or box.area <= 0:
            continue
        area_ratio = box.area / max(page_bbox.area, 1.0)
        scales = (1.5, 2.0, 3.0) if area_ratio <= 0.12 else (1.5, 2.0)
        request = RegionRefinementRequest(
            bbox=box,
            scale_factors=scales,
            rotations=(0.0,),
            quality_variants=True,
            quality_policy=quality_policy,
            goal=RegionRefinementGoal.TEXT,
            page_rotation=page_rotation,
        )
        result = refiner.refine(page_image, page_index, page_bbox, request)
        passes += result.ocr_passes
        batches += result.ocr_batches
        old_tokens = [token for token in refined_tokens if _line_in_box(token, box)]
        new_tokens = list(result.tokens)
        old_quality = assess_ocr_quality(old_tokens, thresholds=thresholds)
        new_quality = assess_ocr_quality(new_tokens, thresholds=thresholds)
        accepted = bool(new_tokens) and (
            not old_tokens
            or new_quality.score >= old_quality.score + 0.03
            or (new_quality.sufficient and not old_quality.sufficient)
        )
        if accepted:
            refined_tokens = _replace_tokens_in_box(refined_tokens, box, new_tokens)
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
) -> float:
    """Preserve requested resolution unless PDFium's ceil-rounded raster exceeds its cap."""
    scale = max(0.0, float(requested_scale))
    if page_width <= 0 or page_height <= 0 or max_pixels <= 0 or scale == 0:
        return scale

    def raster_pixels(candidate: float) -> int:
        return math.ceil(page_width * candidate) * math.ceil(page_height * candidate)

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


def _selected_page_indices(page_indices: tuple[int, ...] | None, page_count: int) -> list[int]:
    if page_indices is None:
        return list(range(page_count))
    selected = sorted(set(page_indices))
    if any(index < 0 or index >= page_count for index in selected):
        raise ValueError(
            f"page_indices must be zero-based values within [0, {page_count}); got {page_indices}"
        )
    return selected


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
) -> None:
    """Attach unmatched OCR to tables/remaining body without duplicating regions."""
    remaining = list(unmatched_lines)
    table_boxes: list[BBox] = []
    for table in tables:
        if table.method != TableMethod.VISUAL_MODEL or not table.page_fragments:
            continue
        boxes = [fragment.bbox for fragment in table.page_fragments if fragment.bbox is not None]
        if not boxes:
            continue
        table_bbox = BBox.union_all(boxes)
        table_boxes.append(table_bbox)
        table_lines = [line for line in remaining if _line_in_box(line, table_bbox)]
        table_tokens = [token for token in unmatched_tokens if _line_in_box(token, table_bbox)]
        override = (table_ocr_overrides or {}).get(table.table_id)
        if override is not None:
            table_lines, table_tokens = override
        if not table_lines:
            continue
        table_lines = _rebuild_table_ocr_lines(table, table_lines, page_index)
        table_region = full_page_text_region(page_index, table_bbox, table_lines, complexity)
        table_region.region_id = f"{table.table_id}:ocr"
        table_region.kind = RegionKind.TABLE
        table_region.layout_confidence = table.confidence
        table_region.ocr_tokens = table_tokens
        regions.append(table_region)
        remaining = [line for line in remaining if not _line_in_box(line, table_bbox)]

    figure_boxes: list[BBox] = []
    for index, (figure_bbox, figure_lines, figure_tokens) in enumerate(
        figure_ocr_bindings or (),
        start=1,
    ):
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

    remaining = [
        line
        for line in remaining
        if not any(_line_in_box(line, box) for box in table_boxes + figure_boxes)
    ]
    if remaining:
        supplemental = full_page_text_region(page_index, page_bbox, remaining, complexity)
        supplemental.region_id = f"page-{page_index + 1}:ocr-supplement"
        supplemental.ocr_tokens = [
            token
            for token in unmatched_tokens
            if not any(_line_in_box(token, box) for box in table_boxes + figure_boxes)
        ]
        regions.append(supplemental)


def _best_figure_region(
    regions: list[LayoutRegion],
    figure_bbox: BBox,
) -> LayoutRegion | None:
    candidates = [
        region
        for region in regions
        if region.kind == RegionKind.FIGURE and region.bbox.iou(figure_bbox) >= 0.25
    ]
    return max(
        candidates,
        key=lambda region: region.bbox.iou(figure_bbox),
        default=None,
    )


def _bind_figure_ocr_to_regions(
    regions: list[LayoutRegion],
    figure_bbox: BBox,
    figure_lines: list[TextLine],
    figure_tokens: list[OcrToken],
) -> None:
    region = _best_figure_region(regions, figure_bbox)
    if region is None:
        return
    region.ocr_lines = list(figure_lines)
    region.ocr_tokens = list(figure_tokens)


def _figures_need_ocr(
    page: NativePageEvidence,
    selected_regions: list[LayoutRegion],
) -> bool:
    image_boxes = [image.bbox for image in page.objects.images if image.bbox is not None]
    if not image_boxes:
        return False
    for image_bbox in image_boxes:
        covered = any(
            region.bbox.overlap_ratio(image_bbox) >= 0.80
            or image_bbox.overlap_ratio(region.bbox) >= 0.80
            for region in selected_regions
        )
        if not covered:
            return True
    return False


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
    for table in tables:
        source_lines = _source_lines_for_table(
            table,
            regions,
            extra_lines,
            table_ocr_overrides,
        )
        direction = _table_writing_direction(source_lines)
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
    *,
    tables: list[Any],
    regions: list[LayoutRegion],
    page_index: int,
) -> set[int]:
    """Attach OCR from an image wholly inside a native table cell.

    Native grid detection owns the cell geometry, while regional OCR owns the
    pixels of an embedded image.  When the image bbox is substantially inside
    one cell, both evidences belong to that cell.  OCR outside a cell, and all
    visual-table refinements, remain on their existing paths.
    """
    consumed: set[int] = set()
    for table in tables:
        if table.method == TableMethod.VISUAL_MODEL:
            continue
        cells = [cell for cell in table.cells if cell.bbox is not None]
        if not cells:
            continue
        for region in regions:
            if not region.ocr_tokens or region.kind == RegionKind.TABLE:
                continue
            matching_cells = [
                cell
                for cell in cells
                if region.bbox.overlap_ratio(cell.bbox) >= 0.80
                and cell.bbox.overlap_ratio(region.bbox) >= 0.75
            ]
            if not matching_cells:
                continue
            for token in region.ocr_tokens:
                cell = next(
                    (
                        candidate
                        for candidate in matching_cells
                        if candidate.bbox.x0 <= token.bbox.cx <= candidate.bbox.x1
                        and candidate.bbox.y0 <= token.bbox.cy <= candidate.bbox.y1
                    ),
                    None,
                )
                if cell is None:
                    continue
                if any(
                    existing.text == token.text
                    and existing.bbox.iou(token.bbox) >= 0.80
                    for existing in cell.tokens
                ):
                    consumed.add(id(token))
                    continue
                cell.tokens.append(
                    TextToken(
                        text=token.text,
                        bbox=token.bbox,
                        sources=[
                            EvidenceRef(
                                token.source,
                                page_index,
                                f"ocr-table:{table.table_id}:{cell.row}:{cell.col}",
                            )
                        ],
                        confidence=max(0.0, min(1.0, token.confidence or 0.0)),
                        normalized_text=token.text,
                        provenance=token.provenance or "table_cell_ocr",
                        rotation=token.rotation,
                    )
                )
                cell.text = join_table_tokens(cell.tokens)
                consumed.add(id(token))
    return consumed


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


def _line_in_box(value: Any, box: BBox) -> bool:
    bbox = value.bbox
    return bbox.overlap_ratio(box) >= 0.25 or box.overlap_ratio(bbox) >= 0.25


def _rebuild_table_ocr_lines(table: Any, lines: list[TextLine], page_index: int) -> list[TextLine]:
    """Serialize OCR text by detected table row/column instead of OCR line order.

    OCR line reconstruction is intentionally page-oriented. On dense raster
    tables it can interleave neighboring rows or split a row into fragments.
    Once a visual grid has assigned tokens to cells, the cell coordinates are
    the stronger ordering signal. Only unmatched OCR tokens are used here so
    the hybrid path does not duplicate authoritative native text.
    """
    if not getattr(table, "cells", None):
        return lines
    source_tokens = [token for line in lines for token in line.tokens]
    if not source_tokens:
        return lines
    # token.rotation is provenance metadata, not a command to mutate the table
    # structure.  By the time tokens reach this function their bbox coordinates
    # are already in canonical page space (box_transform was applied in
    # paddle._tokens_from_result).  Applying any metadata-based axis reversal on
    # top of already-canonical bboxes would produce a double-rotation.  Source geometry
    # validated by _validate_detected_tables is the sole authority for cell order.
    cell_tokens: dict[tuple[int, int], list[TextToken]] = {}
    for cell in table.cells:
        if cell.bbox is None:
            continue
        selected = [
            token
            for token in source_tokens
            if cell.bbox.x0 <= token.bbox.cx <= cell.bbox.x1
            and cell.bbox.y0 <= token.bbox.cy <= cell.bbox.y1
        ]
        while selected and selected[0].text.isspace():
            selected.pop(0)
        while selected and selected[-1].text.isspace():
            selected.pop()
        if selected:
            cell.text = join_table_tokens(selected)
            cell_tokens[(cell.row, cell.col)] = selected

    if not cell_tokens:
        return lines
    rebuilt: list[TextLine] = []
    for row in range(max(0, int(table.row_count))):
        row_tokens: list[TextToken] = []
        occupied_cells = [
            cell
            for cell in table.cells
            if cell.row == row and (cell.row, cell.col) in cell_tokens
        ]
        for cell_index, cell in enumerate(sorted(occupied_cells, key=lambda item: item.col)):
            if cell_index and row_tokens:
                previous = row_tokens[-1]
                current = cell_tokens[(cell.row, cell.col)][0]
                row_tokens.append(
                    TextToken(
                        text=" ",
                        bbox=(
                            BBox(previous.bbox.x1, cell.bbox.y0, current.bbox.x0, cell.bbox.y1)
                            if previous.bbox.x1 <= current.bbox.x0
                            else BBox(previous.bbox.x1, cell.bbox.y0, previous.bbox.x1, cell.bbox.y1)
                        ),
                        sources=[
                            EvidenceRef(
                                SourceKind.TABLE_MODEL,
                                page_index,
                                f"table-gap:{table.table_id}:{cell.row}:{cell.col}",
                            )
                        ],
                        confidence=0.55,
                        normalized_text=" ",
                        flags=set(),
                    )
                )
            row_tokens.extend(cell_tokens[(cell.row, cell.col)])
        if not row_tokens:
            continue
        row_tokens = _merge_short_table_fragments(row_tokens)
        row_bbox = BBox.union_all([token.bbox for token in row_tokens])
        rebuilt.append(
            TextLine(
                tokens=row_tokens,
                bbox=row_bbox,
                baseline=Baseline(y=row_bbox.y1),
                direction=WritingDirection.LEFT_TO_RIGHT,
                native_order_min=row,
                native_order_max=row,
            )
        )
    return rebuilt or lines


def _merge_short_table_fragments(tokens: list[TextToken]) -> list[TextToken]:
    """Remove spaces caused by splitting one alphanumeric word into boxes."""
    compacted: list[TextToken] = []
    for index, token in enumerate(tokens):
        if token.text.isspace():
            previous = next((item for item in reversed(compacted) if not item.text.isspace()), None)
            following = next(
                (item for item in tokens[index + 1 :] if not item.text.isspace()),
                None,
            )
            if (
                previous is not None
                and following is not None
                and len(previous.text.strip()) >= 4
                and len(following.text.strip()) <= 3
                and previous.text.strip()[-1].isalnum()
                and following.text.strip()[0].isalnum()
            ):
                continue
        compacted.append(token)
    return compacted


def _refine_visual_table_ocr(
    engine: Any,
    page_image: Any,
    page: Any,
    page_index: int,
    table: Any,
    native_lines: list[TextLine],
    quality_policy: str = "baseline",
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
    result = OcrRegionRefiner(engine).refine(
        page_image,
        page_index,
        page.bbox,
        RegionRefinementRequest(
            bbox=table_bbox,
            quality_variants=True,
            quality_policy=quality_policy,
            goal=RegionRefinementGoal.TEXT,
        ),
    )
    refined_tokens = [
        replace(token, provenance="visual_table_refinement")
        for token in result.tokens
    ]
    if not refined_tokens:
        return None
    refined_table = detect_visual_table(
        page=page,
        image=page_image,
        table_id=table.table_id,
        tokens=refined_tokens,
    )
    if refined_table is None:
        # Keep the original grid and only replace its cell text through the
        # refined token stream in the hybrid region.
        refined_table = table
    refined_fusion = fuse_native_and_ocr(native_lines, refined_tokens)
    unmatched = list(refined_fusion.unmatched_ocr_tokens)
    lines = reconstruct_ocr_lines(unmatched, page_index, page.bbox)
    lines = _rebuild_table_ocr_lines(refined_table, lines, page_index)
    return refined_table, lines, unmatched, result.ocr_passes, result.ocr_batches


def _refine_figure_ocr(
    engine: Any,
    page_image: Any,
    page: Any,
    page_index: int,
    native_lines: list[TextLine],
    quality_policy: str = "baseline",
    warnings: list[str] | None = None,
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
    for image in page.objects.images:
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
            ocr_boxes = _subfigure_boxes(page_image, page.bbox, figure_bbox)
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
                result = refiner.refine(
                    page_image,
                    page_index,
                    page.bbox,
                    RegionRefinementRequest(
                        bbox=ocr_box,
                        quality_variants=str(quality_policy).lower() != "baseline",
                        quality_policy=quality_policy,
                        goal=RegionRefinementGoal.TEXT,
                    ),
                )
                figure_tokens = [
                    replace(token, provenance="figure_ocr")
                    for token in result.tokens
                ]
                if not figure_tokens:
                    continue
                all_lines = reconstruct_ocr_lines(figure_tokens, page_index, page.bbox)
                figure_fusion = fuse_native_and_ocr(native_lines, figure_tokens)
                unmatched_tokens = list(figure_fusion.unmatched_ocr_tokens)
                unmatched_lines = reconstruct_ocr_lines(unmatched_tokens, page_index, page.bbox)
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


def _subfigure_boxes(image: Any, page_bbox: BBox, figure_bbox: BBox) -> list[BBox]:
    """Find well-separated horizontal subfigures using rendered ink gaps.

    Returns an empty list when the figure has no rendereable area within the
    page (i.e. ``_crop_page_image`` returns ``None``).  Returns
    ``[figure_bbox]`` as a single undivided box when the crop exists but is too
    small to split or splitting finds no significant gaps.
    """
    crop = _crop_page_image(image, page_bbox, figure_bbox)
    if crop is None:
        # Figure is completely outside the rendered page area; no subfigures.
        return []
    width, height = _image_size(crop)
    if width < 240 or height < 80 or figure_bbox.width / max(figure_bbox.height, 1.0) < 2.0:
        return [figure_bbox]
    try:
        pixels = list(crop.convert("L").getdata())
    except Exception:
        return [figure_bbox]
    occupied: list[bool] = []
    for x in range(width):
        dark = sum(
            pixels[y * width + x] < 210
            for y in range(height)
        ) / max(height, 1)
        occupied.append(dark > 0.025)
    gaps: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(occupied + [True]):
        if not value and start is None:
            start = index
        elif value and start is not None:
            if index - start >= max(8, int(width * 0.025)):
                gaps.append((start, index))
            start = None
    if not gaps:
        return [figure_bbox]
    segments: list[tuple[int, int]] = []
    left = 0
    for gap_left, gap_right in gaps:
        if gap_left - left >= int(width * 0.18):
            segments.append((left, gap_left))
        left = gap_right
    if width - left >= int(width * 0.18):
        segments.append((left, width))
    if len(segments) < 2 or len(segments) > 4:
        return [figure_bbox]
    scale_x = figure_bbox.width / max(width, 1)
    return [
        BBox(
            figure_bbox.x0 + left * scale_x,
            figure_bbox.y0,
            figure_bbox.x0 + right * scale_x,
            figure_bbox.y1,
        )
        for left, right in segments
    ]


def _replace_lines_in_box(lines: list[TextLine], box: BBox, replacement: list[TextLine]) -> list[TextLine]:
    outside = [line for line in lines if not _line_in_box(line, box)]
    return outside + replacement


def _replace_tokens_in_box(tokens: list[OcrToken], box: BBox, replacement: list[OcrToken]) -> list[OcrToken]:
    outside = [token for token in tokens if not _line_in_box(token, box)]
    return outside + replacement


def _crop_page_image(image: Any, page_bbox: BBox, region_bbox: BBox) -> Any | None:
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
    import math

    width, height = _image_size(image)

    # Guard: page geometry must be well-formed and match the image.
    if page_bbox.width <= 0 or page_bbox.height <= 0 or width <= 0 or height <= 0:
        return None

    # Guard: figure coordinates must be finite.
    for coord in (region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1):
        if not math.isfinite(coord):
            return None

    # Intersect the figure bbox with the page bbox to find the visible area.
    # BBox.intersection() returns None when there is no overlap.
    visible = region_bbox.intersection(page_bbox)
    if visible is None:
        return None

    scale_x = width / page_bbox.width
    scale_y = height / page_bbox.height

    # Convert the visible intersection to pixel coordinates using the page
    # origin so that pages with a non-zero CropBox origin are handled correctly.
    left = int((visible.x0 - page_bbox.x0) * scale_x)
    top = int((visible.y0 - page_bbox.y0) * scale_y)
    # Use ceil for the right/bottom edges to preserve sub-pixel areas.
    right = math.ceil((visible.x1 - page_bbox.x0) * scale_x)
    bottom = math.ceil((visible.y1 - page_bbox.y0) * scale_y)

    # Clamp to the actual image dimensions.
    left = min(width, max(0, left))
    right = min(width, max(0, right))
    top = min(height, max(0, top))
    bottom = min(height, max(0, bottom))

    # After clamping, the region may have collapsed to zero pixels.
    if right <= left or bottom <= top:
        return None

    if hasattr(image, "crop"):
        return image.crop((left, top, right, bottom))
    return image[top:bottom, left:right]


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


def _bbox_to_dict(box: BBox) -> dict[str, float]:
    return {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1}
