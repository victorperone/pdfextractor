"""EasyOCR-local raster table recovery and conservative OCR-track admission."""
from collections import defaultdict


class EasyOCRTableStructureEngine:
    def infer_header_rows(self, cells, image, raster_bbox):
        from structured_pdf_text.tables.headers import infer_visual_header_rows
        return infer_visual_header_rows(cells, image, raster_bbox, alphanumeric_labels=True)

    def recognize(self, image):
        from structured_pdf_text.tables.visual import detect_visual_grid
        return detect_visual_grid(image)

    def prepare_recovery_image(self, image):
        """Remove smooth illumination only after the original grid failed.

        Closing estimates the local paper background, not cell content. The
        result has exactly the same pixel frame, and is used for rule sampling
        as well as detection; no OCR runs on this synthetic contrast image.
        """
        import cv2
        import numpy as np
        array = np.asarray(image)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY) if array.ndim == 3 else array
        size = min(101, max(15, min(gray.shape[:2]) // 25)) | 1
        background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, (size, size)))
        return cv2.divide(gray, np.maximum(background, 1), scale=255)

    def recover_header_rows(self, cells):
        import re
        labels = re.compile(r"(?:Quantidade|Qtd\.?|Valor|Unidade|Semestre(?:\s+I{1,3})?|Categoria|Item|Lote|Credor|Status|Ref\.?|Confer[eê]ncia)", re.I)
        output = []
        for row in sorted({cell.row for cell in cells})[:2]:
            texts = [cell.text.strip() for cell in cells if cell.row == row and cell.text.strip()]
            if len(texts) < 2 or any(any(char.isdigit() for char in text) for text in texts):
                break
            if sum(bool(labels.fullmatch(text)) for text in texts) < 2:
                break
            output.append(row)
        return tuple(output)

    def accept_ocr_tracks(self, table):
        """A prose gutter alone cannot establish a borderless table.

        At least one column must contain recurrent compact fields. Long text
        in another column remains valid (e.g. item + multiline description).
        Ruled tables do not use this admission path.
        """
        rows = defaultdict(list)
        headers = set(table.header_rows or ())
        for cell in table.cells:
            if cell.row not in headers and cell.text.strip():
                rows[cell.col].append(cell.text.strip())
        return any(len(values) >= 2 and sum(
            len(value) <= 48 and len(value.split()) <= 4
            for value in values) >= .8 * len(values) for values in rows.values())
