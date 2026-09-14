from __future__ import annotations

from structured_pdf_text.document import (
    LayoutRegion,
    NativePageEvidence,
    StructuredTable,
    TableMethod,
)


def detect_ruled_tables(
    page: NativePageEvidence,
    regions: list[LayoutRegion] | None = None,
) -> list[StructuredTable]:
    """Expose strict/relaxed ruled tiers through a stable module boundary."""
    from .detector import detect_tables_native

    return [
        table
        for table in detect_tables_native(page, regions)
        if table.method in {TableMethod.STRICT_GRID, TableMethod.RELAXED_GRID}
    ]
