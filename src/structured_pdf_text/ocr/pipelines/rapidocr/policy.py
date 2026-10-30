"""RapidOCR-owned recovery schedule, matching the pre-migration path."""
from dataclasses import dataclass
import re
import math
from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.quality import _union_area
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import RasterBoundaryPolicy
from structured_pdf_text.ocr.selection_marks import RasterSelectionMarkPolicy

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

_DECIMAL_COMMA = re.compile(r"(?<![\w.,])[+-]?\d+(?:\.\d{3})*,\d+(?:\s*[%‰])?(?![\w.,])")


def _reading_confidence(tokens) -> float:
    values = [token.confidence or 0. for token in tokens if token.text.strip()]
    return sum(values) / len(values) if values else 0.


@dataclass(frozen=True, slots=True)
class RapidOCRTokenRefinementPolicy(TokenRefinementPolicy):
    """A regional rereading replaces a cell reading only on better evidence.

    A different crop alone is no evidence that an observed reading was wrong:
    near-equal confidences read `Jul` as `Jui`, `C` as `c` or `81,25` as
    `B1,25`. Keep the observations of a conflicting cell, while other cells
    can improve:

    - a rereading that only moves the decimal separator keeps the reading
      with a pt-BR decimal comma, whichever of the two it is;
    - any other character change needs a mean confidence gain of
      `confidence_gain`.

    A rereading that adds content (more than one character) always replaces
    the cell: keeping the prior reading would drop its new observations.
    Empty cells always remain eligible for recovery.
    """

    confidence_gain: float = .02

    def identity(self):
        return {"policy_id": "rapidocr-cell-reading-conservation-v3",
                "minimum_cell_iou": .80, "composition": "preserve_conflicting_cell",
                "separator_only": "prefer_decimal_comma", "added_content": "replace",
                "minimum_confidence_gain": self.confidence_gain}

    def _keeps_prior(self, old, new) -> bool:
        old_text, new_text = "".join(old.text.split()), "".join(new.text.split())
        if old_text == new_text or not old_text or len(new_text) > len(old_text) + 1:
            return False
        if re.sub(r"[.,]", "", old_text) == re.sub(r"[.,]", "", new_text):
            old_comma, new_comma = bool(_DECIMAL_COMMA.search(old.text)), bool(_DECIMAL_COMMA.search(new.text))
            if old_comma != new_comma:
                return old_comma
        return _reading_confidence(new.tokens) < _reading_confidence(old.tokens) + self.confidence_gain

    def _conflicts(self, original, candidate):
        for old in original.cells:
            if old.bbox is None:
                continue
            matching = [cell for cell in candidate.cells if cell.bbox is not None
                        and cell.row == old.row and cell.col == old.col
                        and cell.rowspan == old.rowspan and cell.colspan == old.colspan
                        and old.bbox.iou(cell.bbox) >= .80]
            if not matching:
                continue  # A changed grid is still checked by core conservation.
            if self._keeps_prior(old, matching[0]):
                yield old, matching[0]

    def prepare_visual_table(self, original, candidate):
        from copy import deepcopy

        conflicts = list(self._conflicts(original, candidate))
        if not conflicts:
            return candidate
        draft = deepcopy(candidate)
        for old, conflicting in conflicts:
            selected = next(cell for cell in draft.cells
                            if (cell.row, cell.col) == (conflicting.row, conflicting.col))
            # Keep complete, actually observed cell evidence, never repair
            # punctuation by editing strings or mix pieces of two readings.
            selected.tokens = deepcopy(old.tokens)
            selected.text = old.text
            selected.confidence = old.confidence
        return draft

    def accept_visual_table(self, original, candidate):
        return next(self._conflicts(original, candidate), None) is None


TOKEN_REFINEMENT_POLICY = RapidOCRTokenRefinementPolicy()
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
    # Ring and box shapes only: C, D, Q are real single-letter labels (table
    # headers A | B | C) and must stay eligible.
    shape_glyphs: str = "Ooe0口□○◯〇●◎"
    shape_glyph_minimum_confidence: float = .995
    shape_aspect_range: tuple[float, float] = (.85, 1.25)
    # A lone glyph far taller than any label is a drawn shape read as a letter.
    lone_glyph_maximum_height: float = 36.0

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
            if token.bbox.height > self.lone_glyph_maximum_height:
                return "rapidocr_oversized_lone_figure_glyph"
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
    confidence-weighted text as the upright reading, or, when it is nearly
    certain (``confident_mean``) and markedly more confident than the upright
    reading (``confidence_margin``), at least ``confident_gain`` times as much
    and ``confident_minimum_characters`` in absolute terms. A turned reading
    with little text (a shape read as one letter) never wins on confidence. Healthy
    readings never trigger a search, so ordinary pages keep their single pass.
    """

    policy_id: str = "rapidocr-orientation-search-v2"
    confidence_floor: float = .90
    minimum_gain: float = 2.0
    alternatives: tuple[int, ...] = (3, 1, 2)
    confident_mean: float = .97
    confidence_margin: float = .10
    confident_gain: float = 1.1
    confident_minimum_characters: float = 20.0
    # A single glyph (a shape read as a letter) never decides the orientation.
    minimum_characters: float = 3.0

    def acceptance(self, upright_mean: float, upright_score: float, mean: float, score: float) -> str | None:
        """Why an alternative reading may replace the upright one (or ``None``)."""
        if mean < self.confidence_floor or score < self.minimum_characters:
            return None
        if score >= self.minimum_gain * upright_score:
            return "more_text"
        if (mean >= self.confident_mean and mean - upright_mean >= self.confidence_margin
                and score >= self.confident_gain * upright_score
                and score >= self.confident_minimum_characters):
            return "confident_and_complete"
        return None

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
            "confident_mean": self.confident_mean,
            "confidence_margin": self.confidence_margin,
            "confident_gain": self.confident_gain,
            "confident_minimum_characters": self.confident_minimum_characters,
            "minimum_characters": self.minimum_characters,
        }


ORIENTATION_POLICY = RapidOCROrientationPolicy()


@dataclass(frozen=True, slots=True)
class RapidOCRSelectionMarkPolicy(RasterSelectionMarkPolicy):
    policy_id: str = "rapidocr-selection-marks-v2"


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
