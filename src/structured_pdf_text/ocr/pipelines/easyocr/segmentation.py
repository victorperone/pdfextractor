"""Reconcile observed, conflicting line segmentations after EasyOCR fusion."""
from collections import Counter
from dataclasses import replace
import re

from .policy import _prose, _reading_frame, _same_candidate, _vertical_overlap


def reconcile_boundary_words(evidence, candidates, policy):
    """Use an observed coherent tail when two variants reread one boundary.

    A shared word is insufficient: the source boxes must overlap materially,
    and the retained prefix's own candidate must supply the entire remaining
    suffix in a strong contained box. No strings or word boxes are invented.
    Legitimate repetitions in distinct positions remain independent evidence.
    """
    tokens = list(evidence.tokens)
    sources = {candidate.candidate_id: candidate for candidate in candidates}
    repairs = Counter()
    for i, first in enumerate(tokens):
        if not _prose(first) or first.ocr_provenance is None:
            continue
        origin = sources.get(first.ocr_provenance.candidate_id)
        if origin is None:
            continue
        left = _reading_frame(first)
        words = re.findall(r'\w+', first.text.casefold())
        if not words or len(words[-1]) < 4 or not words[-1].isalpha():
            continue
        for j, second in enumerate(tokens):
            if i == j or not _prose(second) or first.rotation != second.rotation:
                continue
            right = _reading_frame(second)
            if not left.bbox.x0 < right.bbox.x0 < left.bbox.x1 < right.bbox.x1:
                continue
            if _vertical_overlap(left, right) < .50:
                continue
            overlap = left.bbox.x1-right.bbox.x0
            if overlap < .20 * min(left.bbox.width,right.bbox.width):
                continue
            suffix = re.findall(r'\w+', second.text.casefold())
            if len(suffix) < 3 or suffix[0] != words[-1]:
                continue
            for observed in origin.tokens:
                if observed.rotation != first.rotation or re.findall(r'\w+', observed.text.casefold()) != suffix[1:]:
                    continue
                framed = _reading_frame(observed)
                if (framed.bbox.x0 < left.bbox.x1-.20*min(left.bbox.height,framed.bbox.height)
                        or observed.bbox.overlap_ratio(second.bbox) < .90
                        or abs(framed.bbox.x1-right.bbox.x1) > .10*right.bbox.width
                        or (observed.confidence or 0.) < max(.70,(second.confidence or 0.)-.05)):
                    continue
                tokens[j] = replace(observed, provenance=observed.provenance or f'candidate:{origin.candidate_id}',
                                    ocr_provenance=replace(policy.provenance(observed,origin),
                                                           refinement_kind='coherent_boundary_tail'))
                repairs[origin.candidate_id] += 1
                break
    if not repairs:
        return evidence, repairs
    counts = Counter(token.ocr_provenance.candidate_id for token in tokens if token.ocr_provenance is not None)
    return replace(evidence,tokens=tuple(tokens),contribution_counts=dict(counts)), repairs


def _letters(text, *, fold_confusables=True):
    """Keys for overlapping readings, retaining every original text index.

    OCR's O/0 and I/l/1 confusions must not duplicate a URL reread from the
    same pixels. Folding is only for geometric overlap comparison: it never
    changes the text, digits or signs retained in either observation.
    """
    import unicodedata
    output = []
    for index, char in enumerate(text):
        if char.isalnum() or char == '|':
            folded = unicodedata.normalize('NFKD', char.casefold())
            base = ''.join(c for c in folded if not unicodedata.combining(c))
            key = base[:1] or char
            output.append(({'0': 'o', '1': 'i', 'l': 'i', '|': 'i'}.get(key, key)
                           if fold_confusables else key, index))
    return output


def _from_reading_frame(box, angle):
    from structured_pdf_text.geometry import BBox
    angle %= 360
    if angle == 90:
        return BBox(-box.y1, box.x0, -box.y0, box.x1)
    if angle == 180:
        return BBox(-box.x1, -box.y1, -box.x0, -box.y0)
    if angle == 270:
        return BBox(box.y0, -box.x1, box.y1, -box.x0)
    return box


