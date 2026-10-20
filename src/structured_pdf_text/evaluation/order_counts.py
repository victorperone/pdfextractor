"""Exact strict pairwise order counts in O(n log n), including tied positions."""
from __future__ import annotations

from collections.abc import Sequence


def concordance_counts(orders: Sequence[int]) -> tuple[int, int]:
    ranks = {value: rank for rank, value in enumerate(sorted(set(orders)), start=1)}
    tree = [0] * (len(ranks) + 1)
    concordant = 0
    for value in orders:
        rank = ranks[value]
        cursor = rank - 1
        while cursor:
            concordant += tree[cursor]
            cursor -= cursor & -cursor
        cursor = rank
        while cursor < len(tree):
            tree[cursor] += 1
            cursor += cursor & -cursor
    n = len(orders)
    return concordant, n * (n - 1) // 2
