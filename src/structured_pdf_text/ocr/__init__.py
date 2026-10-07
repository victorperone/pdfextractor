"""Public re-exports for the structured_pdf_text OCR sub-package.

Aggregates the engine protocol, the PaddleOCR backend, the page renderer,
the line reconstructor, and all recovery utilities so that callers can
access every major OCR component from a single import path.
"""
from .engine import OcrEngine
from .paddle import PaddleOcrEngine, PaddleOcrUnavailable
from .render import render_page
from .reconstruct import dehyphenate_ocr_lines, merge_ocr_bullet_markers, reconstruct_ocr_lines, segment_ocr_paragraphs
from .recovery import (
    OcrRegionRefiner,
    RegionRefinementAttempt,
    RegionRefinementGoal,
    RegionRefinementRequest,
    RegionRefinementResult,
    recover_ocr_tokens,
)
from .critical_data import CriticalDataRefiner, DataType, get_allowlist

__all__ = [
    "OcrEngine",
    "PaddleOcrEngine",
    "PaddleOcrUnavailable",
    "OcrRegionRefiner",
    "RegionRefinementAttempt",
    "RegionRefinementGoal",
    "RegionRefinementRequest",
    "RegionRefinementResult",
    "CriticalDataRefiner",
    "DataType",
    "dehyphenate_ocr_lines",
    "get_allowlist",
    "merge_ocr_bullet_markers",
    "reconstruct_ocr_lines",
    "segment_ocr_paragraphs",
    "recover_ocr_tokens",
    "render_page",
]
