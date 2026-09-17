from __future__ import annotations

import dataclasses
import statistics

from structured_pdf_text.document import LayoutRegion, RegionKind, StructuredPage
from structured_pdf_text.geometry import BBox


def assign_heading_levels(pages: list[StructuredPage]) -> list[StructuredPage]:
    """Assign up to three heading levels from document typography and geometry."""
    pages = [_merge_heading_fragments(page) for page in pages]
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


def _merge_heading_fragments(page: StructuredPage) -> StructuredPage:
    """Join adjacent title lines when style and geometry prove one heading."""
    merged: list[LayoutRegion] = []
    for region in page.regions:
        if merged and _can_merge_heading_fragments(merged[-1], region):
            previous = merged[-1]
            merged[-1] = dataclasses.replace(
                previous,
                bbox=BBox.union_all([previous.bbox, region.bbox]),
                native_lines=[*previous.native_lines, *region.native_lines],
                ocr_tokens=[*previous.ocr_tokens, *region.ocr_tokens],
            )
        else:
            merged.append(region)
    return dataclasses.replace(page, regions=merged)


def _can_merge_heading_fragments(first: LayoutRegion, second: LayoutRegion) -> bool:
    if first.kind != RegionKind.TITLE or second.kind != RegionKind.TITLE:
        return False
    vertical_gap = second.bbox.y0 - first.bbox.y1
    if vertical_gap < -2.0 or vertical_gap > max(8.0, min(first.bbox.height, second.bbox.height) * 0.75):
        return False
    if abs(first.bbox.x0 - second.bbox.x0) > max(12.0, min(first.bbox.height, second.bbox.height)):
        return False
    first_style = _heading_style(first)
    second_style = _heading_style(second)
    if first_style[0] is not None and second_style[0] is not None:
        if abs(first_style[0] - second_style[0]) > max(2.0, first_style[0] * 0.18):
            return False
    if first_style[1] and second_style[1] and first_style[1] != second_style[1]:
        return False
    return True


def _heading_style(region: LayoutRegion) -> tuple[float | None, str | None]:
    tokens = [token for line in region.native_lines for token in line.tokens if token.text.strip()]
    sizes = [token.font_size for token in tokens if token.font_size and token.font_size > 0]
    fonts = [token.font_name for token in tokens if token.font_name]
    return (
        statistics.median(sizes) if sizes else None,
        fonts[0] if fonts and all(font == fonts[0] for font in fonts) else None,
    )
