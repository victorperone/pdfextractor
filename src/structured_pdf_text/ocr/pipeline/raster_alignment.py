"""Page raster aligned with the frame of the page OCR observations.

An engine may straighten a skewed page before reading it (Paddle's deskew,
003-P0-01). Its observations are then expressed in the straightened frame, but
table structure discovery reads the page raster as rendered: on a page turned
by 8° the ruled grid and the observations disagree, the axis-aligned table box
cuts the first column and the values move to the row above (005-P1-02).

An enabled policy reports the angle by which the engine straightened the page
and turns the page raster by the same angle, so that the grid is found in the
frame of the observations. The legacy policy never aligns, which keeps every
other engine, and native extraction, unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True, slots=True)
class PageRasterAlignmentPolicy:
    """Turn the page raster by the straightening angle of the page OCR call.

    ``engine_name`` selects the entry of the coordinator's
    ``last_result.engine_diagnostics`` that carries ``deskew_angle``, in the
    convention of the engine's ``_estimate_skew_deg``: degrees, positive when
    the text descends to the right, undone by a counter-clockwise turn about
    the raster centre with the same size (``cv2.getRotationMatrix2D``).
    Angles under ``minimum_angle`` are not applied by the engine either.
    """

    policy_id: str
    enabled: bool = False
    engine_name: str = ""
    minimum_angle: float = 0.5
    maximum_angle: float = 20.0

    def page_angle(self, engine: Any) -> float:
        """Straightening angle of the engine's last page call, or 0.0."""
        if not self.enabled:
            return 0.0
        result = getattr(engine, "last_result", None)
        diagnostics = getattr(result, "engine_diagnostics", None)
        if not isinstance(diagnostics, Mapping):
            return 0.0
        entry = diagnostics.get(self.engine_name)
        angle = entry.get("deskew_angle") if isinstance(entry, Mapping) else None
        if not isinstance(angle, (int, float)) or isinstance(angle, bool) or not math.isfinite(angle):
            return 0.0
        return float(angle) if self.minimum_angle <= abs(angle) <= self.maximum_angle else 0.0

    def align(self, image: Any, angle: float) -> Any:
        """``image`` turned like the engine's straightened page, or ``image``.

        Same operation as the engine: counter-clockwise turn by ``angle`` about
        the centre, same size, bicubic, border replicated. A PIL image stays a
        PIL image and an array an array.
        """
        if not self.enabled or image is None or not angle:
            return image
        import cv2
        import numpy as np
        from PIL import Image

        is_pil = isinstance(image, Image.Image)
        rgb = np.asarray(image.convert("RGB") if is_pil else Image.fromarray(np.asarray(image)).convert("RGB"))
        height, width = rgb.shape[:2]
        matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
        turned = cv2.warpAffine(rgb, matrix, (width, height), flags=cv2.INTER_CUBIC,
                                borderMode=cv2.BORDER_REPLICATE)
        return Image.fromarray(turned) if is_pil else turned


LEGACY_PAGE_RASTER_ALIGNMENT_POLICY = PageRasterAlignmentPolicy("legacy-source-raster")
