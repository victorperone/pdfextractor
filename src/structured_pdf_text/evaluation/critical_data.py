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
  3. dates
  4. percent
  5. numeric (plain numbers, after all higher-priority spans removed)

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
from collections import Counter
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
    return normalize(text, NormProfile.VISIBLE_TEXT)


# ---------------------------------------------------------------------------
# Span-protected extraction
# ---------------------------------------------------------------------------

def _mask_spans(text: str, pattern: re.Pattern) -> str:
    """Replace all matches of *pattern* with space padding of the same length."""
    result = list(text)
    for m in pattern.finditer(text):
        for i in range(m.start(), m.end()):
            result[i] = " "
    return "".join(result)


def _extract_currency(text: str) -> list[str]:
    """Extract currency tokens as 'SYMBOL:canonical_decimal'.

    Uses format(d, 'f') instead of str(d.normalize()) to avoid scientific
    notation (e.g. Decimal('1234').normalize() → '1.234E+3' which str() gives
    '1.234E+3' but format('f') gives '1234').
    """
    v = _visible(text)
    results = []
    for m in _CURRENCY_RE.finditer(v):
        symbol = m.group(1)
        raw_value = m.group(2).replace(".", "").replace(",", ".")
        try:
            dec = format(Decimal(raw_value).normalize(), "f")
        except InvalidOperation:
            dec = raw_value
        results.append(f"{symbol}:{dec}")
    return results


def _is_valid_date(day: int, month: int, year: int) -> bool:
    if not (1 <= month <= 12):
        return False
    max_d = _DAYS_IN_MONTH.get(month, 30)
    if month == 2:
        # Leap year: divisible by 4, except centuries unless divisible by 400
        is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        max_d = 29 if is_leap else 28
    return 1 <= day <= max_d and 1000 <= year <= 9999


def _extract_dates(text: str) -> list[str]:
    """Extract valid pt-BR dates as canonical 'YYYY-MM-DD' strings."""
    v = _visible(text)
    # Remove currency and identifier spans first to avoid false date matches
    v = _mask_spans(v, _CURRENCY_RE)
    v = _mask_spans(v, _CPF_RE)
    v = _mask_spans(v, _CNPJ_RE)
    v = _mask_spans(v, _PROCESS_RE)
    results = []
    for m in _DATE_RE.finditer(v):
        if m.group(1):  # DD/MM/YYYY or DD-MM-YYYY
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:           # YYYY-MM-DD
            y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if _is_valid_date(d, mo, y):
            results.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return results


def _extract_percent(text: str) -> list[str]:
    """Extract percent tokens as canonical 'canonical_decimal%' strings.

    A06 fix: percent is its own category; '30%' is different from '30'.
    Span protection: percent spans are removed before numeric extraction.
    """
    v = _visible(text)
    # Remove higher-priority spans first
    v = _mask_spans(v, _CURRENCY_RE)
    v = _mask_spans(v, _CPF_RE)
    v = _mask_spans(v, _CNPJ_RE)
    v = _mask_spans(v, _PROCESS_RE)
    v = _mask_spans(v, _DATE_RE)
    results = []
    for m in _PERCENT_RE.finditer(v):
        raw = m.group(1).replace(".", "").replace(",", ".")
        try:
            dec = format(Decimal(raw).normalize(), "f")
        except InvalidOperation:
            dec = raw
        results.append(f"{dec}%")
    return results


def _canonical_number(raw: str) -> str:
    """pt-BR numeric string → canonical decimal string (for comparison)."""
    s = raw.strip()
    # pt-BR: period is thousand separator, comma is decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return format(Decimal(s).normalize(), "f")
    except InvalidOperation:
        return raw


def _extract_numbers(text: str) -> list[str]:
    """Extract numeric tokens (plain integers/decimals) with canonical form.

    A08 fix: recognises integers of any length (not just 4+ digits).
    Span protection: removes currency, CPF, CNPJ, process, dates, and percent
    spans before matching so those values are not double-counted.
    """
    v = _visible(text)
    # Remove all higher-priority spans (order matters)
    v = _mask_spans(v, _CURRENCY_RE)
    v = _mask_spans(v, _CPF_RE)
    v = _mask_spans(v, _CNPJ_RE)
    v = _mask_spans(v, _PROCESS_RE)
    v = _mask_spans(v, _DATE_RE)
    v = _mask_spans(v, _PERCENT_RE)
    return [_canonical_number(m.group(1)) for m in _NUM_RE.finditer(v)
            if m.group(1).strip()]


