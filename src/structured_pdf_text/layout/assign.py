from __future__ import annotations

from structured_pdf_text.document import LayoutRegion, TextLine


def assign_lines_to_regions(lines: list[TextLine], regions: list[LayoutRegion]) -> list[LayoutRegion]:
    for region in regions:
        region.native_lines.clear()
    for line in lines:
        best_region = None
        best_ratio = 0.0
        for region in regions:
            ratio = line.bbox.overlap_ratio(region.bbox)
            if ratio > best_ratio:
                best_ratio = ratio
                best_region = region
        if best_region is not None and best_ratio > 0.5:
            best_region.native_lines.append(line)
    return regions
