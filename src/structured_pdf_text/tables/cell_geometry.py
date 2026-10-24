"""Exact convex cell geometry without optional raster/ML dependencies."""
from __future__ import annotations

import math

from structured_pdf_text.geometry import Point


def polygon(cell):
    if getattr(cell, "polygon", None) is not None:
        return cell.polygon
    b = cell.bbox
    return () if b is None else (Point(b.x0, b.y0), Point(b.x1, b.y0),
                                  Point(b.x1, b.y1), Point(b.x0, b.y1))


def valid_polygon(cell):
    points = polygon(cell)
    if len(points) != 4 or cell.bbox is None:
        return False
    if any(not isinstance(p, Point) or not math.isfinite(p.x) or not math.isfinite(p.y) for p in points):
        return False
    crosses = []
    for a, b, c in zip(points, (*points[1:], points[0]), (*points[2:], *points[:2])):
        cross = (b.x-a.x)*(c.y-b.y)-(b.y-a.y)*(c.x-b.x)
        if abs(cross) > 1e-9:
            crosses.append(cross)
    if len(crosses) != 4 or (min(crosses) < 0 < max(crosses)):
        return False
    envelope = (min(p.x for p in points), min(p.y for p in points),
                max(p.x for p in points), max(p.y for p in points))
    return all(abs(a-b) <= 1e-6 for a, b in zip(envelope,
               (cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1)))


def contains_point(cell, x, y):
    points = polygon(cell)
    if len(points) != 4 or any(not isinstance(p, Point) for p in points):
        return False
    signs = []
    for a, b in zip(points, (*points[1:], points[0])):
        cross = (b.x-a.x)*(y-a.y)-(b.y-a.y)*(x-a.x)
        if abs(cross) > 1e-7*max(1., math.hypot(b.x-a.x, b.y-a.y)):
            signs.append(cross > 0)
    return not signs or all(sign == signs[0] for sign in signs)


def overlaps(first, second):
    if first.bbox is None or second.bbox is None or first.bbox.intersection(second.bbox) is None:
        return False
    if not valid_polygon(first) or not valid_polygon(second):
        return True
    a, b = polygon(first), polygon(second)
    # Separating-axis test. Sharing an edge is not an area overlap.
    for vertices in (a, b):
        for p, q in zip(vertices, (*vertices[1:], vertices[0])):
            nx, ny = -(q.y-p.y), q.x-p.x
            norm = math.hypot(nx, ny)
            if not norm:
                continue
            pa = [(v.x*nx+v.y*ny)/norm for v in a]
            pb = [(v.x*nx+v.y*ny)/norm for v in b]
            if min(max(pa), max(pb))-max(min(pa), min(pb)) <= 1e-7:
                return False
    return True


def reading_coordinates(cells):
    """Project source points onto consistent, observed polygon axes.

    Corners retain logical top-left/top-right/bottom-right/bottom-left order
    after rectification. Indices do not determine the axes: contradictory
    polygons fall back to the ordinary geometry checks.
    """
    if not cells or any(getattr(cell, "polygon", None) is None or not valid_polygon(cell) for cell in cells):
        return None

    def axes(cell):
        a, b, _c, d = cell.polygon
        vx, vy = b.x-a.x, b.y-a.y
        ux, uy = d.x-a.x, d.y-a.y
        vn, un = math.hypot(vx, vy), math.hypot(ux, uy)
        return vx/vn, vy/vn, ux/un, uy/un

    vx, vy, ux, uy = axes(cells[0])
    determinant = vx*uy-vy*ux
    if abs(determinant) < .5:
        return None
    for cell in cells[1:]:
        x, y, u, v = axes(cell)
        if vx*x+vy*y < .99 or ux*u+uy*v < .99:
            return None

    return lambda x, y: ((uy*x-ux*y)/determinant, (-vy*x+vx*y)/determinant)
