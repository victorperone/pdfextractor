"""Exact Levenshtein counts with deterministic ties and bounded working memory.

Every optimal path, including the canonical one, stays within ``|i-j| <= d``.
A banded pass of width ``w >= d`` therefore reproduces the full-matrix
traceback counts (match, substitution, deletion, insertion priority); a pass
whose terminal distance fits its width proves ``w >= d``. The first pass uses
the minimum width ``|n-m|`` (cheap for near-identical sequences). Otherwise the
exact distance comes from Myers/Hyyrö bit-parallel rows and one final pass of
that width follows. Each cell carries the S/D/I counts of its canonical
predecessor; only two band rows are retained. Every band is reserved in full
before allocation, so a spent cell budget raises at once.
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

DEFAULT_MAX_EDIT_CELLS = 64_000_000
EDIT_DISTANCE_POLICY = "levenshtein-exact-banded-sdi-v1"


class EditDistanceResourceLimit(RuntimeError):
    def __init__(self, max_cells: int, evaluated_cells: int):
        self.max_cells = max_cells
        self.evaluated_cells = evaluated_cells
        super().__init__(f"Exact edit distance exceeded cell budget {max_cells}")

    def __reduce__(self):
        # Worker exceptions cross ProcessPoolExecutor's process boundary.
        return type(self), (self.max_cells, self.evaluated_cells)


@dataclass
class EditDistanceBudget:
    """Work shared by distances that contribute to one aggregate metric."""

    max_cells: int
    evaluated_cells: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_cells, bool) or not isinstance(self.max_cells, int) or self.max_cells < 1:
            raise ValueError("max_cells must be a positive integer")
        if self.evaluated_cells != 0:
            raise ValueError("A new edit-distance budget must start at zero")

    def reserve(self, cells: int) -> None:
        if self.evaluated_cells + cells > self.max_cells:
            raise EditDistanceResourceLimit(self.max_cells, self.evaluated_cells)
        self.evaluated_cells += cells


def _band_cells(nrows: int, ncols: int, width: int) -> int:
    """Number of grid states in |i-j| <= width, including boundary states."""
    above = max(0, min(nrows + 1, ncols - width))
    below = max(0, min(ncols + 1, nrows - width))
    return ((nrows + 1) * (ncols + 1)
            - above * (ncols - width) + above * (above - 1) // 2
            - below * (nrows - width) + below * (below - 1) // 2)


def bit_parallel_distance(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """Exact unit-cost Levenshtein distance (Hyyrö's global variant of Myers).

    The shorter sequence is the bit pattern; work is O(len(longer) * words).
    """
    if len(a) < len(b):
        a, b = b, a
    m = len(b)
    if not m:
        return len(a)
    peq: dict = {}
    for index, token in enumerate(b):
        peq[token] = peq.get(token, 0) | (1 << index)
    full = (1 << m) - 1
    high = 1 << (m - 1)
    pv, mv, score = full, 0, m
    for token in a:
        eq = peq.get(token, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = mv | (~(xh | pv) & full)
        mh = pv & xh
        if ph & high:
            score += 1
        elif mh & high:
            score -= 1
        # Row zero of a global alignment increases by one per column.
        ph = ((ph << 1) | 1) & full
        mh = (mh << 1) & full
        pv = mh | (~(xv | ph) & full)
        mv = ph & xv
    return score


def _banded_ops(rows: Sequence, columns: Sequence, width: int, transposed: bool) -> tuple[int, int, int, int]:
    """(distance, S, D, I) inside |i-j| <= width; exact when distance <= width."""
    nrows, ncols = len(rows), len(columns)
    # State is (distance, substitutions, original deletions, original insertions).
    # In transposed coordinates an original deletion is a left edge.
    missing = (nrows + ncols + 1, 0, 0, 0)
    previous = [(j, 0, j if transposed else 0, 0 if transposed else j)
                for j in range(min(ncols, width) + 1)]
    previous_lo = 0
    for i in range(1, nrows + 1):
        lo, hi = max(0, i - width), min(ncols, i + width)
        current = [missing] * (hi - lo + 1)
        previous_hi = previous_lo + len(previous) - 1
        token = rows[i - 1]
        start = lo
        if lo == 0:
            current[0] = (i, 0, 0 if transposed else i, i if transposed else 0)
            start = 1
        for j in range(start, hi + 1):
            diagonal = previous[j - 1 - previous_lo]
            if token == columns[j - 1]:
                current[j - lo] = diagonal
                continue
            above = previous[j - previous_lo] if j <= previous_hi else missing
            left = current[j - 1 - lo] if j > lo else missing
            deletion, insertion = (left, above) if transposed else (above, left)
            # First minimum in diagonal, deletion, insertion order reproduces
            # the full traceback's S > D > I ties.
            dd, de, di = diagonal[0], deletion[0], insertion[0]
            if dd <= de and dd <= di:
                current[j - lo] = (dd + 1, diagonal[1] + 1, diagonal[2], diagonal[3])
            elif de <= di:
                current[j - lo] = (de + 1, deletion[1], deletion[2] + 1, deletion[3])
            else:
                current[j - lo] = (di + 1, insertion[1], insertion[2], insertion[3] + 1)
        previous, previous_lo = current, lo
    return previous[ncols - previous_lo]


def levenshtein_ops(
    ref: Sequence, hyp: Sequence, *, max_cells: int = DEFAULT_MAX_EDIT_CELLS,
    budget: EditDistanceBudget | None = None,
) -> tuple[int, int, int]:
    """Return exact (S, D, I), ref → hyp, or raise before exceeding the budget.

    Equality and empty inputs need no DP cells. No prefix/suffix trimming is
    used: removing common text can alter the canonical S/D/I tie decisions.
    Memory is O(band width); time is at most two banded passes plus, when the
    first does not suffice, the bit-parallel distance. max_cells must be positive.
    """
    if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells < 1:
        raise ValueError("max_cells must be a positive integer")
    nr, nh = len(ref), len(hyp)
    if not nr:
        return 0, 0, nh
    if not nh:
        return 0, nr, 0
    if ref == hyp:
        return 0, 0, 0

    # Transpose to keep rows short.
    transposed = nh > nr
    rows, columns = (hyp, ref) if transposed else (ref, hyp)
    work = budget if budget is not None else EditDistanceBudget(max_cells)
    width = max(abs(nr - nh), 1)
    work.reserve(_band_cells(len(rows), len(columns), width))
    distance, S, D, I = _banded_ops(rows, columns, width, transposed)
    if distance > width:
        width = bit_parallel_distance(ref, hyp)
        work.reserve(_band_cells(len(rows), len(columns), width))
        distance, S, D, I = _banded_ops(rows, columns, width, transposed)
    return S, D, I


def levenshtein_distance(
    ref: Sequence, hyp: Sequence, *, max_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> int:
    return sum(levenshtein_ops(ref, hyp, max_cells=max_cells))
