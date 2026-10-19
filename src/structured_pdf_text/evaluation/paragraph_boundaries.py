"""Exact token correspondence for paragraph spans, with bounded work/memory."""
from __future__ import annotations

from array import array

from .edit_distance import EditDistanceBudget, _band_cells

PARAGRAPH_POLICY = "page-token-spans-exact-gaps-v1"


def matching_gaps(ref, hyp, ref_gaps: set[int], hyp_gaps: set[int],
                  budget: EditDistanceBudget) -> set[tuple[int, int]]:
    """Requested vertices on the exact Levenshtein path: diagonal > delete > insert.

    Traceback uses one byte per evaluated state plus four bytes per row.
    All attempted bands consume the shared budget before allocation.
    """
    nr, nh = len(ref), len(hyp)
    if ref == hyp:
        return {(i, i) for i in ref_gaps & hyp_gaps}
    if not nr:
        return {(0, j) for j in hyp_gaps} if 0 in ref_gaps else set()
    if not nh:
        return {(i, 0) for i in ref_gaps} if 0 in hyp_gaps else set()
    width = max(abs(nr - nh), 1)
    infinity = nr + nh + 1
    while True:
        cells = _band_cells(nr, nh, width)
        budget.reserve(cells)
        trace = bytearray(cells)
        offsets = array("I")
        previous = {}
        offset = 0
        for i in range(nr + 1):
            lo, hi = max(0, i - width), min(nh, i + width)
            offsets.append(offset)
            current = {}
            for j in range(lo, hi + 1):
                if i == 0:
                    distance, direction = j, 3
                elif j == 0:
                    distance, direction = i, 2
                elif ref[i - 1] == hyp[j - 1]:
                    distance, direction = previous.get(j - 1, infinity), 1
                else:
                    choices = (previous.get(j - 1, infinity),
                               previous.get(j, infinity), current.get(j - 1, infinity))
                    best = min(range(3), key=choices.__getitem__)
                    distance, direction = choices[best] + 1, best + 1
                current[j] = distance
                trace[offset + j - lo] = direction
            offset += hi - lo + 1
            previous = current
        if previous[nh] <= width:
            break
        width = min(max(nr, nh), width * 2)
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
