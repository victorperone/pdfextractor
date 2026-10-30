"""Raster checkbox geometry; engines own independent settings and activation."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RasterSelectionMarkPolicy:
    """Recover form check boxes that the recognizer drops from a text row.

    A box is a hollow square, about one text-row tall, at the start of an
    upright row (left of it, or inside the detected box where the recognizer
    reads it as blank), with all four sides inked, a clear ring around it and
    a blank gap before the label (grid cells and letters fail one of these).
    The interior ink decides the state: empty, X-marked or check-marked;
    ambiguous interiors abstain. A detected box becomes its own observation;
    the row keeps its reading and only its left edge moves past the box.
    """

    policy_id: str = "raster-selection-marks-v2"
    unseeded_inner_maximum_words: int = 8
    # Box rules are often one pixel wide at OCR scale; antialiased rule pixels
    # stay around 150-200, so ink is anything clearly darker than paper.
    dark_level: int = 170
    minimum_side_fraction: float = .35
    maximum_side_fraction: float = 1.0
    minimum_edge_ink: float = .85
    maximum_ring_ink: float = .12
    minimum_corner_ink: float = .75
    maximum_gap_ink: float = .05
    empty_interior_ink: float = .03
    marked_interior_ink: float = .08
    maximum_interior_ink: float = .45
    cross_diagonal_ink: float = .55
    confidence: float = .90

    def identity(self) -> dict:
        return {"policy_id": self.policy_id, "dark_level": self.dark_level,
                "unseeded_inner_maximum_words": self.unseeded_inner_maximum_words,
                "inner_marked_requires_form_evidence": True,
                "side_fraction": [self.minimum_side_fraction, self.maximum_side_fraction],
                "minimum_edge_ink": self.minimum_edge_ink, "maximum_ring_ink": self.maximum_ring_ink,
                "minimum_corner_ink": self.minimum_corner_ink, "maximum_gap_ink": self.maximum_gap_ink,
                "interior_ink": [self.empty_interior_ink, self.marked_interior_ink, self.maximum_interior_ink]}

    def apply(self, image, rows) -> list:
        """Return *rows* plus one ``(box, symbol, confidence)`` row per box."""
        import numpy as np
        if image is None or not rows:
            return list(rows)
        pixels = np.asarray(image)
        output, marks, found_boxes = [], [], []
        for row in rows:
            output.append(row)
            try:
                points = [(float(x), float(y)) for x, y in row[0]]
                text = str(row[1]).strip()
            except (TypeError, ValueError, IndexError):
                continue
            if not text or text[0] in "☐☑☒□■口":
                continue
            xs, ys = [x for x, _ in points], [y for _, y in points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            height = y1 - y0
            # Upright rows only: a tall or rotated row has no "left of" box.
            if height <= 4 or (x1 - x0) < height * 1.5:
                continue
            # Work on the row's neighbourhood only, in local coordinates.
            oy0 = max(0, int(y0 - height * .30) - 4)
            ox0 = max(0, int(x0 - height * 2.5) - 4)
            oy1 = min(pixels.shape[0], int(y1 + height * .30) + 4)
            ox1 = min(pixels.shape[1], int(x0 + height * .6 + height * self.maximum_side_fraction) + int(height * .25) + 8)
            if oy1 - oy0 < 8 or ox1 - ox0 < 8:
                continue
            crop = pixels[oy0:oy1, ox0:ox1]
            dark = (crop if crop.ndim == 2 else crop[..., :3].mean(axis=2)) < self.dark_level
            integral = np.pad(dark.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            local = self._box_near(dark, integral, x0 - ox0, y0 - oy0, y1 - oy0, height)
            if local is None:
                continue
            symbol = self._state(dark, local)
            found = (local[0] + ox0, local[1] + oy0, local[2] + ox0, local[3] + oy0)
            if any(_overlaps(found, existing) for existing in found_boxes):
                continue
            if symbol is None:
                continue
            found_boxes.append(found)
            bx0, by0, bx1, by1 = found
            marks.append(([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], symbol, self.confidence))
            if bx1 > x0:
                # The detected row began at the box: its glyphs start after it.
                output[-1] = ([[max(x, bx1 + 1.), y] for x, y in points], *tuple(row)[1:])
        # Options laid out on one line ("☑ CONFERIDO  ☐ COMPLEMENTO") reach
        # the recognizer as a single row with the inner boxes read as blank.
        split_rows = []
        for row in output:
            split_rows.extend(self._split_at_inner_boxes(pixels, row, marks, found_boxes))
        return [*split_rows, *marks]

    def _split_at_inner_boxes(self, pixels, row, marks, found_boxes) -> list:
        import numpy as np
        try:
            points = [(float(x), float(y)) for x, y in row[0]]
            text = str(row[1])
        except (TypeError, ValueError, IndexError):
            return [row]
        xs, ys = [x for x, _ in points], [y for _, y in points]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        height = y1 - y0
        if height <= 4 or (x1 - x0) < height * 4 or len(text.split()) < 2:
            return [row]
        # A recovered/recognized option on this baseline establishes a form
        # row. Prose has no such evidence: do not scan long sentences, and
        # accept only a genuinely empty square in short unseeded labels.
        form_row = any(mark in text for mark in "☐☑☒□■口") or any(
            abs((box[1] + box[3]) * .5 - (y0 + y1) * .5) <= height * .5
            and x0 - height * 3 <= box[0] <= x1 for box in found_boxes
        )
        if not form_row and len(text.split()) > self.unseeded_inner_maximum_words:
            return [row]
        oy0, oy1 = max(0, int(y0 - height * .30) - 4), min(pixels.shape[0], int(y1 + height * .30) + 4)
        ox0, ox1 = max(0, int(x0) - 4), min(pixels.shape[1], int(x1) + 4)
        crop = pixels[oy0:oy1, ox0:ox1]
        dark = (crop if crop.ndim == 2 else crop[..., :3].mean(axis=2)) < self.dark_level
        integral = np.pad(dark.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        boxes = self._box_candidates(dark, integral, x0 + height * .6 - ox0, x1 - height * .6 - ox0,
                                     y0 - oy0, y1 - oy0, height, inner=True)
        pieces = [(x0, x1, text)]
        for left, top, right, bottom in sorted(boxes):
            symbol = self._state(dark, (left, top, right, bottom))
            found = (left + ox0, top + oy0, right + ox0, bottom + oy0)
            if not form_row and symbol != "☐":
                continue
            if symbol is None or any(_overlaps(found, existing) for existing in found_boxes):
                continue
            start, end, piece = pieces[-1]
            if not start < found[0] < found[2] < end:
                continue
            split = _split_text_at(piece, (found[0] - start) / max((found[0] - start) + (end - found[2]), 1.0))
            if split is None:
                continue
            before, after = split
            pieces[-1:] = [(start, found[0] - 1., before), (found[2] + 1., end, after)]
            found_boxes.append(found)
            fx0, fy0, fx1, fy1 = found
            marks.append(([[fx0, fy0], [fx1, fy0], [fx1, fy1], [fx0, fy1]], symbol, self.confidence))
        if len(pieces) == 1:
            return [row]
        rest = tuple(row)[2:]
        return [([[a, y0], [b, y0], [b, y1], [a, y1]], piece, *rest) for a, b, piece in pieces]

    def _box_near(self, dark, integral, x0, y0, y1, height):
        """Best hollow square near the row start (left of it or at its start)."""
        boxes = self._box_candidates(dark, integral, x0 - height * 2.5, x0 + height * .6, y0, y1, height)
        return boxes[0] if boxes else None

    def _box_candidates(self, dark, integral, left_min, left_max, y0, y1, height, *, inner=False):
        """Non-overlapping hollow squares whose left side lies in the range.

        Runs of ink going down and right from each pixel locate top-left
        corners with two long sides; only those candidates are measured. An
        inner box also needs a blank gap before it, separating it from the
        preceding word.
        """
        import numpy as np
        rows_px, cols_px = dark.shape
        smallest = max(5, int(height * self.minimum_side_fraction))
        largest = int(height * self.maximum_side_fraction)
        wy0, wy1 = max(3, int(y0 - height * .30)), min(rows_px - 3, int(y1 + height * .30))
        wx0, wx1 = max(3, int(left_min)), min(cols_px - 3, int(left_max) + largest)
        if wy1 - wy0 < smallest or wx1 - wx0 < smallest:
            return []
        window = dark[wy0:wy1, wx0:wx1]
        down = np.zeros(window.shape, dtype=np.int32)
        right = np.zeros(window.shape, dtype=np.int32)
        for r in range(window.shape[0] - 1, -1, -1):
            down[r] = window[r] * ((down[r + 1] if r + 1 < window.shape[0] else 0) + 1)
        for c in range(window.shape[1] - 1, -1, -1):
            right[:, c] = window[:, c] * ((right[:, c + 1] if c + 1 < window.shape[1] else 0) + 1)
        minimum_run = int(np.ceil(smallest * self.minimum_edge_ink))
        corners = np.argwhere((down >= minimum_run) & (right >= minimum_run))
        # The former scalar lookup returned zero just beyond the window;
        # preserve its two-pixel edge tolerance without indexing past it.
        down = np.pad(down, ((0, 1), (0, 1)))
        right = np.pad(right, ((0, 1), (0, 1)))

        # Validate all corners for a given side together. Dense raster rows
        # used to execute millions of Python scalar integral/run lookups.
        # Array bounds and all geometric tests remain identical; no candidate
        # is truncated and the memory bound is one window plus its corners.
        def ink(ax0, ay0, ax1, ay1):
            area = (ax1 - ax0) * (ay1 - ay0)
            total = integral[ay1, ax1] - integral[ay0, ax1] - integral[ay1, ax0] + integral[ay0, ax0]
            return np.divide(total, area, out=np.ones(np.broadcast_shapes(np.shape(total), np.shape(area))), where=area > 0)

        found = []
        for side in range(smallest, largest + 1):
            r, c = corners.T
            valid = ((wx0 + c <= left_max) & (r + side - 2 < window.shape[0])
                     & (c + side - 2 < window.shape[1]))
            r, c = r[valid], c[valid]
            need = side * self.minimum_edge_ink
            valid = ((down[r, c] >= need) & (right[r, c] >= need)
                     & (np.maximum(down[r, c + side - 1], down[r, c + side - 2]) >= need)
                     & (np.maximum(right[r + side - 1, c], right[r + side - 2, c]) >= need))
            top, left = wy0 + r[valid], wx0 + c[valid]
            bottom, rightmost = top + side, left + side
            valid = (bottom + 2 < rows_px) & (rightmost + 2 < cols_px) & (top >= 2) & (left >= 2)
            top, left, bottom, rightmost = (arr[valid] for arr in (top, left, bottom, rightmost))
            if not top.size:
                continue
            corner_ink = np.minimum.reduce((ink(left, top, left + 2, top + 2),
                ink(rightmost - 2, top, rightmost, top + 2), ink(left, bottom - 2, left + 2, bottom),
                ink(rightmost - 2, bottom - 2, rightmost, bottom)))
            ring = np.maximum.reduce((ink(left - 1, top - 2, rightmost + 1, top - 1),
                ink(left - 1, bottom + 1, rightmost + 1, bottom + 2),
                ink(left - 2, top - 1, left - 1, bottom + 1), ink(rightmost + 1, top - 1, rightmost + 2, bottom + 1)))
            reach = max(3, side // 4)
            extensions = np.maximum.reduce((
                ink(left, np.maximum(0, top - reach), left + 2, top - 1),
                ink(rightmost - 2, np.maximum(0, top - reach), rightmost, top - 1),
                ink(left, bottom + 1, left + 2, np.minimum(rows_px, bottom + reach)),
                ink(rightmost - 2, bottom + 1, rightmost, np.minimum(rows_px, bottom + reach)),
                ink(np.maximum(0, left - reach), top, left - 1, top + 2),
                ink(np.maximum(0, left - reach), bottom - 2, left - 1, bottom),
                ink(rightmost + 1, top, np.minimum(cols_px, rightmost + reach), top + 2),
                ink(rightmost + 1, bottom - 2, np.minimum(cols_px, rightmost + reach), bottom)))
            gap = max(2, int(side * .25))
            valid = ((corner_ink >= self.minimum_corner_ink) & (ring <= self.maximum_ring_ink)
                     & (extensions <= .34)
                     & (ink(rightmost + 2, top, np.minimum(cols_px, rightmost + 2 + gap), bottom) <= self.maximum_gap_ink))
            if inner:
                valid &= ink(np.maximum(0, left - 2 - gap), top, left - 2, bottom) <= self.maximum_gap_ink
            found.extend((1. - float(score), int(x), int(y), side)
                         for score, x, y in zip(ring[valid], left[valid], top[valid]))
        # Preserve the old corner-first order when candidates tie.
        found.sort(key=lambda item: (item[2], item[1], item[3]))
        boxes: list = []
        for _, left, top, side in sorted(found, key=lambda item: -item[0]):
            box = (float(left), float(top), float(left + side), float(top + side))
            if not any(_overlaps(box, other) for other in boxes):
                boxes.append(box)
        return boxes

    def _state(self, dark, box):
        import numpy as np
        left, top, right, bottom = (int(value) for value in box)
        margin = max(2, int((right - left) * .18))
        interior = dark[top + margin:bottom - margin, left + margin:right - margin]
        if interior.size == 0:
            return None
        ink = float(interior.mean())
        if ink <= self.empty_interior_ink:
            return "☐"
        if ink < self.marked_interior_ink or ink > self.maximum_interior_ink:
            # Ambiguous, or a solid blob (a bold glyph), not a drawn box.
            return None
        size = min(interior.shape)
        steps = np.arange(size)

        def diagonal(rows):
            hits = [interior[max(0, r - 1):r + 2, c].any() for r, c in zip(rows, steps)]
            return sum(hits) / max(len(hits), 1)

        falling, rising = diagonal(steps), diagonal(size - 1 - steps)
        return "☒" if min(falling, rising) >= self.cross_diagonal_ink else "☑"


def _split_text_at(text: str, fraction: float):
    """Split *text* at the word boundary nearest to *fraction* of its glyphs.

    Returns ``None`` when no whitespace lies within three characters of the
    estimate: an uncertain split would move words to the wrong option.
    """
    stripped = text.strip()
    if not 0.0 < fraction < 1.0 or " " not in stripped:
        return None
    estimate = fraction * len(stripped)
    spaces = [index for index, char in enumerate(stripped) if char == " "]
    nearest = min(spaces, key=lambda index: abs(index - estimate))
    if abs(nearest - estimate) > 3:
        return None
    before, after = stripped[:nearest].strip(), stripped[nearest:].strip()
    return (before, after) if before and after else None


def _overlaps(first, second) -> bool:
    return not (first[2] <= second[0] or second[2] <= first[0] or first[3] <= second[1] or second[3] <= first[1])

