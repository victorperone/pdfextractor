"""Line-to-region assignment for native PDF layout.

Each :class:`~structured_pdf_text.document.TextLine` is matched to exactly
one :class:`~structured_pdf_text.document.LayoutRegion` based on the fraction
of the line bounding box that falls inside each candidate region.  Lines whose
best coverage is below *_MIN_LINE_REGION_COVERAGE* are left unassigned.
"""
from __future__ import annotations

from structured_pdf_text.document import LayoutRegion, TextLine

# Minimum overlap ratio for a line to be considered inside a region.
_MIN_LINE_REGION_COVERAGE = 0.5

# Two candidates are considered "tied" when their coverage differs by less than
# this fraction. When tied, the region with the smaller area wins (more specific).
_COVERAGE_TIE_TOLERANCE = 0.05


def assign_lines_to_regions(lines: list[TextLine], regions: list[LayoutRegion]) -> list[LayoutRegion]:
    """Assign each line to the best matching region.

    Selection priority for tied coverage (INV-16.2):
    1. Highest overlap ratio.
    2. Within tie tolerance: smaller region area (more specific geometry).
    3. Higher layout_confidence.
    4. Original list order (deterministic).
    """
    for region in regions:
        region.native_lines.clear()
    for line in lines:
        best_region = _best_region_for_line(line, regions)
        if best_region is not None:
            best_region.native_lines.append(line)
    return regions


def _best_region_for_line(
    line: TextLine,
    regions: list[LayoutRegion],
) -> LayoutRegion | None:
    """Return the single best region for *line*, or ``None`` if none qualifies.

    All four selection criteria are encoded as a sortable tuple so that a
    single ``max()`` call resolves ties deterministically without explicit
    branching:

    * Position 0 — coverage ratio (higher is better; compared first).
    * Position 1 — negative region area (negative so that *smaller* regions
      sort *higher*, i.e. more specific geometry wins when coverage is tied).
    * Position 2 — ``layout_confidence`` (higher is better).
    * Position 3 — negative list index (negative so that the *earlier* region
      wins on a final tie, preserving the original ordering).
    * Position 4 — the :class:`~structured_pdf_text.document.LayoutRegion`
      itself (carried along for retrieval; not used in comparisons).

    Only candidates whose coverage exceeds *_MIN_LINE_REGION_COVERAGE* enter
    the tuple list.  Among those, only candidates within
    *_COVERAGE_TIE_TOLERANCE* of the maximum coverage are kept before the
    secondary sort.
    """
    candidates: list[tuple[float, float, float, int, LayoutRegion]] = []
    for index, region in enumerate(regions):
        coverage = line.bbox.overlap_ratio(region.bbox)
        if coverage <= _MIN_LINE_REGION_COVERAGE:
            continue
        candidates.append((
            coverage,
            -region.bbox.area,           # negative → smaller area sorts higher
            region.layout_confidence or 0.0,
            -index,                       # negative → earlier index sorts higher
            region,
        ))
    if not candidates:
        return None
    # Primary sort: highest coverage. Within tie tolerance, smaller area wins.
    best_coverage = max(c[0] for c in candidates)
    tied = [c for c in candidates if best_coverage - c[0] <= _COVERAGE_TIE_TOLERANCE]
    # Among tied candidates sort by (-area, confidence, -index) — all already
    # encoded in tuple positions 1–3, so a plain max() gives the correct winner.
    winner = max(tied, key=lambda c: (c[1], c[2], c[3]))
    return winner[4]
