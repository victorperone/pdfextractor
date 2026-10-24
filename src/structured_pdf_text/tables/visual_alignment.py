"""Reversible pixel geometry for slightly skewed raster grids.

No text is recognized here. Long ruling lines establish two independent axes;
OCR observations keep their original coordinates and evidence identities.
"""
from __future__ import annotations

import math
from statistics import median


IDENTITY = (1., 0., 0., 0., 1., 0.)


def point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a * x + b * y + c, d * x + e * y + f


def compose(outer, inner):
    a, b, c, d, e, f = outer
    g, h, i, j, k, l = inner
    return (a*g+b*j, a*h+b*k, a*i+b*l+c,
            d*g+e*j, d*h+e*k, d*i+e*l+f)


def inverse(matrix):
    a, b, c, d, e, f = matrix
    determinant = a*e-b*d
    if not math.isfinite(determinant) or abs(determinant) < 1e-9:
        raise ValueError("Singular grid transform")
    return (e/determinant, -b/determinant, (b*f-e*c)/determinant,
            -d/determinant, a/determinant, (d*c-a*f)/determinant)


def quarter_turn_to_source(turns, width, height):
    return {0: IDENTITY, 1: (0., -1., float(width), 1., 0., 0.),
            2: (-1., 0., float(width), 0., -1., float(height)),
            3: (0., 1., 0., -1., 0., float(height))}[turns % 4]


def warp(image, to_source, size):
    import cv2
    import numpy as np

    # Geometry describes pixel edges; OpenCV samples integer pixel centres.
    # Conjugate by half a pixel so quarter turns match np.rot90 exactly and
    # thin rules do not move out of their declared cell boundaries.
    matrix = np.asarray(inverse(to_source)).reshape(2, 3)
    matrix[:, 2] += matrix[:, :2] @ np.array([.5, .5]) - .5
    return cv2.warpAffine(np.asarray(image), matrix,
                          size, flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def align_ruling_lines(image):
    """Return (rectified raster, rectified-to-source affine), or no evidence.

    At least three distinct long lines on each axis are required. Independent
    axis slopes handle shear as well as rotation, without treating text
    baselines or a pair of chart axes as a table.
    """
    import cv2
    import numpy as np

    array = np.asarray(image)
    height, width = array.shape[:2]
    scale = min(1., 1600 / max(width, height))
    preview = cv2.resize(array, None, fx=scale, fy=scale) if scale < 1 else array
    gray = cv2.cvtColor(preview, cv2.COLOR_RGB2GRAY) if preview.ndim == 3 else preview
    h, w = gray.shape
    if min(h, w) < 48:
        return None
    # Otsu on a mostly white page separates black text but can discard every
    # light ruling line of the table. The independent-axis/network checks
    # below establish geometry; include light rules in their input as well.
    binary = cv2.threshold(gray, 235, 255, cv2.THRESH_BINARY_INV)[1]
    lines = cv2.HoughLinesP(binary, 1, math.pi/1800,
                            threshold=max(20, min(80, min(h, w)//8)),
                            minLineLength=max(20, min(h, w)*.20),
                            maxLineGap=max(3, min(h, w)*.01))
    if lines is None:
        return None
    horizontal, vertical = [], []
    for x0, y0, x1, y1 in lines[:, 0]:
        dx, dy = float(x1-x0), float(y1-y0)
        if abs(dx) >= w*.20 and abs(dy/dx) <= .14:
            horizontal.append((dy/dx, y0-(dy/dx)*x0, abs(dx)))
        if abs(dy) >= h*.20 and abs(dx/dy) <= .14:
            vertical.append((dx/dy, x0-(dx/dy)*y0, abs(dy)))
    if len(horizontal) < 3 or len(vertical) < 3:
        return None

    def axis(values):
        # Long segments dominate glyph fragments; count independent intercepts.
        longest = max(v[2] for v in values)
        strong = [v for v in values if v[2] >= longest*.40]
        slope = median(v[0] for v in strong)
        compatible = [v for v in strong if abs(v[0]-slope) < .015]
        intercepts = []
        for _, intercept, _ in sorted(compatible, key=lambda v: v[1]):
            if not intercepts or intercept-intercepts[-1] > max(3, min(h, w)*.008):
                intercepts.append(intercept)
        return slope, len(intercepts)

    hs, hn = axis(horizontal)
    vs, vn = axis(vertical)
    if min(hn, vn) < 3 or max(abs(hs), abs(vs)) < .003:
        return None
    axes_to_source = (1., vs, 0., hs, 1., 0.)
    source_to_axes = inverse(axes_to_source)
    corners = [point(source_to_axes, x, y) for x, y in
               ((0, 0), (width, 0), (width, height), (0, height))]
    left, top = min(p[0] for p in corners), min(p[1] for p in corners)
    size = (math.ceil(max(p[0] for p in corners)-left),
            math.ceil(max(p[1] for p in corners)-top))
    to_source = compose(axes_to_source, (1., 0., left, 0., 1., top))
    return warp(array, to_source, size), to_source
