from __future__ import annotations

import time

try:
    import resource as _resource
except ImportError:
    _resource = None  # type: ignore[assignment]
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .assemble.document import assemble_document
from .assemble.page import assemble_page
from .config import ExtractionMode, ExtractorConfig, effective_ocr_quality_policy
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
from .tables.detector import detect_tables_native
from .tables.visual import detect_visual_table
from .text.line_detector import reconstruct_native_lines
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
        timed_out = False
        source = PdfiumNativeEvidenceSource(path, config=self.config, password=password)
        open_start = time.perf_counter()
        with source:
            open_pdf_ms = (time.perf_counter() - open_start) * 1000
            context = source.open() if getattr(source, "_context", None) is None else source._context
            assert context is not None
            page_indices = _selected_page_indices(self.config.page_indices, context.page_count)
            for page_index in page_indices:
                timeout = self.config.security_limits.document_timeout_seconds
                if timeout is not None and time.perf_counter() - document_start >= timeout:
                    timed_out = True
                    document_warnings.append(
                        f"Document timeout reached before page {page_index + 1}"
                    )
                    break
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
                rendered_page = None
                render_start = time.perf_counter()
                if self.config.enable_complexity_render:
                    try:
                        rendered_page = source.render_page(
                            page_index,
                            scale=_safe_complexity_scale(
                                native_page.bbox.area,
                                self.config.security_limits.max_render_pixels,
                                self.config.complexity_render_scale,
                            ),
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
                reconstruct_start = time.perf_counter()
                native_lines = reconstruct_native_lines(native_page.characters)
                timings["native_reconstruct_ms"] = (time.perf_counter() - reconstruct_start) * 1000

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
                ocr_region_stats: dict[str, dict[str, Any]] = {}
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
                    # metadata-rotated pages such as GS2-P34.
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
                                scale=_safe_complexity_scale(
                                    native_page.bbox.area,
                                    self.config.security_limits.max_render_pixels,
                                    self.config.ocr_render_scale,
                                ),
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
                    else:
                        timings["render_ocr_ms"] = (
                            time.perf_counter() - ocr_render_start
                        ) * 1000
                        if self.ocr_engine is None:
                            warnings.append("OCR requested but no OCR engine was configured")
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

                            if region_ocr_requested:
                                for region in selected_regions:
                                    stat = ocr_region_stats.get(region.region_id, {})
                                    if stat.get("ocr_failed"):
                                        warnings.append(
                                            "OCR region recovery produced no usable tokens "
                                            f"for {region.region_id}"
                                        )
                            for attempt_error in ocr_attempt_errors:
                                warnings.append(
                                    f"OCR optional attempt unavailable: {attempt_error}"
                                )

                if ocr_requested and self.ocr_engine is not None and ocr_image is not None:
                    figure_start = time.perf_counter()
                    figure_refinements = _refine_wide_figure_ocr(
                        engine=self.ocr_engine,
                        page_image=ocr_image,
                        page=native_page,
                        page_index=page_index,
                        native_lines=native_lines,
                    )
                    if figure_refinements:
                        for (
                            box,
                            _all_lines,
                            all_tokens,
                            _figure_lines,
                            _figure_tokens,
                            passes,
                            batches,
                        ) in figure_refinements:
                            ocr_tokens = _replace_tokens_in_box(ocr_tokens, box, all_tokens)
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
                table_ocr_overrides: dict[str, tuple[list[Any], list[OcrToken]]] = {}
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
                    if visual_table is not None:
                        try:
                            refined = _refine_visual_table_ocr(
                                engine=self.ocr_engine,
                                page_image=ocr_image,
                                page=native_page,
                                page_index=page_index,
                                table=visual_table,
                                native_lines=native_lines,
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
                    )
                elif not use_ocr_as_primary and unmatched_ocr_lines:
                    _append_hybrid_ocr_regions(
                        regions=regions,
                        tables=tables,
                        unmatched_lines=unmatched_ocr_lines,
                        unmatched_tokens=unmatched_ocr_tokens,
                        page_index=page_index,
                        page_bbox=native_page.bbox,
                        complexity=complexity,
                        table_ocr_overrides=table_ocr_overrides,
                    )
                timings["table_ms"] = (time.perf_counter() - table_start) * 1000
                elapsed_ms = (time.perf_counter() - start) * 1000
                page_memory_end = _process_memory_snapshot()
                source_metrics_end = source.metrics_snapshot()
                actual_strategy = complexity.recommended_strategy
                if page_ocr_requested:
                    actual_strategy = PageStrategy.OCR_CANDIDATE
                elif region_ocr_requested and ocr_tokens:
                    actual_strategy = PageStrategy.MIXED
                if not ocr_requested:
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
                        "layout_regions": len(regions),
                        "layout_engine": type(self.layout_engine).__name__ if len(regions) > 0 else None,
                        "ocr_requested": ocr_requested,
                        "ocr_outcome": ocr_outcome,
                        "ocr_degraded": ocr_degraded,
                        "ocr_degraded_reasons": ocr_degraded_reasons,
                        "ocr_attempt_errors": ocr_attempt_errors,
                        "ocr_quality_policy": effective_ocr_quality_policy(self.config).value,
                        "ocr_baseline_quality": _quality_to_dict(getattr(self.ocr_engine, "last_baseline_quality", None)),
                        "ocr_image_profile": _image_profile_to_dict(getattr(self.ocr_engine, "last_image_profile", None)),
                        "ocr_recovery_triggered": bool(getattr(getattr(self.ocr_engine, "last_baseline_quality", None), "recovery_recommended", False)),
                        "ocr_recovery_reasons": list(getattr(getattr(self.ocr_engine, "last_baseline_quality", None), "reasons", ())),
                        "ocr_selected_variant": getattr(self.ocr_engine, "last_selected_variant", None),
                        "ocr_variants_attempted": list(getattr(self.ocr_engine, "last_variants_attempted", [])),
                        "ocr_variant_scores": dict(getattr(self.ocr_engine, "last_variant_scores", {})),
                        "ocr_variants_succeeded": list(getattr(self.ocr_engine, "last_variants_succeeded", [])),
                        "ocr_variants_failed": list(ocr_attempt_errors),
                        "ocr_candidate_count": len(getattr(self.ocr_engine, "last_variant_scores", {})),
                        "ocr_consensus_replacements": getattr(self.ocr_engine, "last_consensus_replacements", 0),
                        "ocr_consensus_insertions": getattr(self.ocr_engine, "last_consensus_insertions", 0),
                        "ocr_targeted_refinement_regions": [
                            region_id for region_id, stat in ocr_region_stats.items()
                            if stat.get("attempts", 0) > 0
                        ],
                        "ocr_targeted_refinement_passes": sum(
                            int(stat.get("ocr_passes", 0)) for stat in ocr_region_stats.values()
                        ),
                        "ocr_early_stop": effective_ocr_quality_policy(self.config).value == "adaptive" and len(getattr(self.ocr_engine, "last_variants_attempted", [])) <= 2 if self.ocr_engine is not None else False,
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
                        "timings_ms": timings,
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
                        raw_text=native_page.extracted_text,
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
        if timed_out:
            document_warnings.append("Extraction stopped cooperatively at the document timeout")
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
        document.diagnostics.facts["timed_out"] = timed_out
        return document


def _process_memory_snapshot() -> dict[str, int]:
    """Read current and peak resident memory for this Linux/WSL process."""
    current_rss = 0
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                current_rss = int(line.split()[1]) * 1024
                break
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        pass
    peak_rss = None
    if _resource is not None:
        try:
            peak_rss = int(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss) * 1024
        except Exception:
            pass
    return {
        "current_rss_bytes": current_rss,
        "peak_rss_bytes": peak_rss,
    }


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
        "contrast_span", "grayscale_stddev", "sharpness_score", "noise_score",
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
    """Recover only regions selected by the quality gate."""
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
        suppressed_tokens = 0
        if region.kind == RegionKind.FIGURE:
            region_tokens, suppressed_tokens = _suppress_figure_shape_hallucinations(
                region_tokens,
                region.bbox,
            )
        region.ocr_tokens = region_tokens
        tokens.extend(region_tokens)
        passes += result.ocr_passes
        batches += result.ocr_batches
        all_errors = [a.error for a in result.attempts if a.error is not None]
        ocr_failed = bool(result.attempts) and not region_tokens
        stats[region.region_id] = {
            "kind": region.kind.value,
            "tokens": len(region_tokens),
            "suppressed_shape_tokens": suppressed_tokens,
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


def _suppress_figure_shape_hallucinations(
    tokens: list[OcrToken],
    region_bbox: BBox,
) -> tuple[list[OcrToken], int]:
    """Reject repeated large pictograms mistaken for one-character text.

    Small digits and letters are intentionally retained because they are
    common on chart axes. Suppression is activated only when at least three
    unusually large, single-character hypotheses repeat inside one figure,
    a pattern typical of diagram/image cells rather than textual labels.
    """
    minimum_height = max(24.0, region_bbox.height * 0.13)
    suspicious = [
        token
        for token in tokens
        if len("".join(character for character in token.text if character.isalnum())) == 1
        and token.bbox.height >= minimum_height
        and token.bbox.width / max(token.bbox.height, 1.0) >= 0.75
    ]
    if len(suspicious) < 3:
        return tokens, 0
    frequencies: dict[str, int] = {}
    for token in suspicious:
        key = "".join(character for character in token.text.casefold() if character.isalnum())
        frequencies[key] = frequencies.get(key, 0) + 1
    if max(frequencies.values(), default=0) < 3:
        return tokens, 0
    suspicious_ids = {id(token) for token in suspicious}
    kept = [token for token in tokens if id(token) not in suspicious_ids]
    return kept, len(tokens) - len(kept)


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


def _safe_complexity_scale(page_area: float, max_pixels: int, requested_scale: float) -> float:
    scale = max(0.05, float(requested_scale))
    if page_area <= 0 or max_pixels <= 0:
        return scale
    return min(scale, (max_pixels / page_area) ** 0.5)


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
    """Prefer visible OCR over a short layer on an almost-raster page."""
    facts = complexity.facts
    image_coverage = float(facts.get("image_coverage") or 0.0)
    largest_image = float(facts.get("largest_image_coverage") or 0.0)
    native_length = int(facts.get("useful_text_length") or 0)
    return (
        image_coverage >= 0.75
        and largest_image >= 0.75
        and native_length <= 200
        and ComplexityReason.SPARSE_TEXT in complexity.reasons
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

    edge_boxes = [
        region.bbox
        for region in regions
        if region.kind in {RegionKind.HEADER, RegionKind.FOOTER}
    ]
    remaining = [
        line
        for line in remaining
        if not any(_line_in_box(line, box) for box in edge_boxes)
    ]
    if remaining:
        supplemental = full_page_text_region(page_index, page_bbox, remaining, complexity)
        supplemental.region_id = f"page-{page_index + 1}:ocr-supplement"
        supplemental.ocr_tokens = [
            token
            for token in unmatched_tokens
            if not any(_line_in_box(token, box) for box in edge_boxes + table_boxes)
        ]
        regions.append(supplemental)


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
            cell.text = "".join(token.text for token in selected).strip()
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
) -> tuple[Any, list[TextLine], list[OcrToken], int, int] | None:
    """Run a quality-first OCR pass over a detected visual table crop."""
    if engine is None or page_image is None or not table.page_fragments:
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
            goal=RegionRefinementGoal.TEXT,
        ),
    )
    refined_tokens = list(result.tokens)
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


