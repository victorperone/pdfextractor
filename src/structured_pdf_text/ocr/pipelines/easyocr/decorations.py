"""Identify faint diagonal raster decorations without discarding observations."""
from dataclasses import dataclass, replace
import math

from structured_pdf_text.document import OcrProvenance


@dataclass(frozen=True, slots=True)
class EasyOCRWatermarkPolicy:
    policy_id: str = 'easyocr-decorative-watermark-v1'
    minimum_angle: float = 7.
    maximum_angle: float = 83.
    faint_ink_level: float = 180.

    def identity(self):
        return {'policy_id': self.policy_id, 'angle_range': [self.minimum_angle, self.maximum_angle],
                'faint_ink_level': self.faint_ink_level, 'admission_scope': 'figure'}

    def annotate(self, image, tokens):
        """Keep the evidence; figure admission decides whether it is content.

        Require both the original quadrilateral's tilt and faint raster ink.
        Bounding-box diagonals cannot establish tilt. Dark semantic stamps,
        horizontal captions and uncertain geometry stay eligible.
        """
        import numpy as np
        pixels = np.asarray(image)
        output = []
        for token in tokens:
            output.append(token)
            if token.polygon is None or len(token.polygon) < 2:
                continue
            first, second = token.polygon[:2]
            angle = abs(math.degrees(math.atan2(second.y-first.y, second.x-first.x))) % 180
            angle = min(angle, 180-angle)
            if not self.minimum_angle <= angle <= self.maximum_angle:
                continue
            box = token.bbox
            x0, y0 = max(0, math.floor(box.x0)), max(0, math.floor(box.y0))
            x1, y1 = min(pixels.shape[1], math.ceil(box.x1)), min(pixels.shape[0], math.ceil(box.y1))
            crop = pixels[y0:y1, x0:x1]
            if not crop.size:
                continue
            gray = crop if crop.ndim == 2 else crop[..., :3].mean(axis=2)
            darkest = np.sort(gray, axis=None)[:max(1, gray.size//10)]
            if float(np.median(darkest)) >= self.faint_ink_level:
                provenance = token.ocr_provenance or OcrProvenance('easyocr')
                output[-1] = replace(token, ocr_provenance=replace(
                    provenance, refinement_kind='decorative_watermark'))
        return output


WATERMARK_POLICY = EasyOCRWatermarkPolicy()
