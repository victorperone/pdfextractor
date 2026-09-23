"""PDF page rendering utilities backed by pypdfium2."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image


def render_page(path: str | Path, page_index: int, scale: float = 2.0) -> Image.Image:
    """Render a single PDF page to a PIL Image using pypdfium2.

    Opens the PDF, renders the requested page at the given scale factor,
    converts the result to a PIL Image, and releases all pdfium resources
    before returning.  The document, page, and bitmap handles are always
    closed in ``finally`` blocks to prevent resource leaks even when
    rendering raises an exception.

    Args:
        path: Filesystem path to the PDF file.
        page_index: Zero-based index of the page to render.
        scale: Rendering scale factor relative to the PDF's native 72 DPI
            resolution.  ``scale=2.0`` yields approximately 144 DPI;
            ``scale=4.0`` yields approximately 288 DPI.

    Returns:
        A PIL ``Image.Image`` of the rendered page.
    """
    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[page_index]
        try:
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        pdf.close()