def _refine_wide_figure_ocr(
    engine: Any,
    page_image: Any,
    page: Any,
    page_index: int,
    native_lines: list[TextLine],
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
    """OCR wide embedded figures in independent vertical panels."""
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
        if figure_bbox is None or figure_bbox.height <= 0:
            continue
        aspect = figure_bbox.width / figure_bbox.height
        if aspect < 2.4:
            continue
        panel_count = max(2, min(4, round(aspect)))
        panel_tokens: list[OcrToken] = []
        pass_count = 0
        batch_count = 0
        for panel in range(panel_count):
            panel_bbox = BBox(
                figure_bbox.x0 + figure_bbox.width * panel / panel_count,
                figure_bbox.y0,
                figure_bbox.x0 + figure_bbox.width * (panel + 1) / panel_count,
                figure_bbox.y1,
            )
            result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(bbox=panel_bbox),
            )
            tokens = list(result.tokens)
            pass_count += result.ocr_passes
            batch_count += result.ocr_batches
            if not tokens:
                continue
            panel_tokens.extend(tokens)
            title_bbox = BBox(
                panel_bbox.x0,
                figure_bbox.y0,
                panel_bbox.x1,
                figure_bbox.y0 + figure_bbox.height * 0.22,
            )
            title_result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(
                    bbox=title_bbox,
                    scale_factors=(2.0,),
                    goal=RegionRefinementGoal.TEXTUAL,
                ),
            )
            pass_count += title_result.ocr_passes
            batch_count += title_result.ocr_batches
            title_tokens = list(title_result.tokens)
            if title_tokens:
                panel_tokens = [
                    token
                    for token in panel_tokens
                    if not any(token.bbox.iou(title.bbox) >= 0.35 for title in title_tokens)
                ]
                panel_tokens.extend(title_tokens)
            label_bbox = BBox(
                panel_bbox.x0,
                figure_bbox.y0 + figure_bbox.height * 0.68,
                panel_bbox.x1,
                figure_bbox.y1,
            )
            label_result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(
                    bbox=label_bbox,
                    scale_factors=(2.0,),
                ),
            )
            pass_count += label_result.ocr_passes
            batch_count += label_result.ocr_batches
            label_tokens = list(label_result.tokens)
            if label_tokens:
                # F18: arbitrary month normalization removed — let OCR output stand as-is.
                panel_tokens = [
                    token
                    for token in panel_tokens
                    if not any(token.bbox.iou(label.bbox) >= 0.35 for label in label_tokens)
                ]
                panel_tokens.extend(label_tokens)
            numeric_bbox = BBox(
                panel_bbox.x0 + panel_bbox.width * 0.20,
                figure_bbox.y0 + figure_bbox.height * 0.20,
                panel_bbox.x1,
                figure_bbox.y0 + figure_bbox.height * 0.72,
            )
            numeric_result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(
                    bbox=numeric_bbox,
                    scale_factors=(2.0,),
                    goal=RegionRefinementGoal.NUMERIC,
                ),
            )
            pass_count += numeric_result.ocr_passes
            batch_count += numeric_result.ocr_batches
            numeric_tokens = list(numeric_result.tokens)
            if numeric_tokens:
                panel_tokens = [
                    token
                    for token in panel_tokens
                    if not any(token.bbox.iou(numeric.bbox) >= 0.35 for numeric in numeric_tokens)
                ]
                panel_tokens.extend(numeric_tokens)
            axis_bbox = BBox(
                panel_bbox.x0,
                figure_bbox.y0 + figure_bbox.height * 0.22,
                panel_bbox.x0 + panel_bbox.width * 0.20,
                figure_bbox.y0 + figure_bbox.height * 0.82,
            )
            axis_numeric_bbox = BBox(
                panel_bbox.x0,
                figure_bbox.y0 + figure_bbox.height * 0.08,
                panel_bbox.x0 + panel_bbox.width * 0.20,
                figure_bbox.y0 + figure_bbox.height * 0.94,
            )
            axis_numeric_result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(
                    bbox=axis_numeric_bbox,
                    scale_factors=(1.0, 1.5, 2.0, 3.0),
                    goal=RegionRefinementGoal.NUMERIC,
                ),
            )
            pass_count += axis_numeric_result.ocr_passes
            batch_count += axis_numeric_result.ocr_batches
            axis_numeric_tokens = list(axis_numeric_result.tokens)
            if axis_numeric_tokens:
                panel_tokens = [
                    token
                    for token in panel_tokens
                    if not any(
                        token.bbox.iou(axis_number.bbox) >= 0.35
                        for axis_number in axis_numeric_tokens
                    )
                ]
                panel_tokens.extend(axis_numeric_tokens)
            axis_result = refiner.refine(
                page_image,
                page_index,
                page.bbox,
                RegionRefinementRequest(
                    bbox=axis_bbox,
                    rotations=(90.0, 270.0),
                    goal=RegionRefinementGoal.TEXTUAL,
                ),
            )
            pass_count += axis_result.ocr_passes
            batch_count += axis_result.ocr_batches
            axis_text = list(axis_result.tokens)
            for axis_token in axis_text:
                panel_tokens = [
                    token
                    for token in panel_tokens
                    if not _line_in_box(token, axis_token.bbox)
                ]
            panel_tokens.extend(axis_text)
        if not panel_tokens:
            continue
        # F18: A-D single-letter filter removed — all OCR tokens retained.
        all_lines = reconstruct_ocr_lines(panel_tokens, page_index, page.bbox)
        figure_fusion = fuse_native_and_ocr(native_lines, panel_tokens)
        unmatched_tokens = list(figure_fusion.unmatched_ocr_tokens)
        unmatched_lines = reconstruct_ocr_lines(unmatched_tokens, page_index, page.bbox)
        refinements.append(
            (
                figure_bbox,
                all_lines,
                panel_tokens,
                unmatched_lines,
                unmatched_tokens,
                pass_count,
                batch_count,
            )
        )
    return refinements


