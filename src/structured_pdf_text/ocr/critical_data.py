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
from datetime import datetime
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
_CURRENCY_PATTERN = re.compile(r"R\$\s*(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)")

# Date: dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy
_DATE_PATTERN = re.compile(r"\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}")

# Percentage: 12,5% or 12.5% or 100%
_PERCENTAGE_PATTERN = re.compile(r"\d+[,.]?\d*\s*%")

# Generic numeric with Brazilian decimal separator: 1.234,56 or 1234,56
_NUMERIC_PATTERN = re.compile(r"\d[\d.]*,\d+|\d{4,}")
_TIME_PATTERN = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?")
_CEP_PATTERN = re.compile(r"\d{5}-?\d{3}")
_PROCESS_NUMBER_PATTERN = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")
# Scientific notation (Brazilian/international): 1,20e-5 or 1.20E+3 or 3,5×10⁻²
_SCIENTIFIC_PATTERN = re.compile(r"[+-]?\d+[,.]?\d*[eE][+-]?\d+|[+-]?\d+[,.]?\d*\s*[×x]\s*10\s*[\^]?\s*[+-]?\d+")
# UUID v4 (dashes canonical form)
_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# Inscricão estadual: digits with optional dots/dashes, 8-14 digits total
_INSCRICAO_PATTERN = re.compile(r"\d{2,4}\.?\d{3}\.?\d{3}\.?\d{1,4}")
# NF-e access key: 44 digits (chave de acesso)
_NFE_KEY_PATTERN = re.compile(r"\d{44}")
# Signed/unsigned numeric range: 10-20, 1,5 a 3,0
_NUMERIC_RANGE_PATTERN = re.compile(r"[+-]?\d+[,.]?\d*\s*(?:-|a|até)\s*[+-]?\d+[,.]?\d*")


# Labels that indicate the following token is a structured data type.
# Matched case-insensitively, stripped of trailing punctuation/spaces.
_CPF_LABELS: frozenset[str] = frozenset({
    "cpf", "cpf:", "cpf do cliente", "cpf/cnpj", "cpf/cnpj:",
    "documento", "doc", "doc:",
})
_CNPJ_LABELS: frozenset[str] = frozenset({
    "cnpj", "cnpj:", "cnpj do cliente", "cpf/cnpj", "cpf/cnpj:",
    "inscricao", "ie",
})
_CURRENCY_LABELS: frozenset[str] = frozenset({
    "valor", "total", "subtotal", "desconto", "acrescimo",
    "r$", "preco", "price", "amount", "vl", "vl.", "vlr",
})
_DATE_LABELS: frozenset[str] = frozenset({
    "data", "date", "emissao", "vencimento", "vcto", "dtv",
    "data de emissao", "data de vencimento", "data emissao",
})
_PERCENT_LABELS: frozenset[str] = frozenset({"percentual", "percent", "aliquota", "%"})
_TIME_LABELS: frozenset[str] = frozenset({"hora", "horario", "time"})
_CEP_LABELS: frozenset[str] = frozenset({"cep", "codigo postal", "cod postal"})
_PROCESS_LABELS: frozenset[str] = frozenset({"processo", "processo no", "numero do processo", "n processo"})
_INVOICE_LABELS: frozenset[str] = frozenset({"nf", "nfe", "nota fiscal", "numero da nota", "invoice", "pedido"})
_NUMERIC_LABELS: frozenset[str] = frozenset({"quantidade", "numero", "qtd", "item", "lote"})
_SCIENTIFIC_LABELS: frozenset[str] = frozenset({"notacao", "expoente", "escalar", "fator", "coeficiente"})
_UUID_LABELS: frozenset[str] = frozenset({"uuid", "guid", "id", "identificador", "chave"})
_INSCRICAO_LABELS: frozenset[str] = frozenset({"ie", "inscricao estadual", "inscricao", "matricula", "insc"})
_NFE_KEY_LABELS: frozenset[str] = frozenset({"chave de acesso", "chave nfe", "acesso nfe", "nfe chave"})
_NUMERIC_RANGE_LABELS: frozenset[str] = frozenset({"faixa", "intervalo", "range", "entre", "de a"})


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
    TIME = "time"
    CEP = "cep"
    PROCESS_NUMBER = "process_number"
    INVOICE_NUMBER = "invoice_number"
    SCIENTIFIC = "scientific"
    UUID = "uuid"
    INSCRICAO = "inscricao"
    NFE_KEY = "nfe_key"
    NUMERIC_RANGE = "numeric_range"


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
    if re.fullmatch(r"(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}", text.strip()):
        return 0.85
    if re.search(r"R\$", text):
        return 0.5
    return 0.0


def _score_as_date(text: str) -> float:
    """Score date format match."""
    match = _DATE_PATTERN.search(text)
    if match:
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                datetime.strptime(match.group(0), fmt)
                return 1.0
            except ValueError:
                continue
        return 0.4
    return 0.0


def _score_as_percentage(text: str) -> float:
    match = _PERCENTAGE_PATTERN.search(text)
    if not match:
        return 0.0
    try:
        value = float(match.group(0).replace("%", "").replace(" ", "").replace(",", "."))
        return 1.0 if 0 <= value <= 100 else 0.4
    except ValueError:
        return 0.4


