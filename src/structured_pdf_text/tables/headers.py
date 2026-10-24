"""Conservative table header inference from native typography evidence."""
from __future__ import annotations

from statistics import median

from structured_pdf_text.document import TableCell
from structured_pdf_text.geometry import BBox


def infer_header_rows(cells: list[TableCell]) -> tuple[int, ...]:
    """Return row zero only when native weight/size clearly marks a header."""
    rows = {cell.row for cell in cells}
    first = [cell for cell in cells if cell.row == 0 and cell.text.strip()]
    body = [cell for cell in cells if cell.row > 0 and cell.text.strip()]
    if len(rows) < 2 or not first or not body:
        return ()

    token_groups = [cell.tokens for cell in first]
    emphasized = sum(
        bool(tokens) and sum((token.font_weight or 0) >= 600 for token in tokens) >= max(1, len(tokens) // 2)
        for tokens in token_groups
    )
    if emphasized / len(first) >= 0.6:
        return (0,)

    first_sizes = [token.font_size for cell in first for token in cell.tokens if token.font_size]
    body_sizes = [token.font_size for cell in body for token in cell.tokens if token.font_size]
    if first_sizes and body_sizes and median(first_sizes) >= median(body_sizes) * 1.15:
        return (0,)
    return ()


def infer_visual_header_rows(cells: list[TableCell], image, raster_bbox: BBox) -> tuple[int, ...]:
    """Use a distinct leading background band, not a mandatory first header.

    Raster OCR has no font-weight evidence. Consistent shading across leading
    label cells can establish one or more header rows, including merged ones.
    Unshaded tables and numeric first rows remain explicitly headerless.
    """
    try:
        import numpy as np
    except ImportError:
        return ()
    array = np.asarray(image)
    if array.ndim not in (2, 3) or not array.size or raster_bbox.area <= 0:
        return ()
    height, width = array.shape[:2]
    colors = {}
    rows = sorted({c.row for c in cells})
    if len(rows) < 2:
        return ()
    for cell in cells:
        if cell.bbox is None:
            continue
        box = cell.bbox
        left = max(0, int((box.x0+.1*box.width-raster_bbox.x0)*width/raster_bbox.width))
        right = min(width, int((box.x1-.1*box.width-raster_bbox.x0)*width/raster_bbox.width))
        top = max(0, int((box.y0+.1*box.height-raster_bbox.y0)*height/raster_bbox.height))
        bottom = min(height, int((box.y1-.1*box.height-raster_bbox.y0)*height/raster_bbox.height))
        sample = array[top:bottom, left:right]
        if not sample.size:
            continue
        channels = sample[..., :3].reshape(-1, min(3, sample.shape[-1])) if sample.ndim == 3 else sample.reshape(-1, 1)
        colors.setdefault(cell.row, []).append(np.percentile(channels, 80, axis=0))
    if 0 not in colors or not any(row > 0 for row in colors):
        return ()
    band = {row: np.median(values, axis=0) for row, values in colors.items()}
    background = np.percentile([color for row, color in band.items() if row > 0], 75, axis=0)
    if np.mean(np.abs(band[0]-background)) < 8:
        return ()
    headers = []
    for row in range(min(3, max(rows))):
        populated = [c for c in cells if c.row == row and c.text.strip()]
        if row not in band or not populated or np.mean(np.abs(band[row]-band[0])) > 5:
            break
        labels = sum(any(ch.isalpha() for ch in c.text)
                     and sum(ch.isalpha() for ch in c.text) >= sum(ch.isdigit() for ch in c.text)
                     for c in populated)
        if labels / len(populated) < .6:
            break
        headers.append(row)
    return tuple(headers)
