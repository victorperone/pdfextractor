"""Recover table dividers that the raster grid missed, from OCR token geometry.

A thin rule lost in a scan merges two rows or two columns into one cell. The
OCR observations inside the merged cells still show the separation: several
cells of one grid row hold the same number of aligned text lines, a spanned
cell holds one value on each side of a column edge the grid already knows,
or every row of a column holds two values split by the same empty band.

Only OCR-derived visual tables are repaired; native text keeps its grid. The
repair partitions observed tokens and never edits their text. A repaired
table that fails the core validation is discarded by the caller.
"""
from __future__ import annotations

from dataclasses import replace
from statistics import median
import re

from structured_pdf_text.document import SourceKind, StructuredTable, TableCell, TableMethod
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.tables.cell_geometry import polygon
from structured_pdf_text.tables.text_join import join_table_tokens

_NATIVE_SOURCES = {SourceKind.NATIVE_PDF, SourceKind.NATIVE_GENERATED, SourceKind.TABLE_NATIVE}
_NUMERIC_LINE = re.compile(r"^[\s+\-−±R$€%.,\d]*\d[\s+\-−±R$€%.,\d]*$")


def repair_missing_dividers(table: StructuredTable) -> StructuredTable:
    """Return ``table`` with recovered row/column dividers, or ``table`` itself."""
    if table.method != TableMethod.VISUAL_MODEL or not table.cells or _has_native_tokens(table):
        return table
    repaired = table
    for step in (_split_spanned_cells, _split_merged_rows, _split_merged_columns):
        repaired = step(repaired)
    return repaired


def _has_native_tokens(table) -> bool:
    return any(source.source in _NATIVE_SOURCES
               for cell in table.cells for token in cell.tokens for source in getattr(token, "sources", ()) or ())


def _words(cell):
    return [token for token in cell.tokens if token.text.strip() and token.bbox is not None]


def _height(table) -> float:
    heights = [token.bbox.height for cell in table.cells for token in _words(cell) if token.bbox.height > 0]
    return median(heights) if heights else 10.0


def _lines(tokens, tolerance):
    lines: list[list] = []
    for token in sorted(tokens, key=lambda t: t.bbox.cy):
        if lines and abs(token.bbox.cy - median(t.bbox.cy for t in lines[-1])) <= tolerance:
            lines[-1].append(token)
        else:
            lines.append([token])
    return lines


def _cell(cell, *, row=None, col=None, rowspan=None, colspan=None, part=None, tokens=None):
    """A copy of ``cell``; ``part`` = (axis, start, end) keeps that fraction of
    its quadrilateral, so a tilted cell is cut along its own edges."""
    shape, box = cell.polygon, cell.bbox
    if part is not None:
        shape = _sub_polygon(cell, *part)
        box = BBox(min(p.x for p in shape), min(p.y for p in shape), max(p.x for p in shape), max(p.y for p in shape))
        shape = shape if cell.polygon is not None else None
    return TableCell(
        row=cell.row if row is None else row, col=cell.col if col is None else col,
        rowspan=cell.rowspan if rowspan is None else rowspan,
        colspan=cell.colspan if colspan is None else colspan,
        bbox=box, text=cell.text if tokens is None else join_table_tokens(tokens),
        tokens=list(cell.tokens if tokens is None else tokens), confidence=cell.confidence,
        polygon=shape,
    )


def _sub_polygon(cell, axis, start, end):
    tl, tr, br, bl = polygon(cell)

    def lerp(a, b, f):
        return Point(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f)

    if axis == "x":
        return (lerp(tl, tr, start), lerp(tl, tr, end), lerp(bl, br, end), lerp(bl, br, start))
    return (lerp(tl, bl, start), lerp(tr, br, start), lerp(tr, br, end), lerp(tl, bl, end))


def _fraction(value, low, high):
    return min(1., max(0., (value - low) / (high - low))) if high > low else 0.