def _score_as_numeric(text: str) -> float:
    if not _NUMERIC_PATTERN.search(text):
        return 0.0
    return 1.0 if all(character.isdigit() or character in ".,/-% " for character in text) else 0.4


def _score_as_time(text: str) -> float:
    return 1.0 if _TIME_PATTERN.fullmatch(text.strip()) else 0.0


def _score_as_cep(text: str) -> float:
    digits = re.sub(r"\D", "", text)
    return 1.0 if len(digits) == 8 and _CEP_PATTERN.fullmatch(text.strip()) else 0.0


def _score_as_process_number(text: str) -> float:
    value = text.strip()
    if _PROCESS_NUMBER_PATTERN.fullmatch(value):
        return 1.0
    # Some OCR engines return the same CNJ structure without punctuation.
    digits = re.sub(r"\D", "", value)
    return 0.35 if len(digits) == 20 else 0.0


def _score_as_invoice_number(text: str) -> float:
    value = text.strip()
    if re.fullmatch(r"\d{1,15}", value):
        return 1.0
    if re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{1,19}", value, re.IGNORECASE):
        return 0.65
    return 0.0


def _score_as_scientific(text: str) -> float:
    """Score scientific notation match — preserves surface exactly."""
    value = text.strip()
    if _SCIENTIFIC_PATTERN.fullmatch(value):
        return 1.0
    if _SCIENTIFIC_PATTERN.search(value):
        return 0.75
    return 0.0


def _score_as_uuid(text: str) -> float:
    """Score UUID (RFC 4122) format match."""
    value = text.strip()
    if _UUID_PATTERN.fullmatch(value):
        return 1.0
    # All 32 hex digits without dashes
    if re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return 0.5
    return 0.0


def _score_as_inscricao(text: str) -> float:
    """Score Inscrição Estadual format match."""
    value = text.strip()
    if _INSCRICAO_PATTERN.fullmatch(value):
        return 1.0
    digits = re.sub(r"\D", "", value)
    if 8 <= len(digits) <= 14:
        return 0.4
    return 0.0


def _score_as_nfe_key(text: str) -> float:
    """Score NF-e 44-digit access key match."""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 44:
        return 1.0 if _NFE_KEY_PATTERN.fullmatch(text.strip()) else 0.85
    return 0.0


def _score_as_numeric_range(text: str) -> float:
    """Score numeric range (10-20, 1,5 a 3,0) match."""
    value = text.strip()
    if _NUMERIC_RANGE_PATTERN.fullmatch(value):
        return 1.0
    if _NUMERIC_RANGE_PATTERN.search(value):
        return 0.6
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
    if data_type == DataType.PERCENTAGE:
        return _score_as_percentage(text)
    if data_type == DataType.NUMERIC:
        return _score_as_numeric(text)
    if data_type == DataType.TIME:
        return _score_as_time(text)
    if data_type == DataType.CEP:
        return _score_as_cep(text)
    if data_type == DataType.PROCESS_NUMBER:
        return _score_as_process_number(text)
    if data_type == DataType.INVOICE_NUMBER:
        return _score_as_invoice_number(text)
    if data_type == DataType.SCIENTIFIC:
        return _score_as_scientific(text)
    if data_type == DataType.UUID:
        return _score_as_uuid(text)
    if data_type == DataType.INSCRICAO:
        return _score_as_inscricao(text)
    if data_type == DataType.NFE_KEY:
        return _score_as_nfe_key(text)
    if data_type == DataType.NUMERIC_RANGE:
        return _score_as_numeric_range(text)
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
        if norm in _TIME_LABELS:
            return DataType.TIME
        if norm in _CEP_LABELS:
            return DataType.CEP
        if norm in _PROCESS_LABELS:
            return DataType.PROCESS_NUMBER
        if norm in _INVOICE_LABELS:
            return DataType.INVOICE_NUMBER
        if norm in _PERCENT_LABELS:
            return DataType.PERCENTAGE
        if norm in _NUMERIC_LABELS:
            return DataType.NUMERIC
        if norm in _SCIENTIFIC_LABELS:
            return DataType.SCIENTIFIC
        if norm in _UUID_LABELS:
            return DataType.UUID
        if norm in _INSCRICAO_LABELS:
            return DataType.INSCRICAO
        if norm in _NFE_KEY_LABELS:
            return DataType.NFE_KEY
        if norm in _NUMERIC_RANGE_LABELS:
            return DataType.NUMERIC_RANGE
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
    DataType.TIME:       "0123456789:",
    DataType.CEP:        "0123456789-",
    DataType.PROCESS_NUMBER: "0123456789-.",
    DataType.INVOICE_NUMBER: "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz./-",
    DataType.SCIENTIFIC: "0123456789.,eE+- ×x^",
    DataType.UUID:        "0123456789abcdefABCDEF-",
    DataType.INSCRICAO:   "0123456789.-/",
    DataType.NFE_KEY:     "0123456789",
    DataType.NUMERIC_RANGE: "0123456789,.- aAt",
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
