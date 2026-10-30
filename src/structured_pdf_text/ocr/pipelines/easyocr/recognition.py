"""Bounded direct verification of uncertain signed currency observations."""
import math
import re

from .policy import _currency_surface, _amount_surface_key, _context_money_surface


def _ambiguous_real_marker(text):
    # Restrict context repair to the observed A/s confusion, not arbitrary
    # currency codes or bare decimals elsewhere in a financial page.
    if re.fullmatch(r'[+−\-]?[Aa][sS][+−\-]?\d[\d.,]*', ''.join(text.split())):
        return _context_money_surface(text, 'R$')
    return None


def direct_content_box(image):
    """Find the ink inside a known crop, excluding rules on its edges.

    A whole table cell can be much wider than its text. Feeding its rules and
    blank margins to CRNN changes the glyph scale and can turn a short ID into
    a border. This only narrows the existing raster; it never adds pixels or
    removes an interior minus, decimal point or accent.
    """
    import cv2
    import numpy as np

    gray = np.asarray(image)
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape[:2]
    if not height or not width:
        return 0, 0, width, height
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    components = []
    for x, y, w, h, area in stats[1:]:
        horizontal = (width >= 3 * height and w >= .4 * width and w >= 10 * h
                      and (y + h <= .15 * height or y >= .85 * height))
        vertical = (width >= 3 * height and h >= .5 * height and h >= 10 * w
                    and (x <= 1 or x + w >= width - 1)
                    and (y <= 1 or y + h >= height - 1))
        if area >= 2 and not horizontal and not vertical:
            components.append((x, y, w, h, area))
    if not components:
        return 0, 0, width, height
    left = min(c[0] for c in components)
    top = min(c[1] for c in components)
    right = max(c[0] + c[2] for c in components)
    bottom = max(c[1] + c[3] for c in components)
    return (max(0, int(left) - 1), max(0, int(top) - 1),
            min(width, int(right) + 1), min(height, int(bottom) + 1))


def _percent_surface(text):
    match = re.fullmatch(r'([+-]?)(\d+(?:[.,]\d+)?)(%|[‰])', ''.join(text.split()).replace('−', '-'))
    return match.groups() if match else None


def _same_percent_amount(old, new):
    """An observed percent may disambiguate its suffix, never its digits/sign."""
    value = _percent_surface(new)
    if value is None:
        return False
    sign, amount, unit = value
    current = ''.join(old.split()).replace('−', '-')
    if current.startswith(('+', '-')) and current[0] != sign:
        return False
    current = current.lstrip('+-')
    return current == amount + unit or (unit == '%' and any(
        current == amount + suffix for suffix in ('9', '8', '96', '86')))


