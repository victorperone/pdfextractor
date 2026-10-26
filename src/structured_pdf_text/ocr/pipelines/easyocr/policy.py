"""EasyOCR-owned recovery schedule, matching the pre-migration path."""
from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from structured_pdf_text.ocr.pipeline.contracts import WorkGoal
from structured_pdf_text.ocr.pipeline.policies import CellRecoveryPolicy, ConservativeIntegrationPolicy, RegionalCandidatePolicy, RegionalRecoveryProfile, TokenRefinementPolicy
from structured_pdf_text.ocr.pipeline.candidate_policy import CandidateFusionPolicy
from structured_pdf_text.ocr.pipeline.policies import FigureSegmentationPolicy
from structured_pdf_text.ocr.pipeline.policies import RasterBoundaryPolicy

# Kept local so future EasyOCR tuning cannot alter another engine. Values
# capture the common legacy schedule; changing them is a separate experiment.
CANDIDATE_POLICY = RegionalCandidatePolicy()

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

INTEGRATION_POLICY = ConservativeIntegrationPolicy("easyocr-integration-v1")

CELL_POLICY = CellRecoveryPolicy()

_DECIMAL_COMMA = re.compile(r"(?<![\w.,])[+-]?\d+(?:\.\d{3})*,\d+(?:\s*[%‰])?(?![\w.,])")


def _reading_confidence(tokens) -> float:
    values = [token.confidence or 0. for token in tokens if token.text.strip()]
    return sum(values) / len(values) if values else 0.


@dataclass(frozen=True, slots=True)
class EasyOCRTokenRefinementPolicy(TokenRefinementPolicy):
    """A regional table reread replaces a cell reading only on better evidence.

    A different crop of the same cell is no evidence that the observed reading
    was wrong; EasyOCR rereads turn ``TI`` into ``Ti`` or ``R$`` into ``RS``
    with near-equal confidence. A conflicting cell keeps its observations
    unless the reread gains ``confidence_gain`` in mean confidence; when only
    the decimal separator moves, the reading with a pt-BR decimal comma wins.
    A reread that adds content replaces the cell unless its mean confidence
    falls more than ``added_content_tolerance`` below the prior reading; empty
    cells stay eligible for recovery. Adapted from the RapidOCR policy, kept
    local to EasyOCR.
    """

    confidence_gain: float = .02
    added_content_tolerance: float = .10

    def identity(self):
        return {"policy_id": "easyocr-cell-reading-conservation-v1",
                "minimum_cell_iou": .80, "composition": "preserve_conflicting_cell",
                "separator_only": "prefer_decimal_comma", "added_content": "replace_unless_weaker",
                "minimum_confidence_gain": self.confidence_gain,
                "added_content_tolerance": self.added_content_tolerance}

    def _keeps_prior(self, old, new) -> bool:
        old_text, new_text = "".join(old.text.split()), "".join(new.text.split())
        if old_text == new_text or not old_text:
            return False
        if len(new_text) > len(old_text) + 1:
            # Added content replaces the cell unless it arrives with clearly
            # weaker readings (V8 p39: ``Situação`` 1.00 → ``[Situação22``,
            # the extra ``22`` read at 0.45).
            return _reading_confidence(new.tokens) < _reading_confidence(old.tokens) - self.added_content_tolerance
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
            if matching and self._keeps_prior(old, matching[0]):
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
            selected.tokens = deepcopy(old.tokens)
            selected.text = old.text
            selected.confidence = old.confidence
        return draft

    def accept_visual_table(self, original, candidate):
        return next(self._conflicts(original, candidate), None) is None


TOKEN_REFINEMENT_POLICY = EasyOCRTokenRefinementPolicy()


