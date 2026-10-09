"""Heading level assignment and multi-line heading fragment merging.

The public entry point :func:`assign_heading_levels` operates in two passes:

1. **Fragment merging** — adjacent ``TITLE`` regions that share font, size, and
   alignment are joined into a single region before scoring so that multi-line
   headings receive the correct level assignment.

2. **Level assignment** — surviving ``TITLE`` regions are scored against the
   document's body typography.  Scores below *HEADING_ACCEPTANCE_THRESHOLD* or
   without a clear size/weight signal are demoted to plain ``TEXT``.  Accepted
   headings are bucketed into up to three levels by clustering their size ratios
   relative to the median body font.

   When native font metrics (``font_size``, ``font_weight``) are absent — for
   example on fully-scanned pages processed via EasyOCR — heading detection
   falls back to OCR-aware features (§28):

   - **Box height ratio**: OCR line height relative to the document body-text
     median OCR height (computed from non-title regions that have ``ocr_lines``).
   - **Numbered prefix**: section numbering patterns (``1.``, ``1.1``, ``A.``).
   - **Page geometry**: proximity to the top third, centring, short text length.
   - **Vertical spacing**: larger-than-body gaps above a candidate suggest a
     paragraph break rather than a heading transition.

   The OCR path produces a level assignment consistent with the native path:
   clusters of size ratios are mapped to H1/H2/H3 identically.
"""
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
    """Assign up to three heading levels from document typography and geometry.

    Native path: uses ``font_size`` / ``font_weight`` from ``native_lines``.
    OCR fallback (§28): when native font metrics are absent, derives heading
    evidence from OCR line box heights, numbered prefixes, and page geometry.
    Both paths produce the same H1/H2/H3 clustering output.
    """
    pages = [_merge_heading_fragments(page) for page in pages]
    title_font_sizes: list[tuple[int, str, float]] = []
    body_sizes: list[float] = []
    # Collect OCR line heights for the OCR-aware fallback (§28).
    body_ocr_heights: list[float] = []

    for page in pages:
        for region in page.regions:
            sizes = [
                token.font_size
                for line in region.native_lines
                for token in line.tokens
                if token.font_size is not None and token.font_size > 0
            ]
            if region.kind == RegionKind.TITLE and _region_has_alphanumeric_text(region):
                font_size = statistics.median(sizes) if sizes else _ocr_region_height(region)
                if font_size is not None and font_size > 0:
                    title_font_sizes.append((page.page_index, region.region_id, font_size))
            elif region.kind not in {
                RegionKind.HEADER,
                RegionKind.FOOTER,
                RegionKind.TABLE,
                RegionKind.DECORATIVE,
            }:
                body_sizes.extend(sizes)
                body_ocr_heights.extend(_region_ocr_heights(region))

    body_median = statistics.median(body_sizes) if body_sizes else None
    body_p75 = _percentile(body_sizes, 0.75) if body_sizes else None
    body_ocr_height_median = statistics.median(body_ocr_heights) if body_ocr_heights else None

    # OCR path (§28) is used only when there is no native font-size evidence at
    # all — neither from body regions nor from title regions.  When any native
    # font_size is present we always prefer the native path because it is more
    # accurate than the OCR-box-height heuristic.
    has_any_native_font_size = bool(body_sizes)
    if not has_any_native_font_size:
        # Also check title regions for native font evidence.
        for page in pages:
            for region in page.regions:
                if region.kind == RegionKind.TITLE:
                    title_native_sizes = [
                        token.font_size
                        for line in region.native_lines
                        for token in line.tokens
                        if token.font_size is not None and token.font_size > 0
                    ]
                    if title_native_sizes:
                        has_any_native_font_size = True
                        break
            if has_any_native_font_size:
                break

    use_ocr_path = not has_any_native_font_size and body_ocr_height_median is not None

    valid_scores: dict[str, float] = {}
    title_regions: dict[str, LayoutRegion] = {}
    for page in pages:
        for region in page.regions:
            if region.kind == RegionKind.TITLE and _region_has_alphanumeric_text(region):
                title_regions[region.region_id] = region
                if not use_ocr_path:
                    score = _heading_candidate_score(
                        region,
                        body_font_median=body_median,
                        body_font_p75=body_p75,
                        page_bbox=page.bbox,
                    )
                else:
                    # OCR-aware fallback (§28): no native font metrics available.
                    score = _heading_candidate_score_ocr(
                        region,
                        body_ocr_height_median=body_ocr_height_median,
                        page_bbox=page.bbox,
                    )
                valid_scores[region.region_id] = score

    # Effective body reference for native path; for OCR fallback it may be None.
    effective_body_median = body_median
    effective_body_p75 = body_p75

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
                        or not (
                            _heading_is_accepted_ocr(
                                region,
                                valid_scores.get(region.region_id, 0.0),
                                body_ocr_height_median=body_ocr_height_median,
                            )
                            if use_ocr_path
                            else _heading_is_accepted(
                                region,
                                valid_scores.get(region.region_id, 0.0),
                                body_font_median=effective_body_median,
                                body_font_p75=effective_body_p75,
                            )
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

    # For level clustering, normalize by body reference (native or OCR height).
    # VQ-18: expand to up to 6 clusters to support H1-H6 when sufficient
    # typographic distinction is present in the document.
    cluster_ref = effective_body_median if effective_body_median else body_ocr_height_median
    values = sorted((size / cluster_ref if cluster_ref else size for _, _, size in title_font_sizes), reverse=True)
    clusters: list[float] = []
    for value in values:
        if not clusters or clusters[-1] - value > 0.12:
            clusters.append(value)
    clusters = clusters[:6]

    level_map: dict[str, int] = {}
    for _, region_id, font_size in title_font_sizes:
        ratio = font_size / cluster_ref if cluster_ref else font_size
        region = title_regions.get(region_id)
        accepted = (
            _heading_is_accepted_ocr(
                region,
                valid_scores.get(region_id, 0.0),
                body_ocr_height_median=body_ocr_height_median,
            )
            if use_ocr_path
            else _heading_is_accepted(
                region,
                valid_scores.get(region_id, 0.0),
                body_font_median=effective_body_median,
                body_font_p75=effective_body_p75,
            )
        ) if region is not None else False
        if not accepted:
            continue
        cluster = min(range(len(clusters)), key=lambda index: abs(clusters[index] - ratio)) if clusters else 0
        level_map[region_id] = min(6, cluster + 1)

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
        for line in [*region.native_lines, *region.ocr_lines]
        for character in line.text
    )