def recover_numeric_readings(reader, image, candidates, *, page_index, language,
                             workers=0, record_call=None, propagate_error=None,
                             maximum_fields=8, allowlist=None, blocklist=None):
    """Verify compact ambiguous numeric fields with two detector-free reads.

    Only complete readings agreed by both crops are admitted. Currency digits,
    cents and explicit opposite signs are conserved. Percent recovery requires
    the same literal amount; no arithmetic or expected precision is used.
    Work is bounded per authorized raster, and coordinates stay in that raster.
    """
    import cv2
    import numpy as np
    from structured_pdf_text.document import OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox

    observations = [(label, token) for label, tokens in candidates for token in tokens]
    page_percent = any('%' in token.text for _, token in observations)
    page_real = (language or '').casefold().startswith('pt') and any(
        (value := _currency_surface(token.text)) is not None and value[0] for _, token in observations)
    array = np.asarray(image)
    attempted, additions = [], [[], []]
    for _, anchor in observations:
        text = anchor.text.strip()
        if len(attempted) >= maximum_fields:
            break
        money = _currency_surface(text) if (language or '').casefold().startswith('pt') else None
        if money is None and page_real:
            money = _ambiguous_real_marker(text)
        percent = page_percent and bool(re.fullmatch(r'[+−\-]?\d+[.,]\d{1,6}[%‰]?', ''.join(text.split())))
        if (money is None and not percent) or anchor.rotation % 360 not in (0, 90, 180, 270):
            continue
        permitted = set('R$+-.,0123456789 ')
        if allowlist is not None:
            permitted.intersection_update(allowlist)
        permitted.difference_update(blocklist or '')
        if money is not None and not {'R', '$'}.issubset(permitted):
            continue
        if percent and (('%' in (blocklist or '')) or (allowlist is not None and '%' not in allowlist)):
            continue
        if any(anchor.rotation == prior.rotation and anchor.bbox.iou(prior.bbox) >= .60 for prior in attempted):
            continue
        aligned = [token for _, token in observations if token.rotation % 360 == anchor.rotation % 360
                   and anchor.bbox.iou(token.bbox) >= .60]
        # Already unambiguous fields need no extra recognition. R$ evidence
        # remains preferable to a constrained reread of its abbreviation.
        if money is not None and any((value := _currency_surface(t.text)) is not None and value[0]
                                     and value[1:] == money[1:] and (t.confidence or 0.) >= .80 for t in aligned):
            continue
        if percent and len({t.text.strip() for t in aligned}) < 2 and _percent_surface(text):
            continue
        x0, y0 = max(0, math.floor(anchor.bbox.x0)), max(0, math.floor(anchor.bbox.y0))
        x1, y1 = min(array.shape[1], math.ceil(anchor.bbox.x1)), min(array.shape[0], math.ceil(anchor.bbox.y1))
        if x1 <= x0 or y1 <= y0:
            continue
        crop = array[y0:y1, x0:x1]
        correction = (-anchor.rotation) % 360
        if correction:
            crop = np.ascontiguousarray(np.rot90(crop, -correction // 90))
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
        left, top, right, bottom = direct_content_box(gray)
        gray = gray[top:bottom, left:right]
        if gray.size == 0:
            continue
        attempted.append(anchor)
        reads = []
        for fraction in (.35, .70):
            pad = max(4, round(gray.shape[0] * fraction))
            padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
            try:
                if record_call is not None:
                    record_call(None)
                raw = reader.recognize(padded, [[0, padded.shape[1], 0, padded.shape[0]]], [],
                    decoder='beamsearch', beamWidth=10, workers=workers, detail=1, paragraph=False,
                    reformat=False, allowlist=''.join(sorted(permitted)) if money is not None else allowlist,
                    blocklist=blocklist)
            except Exception as exc:
                if propagate_error is not None:
                    propagate_error(exc)
                reads.append(None)
                continue
            try:
                _, observed, confidence = raw[0] if len(raw) == 1 else (None, '', 0.)
                confidence = float(confidence)
            except (TypeError, ValueError, IndexError):
                reads.append(None)
                continue
            valid = math.isfinite(confidence) and .80 <= confidence <= 1.
            if money is not None:
                value = _currency_surface(str(observed))
                valid = valid and value is not None and value[0] and _amount_surface_key(value[2]) is not None
                valid = valid and _amount_surface_key(value[2]) == _amount_surface_key(money[2])
                if valid:
                    valid = not any((opposite := _context_money_surface(t.text, 'R$')) is not None
                        and (opposite[1] != value[1] or not re.search(r'[+−-]', str(observed)))
                        and re.search(r'[+−-]', t.text)
                        and (t.confidence or 0.) >= .60 for t in aligned)
            else:
                valid = valid and any(_same_percent_amount(t.text, str(observed)) for t in aligned)
                new_surface = ''.join(str(observed).split()).replace('−', '-')
                valid = valid and not any(
                    old.startswith(('+', '-')) and not new_surface.startswith(old[0])
                    for t in aligned if (old := ''.join(t.text.split()).replace('−', '-')))
            reads.append((str(observed), confidence) if valid else None)
        if len(reads) != 2 or any(read is None for read in reads):
            continue
        if ''.join(reads[0][0].split()) != ''.join(reads[1][0].split()):
            continue
        for index, (observed, confidence) in enumerate(reads):
            additions[index].append(OcrToken(observed, BBox(x0, y0, x1, y1), confidence, language,
                SourceKind.OCR_PAGE, rotation=anchor.rotation, provenance=f'candidate:numeric_direct_{index}'))
    return candidates + [(f'numeric_direct_{i}', tokens) for i, tokens in enumerate(additions) if tokens]


def prefer_verified_numeric(evidence, candidates):
    """Use two matching direct observations over an ambiguous numeric suffix."""
    from collections import Counter
    from dataclasses import replace
    from structured_pdf_text.document import OcrProvenance

    direct = [(candidate.candidate_id, token) for candidate in candidates
              if candidate.candidate_id in ('numeric_direct_0', 'numeric_direct_1')
              for token in candidate.tokens]
    tokens, adopted = list(evidence.tokens), Counter()
    for index, current in enumerate(tokens):
        matches = [(label, token) for label, token in direct
                   if token.rotation % 360 == current.rotation % 360
                   and token.bbox.iou(current.bbox) >= .60
                   and (token.confidence or 0.) >= .80]
        for label, token in matches:
            if len({name for name, other in matches if ''.join(other.text.split()) == ''.join(token.text.split())}) < 2:
                continue
            old = _currency_surface(current.text) or _ambiguous_real_marker(current.text)
            new = _currency_surface(token.text)
            compatible_money = (old is not None and new is not None
                and _amount_surface_key(old[2]) is not None
                and _amount_surface_key(old[2]) == _amount_surface_key(new[2])
                and (not re.search(r'[+−-]', current.text)
                     or (old[1] == new[1] and re.search(r'[+−-]', token.text))))
            if not compatible_money and not _same_percent_amount(current.text, token.text):
                continue
            if current.text == token.text:
                break
            tokens[index] = replace(token, ocr_provenance=OcrProvenance(
                'easyocr', candidate_id=label, decoder='beamsearch', refinement_kind='verified_numeric'))
            adopted[label] += 1
            break
    if not adopted:
        return evidence, adopted
    counts = Counter(token.ocr_provenance.candidate_id for token in tokens if token.ocr_provenance is not None)
    return replace(evidence, tokens=tuple(tokens), contribution_counts=dict(counts)), adopted


def recover_signed_currency(reader, image, candidates, *, page_index, language,
                            workers=0, record_call=None, propagate_error=None):
    """Ask the recognizer about an observed ambiguity; never compose a value.

    Two negative observations and a reliable R$ observation must agree on
    digits and geometry. A constrained direct read must itself return the
    complete signed R$ amount with confidence >= .80. No ground truth, sums,
    dictionaries, or fixed page/value corrections participate.
    """
    import cv2
    import numpy as np
    from structured_pdf_text.document import OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox

    observations = [(label, token, _currency_surface(token.text))
                    for label, tokens in candidates for token in tokens]
    negative = [(name, token, money) for name, token, money in observations
                if money is not None and money[1] == '-' and (token.confidence or 0.) >= .60]
    array = np.asarray(image)
    recovered, attempted = [], []
    for name, anchor, amount in negative:
        if len(attempted) >= 4:
            break
        if _amount_surface_key(amount[2]) is None:
            continue
        if any(anchor.bbox.iou(previous.bbox) >= .70 for previous in attempted):
            continue
        aligned = [(label, token, value) for label, token, value in observations
                   if value is not None and value[2] == amount[2]
                   and token.rotation % 360 == anchor.rotation % 360
                   and anchor.bbox.iou(token.bbox) >= .70]
        if len({label for label, token, value in aligned
                if value[1] == '-' and (token.confidence or 0.) >= .60}) < 2:
            continue
        if not any(value[0] and (token.confidence or 0.) >= .75 for _, token, value in aligned):
            continue
        if any(value[1] == '+' and (token.confidence or 0.) >= .60
               and re.match(r'(?:\+[Rr][$sS]|[Rr][$sS]\+)', ''.join(token.text.split()))
               for _, token, value in aligned):
            continue  # Explicit opposite signs remain unresolved evidence.
        if any(value[0] and value[1] == '-' and (token.confidence or 0.) >= .60
               for _, token, value in aligned):
            continue  # The ordinary signed-consensus path already has this evidence.
        attempted.append(anchor)
        x0, y0 = max(0, math.floor(anchor.bbox.x0)-3), max(0, math.floor(anchor.bbox.y0)-3)
        x1, y1 = min(array.shape[1], math.ceil(anchor.bbox.x1)+3), min(array.shape[0], math.ceil(anchor.bbox.y1)+3)
        if x1 <= x0 or y1 <= y0:
            continue
        crop = array[y0:y1, x0:x1]
        correction = (-anchor.rotation) % 360
        if correction not in (0, 90, 180, 270):
            continue
        if correction:
            crop = np.ascontiguousarray(np.rot90(crop, -correction // 90))
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
        gray = cv2.copyMakeBorder(gray, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)
        try:
            if record_call is not None:
                record_call(None)
            raw = reader.recognize(gray, [[0, gray.shape[1], 0, gray.shape[0]]], [],
                decoder='beamsearch', beamWidth=10, allowlist='R$+-.,0123456789 ',
                workers=workers, detail=1, paragraph=False, reformat=False)
        except Exception as exc:
            if propagate_error is not None:
                propagate_error(exc)
            continue
        if len(raw or []) != 1 or len(raw[0]) < 3:
            continue
        _, text, confidence = raw[0]
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        money = _currency_surface(str(text))
        if (money is None or not money[0] or money[1] != '-' or
                _amount_surface_key(money[2]) != _amount_surface_key(amount[2]) or
                not re.fullmatch(r'(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}', money[2]) or
                not math.isfinite(float(confidence)) or not .80 <= float(confidence) <= 1.):
            continue
        recovered.append(OcrToken(str(text), BBox(x0, y0, x1, y1), float(confidence), language,
                                  SourceKind.OCR_PAGE, rotation=anchor.rotation,
                                  provenance='candidate:currency_direct'))
    return candidates + [('currency_direct', recovered)] if recovered else candidates
