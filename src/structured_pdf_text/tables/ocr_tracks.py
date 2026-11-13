"""Structure from accepted OCR observations, without another OCR request."""
from dataclasses import replace

from structured_pdf_text.document import RegionKind, TableMethod
from structured_pdf_text.ocr.reconstruct import ocr_text_column_layout, reconstruct_ocr_lines
from .headers import infer_visual_header_rows
from .text_tracks import detect_borderless_tables


def detect_ocr_track_tables(page, regions, image, claimed_tables):
    """Reuse recurrent-track classification within a composed raster scope.

    The native detector normally sees only native lines. This adapter admits
    already integrated regional OCR to the same classifier; geometry and text
    remain the original evidence. Ruled/merged candidates take precedence.
    """
    from structured_pdf_text.ocr.image_views import canonicalize_page_image

    image_boxes = [item.bbox for item in page.objects.images if item.bbox is not None]
    if not image_boxes:
        return []
    claimed = [f.bbox for table in claimed_tables for f in table.page_fragments
               if f.bbox is not None and f.page_index == page.page_index]
    output = []
    raster = canonicalize_page_image(image, page.objects.rotation)
    try:
        for region in regions:
            if region.kind in {RegionKind.HEADER, RegionKind.FOOTER, RegionKind.DECORATIVE} or not region.ocr_tokens:
                continue
            lines = reconstruct_ocr_lines(region.ocr_tokens, page.page_index, page.bbox,
                                          dehyphenate=False, page_rotation=page.objects.rotation)
            gutters, slope = ocr_text_column_layout(region.ocr_tokens, page.bbox, page.objects.rotation)
            view = replace(region, native_lines=lines)
            for table in detect_borderless_tables(page, view, f"{region.region_id}:ocr-tracks"):
                box = table.page_fragments[0].bbox
                if _crosses_text_column_gutter(box, gutters, slope):
                    # Columns of running text recur on the same x tracks as a
                    # borderless table; on skewed scans their lines even pair
                    # up row by row (T3-P17). The engine's own segmentation
                    # already established these tracks as text flows.
                    continue
                if not any(image_box.x0-3 <= box.x0 and image_box.y0-3 <= box.y0
                           and box.x1 <= image_box.x1+3 and box.y1 <= image_box.y1+3
                           for image_box in image_boxes):
                    continue
                if any(box.overlap_ratio(existing) >= .5 or existing.overlap_ratio(box) >= .5
                       for existing in claimed):
                    continue
                texts = [c.text for c in table.cells if c.text.strip()]
                if not any(any(ch.isalpha() for ch in text) and (
                        sum(ch.isalpha() for ch in text) >= 3 or any(ch.isdigit() for ch in text))
                           for text in texts):
                    # Recurrent circles/ticks alone cannot establish a table.
                    continue
                table.method = TableMethod.VISUAL_MODEL
                table.header_rows = infer_visual_header_rows(table.cells, raster, page.bbox)
                output.append(table)
                claimed.append(box)
    finally:
        if raster is not image and hasattr(raster, "close"):
            raster.close()
    return output


def _crosses_text_column_gutter(box, gutters, slope: float = 0.0) -> bool:
    """Whether a validated running-text gutter runs through most of *box*.

    Gutters are expressed in the frame rectified by ``slope`` (see
    ``ocr_text_column_layout``); the box corners are mapped to it.
    """
    corners = [(x, y) for x in (box.x0, box.x1) for y in (box.y0, box.y1)]
    xs = [x + slope * y for x, y in corners]
    ys = [y - slope * x for x, y in corners]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for mid, top, bottom in gutters:
        overlap = min(y1, bottom) - max(y0, top)
        if x0 < mid < x1 and overlap >= 0.5 * max(y1 - y0, 1.0):
            return True
    return False
