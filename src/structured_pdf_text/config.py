from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ExtractionMode(str, Enum):
    """Top-level extraction strategy for a document.

    ``NATIVE`` uses only PDFium-extracted text; no image rendering or OCR.
    ``FAST`` is an alias for NATIVE kept for CLI compatibility.
    ``BALANCED`` enables layout analysis, selective OCR and table detection.
    ``OCR`` forces full-page OCR on every page regardless of native text quality.
    """

    NATIVE = "native"
    FAST = "fast"
    BALANCED = "balanced"
    OCR = "ocr"


class OcrQualityPolicy(str, Enum):
    """Controls how many OCR quality variants are attempted per page.

    ``BASELINE`` runs a single normal inference pass plus orientation recovery.
    ``ADAPTIVE`` adds targeted enhancement variants only when quality signals
    warrant it (the default — trades speed for recall).
    ``EXHAUSTIVE`` runs all available enhancement variants unconditionally;
    intended for benchmarking and diagnostic comparisons.
    """

    BASELINE = "baseline"
    ADAPTIVE = "adaptive"
    EXHAUSTIVE = "exhaustive"


@dataclass(frozen=True, slots=True)
class OcrQualityThresholds:
    """Centralized, auditable starting points for OCR quality decisions.

    All thresholds are dimensionless ratios or confidence scores in [0, 1].
    Changing a threshold here affects every quality gate in the pipeline
    without requiring per-site edits.

    Attributes:
        strong_mean_confidence: Mean token confidence above which a page is
            considered high quality (no recovery triggered).
        strong_lower_quartile: Lower quartile confidence bound for high-quality
            classification; guards against outlier-driven mean inflation.
        max_low_confidence_char_ratio: Fraction of tokens below
            ``low_confidence_threshold`` tolerated before recovery is triggered.
        severe_mean_confidence: Mean confidence threshold below which
            quality is classified as severely degraded.
        severe_low_confidence_char_ratio: Low-confidence fraction that triggers
            a severe-quality label regardless of mean confidence.
        minimum_printable_ratio: Minimum ratio of printable-Unicode characters
            required before the token list is considered valid text.
        low_confidence_threshold: Per-token confidence below which the token
            counts as a low-confidence observation.
        minimum_orientation_ratio: Fraction of tokens that must be horizontal
            for the page orientation to be considered coherent.
    """

    strong_mean_confidence: float = 0.90
    strong_lower_quartile: float = 0.78
    max_low_confidence_char_ratio: float = 0.12
    severe_mean_confidence: float = 0.70
    severe_low_confidence_char_ratio: float = 0.35
    minimum_printable_ratio: float = 0.90
    low_confidence_threshold: float = 0.70
    minimum_orientation_ratio: float = 0.75


@dataclass(frozen=True, slots=True)
class SecurityLimits:
    """Hard resource limits enforced before and during extraction.

    Attributes:
        max_pages: Documents with more pages than this are rejected outright.
        max_file_size_bytes: PDF files larger than this are rejected before
            any page is read.
        max_render_pixels: Maximum total pixels for a single rendered page
            image; oversized renders are rejected to cap memory usage.
    """

    max_pages: int = 5000
    max_file_size_bytes: int = 1_000_000_000
    max_render_pixels: int = 100_000_000


