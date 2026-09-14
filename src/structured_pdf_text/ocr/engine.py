from __future__ import annotations

from typing import Protocol

from structured_pdf_text.document import OcrToken
from structured_pdf_text.geometry import BBox


class OcrEngine(Protocol):
    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
    ) -> list[OcrToken]:
        """Run OCR over a rendered page."""

    def recognize_region(self, page_image: object, page_index: int, region_bbox: BBox) -> list[OcrToken]:
        """Run OCR over a region crop and map tokens back to page coordinates."""
