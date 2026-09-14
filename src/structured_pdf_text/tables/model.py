from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from structured_pdf_text.document import StructuredTable
from structured_pdf_text.geometry import BBox


@dataclass(frozen=True, slots=True)
class RowSignature:
    effective_columns: int
    colspans: tuple[int, ...]
    rowspans: tuple[int, ...]
    normalized_cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TableSignature:
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
    tables: tuple[StructuredTable, ...]
    decisions: tuple[TableContinuationDecision, ...]