@dataclass(frozen=True, slots=True)
class ExtractorConfig:
    """Full configuration for one extraction run.

    Most callers should use :func:`best_extraction_config` or the ``balanced``
    CLI mode rather than constructing this directly.

    Attributes:
        mode: Top-level extraction strategy (see :class:`ExtractionMode`).
        language: OCR language hint used to select the model profile.
        enable_ocr: Activate the PaddleOCR adapter.  Set automatically
            when ``mode`` is ``BALANCED`` or ``OCR``.
        enable_layout: Activate heuristic layout region detection.
        enable_tables: Activate vector-grid, relaxed-grid and text-track
            table detectors.
        merge_cross_page_tables: Attempt to merge table fragments that span
            adjacent pages into a single logical table.
        enable_complexity_render: Render a downscaled page image for
            complexity analysis.  Disabling skips the ink-ratio signal.
        enable_experimental_occlusion_redaction: Remove native text visually
            covered by opaque objects.  Disabled by default — guarantees are
            not yet sufficient for all PDF layouts.
        complexity_render_scale: Scale factor for complexity analysis renders
            (default 0.5 × OCR render scale).
        ocr_render_scale: Scale factor applied when rendering pages for OCR
            (default 2.0 — approximately 144 DPI for a typical 72 DPI PDF).
        ocr_quality_variants: Compatibility alias for policy selection.
            ``False`` forces ``BASELINE`` policy regardless of
            ``ocr_quality_policy``.
        ocr_quality_policy: OCR quality variant strategy; see
            :class:`OcrQualityPolicy`.
        ocr_quality_thresholds: Tunable quality thresholds; see
            :class:`OcrQualityThresholds`.
        ocr_batch_size: Maximum images per Paddle inference batch.
        preserve_headers_footers: When ``False``, repeated page headers and
            footers are suppressed from ``reading_text``.
        retain_native_evidence: Keep full ``NativePageEvidence`` in each page.
            Increases memory significantly on long documents; use only for
            targeted page inspection.
        page_indices: Zero-based page indices to extract.  ``None`` processes
            all pages in document order.
        security_limits: Hard resource caps; see :class:`SecurityLimits`.
        num_threads: CPU thread count for Paddle inference.  ``0`` lets Paddle
            auto-detect via ``os.cpu_count()``; ``-1`` leaves Paddle's own
            default unchanged.
    """

    mode: ExtractionMode | str = ExtractionMode.NATIVE
    language: str = "pt"
    enable_ocr: bool = False
    enable_layout: bool = False
    enable_tables: bool = False
    merge_cross_page_tables: bool = False
    enable_complexity_render: bool = True

    # Experimental safety feature. Disabled by default because visually
    # occluded native text cannot yet be removed with sufficient guarantees
    # across arbitrary PDF layouts such as dark banners, reversed text,
    # highlighted cells, and other legitimate opaque backgrounds.
    enable_experimental_occlusion_redaction: bool = False

    complexity_render_scale: float = 0.5
    ocr_render_scale: float = 2.0
    # OCR quality passes trade throughput and memory for recall. The default
    # keeps the high-recall behavior used by the corpus validations.
    ocr_quality_variants: bool = True
    ocr_quality_policy: OcrQualityPolicy | str = OcrQualityPolicy.ADAPTIVE
    ocr_quality_thresholds: OcrQualityThresholds = OcrQualityThresholds()
    ocr_batch_size: int = 3
    preserve_headers_footers: bool = True
    # Low-level PDFium characters and object summaries are useful for a raw
    # evidence audit, but retaining them for every page can dominate memory on
    # long documents. Text, regions, tables and diagnostics are unaffected.
    retain_native_evidence: bool = False
    # Optional zero-based page selection for targeted manual validation. None
    # keeps the normal full-document behavior.
    page_indices: tuple[int, ...] | None = None
    security_limits: SecurityLimits = SecurityLimits()
    # 0 = auto-detect (os.cpu_count()); -1 = leave Paddle's own default unchanged
    num_threads: int = 0

    def normalized_mode(self) -> ExtractionMode:
        if isinstance(self.mode, ExtractionMode):
            return self.mode
        return ExtractionMode(self.mode)

    def effective_ocr_quality_policy(self) -> OcrQualityPolicy:
        """Resolve the legacy boolean and the explicit policy in one place."""
        if not self.ocr_quality_variants:
            return OcrQualityPolicy.BASELINE
        if isinstance(self.ocr_quality_policy, OcrQualityPolicy):
            return self.ocr_quality_policy
        return OcrQualityPolicy(self.ocr_quality_policy)


def effective_ocr_quality_policy(config: ExtractorConfig) -> OcrQualityPolicy:
    return config.effective_ocr_quality_policy()


def best_extraction_config(
    *,
    language: str = "pt",
    preserve_headers: bool = False,
) -> "ExtractorConfig":
    """Return the config that extracts the maximum content from any PDF.

    Uses BALANCED mode: native text first, OCR fallback for scans/figures,
    table detection (bordered and borderless), cross-page table merging, and
    automatic removal of repeated headers/footers.

    Args:
        language: OCR language hint (default "pt" for Brazilian Portuguese).
        preserve_headers: set True to keep repeated page headers/footers in
            reading_text (default False — removes them).
    """
    return ExtractorConfig(
        mode=ExtractionMode.BALANCED,
        language=language,
        enable_tables=True,
        merge_cross_page_tables=True,
        preserve_headers_footers=preserve_headers,
        ocr_quality_variants=True,
        enable_experimental_occlusion_redaction=False,
    )


@dataclass(frozen=True, slots=True)
class DocumentContext:
    """Immutable document-level context threaded through the extraction pipeline.

    Bundles the source path, page count, PDFium version string and the resolved
    ``ExtractorConfig`` so each pipeline stage can make consistent decisions
    without re-reading the PDF header.
    """

    path: Path
    page_count: int
    pdfium_version: str | None
    config: ExtractorConfig
