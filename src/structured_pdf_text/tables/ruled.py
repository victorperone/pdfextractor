"""Public entry point for ruled (path-separator) table detection.

Provides :func:`detect_ruled_tables`, a thin wrapper around
:func:`~structured_pdf_text.tables.detector.detect_tables_native` that
filters its output to the two ruled-grid methods
(:attr:`~structured_pdf_text.document.TableMethod.STRICT_GRID` and
:attr:`~structured_pdf_text.document.TableMethod.RELAXED_GRID`).

The indirection keeps the external API stable while allowing the internal
detection logic to evolve independently.
"""
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