def _percentile(values: list[float], fraction: float) -> float:
    """Return the value at *fraction* of the sorted *values* list.

    Uses nearest-rank interpolation.  Returns 0.0 for an empty list.
    *fraction* should be in [0, 1]; values outside that range are clamped by
    the index bounds.
    """
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
    """Return ``True`` when *region* should be kept as a heading.

    A region passes when:

    * Its *score* meets *HEADING_ACCEPTANCE_THRESHOLD*, **and**
    * At least one of the following independent signals is present:

      - Median font size is at least 1.15× the body median (or 1.10× the 75th
        percentile body size when available).
      - The region contains at least one bold token.
      - The region text begins with a numbered or lettered section prefix such
        as ``"1."`` or ``"A.1)``.

    When no body font reference is available the score threshold alone governs
    acceptance.
    """
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
    """Return ``True`` when *first* and *second* should be joined into one heading.

    Both regions must be ``TITLE``-kind and contain alphanumeric text.  The
    merge is allowed only when all of the following hold:

    * The vertical gap between the bottom of *first* and the top of *second*
      does not exceed 75 % of the smaller region height (allowing for generous
      line spacing but rejecting paragraph breaks).
    * The left edges are within one line-height of each other (same column).
    * Median font sizes differ by no more than 18 % of the first region's size.
    * When both regions have a single consistent font name, those names must
      match.
    * Median font weights, when available, differ by no more than 150 units.
    """
    if first.kind != RegionKind.TITLE or second.kind != RegionKind.TITLE:
        return False
    if not _region_has_alphanumeric_text(first) or not _region_has_alphanumeric_text(second):
        return False
    vertical_gap = second.bbox.y0 - first.bbox.y1
    horizontal_gap = second.bbox.x0 - first.bbox.x1
    min_height = min(first.bbox.height, second.bbox.height)
    # VQ-18: allow horizontal fragment merging when two TITLE regions are on the
    # same line (negligible vertical gap, second starts right of first).
    # "Same line" means the second bbox begins at or before the first ends vertically,
    # AND the second bbox starts to the right of the first (word fragments).
    same_line = (
        -min_height * 0.50 <= vertical_gap <= min_height * 0.20
        and horizontal_gap >= 0
    )
    if same_line:
        # On the same baseline, accept if horizontal gap is small relative to font size.
        if horizontal_gap > max(24.0, min_height * 2.0):
            return False
    else:
        if vertical_gap < -2.0 or vertical_gap > max(8.0, min_height * 0.75):
            return False
        if abs(first.bbox.x0 - second.bbox.x0) > max(12.0, min_height):
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


