from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ExtractionMode(str, Enum):
    NATIVE = "native"
    FAST = "fast"
    BALANCED = "balanced"
    OCR = "ocr"


class OcrQualityPolicy(str, Enum):
    BASELINE = "baseline"
    ADAPTIVE = "adaptive"
    EXHAUSTIVE = "exhaustive"


@dataclass(frozen=True, slots=True)
class OcrQualityThresholds:
    """Centralized, auditable starting points for OCR quality decisions."""

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
    max_pages: int = 5000
    max_file_size_bytes: int = 1_000_000_000
    max_render_pixels: int = 100_000_000
    document_timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ExtractorConfig:
    mode: ExtractionMode | str = ExtractionMode.NATIVE
    language: str = "pt"
    enable_ocr: bool = False
    enable_layout: bool = False
    enable_tables: bool = False
    merge_cross_page_tables: bool = False
    enable_complexity_render: bool = True
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
    # 0 = auto-detect (usa os.cpu_count()); -1 = não configurar (PaddlePaddle decide)
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
    )


@dataclass(frozen=True, slots=True)
class DocumentContext:
    path: Path
    page_count: int
    pdfium_version: str | None
    config: ExtractorConfig