def _column_edges(table) -> dict[int, float]:
    """Left edge of each grid column, from its unspanned cells."""
    edges: dict[int, list[float]] = {}
    for cell in table.cells:
        if cell.colspan == 1 and cell.bbox is not None:
            edges.setdefault(cell.col, []).append(cell.bbox.x0)
    return {col: median(values) for col, values in edges.items()}


# -- a spanned cell holding one value per spanned column ---------------------

def _split_spanned_cells(table):
    edges = _column_edges(table)
    cells, changed = [], False
    for cell in table.cells:
        words = _words(cell)
        bounds = [edges.get(col) for col in range(cell.col + 1, cell.col + cell.colspan)]
        if cell.colspan < 2 or cell.bbox is None or None in bounds or len(words) < cell.colspan:
            cells.append(cell)
            continue
        cuts = [cell.bbox.x0, *bounds, cell.bbox.x1]
        parts = [[t for t in words if cuts[i] <= t.bbox.cx < cuts[i + 1]] for i in range(cell.colspan)]
        straddles = any(t.bbox.x0 < cut - .2 * t.bbox.width and t.bbox.x1 > cut + .2 * t.bbox.width
                        for cut in bounds for t in words)
        if straddles or not all(part and any(ch.isdigit() for t in part for ch in t.text) for part in parts):
            cells.append(cell)
            continue
        changed = True
        fractions = [_fraction(cut, cell.bbox.x0, cell.bbox.x1) for cut in cuts]
        cells.extend(_cell(cell, col=cell.col + i, colspan=1, tokens=parts[i],
                           part=("x", fractions[i], fractions[i + 1]))
                     for i in range(cell.colspan))
    return replace(table, cells=cells) if changed else table


# -- several cells of one row holding the same aligned lines -----------------

def _split_merged_rows(table):
    height = _height(table)
    tolerance = .5 * height
    for row in range(table.row_count):
        members = [cell for cell in table.cells if cell.row == row]
        crossing = [cell for cell in table.cells if cell.row < row < cell.row + cell.rowspan
                    or (cell.row == row and cell.rowspan != 1)]
        if crossing or not members:
            continue
        lines = {id(cell): _lines(_words(cell), tolerance) for cell in members}
        multi = [cell for cell in members if len(lines[id(cell)]) >= 2]
        counts = {len(lines[id(cell)]) for cell in multi}
        if len(multi) < 2 or len(counts) != 1:
            continue
        k = counts.pop()
        centers = [[median(t.bbox.cy for t in line) for line in lines[id(cell)]] for cell in multi]
        if any(max(c[i] for c in centers) - min(c[i] for c in centers) > tolerance for i in range(k)):
            continue
        if not any(all(_NUMERIC_LINE.match(" ".join(t.text for t in line)) for line in lines[id(cell)])
                   for cell in multi):
            continue  # Wrapped descriptions also align; demand one column of values.
        if any(len(lines[id(cell)]) > k for cell in members):
            continue
        tops = [min(t.bbox.y0 for c in multi for t in lines[id(c)][i]) for i in range(k)]
        bottoms = [max(t.bbox.y1 for c in multi for t in lines[id(c)][i]) for i in range(k)]
        cuts = [(bottoms[i] + tops[i + 1]) / 2 for i in range(k - 1)]
        return _split_merged_rows(_insert_rows(table, row, k, cuts, lines, tolerance))
    return table


