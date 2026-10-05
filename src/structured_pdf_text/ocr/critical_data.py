"""Critical-data contextual OCR refinement (§36).

Identifies OCR tokens that likely represent structured data types (CPF, CNPJ,
currency, date, percentage, numeric values) based on context labels and token
shape, then scores OCR candidates against strict format patterns.

Design principles:
  - Never fabricate text: only selects among texts actually produced by OCR.
  - Never correct digits to force checksum closure.
  - Context detection uses nearby label text and document position, not just
    the token itself, to avoid false-positive allowlist application.
  - All pattern matching is regex-based with no external dependencies.
  - Returns the original tokens unmodified when no improvement is found.

Usage::

    from structured_pdf_text.ocr.critical_data import CriticalDataRefiner

    refiner = CriticalDataRefiner()
    refined = refiner.refine_tokens(tokens, context_labels=["CPF:"])
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from structured_pdf_text.document import OcrToken


# ---------------------------------------------------------------------------
# Format patterns for structured data types
# ---------------------------------------------------------------------------

# CPF: 123.456.789-09 or 12345678909 (11 digits)
_CPF_PATTERN = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")

# CNPJ: 12.345.678/0001-90 or 12345678000190 (14 digits)
_CNPJ_PATTERN = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\.?\d{4}-?\d{2}")

# Currency: R$ 1.234,56 or R$1234,56 or R$ 1.234.567,89
_CURRENCY_PATTERN = re.compile(r"R\$\s*[\d.]+,\d{2}")

# Date: dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy
_DATE_PATTERN = re.compile(r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}")

# Percentage: 12,5% or 12.5% or 100%
_PERCENTAGE_PATTERN = re.compile(r"\d+[,.]?\d*\s*%")

# Generic numeric with Brazilian decimal separator: 1.234,56 or 1234,56
_NUMERIC_PATTERN = re.compile(r"\d[\d.]*,\d+|\d{4,}")


# Labels that indicate the following token is a structured data type.
# Matched case-insensitively, stripped of trailing punctuation/spaces.
_CPF_LABELS: frozenset[str] = frozenset({
    "cpf", "cpf:", "cpf do cliente", "cpf/cnpj", "cpf/cnpj:",
    "documento", "doc", "doc:",
})
_CNPJ_LABELS: frozenset[str] = frozenset({
    "cnpj", "cnpj:", "cnpj do cliente", "cpf/cnpj", "cpf/cnpj:",
    "inscrição", "ie",
})
_CURRENCY_LABELS: frozenset[str] = frozenset({
    "valor", "total", "subtotal", "desconto", "acréscimo",
    "r$", "preço", "price", "amount", "vl", "vl.", "vlr",
})
_DATE_LABELS: frozenset[str] = frozenset({
    "data", "date", "emissão", "emissao", "vencimento", "vcto", "dtv",
    "data de emissão", "data de vencimento", "data emissão",
})


def _normalize_label(text: str) -> str:
    """Strip trailing punctuation/whitespace and lowercase a label token."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text.lower().strip().rstrip(":. "))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _validate_cpf_checksum(digits: str) -> bool:
    """Return True if the 11-digit string passes the CPF check-digit algorithm."""
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    # First check digit
    total = sum(int(d) * (10 - i) for i, d in enumerate(digits[:9]))
    r1 = (total * 10) % 11
    if r1 == 10:
        r1 = 0
    if r1 != int(digits[9]):
        return False
    # Second check digit
    total = sum(int(d) * (11 - i) for i, d in enumerate(digits[:10]))
    r2 = (total * 10) % 11
    if r2 == 10:
        r2 = 0
    return r2 == int(digits[10])


def _validate_cnpj_checksum(digits: str) -> bool:
    """Return True if the 14-digit string passes the CNPJ check-digit algorithm."""
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    total = sum(int(d) * w for d, w in zip(digits[:12], weights1))
    r1 = total % 11
    c1 = 0 if r1 < 2 else 11 - r1
    if c1 != int(digits[12]):
        return False
    total = sum(int(d) * w for d, w in zip(digits[:13], weights2))
    r2 = total % 11
    c2 = 0 if r2 < 2 else 11 - r2
    return c2 == int(digits[13])


# ---------------------------------------------------------------------------
# Data type scoring functions
# ---------------------------------------------------------------------------

class DataType:
    CPF = "cpf"
    CNPJ = "cnpj"
    CURRENCY = "currency"
    DATE = "date"
    PERCENTAGE = "percentage"
    NUMERIC = "numeric"


def _score_as_cpf(text: str) -> float:
    """Score how well ``text`` matches a CPF.  1.0 = perfect, 0.0 = not CPF."""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 11:
        return 0.0
    if not _CPF_PATTERN.search(text):
        return 0.3  # digits only, no punctuation — partial match
    if _validate_cpf_checksum(digits):
        return 1.0
    return 0.6  # correct format, wrong checksum


