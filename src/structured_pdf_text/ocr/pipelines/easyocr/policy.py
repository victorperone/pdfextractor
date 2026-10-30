"""EasyOCR-owned recovery schedule, matching the pre-migration path."""
from dataclasses import dataclass, replace
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

class EasyOCRIntegrationPolicy(ConservativeIntegrationPolicy):
    def prepare_token(self, token, request):
        token = super().prepare_token(token, request)
        if getattr(request, 'selection_stage', None) == 'direct' and request.preprocessing_rotation % 360:
            # RasterFrame maps geometry; this angle describes the source text,
            # not the already-upright raster used by direct recognition.
            angle = (token.rotation+request.preprocessing_rotation) % 360
            token = replace(token, rotation=angle, ocr_provenance=(
                replace(token.ocr_provenance, rotation=float(angle))
                if token.ocr_provenance is not None else None))
        return token


INTEGRATION_POLICY = EasyOCRIntegrationPolicy("easyocr-integration-v1")

@dataclass(frozen=True, slots=True)
class EasyOCRCellRecoveryPolicy(CellRecoveryPolicy):
    """Allow a strong single digit only in an explicitly numeric column."""

    automatic_recovery: bool = True
    maximum_automatic_cells: int = 16

    def direct_preprocessing_rotation(self, cell):
        angles = {token.rotation % 360 for token in cell.tokens if token.text.strip()
                  and (token.confidence or 0.) >= .60}
        if len(angles) == 1:
            angle = next(iter(angles))
            if angle in (90, 180, 270):
                # OCRServices.rotate uses PIL's counterclockwise convention;
                # the backend's applied raster corrections are clockwise.
                return angle
        return 0

    def header_context(self, table, cell, header_rows):
        # A visual two-tier header can be unclassified or expose only its
        # spanning row. Prefer the closest observed field label, never a
        # body value or an inferred column number.
        labels = [item for item in table.cells if item.row < cell.row
                  and item.col <= cell.col < item.col + item.colspan
                  and re.fullmatch(r"(?:Quantidade|Qtd\.?|Valor|Lote|ID|C[oó]digo|Ref\.?)", item.text.strip(), re.I)]
        text = (max(labels, key=lambda item: item.row).text if labels else
                super(EasyOCRCellRecoveryPolicy, self).header_context(table, cell, header_rows))
        if any(item is not cell and item.col == cell.col
               and (money := _currency_surface(item.text)) is not None and money[0]
               for item in table.cells):
            text += " | R$"
        if any(item.col == cell.col and '%' in item.text for item in table.cells):
            text += " | %"
        return text

    def automatic_priority(self, cell, header_text, *, is_header=False):
        text = cell.text.strip()
        if not text:
            return 0 if self.allows_single_digit(header_text) else None
        if not any(getattr(token, "ocr_provenance", None) or
                   any(source.source.value.startswith("ocr_") for source in getattr(token, "sources", ()))
                   for token in cell.tokens):
            return None
        money = _context_money_surface(text, header_text)
        if '%' in header_text and re.fullmatch(r"[+−\-]?\d+[.,]\d+[%‰]?", ''.join(text.split())):
            return 1
        if money is not None and (not money[0] or not re.fullmatch(
                r"[+−-]?R\$\s*[+−-]?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}", text)):
            return 1
        if (is_header or cell.row <= 1) and re.fullmatch(r"Semestre", text, re.I):
            return 2
        if re.search(r"\b(?:id|lote|c[oó]digo|ref)\b", header_text, re.I) and re.fullmatch(r"[A-Za-z0-9/-]{2,18}", text):
            return 3 if _reading_confidence(cell.tokens) < .60 and re.search(r"[O0Il1]", text) else None
        return None

    def allows_single_digit(self, header_text):
        return isinstance(header_text, str) and bool(re.search(
            r"\b(?:qtd|quantidade|quant|n[uú]mero|total de itens)\b", header_text, re.I))

    def select(self, candidates, *, current_text, cell_bbox, header_text):
        candidates = [tuple(group) for group in candidates if group]
        if '%' in header_text:
            from collections import Counter
            from .recognition import _percent_surface, _same_percent_amount
            counts = Counter(''.join(''.join(t.text for t in group).split())
                             for group in candidates if _reading_confidence(group) >= .80)
            repairs = [group for group in candidates
                       if (value := _percent_surface(' '.join(t.text for t in group))) is not None
                       and _same_percent_amount(current_text, ' '.join(t.text for t in group))
                       and counts[''.join(''.join(t.text for t in group).split())] >= 2
                       and _reading_confidence(group) >= .80]
            return max(repairs, key=_reading_confidence) if repairs else ()
        old_money = _context_money_surface(current_text, header_text)
        if old_money is not None:
            if old_money[1] is None:
                from collections import Counter
                votes = Counter(value[1] for group in candidates
                                if (value := _currency_surface(" ".join(t.text for t in group))) is not None
                                and value[0] and _amount_surface_key(value[2]) == _amount_surface_key(old_money[2])
                                and _reading_confidence(group) >= .80)
                signs = [sign for sign, count in votes.items() if count >= 2]
                if len(signs) != 1:
                    return ()
                old_money = old_money[0], signs[0], old_money[2]
            candidates = [group for group in candidates
                          if (value := _currency_surface(" ".join(t.text for t in group))) is not None
                          and value[1] == old_money[1]
                          and (not re.search(r'[+−-]', current_text)
                               or re.search(r'[+−-]', ' '.join(t.text for t in group)))
                          and _amount_surface_key(old_money[2]) is not None
                          and _amount_surface_key(value[2]) == _amount_surface_key(old_money[2])]
            if not candidates:
                return ()
            repairs = [group for group in candidates
                       if (new := _currency_surface(" ".join(token.text for token in group))) is not None
                       and new[0] and new[1] == old_money[1]
                       and _amount_surface_key(new[2]) == _amount_surface_key(old_money[2])
                       and _reading_confidence(group) >= .80
                       and re.fullmatch(r"(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}", new[2])]
            if repairs:
                return max(repairs, key=_reading_confidence)
            return ()  # No complete, well-formed repair was actually observed.
        if re.fullmatch(r"Semestre", current_text.strip(), re.I):
            expanded = [group for group in candidates if re.fullmatch(
                r"Semestre\s+I{1,3}", " ".join(t.text for t in group), re.I)
                        and _reading_confidence(group) >= .80]
            if expanded:
                return max(expanded, key=_reading_confidence)
            return ()  # A slash or another partial read is not a Roman numeral.
        if current_text.strip() and re.search(r"\b(?:id|lote|c[oó]digo|ref)\b", header_text, re.I):
            from collections import Counter
            texts = ["".join(token.text for token in group).strip() for group in candidates]
            counts = Counter(texts)
            agreed = [group for group, text in zip(candidates, texts)
                      if counts[text] >= 2 and len(text) == len(current_text.strip())
                      and _reading_confidence(group) >= .85]
            return max(agreed, key=_reading_confidence) if agreed else ()
        if not current_text.strip() and self.allows_single_digit(header_text):
            strong = [group for group in candidates if len(group) == 1
                      and group[0].text.strip() in "0123456789"
                      and len(group[0].text.strip()) == 1
                      and (group[0].confidence or 0.) >= .95
                      and group[0].bbox.overlap_ratio(cell_bbox) >= .80]
            if strong:
                return max(strong, key=lambda group: group[0].confidence)
        return super(EasyOCRCellRecoveryPolicy, self).select(
            candidates, current_text=current_text, cell_bbox=cell_bbox, header_text=header_text)


