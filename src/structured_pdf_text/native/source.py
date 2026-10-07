"""Protocol definition for native PDF evidence sources.

Defines the ``NativeEvidenceSource`` Protocol that all concrete source
implementations (e.g. ``PdfiumNativeEvidenceSource``) must satisfy. Consumers
depend only on this protocol so that alternative backends can be substituted
without modifying the extraction pipeline.
"""
from __future__ import annotations

from typing import Any, Protocol

from structured_pdf_text.config import DocumentContext
from structured_pdf_text.document import NativePageEvidence
from structured_pdf_text.geometry import BBox


class NativeEvidenceSource(Protocol):
    """Structural protocol for PDF native evidence extractors.

    Implementations open a PDF document, yield immutable ``NativePageEvidence``
    per page, optionally render raster images for diagnostics, and release
    native resources on ``close``. The protocol supports use as a context
    manager (``with source: ...``).
    """

    def open(self) -> DocumentContext:
        """Open and validate the PDF, returning document context."""

    def extract_page(self, page_index: int) -> NativePageEvidence:
        """Extract immutable native evidence for a single page."""

    def render_page(self, page_index: int, scale: float = 0.5) -> Any:
        """Render a page for deterministic visual diagnostics, without OCR."""

    def render_region(self, page_index: int, bbox: BBox, scale: float, page_bbox: BBox | None = None) -> Any:
        """Render a PDF-coordinate region directly from page vectors/images."""

    def extract_embedded_image(self, page_index: int, object_index: int) -> tuple[Any, BBox] | None:
        """Return an image object's original-resolution bitmap and page bounds."""

    def close(self) -> None:
        """Release native resources."""

    def __enter__(self) -> NativeEvidenceSource:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
