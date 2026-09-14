from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class BBox:
    """Canonical internal bbox.

    Origin is the top-left of the page, x grows right, y grows down.
    Values are expressed in PDF points unless explicitly documented otherwise.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"Invalid BBox coordinates: {self!r}")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def expand(self, amount: float) -> BBox:
        return BBox(
            self.x0 - amount,
            self.y0 - amount,
            self.x1 + amount,
            self.y1 + amount,
        )

    def intersection(self, other: BBox) -> BBox | None:
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return BBox(x0, y0, x1, y1)

    def overlap_ratio(self, other: BBox) -> float:
        """Intersection area divided by this bbox area."""
        if self.area == 0:
            return 0.0
        inter = self.intersection(other)
        return 0.0 if inter is None else inter.area / self.area

    def iou(self, other: BBox) -> float:
        inter = self.intersection(other)
        if inter is None:
            return 0.0
        union = self.area + other.area - inter.area
        return 0.0 if union == 0 else inter.area / union

    def to_pdfium_rect(self, page_height: float) -> tuple[float, float, float, float]:
        """Convert canonical top-left bbox to PDFium left, bottom, right, top."""
        return (self.x0, page_height - self.y1, self.x1, page_height - self.y0)

    @staticmethod
    def from_pdfium_rect(
        left: float,
        bottom: float,
        right: float,
        top: float,
        page_height: float,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
    ) -> BBox:
        """Convert PDFium coordinates to the canonical top-left system.

        ``origin_x`` and ``origin_y`` make the conversion reversible for pages
        whose CropBox does not start at ``(0, 0)``.  Existing callers that use
        page-local PDF coordinates can keep the defaults.
        """
        x0 = min(left, right) - origin_x
        x1 = max(left, right) - origin_x
        top_of_page = origin_y + page_height
        y0 = top_of_page - max(bottom, top)
        y1 = top_of_page - min(bottom, top)
        return BBox(x0, y0, x1, y1)

    def rotate_to_visual(self, rotation: int, page_width: float, page_height: float) -> BBox:
        """Transform this bbox from canonical PDF user space to visual/raster space.

        PDFium renders pages with /Rotate applied, so the raster image is in
        visual orientation. Native character bboxes are in canonical PDF user
        space (top-left origin, y-down, before rotation). This method applies
        the same rotation so that the returned bbox can be used to crop the
        rendered image correctly.

        ``rotation`` must be one of 0, 90, 180, 270 (counterclockwise degrees).
        ``page_width`` and ``page_height`` are the canonical PDF user space dims.
        """
        r = rotation % 360
        if r == 0:
            return self
        if r == 90:
            # CCW 90°: visual dims are (page_height × page_width)
            return BBox(
                page_height - self.y1,
                self.x0,
                page_height - self.y0,
                self.x1,
            )
        if r == 180:
            # 180°: visual dims are (page_width × page_height)
            return BBox(
                page_width - self.x1,
                page_height - self.y1,
                page_width - self.x0,
                page_height - self.y0,
            )
        if r == 270:
            # CCW 270° (= CW 90°): visual dims are (page_height × page_width)
            return BBox(
                self.y0,
                page_width - self.x1,
                self.y1,
                page_width - self.x0,
            )
        return self

    @staticmethod
    def union_all(boxes: list[BBox] | tuple[BBox, ...]) -> BBox:
        if not boxes:
            raise ValueError("Cannot build union for empty bbox collection")
        return BBox(
            min(box.x0 for box in boxes),
            min(box.y0 for box in boxes),
            max(box.x1 for box in boxes),
            max(box.y1 for box in boxes),
        )