@dataclass(frozen=True, slots=True)
class EasyOCRFigureAdmissionPolicy:
    """Abstain on lone glyphs that EasyOCR reads from figure shapes.

    latin_g2 reads circles and connectors of diagrams as ``0`` with near
    certain confidence, and specks as ``9``, ``1`` or a lone bracket. A real
    digit zero is taller than wide; a ring, a node or a connector read as
    ``0``/``O`` is as wide as tall or wider. A lone punctuation mark, a glyph
    a few points high or one read with low confidence is no label either.
    Words, numbers of two or more characters and letter labels stay eligible.
    """

    policy_id: str = "easyocr-figure-admission-v1"
    lone_glyph_minimum_confidence: float = .70
    lone_glyph_minimum_height: float = 4.0
    ring_glyphs: str = "0Oo°"
    ring_minimum_aspect: float = .80

    def rejection_reason(self, token, request):
        if request.goal is not WorkGoal.FIGURE or token.confidence_normalized is None:
            return None
        glyphs = "".join(token.text.split())
        if len(glyphs) != 1:
            return None
        if token.confidence_normalized < self.lone_glyph_minimum_confidence:
            return "easyocr_uncertain_lone_figure_glyph"
        if not glyphs.isalnum():
            return "easyocr_lone_figure_mark"
        if token.bbox.height < self.lone_glyph_minimum_height:
            return "easyocr_speck_figure_glyph"
        aspect = token.bbox.width / token.bbox.height if token.bbox.height > 0 else 0.
        if glyphs in self.ring_glyphs and aspect >= self.ring_minimum_aspect:
            return "easyocr_geometric_shape_glyph"
        return None


ADMISSION_POLICY = EasyOCRFigureAdmissionPolicy()
FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()
# Keep every OCR pixel footprint within the exact authorized rectangle.
# Masked fractional border pixels can map detections just outside that scope.
RASTER_BOUNDARY_POLICY = RasterBoundaryPolicy("trim")

_WELL_FORMED_REAL = re.compile(r"(?<![\w$])R\$\s?[+\-−]?\d")
_MALFORMED_REAL = re.compile(r"(?<![\w$])R(?:[sS]|\$?[sS])\s?[+\-−]?\d")


_BRACKETS = {"[": "]", "(": ")", "{": "}"}


def _unbalanced_edge_bracket(text: str) -> bool:
    """A bracket at the reading's edge without its pair: a cell border or rule read as ``[``."""
    glyphs = text.strip()
    if len(glyphs) < 2:
        return False
    first, last = glyphs[0], glyphs[-1]
    if first in _BRACKETS and _BRACKETS[first] not in glyphs:
        return True
    return any(last == closer and opener not in glyphs for opener, closer in _BRACKETS.items())


def _vertical_overlap(first, second) -> float:
    shared = min(first.bbox.y1, second.bbox.y1) - max(first.bbox.y0, second.bbox.y0)
    return max(0.0, shared) / max(min(first.bbox.height, second.bbox.height), 1e-6)


def _width_coverage(parts, big) -> float:
    """Fraction of ``big``'s width covered by the union of ``parts``."""
    spans = sorted((max(p.bbox.x0, big.bbox.x0), min(p.bbox.x1, big.bbox.x1)) for p in parts)
    covered, reach = 0.0, big.bbox.x0
    for x0, x1 in spans:
        if x1 > reach:
            covered += x1 - max(x0, reach)
            reach = x1
    return covered / max(big.bbox.width, 1e-6)


def _reads_same_stretch(small, big) -> float:
    """Share of ``small``'s characters found in ``big``'s text under the same x span."""
    part, whole = "".join(small.text.casefold().split()), "".join(big.text.casefold().split())
    if not part or not whole:
        return 0.0
    scale = len(whole) / max(big.bbox.width, 1e-6)
    start = int((small.bbox.x0 - big.bbox.x0) * scale) - len(part) // 2
    end = int((small.bbox.x1 - big.bbox.x0) * scale) + len(part) // 2
    window = whole[max(0, start):max(0, end)]
    matched = sum(block.size for block in SequenceMatcher(None, part, window, autojunk=False).get_matching_blocks())
    return matched / len(part)


def _contained(small, big) -> float:
    """Fraction of ``small`` covered by ``big`` (0 when they do not meet)."""
    intersection = small.bbox.intersection(big.bbox)
    return 0.0 if intersection is None else intersection.area / max(small.bbox.area, 1e-6)


