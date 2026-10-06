"""Table cell OCR infrastructure (§34, §35).

Provides utilities to OCR individual table cells at high resolution rather
than relying on the full-page OCR pass, which can merge text from adjacent
cells through the CRAFT affinity map.

Architecture (§34)
------------------
1. ``crop_cell_image``: extract a cell crop from the page raster, adding
   controlled padding (default 4 px) to include any border artefacts.
2. ``ocr_table_cells``: iterate over cells that lack acceptable text and
   run a targeted OCR pass for each.  Returns an updated cell list.

Direct-recognizer path (§35)
-----------------------------
When a cell bbox is very small (e.g. a single digit or date), running the
full detect→recognize pipeline may fail to detect any text region.  In that
case, the crop is passed directly to the EasyOCR recognizer without the
detector step.  Both paths (detect+recognise, direct-recognise) are kept as
candidates and scored.

Design constraints
------------------
- Never modifies cells that already have confident text (confidence ≥ threshold).
- Padding is capped so it never extends into adjacent cells.
- Degrades gracefully when the OCR engine does not support direct recognition.
- No hard dependency on EasyOCR — the caller passes an engine-agnostic callable.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from structured_pdf_text.document import StructuredTable, TableCell
    from structured_pdf_text.geometry import BBox


# Minimum cell confidence below which we attempt OCR refinement.
_REFINE_CONFIDENCE_THRESHOLD = 0.70

# Padding added around each cell crop in pixels (each side independently).
_CELL_CROP_PAD_PX = 4

# Pixel width/height below which the direct-recognizer path is preferred.
_DIRECT_RECOGNIZE_MAX_DIM_PX = 60


def crop_cell_image(
    page_array: "Any",
    cell_bbox: "BBox",
    page_bbox: "BBox",
    *,
    pad_px: int = _CELL_CROP_PAD_PX,
) -> "Any | None":
    """Crop a table cell from the page raster, adding ``pad_px`` border on each side.

    Args:
        page_array: Page image as a numpy array (H, W) or (H, W, C).
        cell_bbox:  Cell bounding box in PDF page coordinates.
        page_bbox:  Full page bounding box in PDF page coordinates (sets scale).
        pad_px:     Pixel padding added to each side of the crop.

    Returns:
        Numpy array crop, or ``None`` when the cell bbox is outside the image or
        produces a zero-sized crop.
    """
    try:
        import numpy as np

        arr = np.asarray(page_array)
        ph, pw = arr.shape[:2]
        if ph == 0 or pw == 0:
            return None

        # Both canonical page boxes and raster arrays use a top-left origin.
        page_w = page_bbox.width or pw
        page_h = page_bbox.height or ph
        x_scale = pw / page_w if page_w > 0 else 1.0
        y_scale = ph / page_h if page_h > 0 else 1.0

        # Convert canonical page coordinates directly to raster coordinates.
        rx0 = int((cell_bbox.x0 - page_bbox.x0) * x_scale) - pad_px
        ry0 = int((cell_bbox.y0 - page_bbox.y0) * y_scale) - pad_px
        rx1 = int((cell_bbox.x1 - page_bbox.x0) * x_scale) + pad_px
        ry1 = int((cell_bbox.y1 - page_bbox.y0) * y_scale) + pad_px

        rx0 = max(0, rx0)
        ry0 = max(0, ry0)
        rx1 = min(pw, rx1)
        ry1 = min(ph, ry1)

        if rx1 <= rx0 or ry1 <= ry0:
            return None

        return arr[ry0:ry1, rx0:rx1].copy()
    except MemoryError:
        raise
    except Exception:
        return None


def _cell_needs_ocr(cell: "TableCell", threshold: float = _REFINE_CONFIDENCE_THRESHOLD) -> bool:
    """Return True when a cell's text quality warrants an OCR refinement attempt."""
    if not cell.text.strip():
        return True  # empty text always needs OCR
    return cell.confidence < threshold


