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
