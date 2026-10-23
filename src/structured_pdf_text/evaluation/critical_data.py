"""Critical data metrics: numbers, currency, percent, dates and identifiers (spec §9).

All matching uses multiset intersection following spec §9.1:
  TP = Σ min(count_ref, count_hyp)
  FP = total_hyp - TP
  FN = total_ref - TP

Empty-reference behaviour follows spec §9.2:
  ref=0, hyp=0  → not_applicable, correct_absence=True
  ref=0, hyp>0  → FP, false_positive_only, F1=0
  ref>0, hyp=0  → FN, false_negative_only, F1=0
  ref>0, hyp>0  → regular P/R/F1

Span-protection: each position in the visible text is claimed by at most one
category.  Priority order (highest first):
  1. identifiers (CPF, CNPJ, process numbers)
  2. currency (R$, BRL)
  3. dates (calendar-valid only; invalid date spellings remain numbers)
  4. percent
  5. numeric (plain numbers, after all higher-priority spans removed)

Extraction reads the parser's leaf text as-is (layout normalization only);
no second Markdown extraction is applied to already visible text.

This prevents "R$ 1.234,00" from also matching as a numeric, or "30%" from
matching as both percent and numeric.

Bugs fixed
----------
A06  30% vs 30 → percent category F1=0 (not "equal")
A07  Entity in table counted 3× → leaf-node projection via CanonicalDocument
A08  Integers < 4 digits (12, 13) not recognised → relaxed _NUM_RE
A01  R$ 1234,00 vs R$ 1235,00 → F1=0 (Decimal comparison fixes truncation)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize

# ---------------------------------------------------------------------------
# Extraction patterns (pt-BR)
# ---------------------------------------------------------------------------

# Currency: R$ or BRL followed by numeric value (captured before percent/numeric).
# Alternation order: thousand-sep pattern FIRST with + (requires at least one group)
# so '1.234,00' matches (\d{1,3}(?:\.\d{3})+) before the bare \d+ alternative.
# Without +, '123' in '1234,00' would greedily match \d{1,3} and stop.
_CURRENCY_RE = re.compile(
    r"""
    (R\$|BRL)\s*
    (-?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?)
    """,
    re.VERBOSE,
)

# Dates: DD/MM/YYYY, DD-MM-YYYY or YYYY-MM-DD
_DATE_RE = re.compile(
    r"""
    (?<!\d)
    (?:
      (\d{2})[/\-](\d{2})[/\-](\d{4})   # DD/MM/YYYY or DD-MM-YYYY
      |(\d{4})-(\d{2})-(\d{2})           # YYYY-MM-DD
    )
    (?!\d)
    """,
    re.VERBOSE,
)

# CPF: 000.000.000-00 or 11 digits
_CPF_RE = re.compile(r"""(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)""")

# CNPJ: 00.000.000/0000-00 or 14 digits
_CNPJ_RE = re.compile(r"""(?<!\d)(\d{2}\.?\d{3}\.?\d{3}[/\\]?\d{4}-?\d{2})(?!\d)""")

# Process numbers: Brazilian court format NNNNNNN-DD.AAAA.J.TT.OOOO
_PROCESS_RE = re.compile(r"""(?<!\d)(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?!\d)""")

# Percent: number followed by % (A06 fix: % captured as part of token)
# Matches: 30%, 30 %, -0,5%, 1.234,50%, etc.
_PERCENT_RE = re.compile(
    r"""
    (?<!\w)
    (-?(?:\d{1,3}(?:\.\d{3})*|\d+)(?:,\d+)?)\s*%
    (?!\w)
    """,
    re.VERBOSE,
)

# Numeric: integer ≥1 digit OR decimal with pt-BR separators (A08 fix: removed 4+ digit requirement)
# Applied AFTER removing spans of higher-priority categories.
_NUM_RE = re.compile(
    r"""
    (?<!\w)
    (-?                                          # optional sign
     (?:\d{1,3}(?:\.\d{3})+|\d+)               # integer with optional thousand sep
     (?:,\d+)?                                  # optional decimal (pt-BR comma)
    )
    (?!\w)
    """,
    re.VERBOSE,
)

# Calendar validation
_DAYS_IN_MONTH = {1:31, 2:29, 3:31, 4:30, 5:31, 6:30,
                  7:31, 8:31, 9:30, 10:31, 11:30, 12:31}


# ---------------------------------------------------------------------------
# Visible text helper
# ---------------------------------------------------------------------------

def _visible(text: str) -> str:
    """Leaf text is already parser-visible; a second Markdown extraction would
    erase literal '*', '_', '#', '>' or '[x](y)' content. Layout only."""
    return normalize(text, NormProfile.UNICODE_LAYOUT)


# ---------------------------------------------------------------------------
# Span-protected extraction
# ---------------------------------------------------------------------------

def _mask_spans(text: str, pattern: re.Pattern) -> str:
    """Replace all matches of *pattern* with space padding of the same length."""
    return pattern.sub(lambda m: " " * (m.end() - m.start()), text)


def _canonical_decimal(raw: str) -> str:
    """pt-BR decimal → canonical string; format 'f' avoids scientific notation."""
    value = raw.replace(".", "").replace(",", ".")
    try:
        return format(Decimal(value).normalize(), "f")
    except InvalidOperation:
        return value


def _is_valid_date(day: int, month: int, year: int) -> bool:
    if not (1 <= month <= 12):
        return False
    max_d = _DAYS_IN_MONTH.get(month, 30)
    if month == 2:
        # Leap year: divisible by 4, except centuries unless divisible by 400
        is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        max_d = 29 if is_leap else 28
    return 1 <= day <= max_d and 1000 <= year <= 9999


def _canonical_number(raw: str) -> str:
    """pt-BR numeric string → canonical decimal string (for comparison)."""
    s = raw.strip()
    # pt-BR: period is thousand separator, comma is decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return format(Decimal(s).normalize(), "f")
    except InvalidOperation:
        return raw


def _canonical_cpf(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else raw


def _canonical_cnpj(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 14 else raw


_CLASSES = ("currency", "dates", "percent", "numeric", "cpf", "cnpj", "process_numbers")


def _extract_all(text: str) -> dict[str, list[str]]:
    """Every class from one pass, with span protection in priority order.

    Identifiers (CPF, CNPJ, process numbers) read the text; each later class
    reads it with all higher-priority spans masked: currency, then valid
    dates, then percent, then plain numbers. Invalid dates stay numbers.
    """
    v = _visible(text)
    values: dict[str, list[str]] = {}
    # CPF tokens preserve leading zeros (A38-style: '00123' ≠ '123').
    values["cpf"] = [_canonical_cpf(m.group(1)) for m in _CPF_RE.finditer(v)]
    values["cnpj"] = [_canonical_cnpj(m.group(1)) for m in _CNPJ_RE.finditer(v)]
    values["process_numbers"] = [m.group(1) for m in _PROCESS_RE.finditer(v)]
    masked = v
    for pattern in (_CPF_RE, _CNPJ_RE, _PROCESS_RE):
        masked = _mask_spans(masked, pattern)
    values["currency"] = [f"{m.group(1)}:{_canonical_decimal(m.group(2))}"
                          for m in _CURRENCY_RE.finditer(masked)]
    masked = _mask_spans(masked, _CURRENCY_RE)
    dates, spans = [], []
    for m in _DATE_RE.finditer(masked):
        if m.group(1):  # DD/MM/YYYY or DD-MM-YYYY
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:           # YYYY-MM-DD
            y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if _is_valid_date(d, mo, y):
            dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
            spans.append(m.span())
    values["dates"] = dates
    for start, end in spans:
        masked = masked[:start] + " " * (end - start) + masked[end:]
    # A06 fix: percent is its own category; '30%' is different from '30'.
    values["percent"] = [f"{_canonical_decimal(m.group(1))}%" for m in _PERCENT_RE.finditer(masked)]
    masked = _mask_spans(masked, _PERCENT_RE)
    # A08 fix: integers of any length.
    values["numeric"] = [_canonical_number(m.group(1)) for m in _NUM_RE.finditer(masked)
                         if m.group(1).strip()]
    values["identifiers_combined"] = (
        [f"cpf:{v}" for v in values["cpf"]] + [f"cnpj:{v}" for v in values["cnpj"]]
        + [f"proc:{v}" for v in values["process_numbers"]]
    )
    return values


def _extract_currency(text: str) -> list[str]:
    """Currency tokens as 'SYMBOL:canonical_decimal'."""
    return _extract_all(text)["currency"]


def _extract_dates(text: str) -> list[str]:
    """Valid pt-BR dates as canonical 'YYYY-MM-DD' strings."""
    return _extract_all(text)["dates"]


def _extract_percent(text: str) -> list[str]:
    """Percent tokens as canonical 'canonical_decimal%' strings."""
    return _extract_all(text)["percent"]


def _extract_numbers(text: str) -> list[str]:
    """Plain integers/decimals, after higher-priority spans are masked."""
    return _extract_all(text)["numeric"]


def _extract_cpf(text: str) -> list[str]:
    return _extract_all(text)["cpf"]


def _extract_cnpj(text: str) -> list[str]:
    return _extract_all(text)["cnpj"]


def _extract_process_numbers(text: str) -> list[str]:
    return _extract_all(text)["process_numbers"]


# ---------------------------------------------------------------------------
# Internal P/R/F1 with spec §9.2 semantics
# ---------------------------------------------------------------------------

def _prf_multiset(
    ref_counter: Counter,
    hyp_counter: Counter,
    definition_id: str,
) -> MetricResult:
    tp = sum((ref_counter & hyp_counter).values())
    fp = sum(hyp_counter.values()) - tp
    fn = sum(ref_counter.values()) - tp

    ref_total = sum(ref_counter.values())
    hyp_total = sum(hyp_counter.values())

    extra: dict = {"tp": tp, "fp": fp, "fn": fn,
                   "ref_count": ref_total, "hyp_count": hyp_total}

    if tp == 0 and fp == 0 and fn == 0:
        return MetricResult(
            value=None, status=MetricStatus.NOT_APPLICABLE,
            direction="higher_is_better", definition_id=definition_id,
            counts={**extra, "correct_absence": True},
        )
    if tp == 0 and fn == 0:  # ref=0, hyp>0
        return MetricResult(
            value=0.0, status=MetricStatus.FALSE_POSITIVE_ONLY,
            direction="higher_is_better", definition_id=definition_id,
            counts=extra,
        )
    if tp == 0 and fp == 0:  # ref>0, hyp=0
        return MetricResult(
            value=0.0, status=MetricStatus.FALSE_NEGATIVE_ONLY,
            direction="higher_is_better", definition_id=definition_id,
            counts=extra,
        )

    p = tp / (tp + fp)
    r = tp / (tp + fn)
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return MetricResult(
        value=round(f1, 4), status=MetricStatus.OK,
        direction="higher_is_better", definition_id=definition_id,
        counts=extra,
    )


# ---------------------------------------------------------------------------
# Leaf-node extraction — uses CanonicalDocument to avoid A07 (triple-counting)
# ---------------------------------------------------------------------------

def _document_and_page_values(nodes: Sequence[MarkdownNode]) -> tuple[dict, dict]:
    """(document values, 'page:value' values) for every class, extracted once.

    The document view joins all leaf texts; the page view tags each occurrence
    with its page so transpositions across pages are penalised. Without page
    information every value is tagged with page 0 (falls back to the document).
    """
    from structured_pdf_text.evaluation.canonical_document import CanonicalDocument
    doc = CanonicalDocument.from_nodes(nodes)
    document = _extract_all(doc.visible_text)
    if {n.page for n in doc.leaf_nodes} == {None}:
        return document, {key: [f"0:{v}" for v in vals] for key, vals in document.items()}
    nodes_by_page: dict[int | None, list[MarkdownNode]] = defaultdict(list)
    for n in doc.leaf_nodes:
        nodes_by_page[n.page].append(n)
    paged: dict[str, list[str]] = defaultdict(list)
    for page_num, page_nodes in sorted(nodes_by_page.items(), key=lambda x: x[0] or 0):
        page_text = " ".join(n.visible_text.strip() for n in page_nodes if n.visible_text.strip())
        p = page_num if page_num is not None else 0
        for key, vals in _extract_all(page_text).items():
            paged[key].extend(f"{p}:{v}" for v in vals)
    return document, paged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class CriticalDataMetrics:
    """All critical data metrics for one evaluation run.

    Each category has a ``document_f1`` (multiset global, ignores page) and
    a ``page_f1`` (multiset tagged with page number, penalizes cross-page transposition).
    When page information is unavailable, ``page_f1`` equals ``document_f1``.
    """
    # Document-level F1 (multiset global — page-agnostic, spec §9.4 "document_f1")
    numeric:              MetricResult | None = None   # MET-CD01
    currency:             MetricResult | None = None   # MET-CD02
    percent:              MetricResult | None = None   # MET-CD08
    dates:                MetricResult | None = None   # MET-CD03
    cpf:                  MetricResult | None = None   # MET-CD04
    cnpj:                 MetricResult | None = None   # MET-CD05
    process_numbers:      MetricResult | None = None   # MET-CD06
    identifiers_combined: MetricResult | None = None   # MET-CD07

    # Page-level F1 (multiset over (page, value) pairs — penalizes transpositions)
    numeric_page:              MetricResult | None = None
    currency_page:             MetricResult | None = None
    percent_page:              MetricResult | None = None
    dates_page:                MetricResult | None = None
    cpf_page:                  MetricResult | None = None
    cnpj_page:                 MetricResult | None = None
    process_numbers_page:      MetricResult | None = None
    identifiers_combined_page: MetricResult | None = None

    def to_dict(self) -> dict:
        result: dict = {}
        _DOC_ATTRS = (
            "numeric", "currency", "percent", "dates", "cpf", "cnpj",
            "process_numbers", "identifiers_combined",
        )
        _PAGE_ATTRS = (
            "numeric_page", "currency_page", "percent_page", "dates_page",
            "cpf_page", "cnpj_page", "process_numbers_page",
            "identifiers_combined_page",
        )
        for attr in _DOC_ATTRS + _PAGE_ATTRS:
            v = getattr(self, attr)
            if v is not None:
                result[attr] = v.to_dict()
        return result


def compute_critical_data_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> CriticalDataMetrics:
    """Compute critical data metrics (spec §9).

    Parameters
    ----------
    ref_nodes : Reference MarkdownNode list.
    hyp_nodes : Hypothesis MarkdownNode list.

    Returns
    -------
    CriticalDataMetrics with both ``document_f1`` and ``page_f1`` views.

    Document F1
        Multiset of canonical values across the whole document, independent
        of page.  T17: same value in table vs paragraph on the same document
        still gives F1=1.0.

    Page F1
        Multiset of ``(page_number, canonical_value)`` pairs.  T19: same value
        on different pages gives F1=0.0.  When node.page is None for all nodes
        (no page markers), page_f1 falls back to document_f1 values.

    Notes
    -----
    Span-protection: identifiers > currency > dates > percent > numeric.
    Leaf-node projection via CanonicalDocument prevents A07 triple-counting.
    """
    ref_doc, ref_page = _document_and_page_values(ref_nodes)
    hyp_doc, hyp_page = _document_and_page_values(hyp_nodes)
    ids = {"numeric": "MET-CD01", "currency": "MET-CD02", "dates": "MET-CD03", "cpf": "MET-CD04",
           "cnpj": "MET-CD05", "process_numbers": "MET-CD06", "identifiers_combined": "MET-CD07",
           "percent": "MET-CD08"}
    fields = {}
    for key, definition in ids.items():
        fields[key] = _prf_multiset(Counter(ref_doc[key]), Counter(hyp_doc[key]), definition)
        fields[f"{key}_page"] = _prf_multiset(Counter(ref_page[key]), Counter(hyp_page[key]),
                                              f"{definition}-page")
    return CriticalDataMetrics(**fields)
