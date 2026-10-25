"""Harmonise OCR decimal separators and currency marks inside a table.

A table writes its numbers in one convention. When OCR reads some decimal
commas of a table as dots ("39,25" next to "42.25"), the comma decimals the
table also contains are the evidence: a dot that can only be a decimal
separator in that table is a misread comma. Only OCR text is touched, only
in tables with an unambiguous comma decimal and no unambiguous dot decimal
(``1,234.50``), and only cells that are values (a number with its unit,
sign, currency or percent). Dates, versions and three-digit groups
(``1.200``, a thousands separator) are never changed.

The same consistency holds for the real sign: when other cells of the table
write ``R$ `` followed by a space, OCR's ``R$-350,00`` (space lost) and
``RS-0,01`` (``$`` read as ``S``) are restored to that form.
"""
from __future__ import annotations

import re

from structured_pdf_text.document import SourceKind, StructuredTable, TableMethod

_NATIVE_SOURCES = {SourceKind.NATIVE_PDF, SourceKind.NATIVE_GENERATED, SourceKind.TABLE_NATIVE}
_NUMBER = re.compile(r"(?<![\w.,])\d[\d.,]*\d(?![\w,]|\.\d)|(?<![\w.,])\d(?![\w.,])")
_COMMA_DECIMAL = re.compile(r"(?<![\w.,])\d{1,3}(?:\.\d{3})*,(?:\d{1,2}|\d{4,})(?![\w.,])")
_DOT_DECIMAL = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+\.\d+(?![\w.,])")
_VALUE_LETTERS = 4  # unit/currency letters a value cell may carry: kg, mm, R$, °C, µg
_SPACED_REAL = re.compile(r"(?<![\w$])R\$ [+\-−]?\d")
_GLUED_REAL = re.compile(r"(?<![\w$])R[$S](?=[+\-−]?\d)")


def harmonize_value_surfaces(table: StructuredTable) -> bool:
    """Restore misread value surfaces of ``table`` in place; True if any changed.

    Cells and tokens are edited in place: they keep their identity, so the
    evidence that owns them stays accounted for. Run after the last stage
    that rebuilds cell text.
    """
    if table.method != TableMethod.VISUAL_MODEL or not table.cells:
        return False
    texts = [cell.text for cell in table.cells]
    decimals = any(_COMMA_DECIMAL.search(text) for text in texts) and not any(_DOT_DECIMAL.search(text) for text in texts)
    spaced_real = any(_SPACED_REAL.search(text) for text in texts)
    if not decimals and not spaced_real:
        return False

    def restore(text):
        if decimals:
            text = _NUMBER.sub(_comma_decimal, text)
        if spaced_real:
            text = _GLUED_REAL.sub("R$ ", text)
        return text

    changed = False
    for cell in table.cells:
        if not _is_value(cell.text) or any(source.source in _NATIVE_SOURCES for token in cell.tokens
                                           for source in getattr(token, "sources", ()) or ()):
            continue
        for token in cell.tokens:
            text = restore(token.text)
            if text != token.text:
                token.text, token.normalized_text = text, restore(token.normalized_text)
                token.transformation_type = "table_value_surface"
                token.decision_reason = "restored to the table's decimal comma / currency form"
        text = restore(cell.text)
        if text != cell.text:
            cell.text, changed = text, True
    return changed


def _is_value(text: str) -> bool:
    if not _NUMBER.search(text):
        return False
    rest = _NUMBER.sub(" ", text)
    return sum(ch.isalpha() for ch in rest) <= _VALUE_LETTERS


def _comma_decimal(match: re.Match) -> str:
    number = match.group()
    if "," in number or "." not in number:
        return number
    groups = number.split(".")
    if len(groups) == 2:
        return number if len(groups[1]) == 3 else f"{groups[0]},{groups[1]}"
    if 1 <= len(groups[0]) <= 3 and all(len(group) == 3 for group in groups[1:-1]) and 1 <= len(groups[-1]) <= 2:
        return ".".join(groups[:-1]) + "," + groups[-1]
    return number  # A date, version or code.
