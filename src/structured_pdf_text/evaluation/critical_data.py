"""Critical data metrics: numbers, currency, dates and identifiers (spec §9).

All matching uses multiset intersection following spec §9.1:
  TP = Σ min(count_ref, count_hyp)
  FP = total_hyp - TP
  FN = total_ref - TP

Empty-reference behaviour follows spec §9.2:
  ref=0, hyp=0  → not_applicable, correct_absence=True
  ref=0, hyp>0  → FP, false_positive_only, F1=0
  ref>0, hyp=0  → FN, false_negative_only, F1=0
  ref>0, hyp>0  → regular P/R/F1

Normalisation uses Decimal for monetary values; date parsing is
conservative (only unambiguous pt-BR dates are canonicalised).
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

# Integer (≥1 digit) or decimal with pt-BR separators
_NUM_RE = re.compile(
    r"""
    (?<!\w)
    (-?                                          # optional sign
     (?:\d{1,3}(?:\.\d{3})+|\d+)               # integer, possibly with thousand sep
     (?:,\d+)?                                  # optional decimal (pt-BR comma)
     (?:\s*%)?                                  # optional percent
    )
    (?!\w)
    """,
    re.VERBOSE,
)

# Currency: R$ or BRL followed by numeric value
_CURRENCY_RE = re.compile(
    r"""
    (R\$|BRL)\s*
    (-?(?:\d{1,3}(?:\.\d{3})*|\d+)(?:,\d+)?)
    """,
    re.VERBOSE,
)

# Dates: DD/MM/YYYY or YYYY-MM-DD; also DD-MM-YYYY
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

# CPF: 000.000.000-00 or 00000000000
_CPF_RE = re.compile(r"""(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)""")

# CNPJ: 00.000.000/0000-00 or 14 digits
_CNPJ_RE = re.compile(r"""(?<!\d)(\d{2}\.?\d{3}\.?\d{3}[/\\]?\d{4}-?\d{2})(?!\d)""")

# Process numbers: Brazilian court format NNNNNNN-DD.AAAA.J.TT.OOOO
_PROCESS_RE = re.compile(r"""(?<!\d)(\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4})(?!\d)""")

# Calendar validation
_DAYS_IN_MONTH = {1:31,2:29,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}


# ---------------------------------------------------------------------------
# Extraction and normalisation helpers
# ---------------------------------------------------------------------------

def _visible(text: str) -> str:
    return normalize(text, NormProfile.VISIBLE_TEXT)


def _extract_numbers(text: str) -> list[str]:
    """Extract numeric tokens (excluding those inside currency, CPF, CNPJ, date, process)."""
    v = _visible(text)
    # Remove currency, CPF, CNPJ, process numbers, dates first to avoid overlap
    v = _CURRENCY_RE.sub("", v)
    v = _CPF_RE.sub("", v)
    v = _CNPJ_RE.sub("", v)
    v = _PROCESS_RE.sub("", v)
    v = _DATE_RE.sub("", v)
    return [m.group(1).strip() for m in _NUM_RE.finditer(v) if m.group(1).strip()]


def _canonical_number(raw: str) -> str:
    """pt-BR → canonical decimal string."""
    s = raw.replace("%", "").strip()
    # Remove thousand separators (pt-BR: .) then convert decimal comma
    s = s.replace(".", "").replace(",", ".")
    try:
        d = Decimal(s).normalize()
        # normalize() may produce scientific notation; convert back to plain
        return format(d, "f")
    except InvalidOperation:
        return raw


def _extract_currency(text: str) -> list[str]:
    v = _visible(text)
    results = []
    for m in _CURRENCY_RE.finditer(v):
        symbol = m.group(1)
        value = m.group(2).replace(".", "").replace(",", ".")
        try:
            dec = str(Decimal(value))
        except InvalidOperation:
            dec = value
        results.append(f"{symbol}:{dec}")
    return results


def _is_valid_date(day: int, month: int, year: int) -> bool:
    if not (1 <= month <= 12):
        return False
    max_d = _DAYS_IN_MONTH.get(month, 30)
    if month == 2 and year % 4 != 0:
        max_d = 28
    return 1 <= day <= max_d and 1000 <= year <= 9999


def _extract_dates(text: str) -> list[str]:
    v = _visible(text)
    results = []
    for m in _DATE_RE.finditer(v):
        if m.group(1):  # DD/MM/YYYY
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:           # YYYY-MM-DD
            y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
        if _is_valid_date(d, mo, y):
            results.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return results


def _canonical_cpf(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 else raw


def _canonical_cnpj(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 14 else raw


def _extract_cpf(text: str) -> list[str]:
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
# Text joining helper
# ---------------------------------------------------------------------------

def _text_from_nodes(nodes: Sequence[MarkdownNode]) -> str:
    return " ".join(n.visible_text for n in nodes)


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
    numeric:          MetricResult | None = None
    currency:         MetricResult | None = None
    dates:            MetricResult | None = None
    cpf:              MetricResult | None = None
    cnpj:             MetricResult | None = None
    process_numbers:  MetricResult | None = None
    identifiers_combined: MetricResult | None = None

    def to_dict(self) -> dict:
        result: dict = {}
        for attr in ("numeric", "currency", "dates", "cpf", "cnpj",
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
    """
    ref_text = _text_from_nodes(ref_nodes)
    hyp_text = _text_from_nodes(hyp_nodes)

    # Numbers: canonical form for comparison
    def _num_canonical(text: str) -> list[str]:
        return [_canonical_number(n) for n in _extract_numbers(text)]

    numeric = _metric_for_class(ref_text, hyp_text, _num_canonical, "MET-CD01")
    currency = _metric_for_class(ref_text, hyp_text, _extract_currency, "MET-CD02")
    dates = _metric_for_class(ref_text, hyp_text, _extract_dates, "MET-CD03")
    cpf = _metric_for_class(ref_text, hyp_text, _extract_cpf, "MET-CD04")
    cnpj = _metric_for_class(ref_text, hyp_text, _extract_cnpj, "MET-CD05")
    process_numbers = _metric_for_class(ref_text, hyp_text, _extract_process_numbers, "MET-CD06")

    # Combined identifiers: CPF + CNPJ + process numbers
    def _all_ids(text: str) -> list[str]:
        return (
            [f"cpf:{v}" for v in _extract_cpf(text)]
            + [f"cnpj:{v}" for v in _extract_cnpj(text)]
            + [f"proc:{v}" for v in _extract_process_numbers(text)]
        )

    ids_combined = _metric_for_class(ref_text, hyp_text, _all_ids, "MET-CD07")

    return CriticalDataMetrics(
        numeric           = numeric,
        currency          = currency,
        dates             = dates,
        cpf               = cpf,
        cnpj              = cnpj,
        process_numbers   = process_numbers,
        identifiers_combined = ids_combined,
    )
