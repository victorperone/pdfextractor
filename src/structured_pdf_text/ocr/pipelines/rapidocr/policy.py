"""RapidOCR-owned recovery schedule, matching the pre-migration path."""
from dataclasses import dataclass
import math
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.quality import _union_area
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import RasterBoundaryPolicy

@dataclass(frozen=True, slots=True)
class RapidOCRRegionalCandidatePolicy(RegionalCandidatePolicy):
    """Suppress clipped alternatives only across overlapping memory tiles.

    A confident complete reading must cover the shorter observation at a
    clipping edge of another tile. Conflicting values and same-cell evidence
    from a single reading are conserved. Raw observations remain auditable.
    """

    policy_id: str = "rapidocr-overlapping-tile-composition-v1"
    fragment_coverage: float = .80
    complete_minimum_confidence: float = .90
    confidence_tolerance: float = .03

    def identity(self):
        return {"policy_id": self.policy_id, "fragment_coverage": self.fragment_coverage,
                "complete_minimum_confidence": self.complete_minimum_confidence,
                "confidence_tolerance": self.confidence_tolerance}

    def compose_tiles(self, tile_groups):
        tokens = RegionalCandidatePolicy.compose_tiles(self, tile_groups)
        owners = {
            id(token): (index, bounds)
            for index, (bounds, group) in enumerate(tile_groups) for token in group
        }

        def normalized(token):
            return "".join(token.text.split())

        def clipped_fragment(fragment, complete):
            fragment_group, bounds = owners[id(fragment)]
            if fragment_group == owners[id(complete)][0]:
                return False
            short, full = normalized(fragment), normalized(complete)
            if not short or short == full or not (full.startswith(short) or full.endswith(short)):
                return False
            if (complete.confidence is None or fragment.confidence is None
                    or complete.confidence < self.complete_minimum_confidence
                    or complete.confidence + self.confidence_tolerance < fragment.confidence):
                return False
            intersection = fragment.bbox.intersection(complete.bbox)
            if (intersection is None or fragment.bbox.area <= 0
                    or intersection.area / fragment.bbox.area < self.fragment_coverage):
                return False
            # A complete observation extends beyond the clipped tile edge;
            # merely containing another label is insufficient for suppression.
            tolerance = max(1., min(fragment.bbox.width, fragment.bbox.height) * .5)
            return any((
                fragment.bbox.x0 - bounds.x0 <= tolerance and complete.bbox.x0 < bounds.x0,
                bounds.x1 - fragment.bbox.x1 <= tolerance and complete.bbox.x1 > bounds.x1,
                fragment.bbox.y0 - bounds.y0 <= tolerance and complete.bbox.y0 < bounds.y0,
                bounds.y1 - fragment.bbox.y1 <= tolerance and complete.bbox.y1 > bounds.y1,
            ))

        return [token for token in tokens if not any(
            other is not token and clipped_fragment(token, other) for other in tokens
        )]


# Both RapidOCR providers share policy; their resolved settings stay separate.
CANDIDATE_POLICY = RapidOCRRegionalCandidatePolicy()

RECOVERY_POLICY = RegionalRecoveryProfile(
    candidates=CANDIDATE_POLICY,
    weak_region_markers=("small", "sparse", "missing", "damaged"),
    weak_region_scales=(1.0, 1.5, 2.0),
    ordinary_region_scales=(1.0,),
    critical_data_scales=(1.5, 2.0, 3.0),
    weak_line_area_threshold=0.12,
    weak_line_small_scales=(1.5, 2.0, 3.0),
    weak_line_large_scales=(1.5, 2.0),
)

INTEGRATION_POLICY = ConservativeIntegrationPolicy("rapidocr-integration-v1")

CELL_POLICY = CellRecoveryPolicy()

TOKEN_REFINEMENT_POLICY = TokenRefinementPolicy()
FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()
RASTER_BOUNDARY_POLICY = RasterBoundaryPolicy("trim")

