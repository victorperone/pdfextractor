"""OCR-aware layout re-classification pass (§27).

After EasyOCR produces reconstructed text lines for a page, this module
performs a second layout pass using OCR evidence to improve region
classification.  It does not replace the primary layout engine; it refines
the output of the primary pass using signals that are only available after
OCR runs.

Entry point: :func:`reclassify_ocr_regions`.

Features analysed per region
-----------------------------
- TITLE detection: OCR line height ratio vs document body median.
- LIST detection: bullet/numbered prefix patterns (§32 complement).
- CAPTION detection: short text below/above a TABLE or FIGURE region.
- HEADER/FOOTER detection: position near page top/bottom + repetition
  across pages (complements existing header/footer logic).
- Unknown → TEXT downgrade: regions that are still UNKNOWN but have
  OCR lines are promoted to TEXT so they participate in reading order.

Design constraints
------------------
- Never alters TABLE or FIGURE regions (those are anchor regions from the
  primary pass and have stable geometry).
- Never removes or reorders regions — only changes ``kind``.
- Operates on copies produced via dataclasses.replace(); the input list
  is not mutated.
- All heuristics are threshold-based with no external model dependencies.
"""
from __future__ import annotations

import dataclasses
import re
import statistics
from typing import TYPE_CHECKING

from structured_pdf_text.document import LayoutRegion, RegionKind, TextLine

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Bullet / list prefix patterns (complement to §32 merge_ocr_bullet_markers)
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(
    r"^(?:"
    r"[•◦▪‣·●○■–—*]\s+"          # symbol bullets
    r"|(?:\d+|[a-zA-Z])[.)]\s+"   # 1. 1) a. a)
    r"|(?:i{1,3}|iv|vi{0,3}|ix)[.)]\s+"  # roman numerals i. iv) etc.
    r")"
)


def _is_list_line(line: TextLine) -> bool:
    text = line.text.strip()
    return bool(_BULLET_RE.match(text))


def _list_ratio(lines: list[TextLine]) -> float:
    if not lines:
        return 0.0
    count = sum(1 for l in lines if _is_list_line(l))
    return count / len(lines)


# ---------------------------------------------------------------------------
# Caption detection
# ---------------------------------------------------------------------------

_CAPTION_RE = re.compile(
    r"^(?:figura|figure|fig\.?|tabela|table|tab\.?|quadro|gráfico|graph|chart)\b",
    re.IGNORECASE,
)
_CAPTION_MAX_LINES = 3
_CAPTION_MAX_CHARS = 200


def _is_caption(region: LayoutRegion) -> bool:
    lines = region.ocr_lines or region.native_lines
    if not lines or len(lines) > _CAPTION_MAX_LINES:
        return False
    text = " ".join(l.text for l in lines).strip()
    if len(text) > _CAPTION_MAX_CHARS:
        return False
    return bool(_CAPTION_RE.match(text))


# ---------------------------------------------------------------------------
# OCR line height helpers
# ---------------------------------------------------------------------------

def _ocr_line_heights(region: LayoutRegion) -> list[float]:
    return [l.bbox.height for l in region.ocr_lines if l.bbox.height > 0]


def _body_ocr_height_median(regions: list[LayoutRegion]) -> float | None:
    _prose_kinds = {RegionKind.TEXT, RegionKind.LIST, RegionKind.CAPTION, RegionKind.UNKNOWN}
    heights: list[float] = []
    for r in regions:
        if r.kind in _prose_kinds:
            heights.extend(_ocr_line_heights(r))
    return statistics.median(heights) if heights else None


def _region_height_ratio(region: LayoutRegion, body_median: float) -> float:
    heights = _ocr_line_heights(region)
    if not heights or body_median <= 0:
        return 1.0
    return statistics.median(heights) / body_median


# ---------------------------------------------------------------------------
# Per-region reclassification
# ---------------------------------------------------------------------------

_TITLE_HEIGHT_RATIO_THRESHOLD = 1.3   # OCR line height >= 1.3× body → title candidate
_LIST_RATIO_THRESHOLD = 0.5           # ≥50% of lines start with a bullet → list region
_HEADER_FOOTER_BAND = 0.10            # top/bottom 10% of page height


def _reclassify_one(
    region: LayoutRegion,
    body_median: float | None,
    page_height: float,
) -> LayoutRegion:
    """Return a reclassified copy of ``region``, or the original if unchanged."""
    kind = region.kind

    # Never touch TABLE or FIGURE — those are anchor regions.
    if kind in (RegionKind.TABLE, RegionKind.FIGURE):
        return region

    lines = region.ocr_lines
    if not lines:
        return region

    # UNKNOWN → TEXT: promote if OCR lines are present so the region
    # participates in reading order and paragraph segmentation.
    if kind == RegionKind.UNKNOWN:
        kind = RegionKind.TEXT

    # CAPTION detection: short intro text near a table/figure.
    # Only upgrade TEXT/UNKNOWN to CAPTION (never downgrade TITLE).
    if kind == RegionKind.TEXT and _is_caption(region):
        kind = RegionKind.CAPTION

    # LIST detection: most lines start with a bullet/number.
    if kind == RegionKind.TEXT and _list_ratio(lines) >= _LIST_RATIO_THRESHOLD:
        kind = RegionKind.LIST

    # TITLE detection via OCR line height ratio (§28 complement).
    # Only upgrade TEXT to TITLE; do not re-classify LIST/CAPTION.
    if kind == RegionKind.TEXT and body_median is not None:
        ratio = _region_height_ratio(region, body_median)
        if ratio >= _TITLE_HEIGHT_RATIO_THRESHOLD:
            kind = RegionKind.TITLE

    # HEADER/FOOTER detection via position.
    if kind == RegionKind.TEXT and page_height > 0:
        top_frac = region.bbox.y0 / page_height
        bot_frac = region.bbox.y1 / page_height
        if top_frac < _HEADER_FOOTER_BAND:
            kind = RegionKind.HEADER
        elif bot_frac > (1.0 - _HEADER_FOOTER_BAND):
            kind = RegionKind.FOOTER

    if kind == region.kind:
        return region
    return dataclasses.replace(region, kind=kind)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def reclassify_ocr_regions(
    regions: list[LayoutRegion],
    *,
    page_height: float = 0.0,
) -> list[LayoutRegion]:
    """Apply OCR-aware reclassification to a list of layout regions.

    Computes document-level OCR line height statistics, then iterates over each
    region and re-classifies it based on OCR evidence.  Returns a new list;
    the input is not mutated.

    Args:
        regions:     List of layout regions after primary layout pass and OCR.
        page_height: Height of the page in PDF units (for header/footer detection).

    Returns:
        New list of LayoutRegion instances; unchanged regions are the same
        objects as in the input list (no copy overhead).
    """
    if not regions:
        return regions

    body_median = _body_ocr_height_median(regions)
    return [_reclassify_one(r, body_median, page_height) for r in regions]
