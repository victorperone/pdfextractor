from __future__ import annotations

from pathlib import Path

from PIL import ImageDraw

from structured_pdf_text.document import StructuredPage
from structured_pdf_text.ocr.render import render_page


def render_overlay(
    pdf_path: str | Path,
    page: StructuredPage,
    out_path: str | Path,
    scale: float = 2.0,
) -> Path:
    """Render a simple page overlay with native lines, chars and regions."""
    image = render_page(pdf_path, page.page_index, scale=scale).convert("RGB")
    draw = ImageDraw.Draw(image)
    for region in page.regions:
        draw.rectangle(_scale_box(region.bbox, scale), outline=(255, 165, 0), width=3)
        draw.text((region.bbox.x0 * scale, region.bbox.y0 * scale), region.kind.value, fill=(255, 165, 0))
        for line_index, line in enumerate(region.native_lines, start=1):
            draw.rectangle(_scale_box(line.bbox, scale), outline=(0, 128, 255), width=2)
            draw.text((line.bbox.x0 * scale, line.bbox.y0 * scale), str(line_index), fill=(0, 128, 255))
            for token in line.tokens:
                if token.text.isspace():
                    continue
                draw.rectangle(_scale_box(token.bbox, scale), outline=(255, 0, 0), width=1)
    for table in page.tables:
        for cell in table.cells:
            if cell.bbox is None:
                continue
            draw.rectangle(_scale_box(cell.bbox, scale), outline=(0, 170, 70), width=2)
            draw.text(
                (cell.bbox.x0 * scale + 2, cell.bbox.y0 * scale + 2),
                f"{cell.row},{cell.col}",
                fill=(0, 120, 50),
            )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


def _scale_box(bbox, scale: float) -> tuple[int, int, int, int]:
    return (
        round(bbox.x0 * scale),
        round(bbox.y0 * scale),
        round(bbox.x1 * scale),
        round(bbox.y1 * scale),
    )
