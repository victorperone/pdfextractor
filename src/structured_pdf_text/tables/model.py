"""Immutable data models shared by cross-page table resolution.

These dataclasses are deliberately kept dependency-free (only stdlib and the
project's own document/geometry modules) so they can be imported cheaply by
diagnostic tooling without pulling in detection logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.document import StructuredTable
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class RowSignature:
    """Structural fingerprint for a single table row.

    Used to compare header rows across page boundaries: if two fragments share
    an identical :attr:`normalized_cells` sequence they likely display the same
    column labels and the second occurrence is a repeated header.

    Attributes:
        effective_columns: Total column slots occupied, accounting for colspan.
        colspans: Colspan value for each cell in reading order.
        rowspans: Rowspan value for each cell in reading order.
        normalized_cells: Normalised (ASCII, case-folded, punctuation-stripped)
            text of each cell.
    """

    effective_columns: int
    colspans: tuple[int, ...]
    rowspans: tuple[int, ...]
    normalized_cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableSignature:
    """Compact structural fingerprint for one page's worth of a table.

    Captures the properties inspected during cross-page continuation scoring
    without retaining the full cell list, so comparison stays cheap.

    Attributes:
        column_count: Number of declared columns.
        normalized_x_tracks: Relative horizontal centre positions of each
            column, normalised to [0, 1] within the table bbox.
        header_rows: Detected header row signatures (zero or one element).
        first_data_row: Signature of the first non-header row, or ``None``.
        last_data_row: Signature of the last row, or ``None``.
        bbox: Bounding box of this fragment on its page.
        page_index: Zero-based page number.
        touches_top: Table starts within the top 20 % of the page.
        touches_bottom: Table ends within the bottom 15 % of the page.
    """

    column_count: int
    normalized_x_tracks: tuple[float, ...]
    header_rows: tuple[RowSignature, ...]
    first_data_row: RowSignature | None
    last_data_row: RowSignature | None
    bbox: BBox
    page_index: int
    touches_top: bool
    touches_bottom: bool


@dataclass(frozen=True, slots=True)
class TableContinuationDecision:
    """Outcome and evidence record for a single cross-page continuation check.

    Attributes:
        previous_table_id: Identifier of the candidate predecessor table.
        following_table_id: Identifier of the candidate continuation table.
        previous_page: Zero-based page index of the predecessor, or ``None``.
        following_page: Zero-based page index of the continuation, or ``None``.
        accepted: ``True`` when the pair was merged.
        score: Numeric sum of all evidence contributions.
        reasons: Ordered sequence of string labels, one per evaluated signal.
        facts: Raw numeric or boolean values for each signal, for diagnostics.
    """

    previous_table_id: str
    following_table_id: str
    previous_page: int | None
    following_page: int | None
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    facts: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CrossPageTableResolution:
    """Result of resolving all cross-page table pairs in a document.

    Attributes:
        tables: Merged table list, ready for downstream consumption.
        decisions: One :class:`TableContinuationDecision` per evaluated pair,
            including rejected pairs, for audit and diagnostics.
    """

    tables: tuple[StructuredTable, ...]
    decisions: tuple[TableContinuationDecision, ...]
