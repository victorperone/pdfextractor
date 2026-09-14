from __future__ import annotations

from typing import Any, Protocol

from structured_pdf_text.config import DocumentContext
from structured_pdf_text.document import NativePageEvidence


class NativeEvidenceSource(Protocol):
    def open(self) -> DocumentContext:
        """Open and validate the PDF, returning document context."""

    def extract_page(self, page_index: int) -> NativePageEvidence:
        """Extract immutable native evidence for a single page."""

    def render_page(self, page_index: int, scale: float = 0.5) -> Any:
        """Render a page for deterministic visual diagnostics, without OCR."""

    def close(self) -> None:
        """Release native resources."""

    def __enter__(self) -> NativeEvidenceSource:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