def _insert_rows(table, row, k, cuts, lines, tolerance):
    cells = []
    for cell in table.cells:
        if cell.row > row:
            cells.append(_cell(cell, row=cell.row + k - 1))
            continue
        if cell.row < row:
            cells.append(cell if cell.row + cell.rowspan <= row else _cell(cell, rowspan=cell.rowspan + k - 1))
            continue
        box = cell.bbox
        bands = [BBox(box.x0, y0, box.x1, y1) for y0, y1 in zip([box.y0, *cuts], [*cuts, box.y1])]
        fractions = [_fraction(y, box.y0, box.y1) for y in (box.y0, *cuts, box.y1)]
        parts = [("y", fractions[i], fractions[i + 1]) for i in range(k)]
        cell_lines = lines[id(cell)]
        if len(cell_lines) == k:
            cells.extend(_cell(cell, row=row + i, tokens=cell_lines[i], part=parts[i]) for i in range(k))
            continue
        words = [t for line in cell_lines for t in line]
        if not words:
            cells.extend(_cell(cell, row=row + i, tokens=[], part=parts[i]) for i in range(k))
            continue
        top, bottom = min(t.bbox.y0 for t in words), max(t.bbox.y1 for t in words)
        shares = [max(0., min(bottom, b.y1) - max(top, b.y0)) / max(bottom - top, 1e-6) for b in bands]
        owner = max(range(k), key=lambda i: shares[i])
        if shares[owner] < .6:
            cells.append(_cell(cell, rowspan=k))  # A label centred on the merged rows spans them.
            continue
        cells.extend(_cell(cell, row=row + i, tokens=words if i == owner else [], part=parts[i]) for i in range(k))
    fragments = [replace(fragment, row_end=fragment.row_end + k - 1) if fragment.row_start <= row < fragment.row_end
                 else replace(fragment, row_start=fragment.row_start + k - 1, row_end=fragment.row_end + k - 1)
                 if fragment.row_start > row else fragment for fragment in table.page_fragments]
    header_rows = tuple(sorted({h + (k - 1 if h > row else 0) for h in table.header_rows}
                               | ({row + i for i in range(k)} if row in table.header_rows else set())))
    return replace(table, cells=cells, row_count=table.row_count + k - 1,
                   page_fragments=fragments, header_rows=header_rows)


# -- every row of a column holding two values split by one empty band --------

def _split_merged_columns(table):
    height = _height(table)
    for col in range(table.column_count):
        members = sorted((cell for cell in table.cells if cell.col == col), key=lambda cell: cell.row)
        if any(cell.colspan != 1 for cell in members) or any(
                cell.col < col < cell.col + cell.colspan for cell in table.cells):
            continue
        pairs = {}
        for cell in members:
            words = sorted(_words(cell), key=lambda t: t.bbox.x0)
            if len(_lines(words, .5 * height)) != 1:
                continue
            gaps = [(words[i + 1].bbox.x0 - words[i].bbox.x1, i) for i in range(len(words) - 1)]
            if gaps and max(gaps)[0] >= 3 * height:
                index = max(gaps)[1]
                pairs[cell.row] = (max(t.bbox.x1 for t in words[:index + 1]), words[index + 1].bbox.x0)
        non_empty = [cell for cell in members if _words(cell)]
        if len(pairs) < 2 or len(pairs) * 2 < len(non_empty):
            continue
        starts = [start for _, start in pairs.values()]
        left_end, right_start = max(end for end, _ in pairs.values()), min(starts)
        if max(starts) - min(starts) > 1.5 * height or left_end >= right_start:
            continue
        return _split_merged_columns(_insert_column(table, col, (left_end + right_start) / 2, min(pairs)))
    return table


def _insert_column(table, col, cut, first_split_row):
    cells = []
    for cell in table.cells:
        if cell.col > col:
            cells.append(_cell(cell, col=cell.col + 1))
            continue
        if cell.col < col:
            cells.append(cell if cell.col + cell.colspan <= col else _cell(cell, colspan=cell.colspan + 1))
            continue
        fraction = _fraction(cut, cell.bbox.x0, cell.bbox.x1)
        words = _words(cell)
        left = [t for t in words if t.bbox.cx < cut]
        right = [t for t in words if t.bbox.cx >= cut]
        if words and not right and cell.row < first_split_row:
            cells.append(_cell(cell, colspan=2))  # A group heading above the split rows.
            continue
        cells.extend((_cell(cell, tokens=left, part=("x", 0., fraction)),
                      _cell(cell, col=col + 1, tokens=right, part=("x", fraction, 1.))))
    return replace(table, cells=cells, column_count=table.column_count + 1)
