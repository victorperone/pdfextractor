from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path

from structured_pdf_text.errors import ConfigurationError


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
    max_render_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in ("max_pages", "max_file_size_bytes", "max_render_pixels", "max_render_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConfigurationError(f"{name} must be a positive integer, got {value!r}")


@dataclass(frozen=True, slots=True)
class ExtractorConfig:
    """Full configuration for one extraction run.

    Most callers should use :func:`best_extraction_config` or the ``balanced``
    CLI mode rather than constructing this directly.

    Attributes:
        mode: Top-level extraction strategy (see :class:`ExtractionMode`).
        language: OCR language hint used to select the model profile.
        enable_ocr: Optional override for ``balanced`` mode. ``None`` enables
            its normal selective OCR behavior; ``False`` disables it. Native and
            fast modes never render, while ``ocr`` always requires OCR.
        enable_layout: Activate heuristic layout region detection.
        enable_tables: Activate vector-grid, relaxed-grid and text-track
            table detectors.
        merge_cross_page_tables: Attempt to merge table fragments that span
            adjacent pages into a single logical table.
        enable_complexity_render: Render a downscaled page image for
            complexity analysis.  Disabling skips the ink-ratio signal.
        enable_experimental_occlusion_redaction: Remove native text visually
            covered by solid opaque objects. Enabled by default and guarded by
            rendered-pixel uniformity checks.
        complexity_render_scale: Scale factor for complexity analysis renders
            (default 0.5 × OCR render scale).
        ocr_render_scale: Optional scale override for OCR rendering. When omitted,
            the selected engine's registered default is used.
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
    enable_ocr: bool | None = None
    enable_layout: bool = False
    enable_tables: bool = False
    merge_cross_page_tables: bool = False
    enable_complexity_render: bool = True

    # Visual filtering is enabled by default. Solid object boxes must also
    # pass rendered-pixel uniformity checks before their covered text is cut.
    enable_experimental_occlusion_redaction: bool = True

    complexity_render_scale: float = 0.5
    ocr_render_scale: float | None = None
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

    # OCR engine selection — new fields, both with defaults so existing code
    # that constructs ExtractorConfig without these args continues to work.
    # Public engine family. RapidOCR providers remain available as legacy aliases.
    ocr_engine: str = "paddle"
    ocr_runtime: str = "paddle_static"
    ocr_provider: str | None = None

    def __post_init__(self) -> None:
        try:
            ExtractionMode(self.mode)
            OcrQualityPolicy(self.ocr_quality_policy)
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(str(exc)) from exc
        if self.enable_ocr is not None and not isinstance(self.enable_ocr, bool):
            raise ConfigurationError(f"enable_ocr must be True, False, or None, got {self.enable_ocr!r}")
        if ExtractionMode(self.mode) == ExtractionMode.OCR and self.enable_ocr is False:
            raise ConfigurationError("mode='ocr' requires OCR and cannot set enable_ocr=False")
        if ExtractionMode(self.mode) in (ExtractionMode.NATIVE, ExtractionMode.FAST) and self.enable_ocr is True:
            raise ConfigurationError("native/fast modes are raster-free and cannot enable OCR")
        for name in ("complexity_render_scale",):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ConfigurationError(f"{name} must be a finite positive number, got {value!r}")
        if self.ocr_render_scale is not None and (
            isinstance(self.ocr_render_scale, bool)
            or not isinstance(self.ocr_render_scale, (int, float))
            or not math.isfinite(self.ocr_render_scale)
            or self.ocr_render_scale <= 0
        ):
            raise ConfigurationError(f"ocr_render_scale must be None or a finite positive number, got {self.ocr_render_scale!r}")
        if self.ocr_batch_size < 1:
            raise ConfigurationError(f"ocr_batch_size must be >= 1, got {self.ocr_batch_size!r}")
        if self.num_threads < -1:
            raise ConfigurationError(f"num_threads must be -1, 0, or positive, got {self.num_threads!r}")
        if self.page_indices is not None and any(index < 0 for index in self.page_indices):
            raise ConfigurationError("page_indices must contain zero-based non-negative integers")

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

    def effective_ocr_render_scale(self) -> float:
        return float(self.ocr_render_scale if self.ocr_render_scale is not None else best_ocr_render_scale(self.ocr_engine))


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
        enable_experimental_occlusion_redaction=True,
    )


# Per-engine render scale defaults for the E2E benchmark.
#
# Rationale (research-backed, subject to A/B refinement):
#
#   Paddle     2.0  — Paddle has internal quality variants and
#                     adaptive upscaling; 2.0 (≈144 DPI) is sufficient
#                     as the model's internal passes compensate for
#                     lower input resolution.
#
#   EasyOCR    3.0  — CRAFT minimum text height is ~20 px. At 2.0
#                     (≈144 DPI) characters on A4 are borderline.
#                     3.0 (≈216 DPI) combined with mag_ratio=1.2 gives
#                     adequate coverage for small-text and degraded pages.
#
#   RapidOCR   3.0  — Shares PP-OCR family detection/recognition architecture
#                     with PaddleOCR but lacks the adaptive quality-
#                     variant upscaling layer; 3.0 recommended by community
#                     for reliable diacritic detection.
#
#   Tesseract  4.0  — Officially documented minimum is 300 DPI.
#                     Below 200 DPI error rate roughly doubles.
#                     4.0 ≈ 288 DPI (72 DPI base × 4.0); the closest
#                     integer scale to the 300 DPI target (4.17).
#
# Override at call time or via OCR_RENDER_SCALE env var in evaluate_e2e.py.
_ENGINE_RENDER_SCALE: dict[str, float] = {
    "paddle":             2.0,
    "easyocr":            3.0,
    "rapidocr":            3.0,
    "rapidocr-onnx":      3.0,
    "rapidocr-openvino":  3.0,
    "tesseract":          4.0,
}


def best_ocr_render_scale(engine: str) -> float:
    """Return the recommended ocr_render_scale for the given engine.

    Values are research-backed starting points (not final A/B-validated).
    Unknown engines fall back to the ExtractorConfig default (2.0).
    """
    return _ENGINE_RENDER_SCALE.get(engine, 2.0)


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
