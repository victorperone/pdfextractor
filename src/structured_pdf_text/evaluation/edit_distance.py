"""Exact Levenshtein counts with deterministic ties and bounded working memory.

Each cell carries the S/D/I counts of its canonical predecessor. This yields
the same counts as full-matrix traceback (match, substitution, deletion,
insertion priority), retaining only two diagonal bands. A band is widened
until its terminal distance fits; at that point every optimal path, including
the canonical one, is inside the band. A spent cell budget raises explicitly.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_MAX_EDIT_CELLS = 8_000_000
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


def levenshtein_ops(
    ref: Sequence, hyp: Sequence, *, max_cells: int = DEFAULT_MAX_EDIT_CELLS,
    budget: EditDistanceBudget | None = None,
) -> tuple[int, int, int]:
    """Return exact (S, D, I), ref → hyp, or raise before exceeding the budget.

    Equality and empty inputs need no DP cells. No prefix/suffix trimming is
    used: removing common text can alter the canonical S/D/I tie decisions.
    Memory is O(min(n, band_width)); worst-case time remains quadratic, bounded
    by max_cells across all attempted bands. max_cells must be positive.
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

    # Transpose to keep rows short. In transposed coordinates original deletion
    # is a left edge, so its tie priority is reversed relative to up edges.
    transposed = nh > nr
    rows, columns = (hyp, ref) if transposed else (ref, hyp)
    nrows, ncols = len(rows), len(columns)
    width = max(abs(nr - nh), 1)
    work = budget if budget is not None else EditDistanceBudget(max_cells)
    while True:
        # Reserve the entire pass before allocating its first row. This also
        # bounds workspace when the initial length difference is very large.
        cells = _band_cells(nrows, ncols, width)
        work.reserve(cells)
        infinity = nr + nh + 1
        # State is (distance, substitutions, original deletions, original insertions).
        missing = (infinity, 0, 0, 0)
        previous = {
            j: (j, 0, j if transposed else 0, 0 if transposed else j)
            for j in range(min(ncols, width) + 1)
        }
        for i in range(1, nrows + 1):
            lo, hi = max(1, i - width), min(ncols, i + width)
            current = {}
            if i <= width:
                current[0] = (i, 0, 0 if transposed else i, i if transposed else 0)
            for j in range(lo, hi + 1):
                diagonal = previous.get(j - 1, missing)
                if rows[i - 1] == columns[j - 1]:
                    current[j] = diagonal
                    continue
                above = previous.get(j, missing)
                left = current.get(j - 1, missing)
                deletion, insertion = (left, above) if transposed else (above, left)
                # Stable min reproduces the full traceback's S > D > I ties.
                best = min((diagonal, deletion, insertion), key=lambda state: state[0])
                if best is diagonal:
                    current[j] = (best[0] + 1, best[1] + 1, best[2], best[3])
                elif best is deletion:
                    current[j] = (best[0] + 1, best[1], best[2] + 1, best[3])
                else:
                    current[j] = (best[0] + 1, best[1], best[2], best[3] + 1)
            previous = current
        final = previous.get(ncols, missing)
        if final[0] <= width:
            return final[1], final[2], final[3]
        width = min(max(nr, nh), width * 2)


def levenshtein_distance(
    ref: Sequence, hyp: Sequence, *, max_cells: int = DEFAULT_MAX_EDIT_CELLS,
) -> int:
    return sum(levenshtein_ops(ref, hyp, max_cells=max_cells))