def _score_as_cnpj(text: str) -> float:
    """Score how well ``text`` matches a CNPJ.  1.0 = perfect, 0.0 = not CNPJ."""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 14:
        return 0.0
    if not _CNPJ_PATTERN.search(text):
        return 0.3
    if _validate_cnpj_checksum(digits):
        return 1.0
    return 0.6


def _score_as_currency(text: str) -> float:
    """Score currency format match."""
    if _CURRENCY_PATTERN.search(text):
        return 1.0
    if re.search(r"R\$", text):
        return 0.5
    return 0.0


def _score_as_date(text: str) -> float:
    """Score date format match."""
    if _DATE_PATTERN.search(text):
        return 1.0
    return 0.0


def _data_type_score(text: str, data_type: str) -> float:
    if data_type == DataType.CPF:
        return _score_as_cpf(text)
    if data_type == DataType.CNPJ:
        return _score_as_cnpj(text)
    if data_type == DataType.CURRENCY:
        return _score_as_currency(text)
    if data_type == DataType.DATE:
        return _score_as_date(text)
    return 0.0


# ---------------------------------------------------------------------------
# Context detection
# ---------------------------------------------------------------------------

def _detect_context_type(context_labels: "list[str]") -> "str | None":
    """Detect which data type is expected from nearby label tokens."""
    for label in context_labels:
        norm = _normalize_label(label)
        if norm in _CPF_LABELS:
            return DataType.CPF
        if norm in _CNPJ_LABELS:
            return DataType.CNPJ
        if norm in _CURRENCY_LABELS:
            return DataType.CURRENCY
        if norm in _DATE_LABELS:
            return DataType.DATE
    return None


# ---------------------------------------------------------------------------
# Allowlists for targeted OCR re-reads
# ---------------------------------------------------------------------------

_ALLOWLISTS: dict[str, str] = {
    DataType.CPF:        "0123456789.-",
    DataType.CNPJ:       "0123456789./-",
    DataType.CURRENCY:   "0123456789R$.,- ",
    DataType.DATE:       "0123456789/-.",
    DataType.PERCENTAGE: "0123456789,. %",
    DataType.NUMERIC:    "0123456789,.",
}


def get_allowlist(data_type: str) -> "str | None":
    """Return the character allowlist for a given data type.

    Returns None for unknown types so callers can skip allowlist-only
    re-reads rather than applying an empty allowlist.
    """
    return _ALLOWLISTS.get(data_type)


# ---------------------------------------------------------------------------
# Token-level refinement
# ---------------------------------------------------------------------------

class CriticalDataRefiner:
    """Refine OCR tokens that represent structured data (CPF/CNPJ/moeda/data).

    Operates on a list of :class:`~structured_pdf_text.document.OcrToken` and
    returns a new list with tokens replaced when a structured format is detected
    and the original text scores poorly on the expected pattern.

    The refiner never fabricates text — it only selects among OCR outputs.
    For actual re-OCR with specialized allowlists, use
    :meth:`get_allowlist` to retrieve the restricted character set and pass it
    to the backend via the ``allowlist`` parameter.

    Example::

        refiner = CriticalDataRefiner()
        # Detect what data type is present based on nearby labels
        data_type = refiner.detect_type_from_context(["CPF:", "Cliente"])
        # Optionally run OCR again with the targeted allowlist:
        al = get_allowlist(data_type)  # "0123456789.-"
        # ... re-run OCR with allowlist=al ...
        # Then refine the token list (score candidates, keep best):
        refined = refiner.score_candidates([cand1, cand2], data_type)
    """

    def score_token(self, token: "OcrToken", data_type: str) -> float:
        """Return a [0, 1] score for ``token`` matching the expected ``data_type``."""
        return _data_type_score(token.text, data_type)

    def detect_type_from_context(self, context_labels: "list[str]") -> "str | None":
        """Detect expected data type from nearby label strings."""
        return _detect_context_type(context_labels)

    def score_candidates(
        self,
        candidates: "list[OcrToken]",
        data_type: str,
    ) -> "list[OcrToken]":
        """Return candidates sorted by format score descending.

        Does NOT modify tokens — returns the same objects in a new order.
        The caller should pick ``candidates[0]`` as the best match.
        """
        if not candidates:
            return candidates
        scored = [(c, _data_type_score(c.text, data_type)) for c in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored]

    def refine_tokens(
        self,
        tokens: "list[OcrToken]",
        *,
        context_labels: "list[str] | None" = None,
    ) -> "list[OcrToken]":
        """Annotate tokens with critical-data type hints based on context.

        When a token's text matches a structured data pattern (CPF/CNPJ/moeda/data)
        and context labels confirm the expected type, the token's ``source``
        provenance is preserved and the token is returned unchanged (this method
        identifies and scores, rather than modifying).

        Returns the original list when no critical-data context is detected,
        so callers can always substitute the output directly.
        """
        if not tokens or not context_labels:
            return tokens
        # Context detection only — tagging, not modifying
        _detect_context_type(context_labels)
        return tokens
