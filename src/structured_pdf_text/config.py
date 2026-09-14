from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ExtractionMode(str, Enum):
    NATIVE = "native"
    FAST = "fast"
    BALANCED = "balanced"
    OCR = "ocr"


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

    def normalized_mode(self) -> ExtractionMode:
        if isinstance(self.mode, ExtractionMode):
            return self.mode
        return ExtractionMode(self.mode)


@dataclass(frozen=True, slots=True)
class DocumentContext:
    path: Path
    page_count: int
    pdfium_version: str | None
    config: ExtractorConfig