CELL_POLICY = EasyOCRCellRecoveryPolicy()

_DECIMAL_COMMA = re.compile(r"(?<![\w.,])[+-]?\d+(?:\.\d{3})*,\d+(?:\s*[%‰])?(?![\w.,])")


def _reading_confidence(tokens) -> float:
    values = [token.confidence or 0. for token in tokens if token.text.strip()]
    return sum(values) / len(values) if values else 0.


def _currency_surface(text):
    """Observed currency marker, explicit sign and amount; never repair digits."""
    compact = "".join(text.split()).replace("−", "-")
    match = re.fullmatch(r"([+-]?)[Rr](\$|[sS])([+-]?)(\d[\d.,]*)", compact)
    if match is None:
        return None
    before, marker, after, amount = match.groups()
    if before and after and before != after:
        return None
    return marker == "$", before or after or "+", amount


def _amount_surface_key(amount):
    """Keep explicit cents and leading zeroes when comparing separators.

    An integer without a two-digit fractional surface is ambiguous; never
    reinterpret R$ 1.234 or R$ 1234 as R$ 12,34 merely because digits agree.
    """
    match = re.fullmatch(r"(\d[\d.,]*)[.,](\d{2})", amount)
    return (re.sub(r"[.,]", "", match[1]), match[2]) if match else None


