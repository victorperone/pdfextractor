"""Path-based table geometry detection.

Replaces the global BBox.union_all approach with connected-component analysis:
segments are only grouped when they actually touch or intersect, preventing
decorative rules and table grids from merging into one oversized region.

Each connected component that has evidence on both axes (horizontal AND
vertical segments) produces one TableGeometryCandidate.
"""
from __future__ import annotations

from dataclasses import dataclass

from structured_pdf_text.document import NativePageEvidence
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class TableGeometryCandidate:
    bbox: BBox
    horizontal_segments: tuple[BBox, ...]
    vertical_segments: tuple[BBox, ...]
    confidence: float


def detect_path_table_candidates(
    page: NativePageEvidence,
) -> list[TableGeometryCandidate]:
    """Return one candidate per connected grid component found in native paths.

    Only segments with evidence on both axes are returned. Isolated lines and
    page-background paths are discarded.
    """
    paths = [
        path.bbox
        for path in page.objects.paths
        if path.bbox is not None and not _is_page_background(path.bbox, page.bbox)
    ]

    horizontal = [
        p for p in paths
        if p.height <= 4.0 and p.width >= page.bbox.width * 0.20
    ]
    vertical = [
        p for p in paths
        if p.width <= 4.0 and p.height >= page.bbox.height * 0.12
    ]

    if len(horizontal) < 2 or len(vertical) < 2:
        return []

    all_segments = horizontal + vertical
    components = _connected_components(horizontal, vertical)

    candidates: list[TableGeometryCandidate] = []
    for h_segs, v_segs in components:
        if len(h_segs) < 2 or len(v_segs) < 2:
            continue
        all_in_component = list(h_segs) + list(v_segs)
        component_bbox = BBox.union_all(all_in_component)
        confidence = min(0.95, 0.60 + 0.05 * min(len(h_segs) + len(v_segs), 7))
        candidates.append(
            TableGeometryCandidate(
                bbox=component_bbox,
                horizontal_segments=tuple(h_segs),
                vertical_segments=tuple(v_segs),
                confidence=confidence,
            )
        )

    return _deduplicate_candidates(candidates)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _segments_connected(a: BBox, b: BBox, tolerance: float = 6.0) -> bool:
    """Return True when two segments touch, cross, or are within tolerance."""
    x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    y_overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    # They intersect if both overlaps are positive.
    if x_overlap >= -tolerance and y_overlap >= -tolerance:
        return True
    return False


def _connected_components(
    horizontal: list[BBox],
    vertical: list[BBox],
) -> list[tuple[list[BBox], list[BBox]]]:
    """Group h/v segments into connected components via union-find."""
    all_segs: list[BBox] = horizontal + vertical
    n = len(all_segs)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _segments_connected(all_segs[i], all_segs[j]):
                union(i, j)

    groups: dict[int, tuple[list[BBox], list[BBox]]] = {}
    n_h = len(horizontal)
    for i, seg in enumerate(all_segs):
        root = find(i)
        if root not in groups:
            groups[root] = ([], [])
        if i < n_h:
            groups[root][0].append(seg)
        else:
            groups[root][1].append(seg)

    return list(groups.values())


def _deduplicate_candidates(
    candidates: list[TableGeometryCandidate],
) -> list[TableGeometryCandidate]:
    """Remove candidates whose bbox is almost identical to a larger one."""
    result: list[TableGeometryCandidate] = []
    for candidate in sorted(candidates, key=lambda c: -c.bbox.area):
        if any(
            candidate.bbox.overlap_ratio(existing.bbox) >= 0.85
            or existing.bbox.overlap_ratio(candidate.bbox) >= 0.85
            for existing in result
        ):
            continue
        result.append(candidate)
    return result


def _is_page_background(box: BBox, page_bbox: BBox) -> bool:
    return box.overlap_ratio(page_bbox) >= 0.98 and box.area / max(page_bbox.area, 1.0) >= 0.90
