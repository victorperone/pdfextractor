from __future__ import annotations

import dataclasses
import statistics

from structured_pdf_text.document import RegionKind, StructuredPage


def assign_heading_levels(pages: list[StructuredPage]) -> list[StructuredPage]:
    """Assign heading_level (1, 2 or 3) to TITLE regions based on relative font size."""
    title_font_sizes: list[tuple[int, str, float]] = []

    for page in pages:
        for region in page.regions:
            if region.kind != RegionKind.TITLE:
                continue
            sizes = [
                token.font_size  # type: ignore[attr-defined]
                for line in region.native_lines
                for token in line.tokens
                if getattr(token, "font_size", None) is not None and token.font_size > 0  # type: ignore[attr-defined]
            ]
            font_size = statistics.median(sizes) if sizes else region.bbox.height
            title_font_sizes.append((page.page_index, region.region_id, font_size))

    if not title_font_sizes:
        return pages

    all_sizes = sorted(set(size for _, _, size in title_font_sizes), reverse=True)
    n = len(all_sizes)
    if n == 1:
        thresholds = (all_sizes[0], all_sizes[0])
    elif n == 2:
        thresholds = (all_sizes[0], all_sizes[1])
    else:
        thresholds = (all_sizes[n // 3 - 1], all_sizes[2 * n // 3 - 1])

    level_map: dict[str, int] = {}
    for _, region_id, font_size in title_font_sizes:
        if font_size >= thresholds[0]:
            level_map[region_id] = 1
        elif font_size >= thresholds[1]:
            level_map[region_id] = 2
        else:
            level_map[region_id] = 3

    new_pages = []
    for page in pages:
        new_regions = [
            dataclasses.replace(region, heading_level=level_map.get(region.region_id))
            if region.kind == RegionKind.TITLE
            else region
            for region in page.regions
        ]
        new_pages.append(dataclasses.replace(page, regions=new_regions))
    return new_pages
