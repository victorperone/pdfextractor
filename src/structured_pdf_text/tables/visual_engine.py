"""Table structure engine protocol and OpenCV default implementation.

:class:`TableStructureEngine` defines the single-method protocol that all
structure recognisers must satisfy.  :class:`OpenCvTableStructureEngine`
provides the default deterministic implementation based on
:func:`~structured_pdf_text.tables.visual.detect_visual_grid`.

Keeping the engine behind a protocol allows a learned model to be substituted
without changes to the detection pipeline or cell-mapping code.
"""
from __future__ import annotations

from typing import Protocol


class TableStructureEngine(Protocol):
    def recognize(self, image: object) -> object:
        """Recognize table structure from a region image."""


class OpenCvTableStructureEngine:
    """Deterministic raster-grid implementation of ``TableStructureEngine``.

    The import stays lazy so native extraction does not require OpenCV. A
    learned table model can replace this adapter without changing the table
    API or the cell-mapping stage.
    """

    def recognize(self, image: object) -> object:
        from .visual import detect_visual_grid

        return detect_visual_grid(image)
