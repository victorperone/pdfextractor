from __future__ import annotations

from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image


def render_page(path: str | Path, page_index: int, scale: float = 2.0) -> Image.Image:
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