def _context_money_surface(text, header_text):
    money = _currency_surface(text)
    if money is not None or "R$" not in header_text:
        return money
    compact = "".join(text.split()).replace("−", "-")
    bare = re.fullmatch(r"([+-]?)(\d[\d.,]*)", compact)
    if bare is not None and _amount_surface_key(bare[2]) is not None:
        # A neighboring R$ value establishes the column, but a missing sign
        # remains unknown until two complete rereads agree.
        return False, bare[1] or None, bare[2]
    match = re.fullmatch(r"([+-]?)([A-Za-z$]{1,3})([+-]?)(\.?)(\d[\d.,]*)", compact)
    if match is None:
        return None
    before, marker, after, noise, amount = match.groups()
    if before and after and before != after:
        return None
    # A dot where a sign might have been read is explicitly ambiguous. Only
    # agreement between complete new readings can resolve that ambiguity.
    sign = before or after or (None if noise else "+")
    return False, sign, amount


def _signed_surface(token):
    text = ''.join(token.text.split()).replace('−', '-')
    money = _currency_surface(text)
    if money is not None:
        _, sign, amount = money
        explicit_positive = text.startswith('+') or bool(re.search(r'[sS$]\+', text))
        return 'money', amount, sign, explicit_positive
    percent = re.fullmatch(r'([+-]?)(\d+(?:[.,]\d+)*)([%‰])', text)
    if percent is not None:
        sign, amount, unit = percent.groups()
        return unit, amount, sign or '+', sign == '+'
    return None

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
        # Compare only observed surfaces of the same amount/sign. A lower
        # native CTC confidence must not prevent an authorized Rs -> R$ repair,
        # nor may a crop turn an already well-formed R$ into Rs.
        old_money, new_money = _currency_surface(old_text), _currency_surface(new_text)
        if old_money is not None and new_money is not None:
            old_valid, old_sign, old_amount = old_money
            new_valid, new_sign, new_amount = new_money
            if old_sign == "-" and new_sign != "-":
                return True
            if (old_amount != new_amount
                    and re.sub(r"[.,]", "", old_amount) == re.sub(r"[.,]", "", new_amount)
                    and (_amount_surface_key(old_amount) is None
                         or _amount_surface_key(new_amount) != _amount_surface_key(old_amount))):
                return True
            if (old_sign, old_amount) == (new_sign, new_amount):
                if old_valid and not new_valid:
                    return True
                if not old_valid and new_valid and _reading_confidence(new.tokens) >= .75:
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
    ring_glyphs: str = "0°"
    ring_minimum_aspect: float = .80

    def rejection_reason(self, token, request):
        if request.goal is WorkGoal.FIGURE:
            provenance = getattr(token, 'metadata', {}).get('domain', {}).get('ocr_provenance') or {}
            if provenance.get('refinement_kind') == 'decorative_watermark':
                return 'easyocr_decorative_watermark'
            if (provenance.get('refinement_kind') == 'raster_selection_mark'
                    and token.text.strip() in {'☐', '☑', '☒'}):
                return None
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


def _reading_frame(token):
    """A temporary comparison box; original evidence stays in page coordinates."""
    from structured_pdf_text.geometry import BBox

    box = token.bbox
    angle = token.rotation % 360
    if angle == 90:
        box = BBox(box.y0, -box.x1, box.y1, -box.x0)
    elif angle == 180:
        box = BBox(-box.x1, -box.y1, -box.x0, -box.y0)
    elif angle == 270:
        box = BBox(-box.y1, box.x0, -box.y0, box.x1)
    else:
        return token
    return replace(token, bbox=box, rotation=0, polygon=None)


