"""Engine-owned reading order of running-text columns on OCR-only pages.

Line-level detectors (PaddleOCR) return one observation per column line, but
the shared line reconstruction joins observations at the same height into one
visual row, so a page with three columns above and two below reads every row
across all columns. This policy validates column gutters from the engine's
own line observations, rebuilds lines inside each column and reads each band
of columns column by column. It is disabled unless an engine opts in.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from structured_pdf_text.document import OcrToken, TextLine, TokenFlag
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class TextColumnPolicy:
    """Validated running-text gutters (``x_mid, y_top, y_bottom``) and their bands.

    A candidate gutter is a horizontal gap of at least ``minimum_gap_heights``
    body heights between two prose observations of one visual row. A gutter
    is accepted when candidates sharing one empty vertical band occur in at
    least ``minimum_rows`` vertically contiguous rows; it then applies only to
    the vertical extent of those rows (grown over a few adjacent rows that
    leave it empty), so a table or a full-width line elsewhere is never split.
    Positions are measured in the frame rectified by the dominant text tilt,
    so the gutters of a skewed scan stay vertical.
    """

    policy_id: str
    enabled: bool = False
    minimum_gap_heights: float = 1.5
    minimum_rows: int = 3
    maximum_row_gap_heights: float = 6.0
    maximum_extension_rows: int = 2
    prose_minimum_words: int = 4
    prose_minimum_page_width: float = 0.15
    heading_maximum_lines: int = 3
    heading_maximum_chars: int = 120
    heading_minimum_gap: float = 1.5

    def arrange(self, lines: list[TextLine], tokens: list[OcrToken], page_index: int,
                page_bbox: BBox) -> list[list[TextLine]] | None:
        """Return ``lines`` rebuilt as text flows in reading order, or ``None``.

        A flow is one column of a band (read top to bottom) or one line
        outside every band. ``None`` keeps the caller's ordering: the policy
        is disabled, the page is rotated, no gutter validates, or the tokens
        do not account for exactly the given lines.
        """
        if not self.enabled or len(lines) < self.minimum_rows:
            return None
        from structured_pdf_text.ocr.reconstruct import _dominant_slope

        observed = _tokens_of_lines(lines, tokens)
        if observed is None or any(token.rotation % 360 for token in observed):
            return None
        slope = _dominant_slope(observed) or self._box_tilt(observed, page_bbox)
        frame = _RectifiedFrame(observed, page_bbox, slope)
        gutters = self._gutters(frame, page_bbox.width)
        if not gutters:
            return None
        bands = text_column_bands(gutters)
        partitions: dict[tuple[int, int] | None, list[OcrToken]] = {}
        for token in observed:
            partitions.setdefault(frame.cell(token, bands), []).append(token)
        # Lines outside every band keep their top-to-bottom position; a band is
        # read column by column where its first line appears in that order.
        entries: list[tuple[float, int, list[list[TextLine]]]] = [
            (frame.top(line), 0, [[line]])
            for line in _flow_lines(frame, partitions.pop(None, []), page_index, page_bbox, join=False)
        ]
        flows_by_band: dict[int, list[list[TextLine]]] = {}
        for (band, _column), members in sorted(partitions.items()):
            flows_by_band.setdefault(band, []).append(_flow_lines(frame, members, page_index, page_bbox))
        for flows in flows_by_band.values():
            entries.append((min(frame.top(line) for flow in flows for line in flow), 1, flows))
        return [flow for _, _, flows in sorted(entries, key=lambda entry: (entry[0], entry[1])) for flow in flows if flow]

    def column_heading(self, flow: list[TextLine], page_bbox: BBox) -> list[TextLine]:
        """Leading lines of a column flow that title the column.

        At most ``heading_maximum_lines`` short lines at the top of a column,
        not ending a sentence, followed by the widest of the first gaps of the
        column, at least ``heading_minimum_gap`` times its median line pitch.
        Pitches are measured between line centres in the frame rectified by
        the column's own tilt, which a skewed scan otherwise distorts.
        """
        if len(flow) < 3:
            return []
        levels = _flow_levels(flow)
        pitches = [b - a for a, b in zip(levels, levels[1:]) if b > a]
        if not pitches:
            return []
        pitch = median(pitches)
        best = None
        for count in range(1, min(self.heading_maximum_lines, len(flow) - 2) + 1):
            heading = flow[:count]
            text = " ".join(line.text.strip() for line in heading)
            if len(text) > self.heading_maximum_chars or heading[-1].text.strip().endswith((".", ";", ",")):
                break
            gap = levels[count] - levels[count - 1]
            if gap >= self.heading_minimum_gap * pitch and (best is None or gap > best[1]):
                best = (count, gap)
        return flow[:best[0]] if best is not None else []

    def layout(self, tokens: list[OcrToken], page_bbox: BBox) -> tuple[list[tuple[float, float, float]], float]:
        """Validated running-text gutters of *tokens* and the text tilt.

        Gutters are ``(x_mid, y_top, y_bottom)`` in the page frame rectified
        by the tilt (see ``crosses_gutter``); empty when disabled.
        """
        from structured_pdf_text.ocr.reconstruct import _dominant_slope

        observed = [token for token in tokens if token.text and token.bbox.width >= 0 and token.bbox.height >= 0]
        if not self.enabled or not observed or any(token.rotation % 360 for token in observed):
            return [], 0.0
        slope = _dominant_slope(observed) or self._box_tilt(observed, page_bbox)
        frame = _RectifiedFrame(observed, page_bbox, slope)
        origin_x, origin_y = page_bbox.x0, page_bbox.y0
        shift_x, shift_y = origin_x + slope * origin_y, origin_y - slope * origin_x
        return ([(mid + shift_x, top + shift_y, bottom + shift_y)
                 for mid, top, bottom in self._gutters(frame, page_bbox.width)], slope)

    def _box_tilt(self, tokens: list[OcrToken], page_bbox: BBox) -> float:
        """Text tilt (dy/dx) of a skewed scan read as axis-aligned line boxes.

        Without detector polygons the tilt shows in the column edges: the
        left edges of the lines of one column drift by ``-tilt`` per unit of
        height. Pairs of prose lines starting at nearly the same x, at least
        two lines apart, each give a drift; their median is the tilt when the
        pairs agree on a value between 0.5% and 20%, else 0.
        """
        wide = sorted((token for token in tokens
                       if len(token.text.split()) >= self.prose_minimum_words
                       and token.bbox.width >= self.prose_minimum_page_width * page_bbox.width),
                      key=lambda token: token.bbox.cy)
        if len(wide) < 2 * self.minimum_rows:
            return 0.0
        reach = 0.05 * page_bbox.width
        drifts = []
        for index, first in enumerate(wide):
            for second in wide[index + 1:]:
                rise = second.bbox.cy - first.bbox.cy
                shift = second.bbox.x0 - first.bbox.x0
                if rise >= 2.0 * min(first.bbox.height, second.bbox.height) and abs(shift) <= reach:
                    drifts.append(shift / rise)
        if len(drifts) < 2 * self.minimum_rows:
            return 0.0
        middle = median(drifts)
        spread = median(abs(value - middle) for value in drifts)
        if not 0.005 <= abs(middle) <= 0.2 or spread > max(0.004, abs(middle) * 0.35):
            return 0.0
        return -middle

    def _gutters(self, frame: "_RectifiedFrame", page_width: float) -> list[tuple[float, float, float]]:
        rows, centers = frame.rows()
        body = frame.body

        def prose(token: OcrToken) -> bool:
            start, end = frame.span(token)
            return (len(token.text.split()) >= self.prose_minimum_words
                    and end - start >= self.prose_minimum_page_width * page_width)

        candidates: list[tuple[float, float, float]] = []
        for row, center in zip(rows, centers):
            left = row[0]
            for token in row[1:]:
                low, high = frame.span(left)[1], frame.span(token)[0]
                if high - low >= self.minimum_gap_heights * body and prose(left) and prose(token):
                    candidates.append((center, low, high))
                if frame.span(token)[1] >= frame.span(left)[1]:
                    left = token

        # (mid, top, bottom, empty band low, empty band high)
        found: list[tuple[float, float, float, float, float]] = []
        for center, low, high in sorted(candidates):
            mid = (low + high) / 2.0
            if any(top <= center <= bottom and band_low <= mid <= band_high
                   for _, top, bottom, band_low, band_high in found):
                continue
            support = sorted(item for item in candidates if item[1] <= mid <= item[2])
            runs: list[list[tuple[float, float, float]]] = []
            for item in support:
                if runs and item[0] - runs[-1][-1][0] <= self.maximum_row_gap_heights * body:
                    runs[-1].append(item)
                else:
                    runs.append([item])
            run = next(run for run in runs if run[0][0] <= center <= run[-1][0])
            if len(run) < self.minimum_rows:
                continue
            band_low, band_high = max(item[1] for item in run), min(item[2] for item in run)
            gutter_mid = (band_low + band_high) / 2.0
            top, bottom = self._extend(frame, rows, centers, gutter_mid, run[0][0], run[-1][0])
            top, bottom = top - 2.0 * body, bottom + 2.0 * body
            # Another run through an overlapping empty band is the same gutter;
            # it keeps the band both runs leave empty.
            for index, (_, top0, bottom0, low0, high0) in enumerate(found):
                low1, high1 = max(low0, band_low), min(high0, band_high)
                if low1 < high1 and top <= bottom0 and top0 <= bottom:
                    found[index] = ((low1 + high1) / 2.0, min(top, top0), max(bottom, bottom0), low1, high1)
                    break
            else:
                found.append((gutter_mid, top, bottom, band_low, band_high))
        return [(mid, top, bottom) for mid, top, bottom, _, _ in found]

    def _extend(self, frame, rows, centers, mid, top, bottom) -> tuple[float, float]:
        """Grow a gutter over adjacent rows that leave it empty (column headings,
        short final lines), stopping at a row running across it or a large gap."""
        order = sorted(range(len(rows)), key=lambda index: centers[index])
        limit = self.maximum_row_gap_heights * frame.body

        def clear(index: int) -> bool:
            return not any(frame.span(token)[0] < mid < frame.span(token)[1] for token in rows[index])

        above = list(reversed([index for index in order if centers[index] < top]))
        for index in above[:self.maximum_extension_rows]:
            if top - centers[index] > limit or not clear(index):
                break
            top = centers[index]
        below = [index for index in order if centers[index] > bottom]
        for index in below[:self.maximum_extension_rows]:
            if centers[index] - bottom > limit or not clear(index):
                break
            bottom = centers[index]
        return top, bottom


LEGACY_TEXT_COLUMN_POLICY = TextColumnPolicy("legacy-row-order")


def crosses_gutter(box: BBox, gutters: list[tuple[float, float, float]], slope: float = 0.0) -> bool:
    """Whether a running-text gutter from ``TextColumnPolicy.layout`` runs
    through most of the height of *box* (corners mapped to the rectified frame)."""
    corners = [(x, y) for x in (box.x0, box.x1) for y in (box.y0, box.y1)]
    xs = [x + slope * y for x, y in corners]
    ys = [y - slope * x for x, y in corners]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return any(x0 < mid < x1 and min(y1, bottom) - max(y0, top) >= 0.5 * max(y1 - y0, 1.0)
               for mid, top, bottom in gutters)


def text_column_bands(
    gutters: list[tuple[float, float, float]],
) -> list[tuple[float, float, list[float]]]:
    """Group gutters sharing most of their vertical extent into bands
    ``(top, bottom, sorted gutter x)``; three columns above two make two bands."""
    bands: list[tuple[float, float, list[float]]] = []
    for mid, top, bottom in sorted(gutters, key=lambda item: (item[1], item[0])):
        for index, (band_top, band_bottom, mids) in enumerate(bands):
            overlap = min(bottom, band_bottom) - max(top, band_top)
            if overlap >= 0.5 * min(bottom - top, band_bottom - band_top):
                bands[index] = (min(top, band_top), max(bottom, band_bottom), sorted({*mids, mid}))
                break
        else:
            bands.append((top, bottom, [mid]))
    return bands


class _RectifiedFrame:
    """Token geometry in the page frame rectified by the text tilt ``slope``:
    ``(x, y) -> (x + slope * y, y - slope * x)``."""

    def __init__(self, tokens: list[OcrToken], page_bbox: BBox, slope: float):
        self.tokens = tokens
        self.slope = slope
        self.origin = (page_bbox.x0, page_bbox.y0)
        heights = [max(token.bbox.height - abs(slope) * token.bbox.width, token.bbox.height * 0.35)
                   for token in tokens if token.bbox.height > 0]
        self.body = median(heights) if heights else 10.0

    def _local(self, box: BBox) -> BBox:
        return BBox(box.x0 - self.origin[0], box.y0 - self.origin[1], box.x1 - self.origin[0], box.y1 - self.origin[1])

    def span(self, token: OcrToken) -> tuple[float, float]:
        box = self._local(token.bbox)
        return box.x0 + self.slope * box.cy, box.x1 + self.slope * box.cy

    def level(self, token: OcrToken) -> float:
        box = self._local(token.bbox)
        return box.cy - self.slope * box.cx

    def top(self, line: TextLine) -> float:
        box = self._local(line.bbox)
        return box.y0 - self.slope * box.cx

    def rows(self, tokens: list[OcrToken] | None = None) -> tuple[list[list[OcrToken]], list[float]]:
        """Tokens at the same rectified level, each row ordered left to right."""
        tolerance = max(2.0, self.body * 0.65)
        rows: list[list[OcrToken]] = []
        centers: list[float] = []
        for token in sorted(self.tokens if tokens is None else tokens,
                            key=lambda item: (self.level(item), self.span(item)[0])):
            center = self.level(token)
            start, end = self.span(token)
            # Two observations overlapping horizontally are two lines of one
            # column, never one row (tall boxes of a skewed scan).
            best = min((index for index in range(len(centers))
                        if abs(centers[index] - center) <= tolerance
                        and not any(self.span(item)[0] < end and start < self.span(item)[1] for item in rows[index])),
                       key=lambda index: abs(centers[index] - center), default=None)
            if best is None:
                rows.append([token])
                centers.append(center)
            else:
                rows[best].append(token)
                centers[best] = median(self.level(item) for item in rows[best])
        return [sorted(row, key=lambda item: self.span(item)[0]) for row in rows], centers

    def cell(self, token: OcrToken, bands) -> tuple[int, int] | None:
        """``(band, column)`` of a token lying inside a band without crossing a gutter."""
        level = self.level(token)
        start, end = self.span(token)
        for index, (top, bottom, mids) in enumerate(bands):
            if top <= level <= bottom and not any(start < mid < end for mid in mids):
                return index, sum(mid < start for mid in mids)
        return None


def _flow_levels(flow: list[TextLine]) -> list[float]:
    """Line centres of a column flow, rectified by the tilt of its line boxes.

    A column's left edge drifts by ``-tilt`` per unit of height; fitting that
    drift over the flow gives the tilt without detector polygons.
    """
    ys = [line.bbox.cy for line in flow]
    xs = [line.bbox.x0 for line in flow]
    mean_y, mean_x = sum(ys) / len(ys), sum(xs) / len(xs)
    spread = sum((y - mean_y) ** 2 for y in ys)
    tilt = -sum((y - mean_y) * (x - mean_x) for x, y in zip(xs, ys)) / spread if spread > 0 else 0.0
    if not 0.005 <= abs(tilt) <= 0.2:
        tilt = 0.0
    return [line.bbox.cy - tilt * line.bbox.cx for line in flow]


def _flow_lines(frame: _RectifiedFrame, tokens: list[OcrToken], page_index: int, page_bbox: BBox,
                *, join: bool = True) -> list[TextLine]:
    """One line per rectified row, top to bottom.

    A line-level detector already returns one observation per column line;
    rows are measured in the rectified frame, so the tall boxes of a skewed
    scan never merge two lines. Hyphenated words and bullet markers are
    joined within a column flow (``join``).
    """
    from structured_pdf_text.ocr.reconstruct import dehyphenate_ocr_lines, merge_ocr_bullet_markers, reconstruct_ocr_lines

    rows, centers = frame.rows(tokens)
    lines = [line for _, row in sorted(zip(centers, rows), key=lambda item: item[0])
             for line in reconstruct_ocr_lines(row, page_index, page_bbox, dehyphenate=False)]
    if join:
        lines = merge_ocr_bullet_markers(dehyphenate_ocr_lines(lines), page_index=page_index)
    return lines


def _tokens_of_lines(lines: list[TextLine], tokens: list[OcrToken]) -> list[OcrToken] | None:
    """The OCR observations that make up ``lines``, or ``None`` if any is missing."""
    by_key: dict[tuple, list[OcrToken]] = {}
    for token in tokens:
        if token.text and token.bbox.width >= 0 and token.bbox.height >= 0:
            by_key.setdefault((token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1), []).append(token)
    observed: list[OcrToken] = []
    for line in lines:
        for text_token in line.tokens:
            if TokenFlag.WHITESPACE_INFERRED in text_token.flags and not text_token.text.strip():
                continue
            key = (text_token.bbox.x0, text_token.bbox.y0, text_token.bbox.x1, text_token.bbox.y1)
            candidates = by_key.get(key)
            if not candidates:
                return None
            observed.append(candidates.pop(0))
    return observed