FUSION_POLICY = CandidateFusionPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCRFigureAdmissionPolicy:
    """Abstain on figure detections that are shapes rather than text.

    Uncertain detections spanning almost an entire figure are rejected. So is
    a lone glyph read from a geometric mark: a closed-contour character
    (``O``, ``口``, ...) in a square box below near-certain confidence — PP-OCRv6
    reads chart circles and markers that way — or any lone glyph read with low
    confidence. Real labels (ticks, letters, multi-character text) remain
    eligible. This is local v6 calibration, not a corpus/page rule or a change
    to shared conservation.
    """

    policy_id: str = "rapidocr-figure-admission-v2"
    minimum_confidence: float = .70
    minimum_scope_coverage: float = .85
    lone_glyph_minimum_confidence: float = .80
    shape_glyphs: str = "OoQDCe0口□○◯〇●◎"
    shape_glyph_minimum_confidence: float = .995
    shape_aspect_range: tuple[float, float] = (.85, 1.25)

    def __post_init__(self):
        for value in (self.minimum_confidence, self.minimum_scope_coverage,
                      self.lone_glyph_minimum_confidence, self.shape_glyph_minimum_confidence):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Figure admission calibration must be finite in [0, 1]")

    def rejection_reason(self, token, request):
        if request.goal is not WorkGoal.FIGURE or token.confidence_normalized is None:
            return None
        glyphs = "".join(token.text.split())
        if len(glyphs) == 1:
            confidence = token.confidence_normalized
            if confidence < self.lone_glyph_minimum_confidence:
                return "rapidocr_uncertain_lone_figure_glyph"
            aspect = token.bbox.width / token.bbox.height if token.bbox.height > 0 else 0.
            low, high = self.shape_aspect_range
            if glyphs in self.shape_glyphs and confidence < self.shape_glyph_minimum_confidence and low <= aspect <= high:
                return "rapidocr_geometric_shape_glyph"
        if token.confidence_normalized >= self.minimum_confidence:
            return None
        authorization = next(a for a in request.authorizations if a.authorization_id == token.authorization_id)
        # Use actual authorized rectangles, not a convex envelope that could
        # include unobserved gaps. The service already validated the token.
        if token.bbox.area / _union_area(list(authorization.regions)) >= self.minimum_scope_coverage:
            return "rapidocr_uncertain_figure_spanning_detection"
        return None


ADMISSION_POLICY = RapidOCRFigureAdmissionPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCROrientationPolicy:
    """Recover rotated text when the upright reading is weak.

    The pinned PP-OCRv6 profile keeps the PP-OCRv4 angle classifier disabled:
    it separates only 0 and 180 degrees. Instead, a weak upright reading (mean
    confidence below ``confidence_floor``) is compared with the 270, 90 and 180
    degree turns of the same authorized crop. An alternative is kept only if it
    is itself confident and reads at least ``minimum_gain`` times as much
    confidence-weighted text as the upright reading. Healthy readings never
    trigger a search, so ordinary pages keep their single pass.
    """

    policy_id: str = "rapidocr-orientation-search-v1"
    confidence_floor: float = .90
    minimum_gain: float = 2.0
    alternatives: tuple[int, ...] = (3, 1, 2)

    def __post_init__(self):
        if not 0 <= self.confidence_floor <= 1 or not math.isfinite(self.confidence_floor):
            raise ValueError("Orientation confidence floor must be finite in [0, 1]")
        if not math.isfinite(self.minimum_gain) or self.minimum_gain < 1:
            raise ValueError("Orientation minimum gain must be finite and at least 1")
        if not self.alternatives or any(turn not in (1, 2, 3) for turn in self.alternatives):
            raise ValueError("Orientation alternatives must be quarter turns 1, 2 or 3")

    def identity(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "confidence_floor": self.confidence_floor,
            "minimum_gain": self.minimum_gain,
            "alternatives": list(self.alternatives),
        }