def _replace_lines_in_box(lines: list[TextLine], box: BBox, replacement: list[TextLine]) -> list[TextLine]:
    outside = [line for line in lines if not _line_in_box(line, box)]
    return outside + replacement


def _replace_tokens_in_box(tokens: list[OcrToken], box: BBox, replacement: list[OcrToken]) -> list[OcrToken]:
    outside = [token for token in tokens if not _line_in_box(token, box)]
    return outside + replacement


def _normalize_chart_label(token: OcrToken) -> OcrToken:
    """Repair an unambiguous truncated Portuguese month in chart labels."""
    normalized = {"ju": "Jun", "jui": "Jun", "jur": "Jun", "jui n": "Jun"}.get(
        " ".join(token.text.casefold().split())
    )
    if normalized is None:
        return token
    return OcrToken(
        text=normalized,
        bbox=token.bbox,
        confidence=token.confidence,
        language=token.language,
        source=token.source,
        rotation=token.rotation,
    )


def _keep_wide_figure_token(token: OcrToken) -> bool:
    text = " ".join(token.text.casefold().split())
    if len(text) != 1 or not text.isalpha():
        return True
    # Single letters are meaningful chart series labels in this corpus. Other
    # isolated letters are recurrent OCR noise from axes and grid marks.
    return text.upper() in {"A", "B", "C", "D"}


def _crop_page_image(image: Any, page_bbox: BBox, region_bbox: BBox) -> Any:
    """Crop a rendered page using PDF-point coordinates at any render scale."""
    width, height = _image_size(image)
    scale_x = width / max(page_bbox.width, 1.0)
    scale_y = height / max(page_bbox.height, 1.0)
    left = max(0, int((region_bbox.x0 - page_bbox.x0) * scale_x))
    top = max(0, int((region_bbox.y0 - page_bbox.y0) * scale_y))
    right = min(width, max(left + 1, int((region_bbox.x1 - page_bbox.x0) * scale_x)))
    bottom = min(height, max(top + 1, int((region_bbox.y1 - page_bbox.y0) * scale_y)))
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