# ---------------------------------------------------------------------------
# §28 — OCR-aware heading detection helpers
# ---------------------------------------------------------------------------

def _ocr_region_height(region: LayoutRegion) -> float | None:
    """Return the median OCR line height for *region*, or ``None`` if unavailable."""
    heights = [
        line.bbox.height
        for line in region.ocr_lines
        if line.bbox.height > 0
    ]
    return statistics.median(heights) if heights else None


def _region_ocr_heights(region: LayoutRegion) -> list[float]:
    """Return all positive OCR line heights for body-median computation."""
    return [line.bbox.height for line in region.ocr_lines if line.bbox.height > 0]


def _heading_candidate_score_ocr(
    region: LayoutRegion,
    *,
    body_ocr_height_median: float | None,
    page_bbox: BBox | None = None,
) -> float:
    """Score a TITLE region using OCR line geometry when native font metrics are absent (§28).

    Features (all geometry-based, no font metadata required):

    - **Height ratio** — median OCR line height vs body median.  Lines taller
      than the body baseline score higher.  When no body reference exists the
      region bbox height is used as a self-referential proxy.
    - **Numbered prefix** — section numbering patterns add evidence
      (``1.``, ``1.1``, ``A.``, etc.).
    - **Page position** — proximity to the top third of the page adds a small
      bonus; proximity to the bottom third reduces score.
    - **Centering** — horizontally centred text is often a heading.
    - **Short text** — headings rarely exceed 90 characters.
    - **Vertical isolation** — regions with no direct neighbour above/below
      are more likely to be structural markers.
    """
    text = " ".join(line.text for line in region.ocr_lines).strip()
    if not text:
        text = " ".join(line.text for line in region.native_lines).strip()
    if not any(character.isalnum() for character in text):
        return 0.0

    ocr_height = _ocr_region_height(region)
    box_height = ocr_height if ocr_height is not None else region.bbox.height

    score = 0.5

    if body_ocr_height_median and body_ocr_height_median > 0:
        ratio = box_height / body_ocr_height_median
        score += min(2.2, max(0.0, (ratio - 1.0) * 2.0))
    else:
        # Self-referential: taller regions within the page are heading candidates.
        if page_bbox is not None and page_bbox.height > 0:
            relative_height = box_height / (page_bbox.height * 0.03)
            score += min(1.0, max(0.0, relative_height - 1.0) * 0.5)

    # Numbered section prefix is strong independent evidence.
    if re.match(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)[.)]?\s+", text):
        score += 0.45

    # ALL-CAPS short line is a weak heading signal (could be header leakage — keep small).
    if text.isupper() and len(text) <= 60:
        score += 0.10

    if page_bbox is not None:
        # Proximity to page top.
        if region.bbox.y0 <= page_bbox.y0 + page_bbox.height * 0.20:
            score += 0.15
        # Horizontal centering.
        if abs(region.bbox.cx - page_bbox.cx) <= page_bbox.width * 0.16:
            score += 0.20
        # Bottom proximity penalty (likely footer / caption).
        if region.bbox.y1 >= page_bbox.y1 - page_bbox.height * 0.12:
            score -= 0.40

    # Short text is a positive signal.
    if len(text) <= 90:
        score += 0.15
    # Very long text is unlikely to be a heading.
    if len(text) > 200:
        score -= 0.40

    return max(0.0, score)


def _heading_is_accepted_ocr(
    region: LayoutRegion,
    score: float,
    *,
    body_ocr_height_median: float | None,
) -> bool:
    """Return ``True`` when *region* should be kept as a heading in the OCR path (§28).

    Acceptance requires both a score above the threshold and at least one of:
    - OCR line height is at least 1.15× the body OCR median.
    - The text begins with a numbered section prefix.
    - Score is especially high (>= 2.0), suggesting unambiguous heading geometry.
    """
    if score < HEADING_ACCEPTANCE_THRESHOLD:
        return False
    if body_ocr_height_median is None or body_ocr_height_median <= 0:
        # No reference: accept on score alone (conservative fallback).
        return score >= HEADING_ACCEPTANCE_THRESHOLD

    ocr_height = _ocr_region_height(region)
    box_height = ocr_height if ocr_height is not None else region.bbox.height
    size_signal = box_height >= body_ocr_height_median * 1.15

    text = " ".join(line.text for line in region.ocr_lines).strip()
    if not text:
        text = " ".join(line.text for line in region.native_lines).strip()
    has_numbered_prefix = bool(re.match(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)[.)]?\s+", text))

    return size_signal or has_numbered_prefix or score >= 2.0