ORIENTATION_POLICY = RapidOCROrientationPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCRSelectionMarkPolicy:
    """Recover form check boxes that the recognizer drops from a text row.

    A box is a hollow square, about one text-row tall, at the start of an
    upright row (left of it, or inside the detected box where the recognizer
    reads it as blank), with all four sides inked, a clear ring around it and
    a blank gap before the label (grid cells and letters fail one of these).
    The interior ink decides the state: empty, X-marked or check-marked;
    ambiguous interiors abstain. A detected box becomes its own observation;
    the row keeps its reading and only its left edge moves past the box.
    """

    policy_id: str = "rapidocr-selection-marks-v1"
    # Box rules are often one pixel wide at OCR scale; antialiased rule pixels
    # stay around 150-200, so ink is anything clearly darker than paper.
    dark_level: int = 170
    minimum_side_fraction: float = .35
    maximum_side_fraction: float = 1.0
    minimum_edge_ink: float = .85
    maximum_ring_ink: float = .12
    minimum_corner_ink: float = .75
    maximum_gap_ink: float = .05
    empty_interior_ink: float = .03
    marked_interior_ink: float = .08
    cross_diagonal_ink: float = .55
    confidence: float = .90

    def identity(self) -> dict:
        return {"policy_id": self.policy_id, "dark_level": self.dark_level,
                "side_fraction": [self.minimum_side_fraction, self.maximum_side_fraction],
                "minimum_edge_ink": self.minimum_edge_ink, "maximum_ring_ink": self.maximum_ring_ink,
                "minimum_corner_ink": self.minimum_corner_ink, "maximum_gap_ink": self.maximum_gap_ink,
                "interior_ink": [self.empty_interior_ink, self.marked_interior_ink]}

    def apply(self, image, rows) -> list:
        """Return *rows* plus one ``(box, symbol, confidence)`` row per box."""
        import numpy as np
        if image is None or not rows:
            return list(rows)
        pixels = np.asarray(image)
        output, marks, found_boxes = [], [], []
        for row in rows:
            output.append(row)
            try:
                points = [(float(x), float(y)) for x, y in row[0]]
                text = str(row[1]).strip()
            except (TypeError, ValueError, IndexError):
                continue
            if not text or text[0] in "☐☑☒□■口":
                continue
            xs, ys = [x for x, _ in points], [y for _, y in points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            height = y1 - y0
            # Upright rows only: a tall or rotated row has no "left of" box.
            if height <= 4 or (x1 - x0) < height * 1.5:
                continue
            # Work on the row's neighbourhood only, in local coordinates.
            oy0 = max(0, int(y0 - height * .30) - 4)
            ox0 = max(0, int(x0 - height * 2.5) - 4)
            oy1 = min(pixels.shape[0], int(y1 + height * .30) + 4)
            ox1 = min(pixels.shape[1], int(x0 + height * .6 + height * self.maximum_side_fraction) + int(height * .25) + 8)
            if oy1 - oy0 < 8 or ox1 - ox0 < 8:
                continue
            crop = pixels[oy0:oy1, ox0:ox1]
            dark = (crop if crop.ndim == 2 else crop[..., :3].mean(axis=2)) < self.dark_level
            integral = np.pad(dark.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
            local = self._box_near(dark, integral, x0 - ox0, y0 - oy0, y1 - oy0, height)
            if local is None:
                continue
            symbol = self._state(dark, local)
            found = (local[0] + ox0, local[1] + oy0, local[2] + ox0, local[3] + oy0)
            if any(_overlaps(found, existing) for existing in found_boxes):
                continue
            if symbol is None:
                continue
            found_boxes.append(found)
            bx0, by0, bx1, by1 = found
            marks.append(([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], symbol, self.confidence))
            if bx1 > x0:
                # The detected row began at the box: its glyphs start after it.
                output[-1] = ([[max(x, bx1 + 1.), y] for x, y in points], *tuple(row)[1:])
        return [*output, *marks]

    def _box_near(self, dark, integral, x0, y0, y1, height):
        """Best hollow square near the row start, searched from its corners.

        Runs of ink going down and right from each pixel locate top-left
        corners with two long sides; only those candidates are measured.
        """
        import numpy as np
        rows_px, cols_px = dark.shape
        smallest = max(5, int(height * self.minimum_side_fraction))
        largest = int(height * self.maximum_side_fraction)
        wy0, wy1 = max(3, int(y0 - height * .30)), min(rows_px - 3, int(y1 + height * .30))
        wx0, wx1 = max(3, int(x0 - height * 2.5)), min(cols_px - 3, int(x0 + height * .6) + largest)
        if wy1 - wy0 < smallest or wx1 - wx0 < smallest:
            return None
        window = dark[wy0:wy1, wx0:wx1]
        down = np.zeros(window.shape, dtype=np.int32)
        right = np.zeros(window.shape, dtype=np.int32)
        for r in range(window.shape[0] - 1, -1, -1):
            down[r] = window[r] * ((down[r + 1] if r + 1 < window.shape[0] else 0) + 1)
        for c in range(window.shape[1] - 1, -1, -1):
            right[:, c] = window[:, c] * ((right[:, c + 1] if c + 1 < window.shape[1] else 0) + 1)
        minimum_run = int(np.ceil(smallest * self.minimum_edge_ink))
        corners = np.argwhere((down >= minimum_run) & (right >= minimum_run))

        def ink(ax0, ay0, ax1, ay1):
            area = (ax1 - ax0) * (ay1 - ay0)
            if area <= 0:
                return 1.0
            total = integral[ay1, ax1] - integral[ay0, ax1] - integral[ay1, ax0] + integral[ay0, ax0]
            return float(total) / area

        def run(array, r, c):
            return int(array[r, c]) if 0 <= r < array.shape[0] and 0 <= c < array.shape[1] else 0

        best = None
        for r, c in corners:
            top, left = wy0 + int(r), wx0 + int(c)
            if left > x0 + height * .6:
                continue
            limit = min(largest, int(min(down[r, c], right[r, c]) / self.minimum_edge_ink))
            for side in range(smallest, limit + 1):
                need = side * self.minimum_edge_ink
                if max(run(down, r, c + side - 1), run(down, r, c + side - 2)) < need:
                    continue
                if max(run(right, r + side - 1, c), run(right, r + side - 2, c)) < need:
                    continue
                bottom, rightmost = top + side, left + side
                if bottom + 2 >= rows_px or rightmost + 2 >= cols_px or top < 2 or left < 2:
                    continue
                corner_ink = min(ink(left, top, left + 2, top + 2), ink(rightmost - 2, top, rightmost, top + 2),
                                 ink(left, bottom - 2, left + 2, bottom), ink(rightmost - 2, bottom - 2, rightmost, bottom))
                if corner_ink < self.minimum_corner_ink:
                    continue
                ring = max(ink(left - 1, top - 2, rightmost + 1, top - 1), ink(left - 1, bottom + 1, rightmost + 1, bottom + 2),
                           ink(left - 2, top - 1, left - 1, bottom + 1), ink(rightmost + 1, top - 1, rightmost + 2, bottom + 1))
                if ring > self.maximum_ring_ink:
                    continue
                gap = max(2, int(side * .25))
                if ink(rightmost + 2, top, min(cols_px, rightmost + 2 + gap), bottom) > self.maximum_gap_ink:
                    continue
                score = 1.0 - ring
                if best is None or score > best[0]:
                    best = (score, left, top, side)
        if best is None:
            return None
        _, left, top, side = best
        return (float(left), float(top), float(left + side), float(top + side))

    def _state(self, dark, box):
        import numpy as np
        left, top, right, bottom = (int(value) for value in box)
        margin = max(2, int((right - left) * .18))
        interior = dark[top + margin:bottom - margin, left + margin:right - margin]
        if interior.size == 0:
            return None
        ink = float(interior.mean())
        if ink <= self.empty_interior_ink:
            return "☐"
        if ink < self.marked_interior_ink:
            return None
        size = min(interior.shape)
        steps = np.arange(size)

        def diagonal(rows):
            hits = [interior[max(0, r - 1):r + 2, c].any() for r, c in zip(rows, steps)]
            return sum(hits) / max(len(hits), 1)

        falling, rising = diagonal(steps), diagonal(size - 1 - steps)
        return "☒" if min(falling, rising) >= self.cross_diagonal_ink else "☑"


def _overlaps(first, second) -> bool:
    return not (first[2] <= second[0] or second[2] <= first[0] or first[3] <= second[1] or second[3] <= first[1])


SELECTION_MARK_POLICY = RapidOCRSelectionMarkPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCRWatermarkPolicy:
    """Abstain on diagonal, faint rows: decorative watermarks, not content.

    A watermark is drawn at an angle with low-opacity ink. Both signals are
    required: body text, labels and semantic stamps are printed with dark or
    saturated ink (even when slightly tilted), and faint horizontal text such
    as light captions stays eligible.
    """

    policy_id: str = "rapidocr-decorative-watermark-v1"
    minimum_angle: float = 7.0
    maximum_angle: float = 83.0
    faint_ink_level: float = 180.0

    def identity(self) -> dict:
        return {"policy_id": self.policy_id, "angle_range": [self.minimum_angle, self.maximum_angle],
                "faint_ink_level": self.faint_ink_level}

    def is_watermark(self, pixels, row) -> bool:
        import numpy as np
        try:
            points = [(float(x), float(y)) for x, y in row[0]]
            (ax, ay), (bx, by) = points[0], points[1]
        except (TypeError, ValueError, IndexError):
            return False
        angle = abs(math.degrees(math.atan2(by - ay, bx - ax))) % 180
        angle = min(angle, 180 - angle)
        if not self.minimum_angle <= angle <= self.maximum_angle:
            return False
        xs, ys = [x for x, _ in points], [y for _, y in points]
        x0, x1 = max(0, int(min(xs))), min(pixels.shape[1], int(max(xs)) + 1)
        y0, y1 = max(0, int(min(ys))), min(pixels.shape[0], int(max(ys)) + 1)
        crop = pixels[y0:y1, x0:x1]
        if crop.size == 0:
            return False
        crop = crop if crop.ndim == 2 else crop[..., :3].mean(axis=2)
        darkest = np.sort(crop, axis=None)[:max(1, crop.size // 10)]
        return float(np.median(darkest)) >= self.faint_ink_level

    def filter(self, image, rows) -> tuple[list, int]:
        """Return the rows that are not watermarks and the abstention count."""
        import numpy as np
        if image is None or not rows:
            return list(rows), 0
        pixels = np.asarray(image)
        kept = [row for row in rows if not self.is_watermark(pixels, row)]
        return kept, len(rows) - len(kept)


WATERMARK_POLICY = RapidOCRWatermarkPolicy()