def _canonical_cpf(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else raw


def _canonical_cnpj(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 14 else raw


def _extract_cpf(text: str) -> list[str]:
    """Extract CPF tokens preserving leading zeros (A38-style: '00123' ≠ '123')."""
    return [_canonical_cpf(m.group(1)) for m in _CPF_RE.finditer(_visible(text))]


def _extract_cnpj(text: str) -> list[str]:
    return [_canonical_cnpj(m.group(1)) for m in _CNPJ_RE.finditer(_visible(text))]


def _extract_process_numbers(text: str) -> list[str]:
    return [m.group(1) for m in _PROCESS_RE.finditer(_visible(text))]


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
# Text joining helper — uses CanonicalDocument to avoid A07 (triple-counting)
# ---------------------------------------------------------------------------

def _text_from_nodes(nodes: Sequence[MarkdownNode]) -> str:
    """Join visible_text from leaf nodes only (A07 fix: no ancestor duplication)."""
    # Import here to avoid circular import at module level
    from structured_pdf_text.evaluation.canonical_document import CanonicalDocument
    doc = CanonicalDocument.from_nodes(nodes)
    return doc.visible_text


# ---------------------------------------------------------------------------
# Per-class extraction + metric
# ---------------------------------------------------------------------------

def _metric_for_class(
    ref_text: str,
    hyp_text: str,
    extractor,
    definition_id: str,
) -> MetricResult:
    ref_vals = extractor(ref_text)
    hyp_vals = extractor(hyp_text)
    return _prf_multiset(Counter(ref_vals), Counter(hyp_vals), definition_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class CriticalDataMetrics:
    """All critical data metrics for one evaluation run."""
    numeric:              MetricResult | None = None   # MET-CD01
    currency:             MetricResult | None = None   # MET-CD02
    percent:              MetricResult | None = None   # MET-CD08 (new — D3 approval)
    dates:                MetricResult | None = None   # MET-CD03
    cpf:                  MetricResult | None = None   # MET-CD04
    cnpj:                 MetricResult | None = None   # MET-CD05
    process_numbers:      MetricResult | None = None   # MET-CD06
    identifiers_combined: MetricResult | None = None   # MET-CD07

    def to_dict(self) -> dict:
        result: dict = {}
        for attr in ("numeric", "currency", "percent", "dates", "cpf", "cnpj",
                     "process_numbers", "identifiers_combined"):
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
    CriticalDataMetrics dataclass.

    Notes
    -----
    All extraction uses span-protection: each text position is claimed by
    at most one category (priority: identifiers > currency > dates > percent
    > numeric). This prevents a value like 'R$ 1.234,00' from matching both
    currency and numeric, and '30%' from matching both percent and numeric.

    Entity text is collected from leaf nodes only (CanonicalDocument projection)
    to prevent double-counting when the same value appears in a table cell that
    also has a parent TABLE/TABLE_ROW node (bug A07 fix).
    """
    ref_text = _text_from_nodes(ref_nodes)
    hyp_text = _text_from_nodes(hyp_nodes)

    # Numbers: canonical form for comparison (A08 fix: any integer length)
    def _num_canonical(text: str) -> list[str]:
        return _extract_numbers(text)

    numeric = _metric_for_class(ref_text, hyp_text, _num_canonical, "MET-CD01")
    currency = _metric_for_class(ref_text, hyp_text, _extract_currency, "MET-CD02")
    dates = _metric_for_class(ref_text, hyp_text, _extract_dates, "MET-CD03")
    cpf = _metric_for_class(ref_text, hyp_text, _extract_cpf, "MET-CD04")
    cnpj = _metric_for_class(ref_text, hyp_text, _extract_cnpj, "MET-CD05")
    process_numbers = _metric_for_class(
        ref_text, hyp_text, _extract_process_numbers, "MET-CD06"
    )

    # Combined identifiers: CPF + CNPJ + process numbers
    def _all_ids(text: str) -> list[str]:
        return (
            [f"cpf:{v}" for v in _extract_cpf(text)]
            + [f"cnpj:{v}" for v in _extract_cnpj(text)]
            + [f"proc:{v}" for v in _extract_process_numbers(text)]
        )

    ids_combined = _metric_for_class(ref_text, hyp_text, _all_ids, "MET-CD07")

    # Percent: separate category (D3 approval, A06 fix)
    percent = _metric_for_class(ref_text, hyp_text, _extract_percent, "MET-CD08")

    return CriticalDataMetrics(
        numeric           = numeric,
        currency          = currency,
        percent           = percent,
        dates             = dates,
        cpf               = cpf,
        cnpj              = cnpj,
        process_numbers   = process_numbers,
        identifiers_combined = ids_combined,
    )