@dataclass(frozen=True, slots=True)
class EasyOCRCandidateFusionPolicy(CandidateFusionPolicy):
    """Fuse EasyOCR preprocessing variants as readings of the same pixels.

    The adaptive policy reads one raster several times (default, high recall,
    grayscale, sharpen, Otsu...). Their boxes for one word or line coincide
    while their texts disagree ("OCR-Ooo1" / "OCR-0001", "L-l" / "L-1"). The
    shared rule demands text similarity before treating boxes as the same
    evidence, so both readings survived and line reconstruction concatenated
    them ("OCR-Ooo1OCR-0001"); stacked readings of a watermark also formed one
    tall pseudo-line that no longer matched the native line beneath it.

    Here two boxes of similar size that cover each other are the same evidence
    whatever they read, and fusion keeps one reading. A narrower reading within
    the span of a wider one, on the same line, is a competing reading of part
    of it: the group keeps whichever side is more confident (width-weighted),
    but the wider reading yields only to parts that cover its width; a
    confident fragment never erases the rest of a line. A reading that turns a
    well-formed ``R$`` amount into ``RS``/``Rs`` scores lower than the reading
    it would replace, and so does one with an unpaired bracket at its edge: a
    table border read as ``[`` (V8 p22: high recall's ``[Situação`` 0.997
    displaced the default ``Situação`` 0.749 before the 1.00 readings came).
    Different words never share pixels, so distinct content keeps its own
    boxes.
    """

    same_box_iou: float = .50
    same_box_containment: float = .80
    same_box_size_ratio: float = 1.5
    same_line_overlap: float = .50
    malformed_value_penalty: float = .25
    edge_bracket_penalty: float = .10
    same_text_share: float = .70

    def _same_pixels(self, first, second) -> bool:
        if _vertical_overlap(first, second) < self.same_line_overlap:
            return False
        if first.bbox.iou(second.bbox) >= self.same_box_iou:
            return True
        areas = sorted((max(first.bbox.area, 1e-6), max(second.bbox.area, 1e-6)))
        small, big = (first, second) if first.bbox.area <= second.bbox.area else (second, first)
        return areas[1] / areas[0] <= self.same_box_size_ratio and _contained(small, big) >= self.same_box_containment

    def same_evidence(self, first, second) -> bool:
        return CandidateFusionPolicy.same_evidence(self, first, second) or self._same_pixels(first, second)

    def _fragment_of(self, small, big) -> bool:
        """``small`` re-reads part of ``big``: inside its span, on its line.

        Horizontal coverage, not area: variants on a watermark return boxes
        two or three lines tall that still cross only the line they read.
        On skewed dense text the boxes are taller than the line pitch, and
        the next line overlaps half of a box without re-reading it (V4 p105
        lost a third of its words to that in run 002). A part that does not
        cross the middle of the wider reading is therefore a fragment only
        when it reads what the wider one reads at the same x span (V4 p113:
        an offset reread ``Pardgrafo 113.01: Extraçoo``).
        """
        if small.bbox.width >= big.bbox.width or _vertical_overlap(small, big) < self.same_line_overlap:
            return False
        middle = (big.bbox.y0 + big.bbox.y1) / 2
        if not small.bbox.y0 <= middle <= small.bbox.y1 and _reads_same_stretch(small, big) < self.same_text_share:
            return False
        shared = min(small.bbox.x1, big.bbox.x1) - max(small.bbox.x0, big.bbox.x0)
        return max(0.0, shared) / max(small.bbox.width, 1e-6) >= self.same_box_containment

    def is_explained_fragment(self, token, selected) -> bool:
        if CandidateFusionPolicy.is_explained_fragment(self, token, selected):
            return True
        # A novel reading lying inside an already selected one re-reads its pixels.
        return any(self._fragment_of(token, existing) for existing in selected)

    def suppressed_fragment_indices(self, selected) -> set[int]:
        suppressed = set(CandidateFusionPolicy.suppressed_fragment_indices(self, selected))
        for j, big in enumerate(selected):
            if j in suppressed:
                continue
            parts = [i for i, small in enumerate(selected)
                     if i != j and i not in suppressed and self._fragment_of(small, big)]
            if not parts:
                continue
            widths = [max(selected[i].bbox.width, 1e-6) for i in parts]
            part_confidence = sum((selected[i].confidence or 0.) * w for i, w in zip(parts, widths)) / sum(widths)
            if (big.confidence or 0.) < part_confidence and _width_coverage(
                    [selected[i] for i in parts], big) >= self.same_box_containment:
                suppressed.add(j)
            else:
                suppressed.update(parts)
        return suppressed

    def score_token(self, token) -> float:
        score = CandidateFusionPolicy.score_token(self, token)
        if _MALFORMED_REAL.search(token.text) and not _WELL_FORMED_REAL.search(token.text):
            score -= self.malformed_value_penalty
        if _unbalanced_edge_bracket(token.text):
            score -= self.edge_bracket_penalty
        return score


FUSION_POLICY = EasyOCRCandidateFusionPolicy(
    detector_families=("craft_greedy", "craft_beam", "dbnet"),
    decoder_markers=(("wordbeam", "wordbeamsearch"), ("beam", "beamsearch")),
)