def trim_overlapping_readings(evidence, *, minimum_letters=5):
    """Drop the tail of a reading that another variant reread from the same pixels.

    Two variants can segment one printed line differently: ``…05.pdf. A frase
    final confirma`` and ``A frase final confirma controle…`` partially
    overlap, neither contains the other, and both survive fusion; line
    reconstruction then prints the shared stretch twice (run 009, V4: 83
    repetitions on 36 pages). The left reading loses its tail only when the
    text it shares with the start of the right reading also fits the pixel
    overlap of the two boxes. Characters are removed, never added; the right
    reading, which continues the line, stays intact.
    """
    tokens = list(evidence.tokens)
    trimmed = Counter()
    for j, right in enumerate(tokens):
        for i, left in enumerate(tokens):
            if i == j or not left.text.strip() or left.rotation % 360 != right.rotation % 360:
                continue
            if _same_candidate(left, right):
                continue
            first, second = _reading_frame(left), _reading_frame(right)
            if _vertical_overlap(first, second) < .50:
                continue
            if not first.bbox.x0 < second.bbox.x0 < first.bbox.x1 < second.bbox.x1:
                continue
            ours, theirs = _letters(left.text), _letters(right.text)
            if not ours or not theirs:
                continue
            ours_key = ''.join(char for char, _ in ours)
            theirs_key = ''.join(char for char, _ in theirs)
            limit = min(len(ours_key), len(theirs_key))
            shared = next((size for size in range(limit, minimum_letters - 1, -1)
                           if ours_key[-size:] == theirs_key[:size]), 0)
            if not shared or shared == len(ours_key):
                # A reading that is entirely the overlap is a fragment and
                # remains the concern of the fusion's fragment rules.
                continue
            plain_ours = ''.join(char for char, _ in _letters(left.text, fold_confusables=False))
            plain_theirs = ''.join(char for char, _ in _letters(right.text, fold_confusables=False))
            if (plain_ours[-shared:] != plain_theirs[:shared]
                    and sum(char.isalpha() and char not in 'io' for char in ours_key[-shared:]) < minimum_letters):
                # A printed list of confusable glyphs in two columns has too
                # little stable text to establish a shared reading. URLs and
                # prose need substantial ordinary letters as corroboration.
                continue
            pitch = first.bbox.width / len(ours_key)
            overlap = first.bbox.x1 - second.bbox.x0
            if not .5 <= overlap / (shared * pitch) <= 2.:
                continue
            cut = ours[len(ours) - shared][1]
            text = left.text[:cut].rstrip()
            # The shared alphanumeric key starts after opening punctuation.
            # If both observations include that same opening quote/bracket,
            # let the intact right reading own it as well as the shared word.
            opening = right.text[:theirs[0][1]].strip()
            if opening and all(char in '\"\'“‘«([{`' for char in opening) and text.endswith(opening):
                text = text[:-len(opening)].rstrip()
            box = replace(first.bbox, x1=max(first.bbox.x0 + pitch, second.bbox.x0))
            tokens[i] = replace(left, text=text, bbox=_from_reading_frame(box, left.rotation), polygon=None,
                                ocr_provenance=(replace(left.ocr_provenance, refinement_kind='variant_overlap_trimmed')
                                                if left.ocr_provenance is not None else None))
            trimmed[left.ocr_provenance.candidate_id if left.ocr_provenance is not None else None] += 1
    if not trimmed:
        return evidence, trimmed
    return replace(evidence, tokens=tuple(tokens)), trimmed


_STROKES = frozenset('Il1|')


def _differs_only_by_strokes(first, second):
    return len(first) == len(second) and first != second and all(
        a == b or (a in _STROKES and b in _STROKES) for a, b in zip(first, second))


def prefer_stroke_majority(evidence, candidates, *, minimum_votes=3, same_box_iou=.60):
    """Let the variants' majority settle an ``I/l/1/|`` disagreement.

    Fusion keeps the single most confident reading of a box. When it is the
    only variant reading it that way while most variants that read the same
    pixels agree on another text differing just by vertical strokes, the
    majority's own most confident observation replaces it (run 009, V6 p21:
    ``Semestre Il`` from one variant against seven ``Semestre II``). Other
    disagreements, signs and O/0 among them, keep the fusion's choice.
    """
    return _prefer_visual_majority(evidence, candidates, minimum_votes=minimum_votes,
                                   same_box_iou=same_box_iou, kind='stroke')


def prefer_case_majority(evidence, candidates, *, minimum_votes=3, same_box_iou=.60):
    """Choose an observed majority for case alone, retaining IDs and signs."""
    return _prefer_visual_majority(evidence, candidates, minimum_votes=minimum_votes,
                                   same_box_iou=same_box_iou, kind='case')


def _prefer_visual_majority(evidence, candidates, *, minimum_votes, same_box_iou, kind):
    from collections import defaultdict
    tokens = list(evidence.tokens)
    adopted = Counter()
    for index, token in enumerate(tokens):
        current = token.text.strip()
        if not current or (kind == 'stroke' and not _STROKES.intersection(current)):
            continue
        readings = {}
        for candidate in candidates:
            matches = [item for item in candidate.tokens if item.text.strip()
                       and item.rotation % 360 == token.rotation % 360
                       and item.bbox.iou(token.bbox) >= same_box_iou]
            if matches:
                readings[candidate.candidate_id] = max(matches, key=lambda item: item.bbox.iou(token.bbox))
        votes = defaultdict(list)
        for candidate_id, item in readings.items():
            votes[item.text.strip()].append((candidate_id, item))
        if len(votes.get(current, ())) != 1:
            continue
        text, supporters = max(votes.items(), key=lambda pair: len(pair[1]))
        differs = (_differs_only_by_strokes(current, text) if kind == 'stroke' else
                   current != text and len(current) == len(text) and current.casefold() == text.casefold())
        if len(supporters) < minimum_votes or 2 * len(supporters) <= len(readings) or not differs:
            continue
        candidate_id, observed = max(supporters, key=lambda pair: pair[1].confidence or 0.)
        origin = next(candidate for candidate in candidates if candidate.candidate_id == candidate_id)
        provenance = observed.ocr_provenance or token.ocr_provenance
        tokens[index] = replace(observed, provenance=observed.provenance or f'candidate:{candidate_id}',
                                ocr_provenance=(replace(provenance, candidate_id=origin.candidate_id,
                                                        refinement_kind=f'{kind}_majority')
                                                if provenance is not None else None))
        adopted[candidate_id] += 1
    if not adopted:
        return evidence, adopted
    counts = Counter(token.ocr_provenance.candidate_id for token in tokens if token.ocr_provenance is not None)
    return replace(evidence, tokens=tuple(tokens), contribution_counts=dict(counts)), adopted
