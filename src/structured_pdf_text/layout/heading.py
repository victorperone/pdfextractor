from __future__ import annotations

import dataclasses
import statistics

from structured_pdf_text.document import RegionKind, StructuredPage


def assign_heading_levels(pages: list[StructuredPage]) -> list[StructuredPage]:
    """Assign up to three heading levels from document typography and geometry."""
    title_font_sizes: list[tuple[int, str, float]] = []
    body_sizes: list[float] = []

    for page in pages:
        for region in page.regions:
            sizes = [
                token.font_size
                for line in region.native_lines
                for token in line.tokens
                if token.font_size is not None and token.font_size > 0
            ]
            if region.kind == RegionKind.TITLE:
                font_size = statistics.median(sizes) if sizes else region.bbox.height
                title_font_sizes.append((page.page_index, region.region_id, font_size))
            elif region.kind not in {RegionKind.HEADER, RegionKind.FOOTER, RegionKind.TABLE}:
                body_sizes.extend(sizes)

    if not title_font_sizes:
        return pages

    body_median = statistics.median(body_sizes) if body_sizes else None
    values = sorted((size / body_median if body_median else size for _, _, size in title_font_sizes), reverse=True)
    clusters: list[float] = []
    for value in values:
        if not clusters or clusters[-1] - value > 0.18:
            clusters.append(value)
    clusters = clusters[:3]

    level_map: dict[str, int] = {}
    for _, region_id, font_size in title_font_sizes:
        ratio = font_size / body_median if body_median else font_size
        cluster = min(range(len(clusters)), key=lambda index: abs(clusters[index] - ratio)) if clusters else 0
        level_map[region_id] = min(3, cluster + 1)

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
