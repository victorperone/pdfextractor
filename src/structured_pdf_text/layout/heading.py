from __future__ import annotations

import dataclasses
import re
import statistics

from structured_pdf_text.document import LayoutRegion, RegionKind, StructuredPage
from structured_pdf_text.geometry import BBox


# A title prediction needs more than positional/short-text heuristics.  The
# contextual style check below keeps body-sized labels from becoming H1 while
# retaining clear typography.
HEADING_ACCEPTANCE_THRESHOLD = 1.0


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
            if region.kind == RegionKind.TITLE and _region_has_alphanumeric_text(region):
                font_size = statistics.median(sizes) if sizes else region.bbox.height
                title_font_sizes.append((page.page_index, region.region_id, font_size))
            elif region.kind not in {
                RegionKind.HEADER,
                RegionKind.FOOTER,
                RegionKind.TABLE,
                RegionKind.DECORATIVE,
            }:
                body_sizes.extend(sizes)

    body_median = statistics.median(body_sizes) if body_sizes else None
    body_p75 = _percentile(body_sizes, 0.75) if body_sizes else None
    valid_scores: dict[str, float] = {}
    title_regions: dict[str, LayoutRegion] = {}
    for page in pages:
        for region in page.regions:
            if region.kind == RegionKind.TITLE and _region_has_alphanumeric_text(region):
                title_regions[region.region_id] = region
                valid_scores[region.region_id] = _heading_candidate_score(
                    region,
                    body_font_median=body_median,
                    body_font_p75=body_p75,
                    page_bbox=page.bbox,
                )

    # Convert punctuation-only and weak title predictions back to ordinary
    # text before assembly. This makes the rejection invariant independent of
    # the renderer.
    pages = [
        dataclasses.replace(
            page,
            regions=[
                dataclasses.replace(region, kind=RegionKind.TEXT, heading_level=None)
                if (
                    region.kind == RegionKind.TITLE
                    and (
                        not _region_has_alphanumeric_text(region)
                        or not _heading_is_accepted(
                            region,
                            valid_scores.get(region.region_id, 0.0),
                            body_font_median=body_median,
                            body_font_p75=body_p75,
                        )
                    )
                )
                else region
                for region in page.regions
            ],
        )
        for page in pages
    ]
    if not title_font_sizes:
        return pages

    values = sorted((size / body_median if body_median else size for _, _, size in title_font_sizes), reverse=True)
    clusters: list[float] = []
    for value in values:
        if not clusters or clusters[-1] - value > 0.18:
            clusters.append(value)
    clusters = clusters[:3]

    level_map: dict[str, int] = {}
    for _, region_id, font_size in title_font_sizes:
        ratio = font_size / body_median if body_median else font_size
        region = title_regions.get(region_id)
        if region is None or not _heading_is_accepted(
            region,
            valid_scores.get(region_id, 0.0),
            body_font_median=body_median,
            body_font_p75=body_p75,
        ):
            continue
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


def _region_has_alphanumeric_text(region: LayoutRegion) -> bool:
    return any(
        character.isalnum()
        for line in region.native_lines
        for character in line.text
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _heading_candidate_score(
    region: LayoutRegion,
    *,
    body_font_median: float | None = None,
    body_font_p75: float | None = None,
    page_bbox: BBox | None = None,
) -> float:
    """Score heading evidence without relying on uppercase or lexical names."""
    text = " ".join(line.text for line in region.native_lines).split()
    text = " ".join(text).strip()
    if not any(character.isalnum() for character in text):
        return 0.0
    tokens = [token for line in region.native_lines for token in line.tokens if token.text.strip()]
    sizes = [token.font_size for token in tokens if token.font_size and token.font_size > 0]
    median_size = statistics.median(sizes) if sizes else region.bbox.height
    score = 0.5
    if body_font_median and body_font_median > 0:
        ratio = median_size / body_font_median
        score += min(2.2, max(0.0, (ratio - 1.0) * 2.0))
    if body_font_p75 and median_size >= body_font_p75:
        score += 0.35
    if any((token.font_weight or 0) >= 600 or "bold" in (token.font_name or "").casefold() for token in tokens):
        score += 0.45
    line_heights = [line.bbox.height for line in region.native_lines if line.bbox.height > 0]
    if line_heights:
        score += min(0.35, max(line_heights) / max(region.bbox.height, 1.0))
    if page_bbox is not None:
        if abs(region.bbox.cx - page_bbox.cx) <= page_bbox.width * 0.16:
            score += 0.25
        if region.bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.20:
            score += 0.15
        if region.bbox.y1 >= page_bbox.y1 - page_bbox.height * 0.15 and not any(
            (token.font_weight or 0) >= 600 or "bold" in (token.font_name or "").casefold()
            for token in tokens
        ):
            if not body_font_median or median_size <= body_font_median * 1.20:
                score -= 0.45
    if len(text) <= 90:
        score += 0.15
    if re.match(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)[.)]?\s+", text):
        score += 0.15
    # Capitalization is deliberately only a weak signal.
    if text[:1].isupper() and not text.isupper():
        score += 0.05
    if len(text) <= 40 and score < 2.0:
        score -= 0.25
    return max(0.0, score)


def _heading_is_accepted(
    region: LayoutRegion,
    score: float,
    *,
    body_font_median: float | None,
    body_font_p75: float | None,
) -> bool:
    if score < HEADING_ACCEPTANCE_THRESHOLD:
        return False
    if body_font_median is None or body_font_median <= 0:
        return True

    tokens = [token for line in region.native_lines for token in line.tokens if token.text.strip()]
    sizes = [token.font_size for token in tokens if token.font_size and token.font_size > 0]
    median_size = statistics.median(sizes) if sizes else region.bbox.height
    is_bold = any(
        (token.font_weight or 0) >= 600 or "bold" in (token.font_name or "").casefold()
        for token in tokens
    )
    text = " ".join(line.text for line in region.native_lines).strip()
    has_numbered_prefix = bool(re.match(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)[.)]?\s+", text))
    size_signal = median_size >= body_font_median * 1.15
    if body_font_p75 is not None:
        size_signal = size_signal or median_size >= body_font_p75 * 1.10
    return size_signal or is_bold or has_numbered_prefix


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
    if not _region_has_alphanumeric_text(first) or not _region_has_alphanumeric_text(second):
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
    first_weight = _heading_weight(first)
    second_weight = _heading_weight(second)
    if first_weight is not None and second_weight is not None and abs(first_weight - second_weight) > 150:
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


def _heading_weight(region: LayoutRegion) -> float | None:
    weights = [
        float(token.font_weight)
        for line in region.native_lines
        for token in line.tokens
        if token.font_weight is not None
    ]
    return statistics.median(weights) if weights else None
