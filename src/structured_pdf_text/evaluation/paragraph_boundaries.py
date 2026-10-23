"""Exact token correspondence for paragraph spans, with bounded work/memory."""
from __future__ import annotations

from array import array

from .edit_distance import EditDistanceBudget, _band_cells, bit_parallel_distance

PARAGRAPH_POLICY = "page-token-spans-exact-gaps-v1"


def matching_gaps(ref, hyp, ref_gaps: set[int], hyp_gaps: set[int],
                  budget: EditDistanceBudget) -> set[tuple[int, int]]:
    """Requested vertices on the exact Levenshtein path: diagonal > delete > insert.

    Every optimal path lies within |i-j| <= distance: a first pass of width
    |n-m| is exact when its terminal distance fits; otherwise the exact
    bit-parallel distance fixes the final width. Traceback uses one byte per
    evaluated state plus four bytes per row; each band is reserved first.
    """
    nr, nh = len(ref), len(hyp)
    if ref == hyp:
        return {(i, i) for i in ref_gaps & hyp_gaps}
    if not nr:
        return {(0, j) for j in hyp_gaps} if 0 in ref_gaps else set()
    if not nh:
        return {(i, 0) for i in ref_gaps} if 0 in hyp_gaps else set()
    infinity = nr + nh + 1
    width = max(abs(nr - nh), 1)
    while True:
        cells = _band_cells(nr, nh, width)
        budget.reserve(cells)
        trace = bytearray(cells)
        offsets = array("I")
        previous: list[int] = []
        previous_lo = offset = 0
        for i in range(nr + 1):
            lo, hi = max(0, i - width), min(nh, i + width)
            offsets.append(offset)
            current = [infinity] * (hi - lo + 1)
            previous_hi = previous_lo + len(previous) - 1
            token = ref[i - 1] if i else None
            for j in range(lo, hi + 1):
                if i == 0:
                    distance, direction = j, 3
                elif j == 0:
                    distance, direction = i, 2
                else:
                    diagonal = previous[j - 1 - previous_lo] if j - 1 >= previous_lo else infinity
                    if token == hyp[j - 1]:
                        distance, direction = diagonal, 1
                    else:
                        above = previous[j - previous_lo] if j <= previous_hi else infinity
                        left = current[j - 1 - lo] if j > lo else infinity
                        if diagonal <= above and diagonal <= left:
                            distance, direction = diagonal + 1, 1
                        elif above <= left:
                            distance, direction = above + 1, 2
                        else:
                            distance, direction = left + 1, 3
                current[j - lo] = distance
                trace[offset + j - lo] = direction
            offset += hi - lo + 1
            previous, previous_lo = current, lo
        if previous[nh - previous_lo] <= width:
            break
        width = bit_parallel_distance(ref, hyp)
    result = set()
    i, j = nr, nh
    while True:
        if i in ref_gaps and j in hyp_gaps:
            result.add((i, j))
        if not i and not j:
            return result
        direction = trace[offsets[i] + j - max(0, i - width)]
        if direction == 1:
            i, j = i - 1, j - 1
        elif direction == 2:
            i -= 1
        else:
            j -= 1


def paragraph_stream(nodes):
    """Whitespace-separated visible tokens and spans, without ancestor text."""
    tokens = []
    spans = {}
    for node in nodes:
        start = len(tokens)
        tokens.extend(node.visible_text.split())
        spans[node.node_id] = (start, len(tokens))
    return tokens, spans
