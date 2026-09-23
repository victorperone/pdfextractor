"""Protocol definition for the OCR engine abstraction layer."""
from __future__ import annotations

from typing import Protocol

from structured_pdf_text.document import OcrToken
from structured_pdf_text.geometry import BBox


class OcrEngine(Protocol):
    """Structural interface that every OCR backend must satisfy.

    Any class that provides ``recognize_page`` and ``recognize_region``
    with the correct signatures satisfies this protocol without explicit
    inheritance.  The protocol is checked at static-analysis time only;
    no runtime registration is required.
    """

    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
    ) -> list[OcrToken]:
        """Run OCR on a fully rendered page image.

        Args:
            page_image: A PIL ``Image`` (or compatible object) of the rendered
                page.  Resolution should be at least 150 DPI; 300 DPI is
                recommended for reliable detection.
            page_index: Zero-based index of the page within the document,
                used to populate source references on the returned tokens.
            page_bbox: Optional bounding box of the page in document
                coordinates.  When provided, tokens are expressed in that
                coordinate space; otherwise, pixel coordinates are used.
            quality_variants: When ``True``, the engine may run several
                image-enhancement variants and return the highest-quality
                result.  ``None`` defers to the engine's default policy.

        Returns:
            A list of :class:`~structured_pdf_text.document.OcrToken` objects
            in reading order, each carrying its bounding box and recognition
            confidence.
        """

    def recognize_region(self, page_image: object, page_index: int, region_bbox: BBox) -> list[OcrToken]:
        """Run OCR over a region crop and map tokens back to page coordinates."""