def _prose(token):
    """Long text has span semantics; short fields retain their calibrated geometry."""
    return (sum(any(char.isalpha() for char in word) and len(word) >= 2
                for word in token.text.split()) >= 3
            or sum(char.isalpha() for char in token.text) >= 24)


def _comparison_pair(first, second):
    if _prose(first) or _prose(second):
        return _reading_frame(first), _reading_frame(second)
    return first, second


def _same_candidate(first, second):
    def candidate(token):
        if token.ocr_provenance is not None:
            origin = token.ocr_provenance
            return (origin.candidate_id, origin.tile_id) if origin.candidate_id is not None else None
        return token.provenance
    identity = candidate(first)
    return identity is not None and identity == candidate(second)


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
    same_pass_line_separation: float = .35
    complete_line_confidence: float = .70
    strong_sign_confidence: float = .80

    def _separate_lines(self, first, second):
        if (not _same_candidate(first, second) or not (_prose(first) or _prose(second))
                or not any(char.isalnum() for char in first.text)
                or not any(char.isalnum() for char in second.text)):
            return False
        first, second = _reading_frame(first), _reading_frame(second)
        return abs(first.bbox.cy-second.bbox.cy) >= self.same_pass_line_separation * min(
            first.bbox.height, second.bbox.height)

    def provenance(self, token, candidate):
        origin = CandidateFusionPolicy.provenance(self, token, candidate)
        if candidate.family == "tile" or candidate.candidate_id == "full-page":
            return replace(origin, tile_id=(candidate.candidate_id.removeprefix("tile:")
                                           if candidate.family == "tile" else None))
        return origin

    signed_value_anchors: tuple = ()

    def with_observed_signs(self, candidates):
        """Keep signed values corroborated by two aligned preprocessing reads.

        Never synthesize a character or choose between explicit opposite signs.
        Only an unsigned alternative of exactly the same amount is down-ranked;
        distant rows, other rotations and weak/unconfirmed signs do not vote.
        The observations retain their actual confidence and provenance.
        """

        observations = [(candidate.candidate_id, token, _signed_surface(token))
                        for candidate in candidates for token in candidate.tokens]
        negative = [(name, token, value) for name, token, value in observations
                    if value is not None and value[2] == '-' and (token.confidence or 0.) >= .60]
        protected = []
        for name, token, value in negative:
            agreeing = {}  # other candidate id -> its best agreeing confidence
            for other, second, other_value in negative:
                if (other != name and value == other_value
                        and token.rotation % 360 == second.rotation % 360
                        and token.bbox.iou(second.bbox) >= .70):
                    agreeing[other] = max(agreeing.get(other, 0.), second.confidence or 0.)
            if not agreeing:
                continue
            if value[0] == "money" and not _currency_surface(token.text)[0]:
                # The sign and the ``$`` are different glyphs: a misread marker
                # does not weaken an observed minus. Without a well-formed R$
                # anchor, the minus needs stronger agreement: three distinct
                # preprocessing reads, or two that are both confident (V6 p11:
                # ``-Rs 350,00`` 0.90/0.85 lost to ``R$ 350,00`` 0.85).
                strong = (token.confidence or 0.) >= self.strong_sign_confidence and any(
                    confidence >= self.strong_sign_confidence for confidence in agreeing.values())
                if len(agreeing) < 2 and not strong:
                    continue
            protected.append((token, value))
        if not protected:
            return self, {}
        adjustments = {}
        for candidate in candidates:
            count = sum(self._unsigned_conflict(token, protected) for token in candidate.tokens)
            if count:
                adjustments[candidate.candidate_id] = count
        return (replace(self, signed_value_anchors=tuple(protected)) if adjustments else self), adjustments

    @staticmethod
    def _unsigned_conflict(token, anchors):
        value = _signed_surface(token)
        return value is not None and value[2] == '+' and not value[3] and any(
            value[:2] == signed[:2] and token.rotation % 360 == anchor.rotation % 360
            and token.bbox.iou(anchor.bbox) >= .70 for anchor, signed in anchors)

    def _same_pixels(self, first, second) -> bool:
        if _vertical_overlap(first, second) < self.same_line_overlap:
            return False
        if first.bbox.iou(second.bbox) >= self.same_box_iou:
            return True
        areas = sorted((max(first.bbox.area, 1e-6), max(second.bbox.area, 1e-6)))
        small, big = (first, second) if first.bbox.area <= second.bbox.area else (second, first)
        return areas[1] / areas[0] <= self.same_box_size_ratio and _contained(small, big) >= self.same_box_containment

    def same_evidence(self, first, second) -> bool:
        if first.rotation % 360 != second.rotation % 360:
            return False
        prose = _prose(first) or _prose(second)
        first, second = _comparison_pair(first, second)
        # IoU alone confuses a confident reread of a line tail with the whole
        # line. This gate also precedes the inherited text-similarity branch.
        widths = sorted((first.bbox.width, second.bbox.width))
        if prose and widths[0] < self.same_box_containment * widths[1]:
            return False
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
        if self._separate_lines(small, big) or small.rotation % 360 != big.rotation % 360:
            return False
        small, big = _comparison_pair(small, big)
        if small.bbox.width >= big.bbox.width or _vertical_overlap(small, big) < self.same_line_overlap:
            return False
        middle = (big.bbox.y0 + big.bbox.y1) / 2
        if not small.bbox.y0 <= middle <= small.bbox.y1 and _reads_same_stretch(small, big) < self.same_text_share:
            return False
        shared = min(small.bbox.x1, big.bbox.x1) - max(small.bbox.x0, big.bbox.x0)
        return max(0.0, shared) / max(small.bbox.width, 1e-6) >= self.same_box_containment

    def is_explained_fragment(self, token, selected) -> bool:
        # Separate detections of one pass can have overlapping AABBs on skewed
        # text. Only competing hypotheses may explain away another observation.
        return any(not self._separate_lines(token, existing) and (
            self._fragment_of(token, existing) or (
                token.rotation % 360 == existing.rotation % 360
                and CandidateFusionPolicy.is_explained_fragment(
                    self, _comparison_pair(token, existing)[0], [_comparison_pair(token, existing)[1]])
            )) for existing in selected)

    def suppressed_fragment_indices(self, selected) -> set[int]:
        # Retain field-level calibration: a one-character box and a money
        # reading do not have the same geometry as a prose line and its tail.
        fields = [i for i, token in enumerate(selected) if not _prose(token)]
        suppressed = {fields[i] for i in CandidateFusionPolicy.suppressed_fragment_indices(
            self, [selected[i] for i in fields])}
        for j, big in enumerate(selected):
            if j in suppressed:
                continue
            parts = [i for i, small in enumerate(selected)
                     if i != j and i not in suppressed and self._fragment_of(small, big)]
            if not parts:
                continue
            frame = _reading_frame if _prose(big) or any(_prose(selected[i]) for i in parts) else lambda token: token
            widths = [max(frame(selected[i]).bbox.width, 1e-6) for i in parts]
            part_confidence = sum((selected[i].confidence or 0.) * w for i, w in zip(parts, widths)) / sum(widths)
            trusted_line = _prose(big) and (big.confidence or 0.) >= self.complete_line_confidence
            if not trusted_line and (big.confidence or 0.) < part_confidence and _width_coverage(
                    [frame(selected[i]) for i in parts], frame(big)) >= self.same_box_containment:
                suppressed.add(j)
            else:
                suppressed.update(parts)
        return suppressed

    def score_token(self, token) -> float:
        score = CandidateFusionPolicy.score_token(self, token)
        if self._unsigned_conflict(token, self.signed_value_anchors):
            score -= .75
        if _MALFORMED_REAL.search(token.text) and not _WELL_FORMED_REAL.search(token.text):
            score -= self.malformed_value_penalty
        if _unbalanced_edge_bracket(token.text):
            score -= self.edge_bracket_penalty
        return score


FUSION_POLICY = EasyOCRCandidateFusionPolicy(
    detector_families=("craft_greedy", "craft_beam", "dbnet"),
    decoder_markers=(("wordbeam", "wordbeamsearch"), ("beam", "beamsearch")),
)