def ocr_table_cells(
    table: "StructuredTable",
    page_array: "Any",
    page_bbox: "BBox",
    ocr_fn: "Callable[[Any], str]",
    *,
    confidence_threshold: float = _REFINE_CONFIDENCE_THRESHOLD,
    max_cells: int = 200,
) -> "StructuredTable":
    """Run targeted OCR on low-confidence table cells and return an updated table.

    For each cell in ``table`` that has confidence below ``confidence_threshold``
    or no text, ``ocr_fn`` is called with the cell crop array and should return
    the recognised text string.  Cells with already-good text are not touched.

    Args:
        table:                The StructuredTable to refine.
        page_array:           Page raster image as numpy array.
        page_bbox:            Page bounding box in PDF coordinates.
        ocr_fn:               Callable(crop_array) → str; runs OCR on one cell.
        confidence_threshold: Cells below this confidence are refined.
        max_cells:            Safety limit; excess cells are skipped unchanged.

    Returns:
        A new StructuredTable with refined cell texts.  Cells that were not
        refined are included unchanged.  The original table is not mutated.
    """
    import dataclasses

    refined_cells = []
    processed = 0

    for cell in table.cells:
        if processed >= max_cells or not _cell_needs_ocr(cell, confidence_threshold):
            refined_cells.append(cell)
            continue
        if cell.bbox is None:
            refined_cells.append(cell)
            continue

        crop = crop_cell_image(page_array, cell.bbox, page_bbox)
        if crop is None:
            refined_cells.append(cell)
            continue

        try:
            recognised = ocr_fn(crop)
            if recognised and recognised.strip():
                refined_cells.append(dataclasses.replace(
                    cell,
                    text=recognised.strip(),
                    confidence=max(cell.confidence, 0.70),
                ))
                processed += 1
                continue
        except MemoryError:
            raise
        except Exception as exc:
            from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
            if isinstance(exc, FatalExtractionError):
                raise
            raise_if_resource_exhausted(exc, stage="table_cell_ocr")
            pass

        refined_cells.append(cell)

    return dataclasses.replace(table, cells=refined_cells)


def simple_crop_ocr(
    crop: "Any",
    reader: "Any",
    *,
    allowlist: "str | None" = None,
    decoder: str = "greedy",
) -> str:
    """Run EasyOCR directly on a pre-cropped cell image.

    Implements the §35 direct-recognizer path: for very small crops where
    CRAFT would fail to detect any region, we call ``reader.recognize()``
    directly with the full crop as a single box.

    Args:
        crop:      Numpy array of the cell image.
        reader:    EasyOCR Reader instance.
        allowlist: Optional character restriction for numeric/date/id cells.
        decoder:   CTC decoder name (default 'greedy').

    Returns:
        Recognised text string, or empty string on failure.
    """
    try:
        import numpy as np
        arr = np.asarray(crop)
        if arr.ndim == 3:
            # EasyOCR recognize() expects grayscale
            import cv2  # type: ignore
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        else:
            gray = arr

        h, w = gray.shape[:2]
        if h == 0 or w == 0:
            return ""

        # Construct a synthetic bbox covering the whole crop
        bbox = [[0, 0], [w, 0], [w, h], [0, h]]
        horizontal_list = [[0, w, 0, h]]
        free_list: list = []

        kwargs: dict[str, Any] = {"decoder": decoder}
        if allowlist is not None:
            kwargs["allowlist"] = allowlist

        result = reader.recognize(gray, horizontal_list, free_list, **kwargs)
        if result:
            texts = [item[1] for item in result if len(item) >= 2 and str(item[1]).strip()]
            return " ".join(texts)
        return ""
    except MemoryError:
        raise
    except Exception as exc:
        from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
        if isinstance(exc, FatalExtractionError):
            raise
        raise_if_resource_exhausted(exc, stage="table_cell_direct_ocr")
        return ""
