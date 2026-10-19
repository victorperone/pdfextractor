"""V2 aggregation: micro, macro, worst-case and coverage tracking (spec §10).

Rules
-----
- Micro: sum numerators / sum denominators before dividing.
- Macro: arithmetic mean of *applicable* page scores only.
- Finite numeric FP-only/FN-only outcomes are applicable, including zero.
- N/A values are EXCLUDED from averages, not replaced with 0 or 1.
- Worst-case: min for quality metrics (higher_is_better),
              max for error metrics (lower_is_better).
- Median: true arithmetic median (average of two middle values for even n).
- No rounding until serialisation; keep full float64 precision in memory.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Iterable

from structured_pdf_text.evaluation.models import MetricResult, MetricStatus
from structured_pdf_text.evaluation.comparability import validate_comparison

AGGREGATION_POLICY = "finite-numeric-outcomes-v1"
_NUMERIC_STATUSES = frozenset({MetricStatus.OK, MetricStatus.FALSE_POSITIVE_ONLY,
                              MetricStatus.FALSE_NEGATIVE_ONLY})
_COMPLETE_COUNT_STATUSES = _NUMERIC_STATUSES | {
    MetricStatus.NOT_APPLICABLE, MetricStatus.UNDEFINED_DENOMINATOR,
}

# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

_LOWER_IS_WORSE = frozenset({
    "higher_is_better",
})


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_applicable_value(value: object, status: object) -> bool:
    return isinstance(status, str) and status in _NUMERIC_STATUSES and _finite_number(value)


def _is_applicable(result: MetricResult) -> bool:
    """Numeric zero in FP/FN-only outcomes is defined and applicable."""
    return _is_applicable_value(result.value, result.status)


def _micro_from_counts(results: list[MetricResult], numerator: str, denominator: str) -> float | None:
    # An unavailable page cannot be treated as a page with zero errors.
    if any(r.status not in _COMPLETE_COUNT_STATUSES for r in results):
        return None
    numerators, denominators = [], []
    for result in results:
        counts = result.counts
        if (result.status == MetricStatus.NOT_APPLICABLE
                and numerator not in counts and denominator not in counts):
            # A declared correct absence contributes no events/characters.
            continue
        if numerator not in counts or denominator not in counts:
            raise ValueError(f"Micro aggregation requires counts {numerator!r} and {denominator!r}")
        num, den = counts[numerator], counts[denominator]
        if any(not _finite_number(v) or v < 0 for v in (num, den)):
            raise ValueError("Micro counts must be finite, nonnegative numbers")
        if result.status == MetricStatus.NOT_APPLICABLE and (num != 0 or den != 0):
            raise ValueError("Not-applicable micro counts must describe a correct absence (0/0)")
        numerators.append(num)
        denominators.append(den)
    # Keep integer counts exact, including totals beyond float's 53-bit mantissa.
    try:
        total_num = sum(numerators) if all(type(v) is int for v in numerators) else math.fsum(numerators)
        total_den = sum(denominators) if all(type(v) is int for v in denominators) else math.fsum(denominators)
        value = total_num / total_den if total_den > 0 else None
    except OverflowError as exc:
        raise ValueError("Micro aggregation exceeds finite numeric range") from exc
    if value is not None and not math.isfinite(value):
        raise ValueError("Micro aggregation exceeds finite numeric range")
    return value


def _worst_value(values: list[float], direction: str) -> float | None:
    if not values:
        return None
    if direction == "higher_is_better":
        return min(values)   # quality metric: worst = lowest
    else:
        return max(values)   # error metric: worst = highest


# ---------------------------------------------------------------------------
# Single metric aggregation across pages / runs
# ---------------------------------------------------------------------------

@dataclass
class AggregatedMetric:
    """Aggregated statistics for one named metric across multiple runs/pages."""
    definition_id:   str
    direction:       str
    n_applicable:    int
    n_total:         int
    micro_value:     float | None   # sum(num)/sum(den) — requires counts
    macro_value:     float | None   # arithmetic mean of applicable values
    worst_value:     float | None   # min (quality) or max (error)
    median_value:    float | None
    min_value:       float | None
    max_value:       float | None
    values:          list[float]    # raw applicable values (for distribution)

    def to_dict(self) -> dict:
        return {
            "definition_id": self.definition_id,
            "direction":     self.direction,
            "n_applicable":  self.n_applicable,
            "n_total":       self.n_total,
            "micro":         round(self.micro_value, 6) if self.micro_value is not None else None,
            "macro":         round(self.macro_value, 6) if self.macro_value is not None else None,
            "worst":         round(self.worst_value, 6) if self.worst_value is not None else None,
            "median":        round(self.median_value, 6) if self.median_value is not None else None,
            "min":           round(self.min_value, 6) if self.min_value is not None else None,
            "max":           round(self.max_value, 6) if self.max_value is not None else None,
            "coverage":      round(self.n_applicable / self.n_total, 4)
                             if self.n_total > 0 else None,
        }


def aggregate_metric(
    results: Iterable[MetricResult],
    *,
    numerator_key: str | None = None,
    denominator_key: str | None = None,
) -> AggregatedMetric:
    """Aggregate a list of MetricResult objects.

    Parameters
    ----------
    results          : Iterable of per-page MetricResult for the same metric.
    numerator_key    : Key in result.counts used for micro numerator.
    denominator_key  : Key in result.counts used for micro denominator.
                       Both must be supplied. Computes micro as Σnum/Σden
                       from complete validated counts, including insertion-only
                       pages. An unavailable page makes the full micro null;
                       invalid/missing counts raise instead of fabricating zero.
    """
    results_list = list(results)
    n_total = len(results_list)
    if (numerator_key is None) != (denominator_key is None):
        raise ValueError("Micro aggregation requires both count keys")
    if numerator_key is not None and any(not isinstance(key, str) or not key
                                         for key in (numerator_key, denominator_key)):
        raise ValueError("Micro count keys must be nonempty strings")

    if n_total == 0:
        return AggregatedMetric(
            definition_id="", direction="higher_is_better",
            n_applicable=0, n_total=0,
            micro_value=None, macro_value=None, worst_value=None,
            median_value=None, min_value=None, max_value=None, values=[],
        )

    direction = results_list[0].direction
    for result in results_list[1:]:
        for field_name in ("definition_id", "direction", "unit", "normalization_profile"):
            if getattr(result, field_name) != getattr(results_list[0], field_name):
                raise ValueError(f"Cannot aggregate different metric {field_name}")
    applicable = [r for r in results_list if _is_applicable(r)]
    values = [r.value for r in applicable]  # type: ignore[misc]
    n_applicable = len(values)

    # Micro
    micro: float | None = None
    if numerator_key is not None and denominator_key is not None:
        micro = _micro_from_counts(results_list, numerator_key, denominator_key)

    # Macro (mean of applicable values only)
    macro = statistics.mean(values) if values else None

    # Median (true arithmetic; average of two middle for even n)
    median: float | None = None
    if values:
        ordered = sorted(values)
        middle = len(ordered) // 2
        median = (ordered[middle] if len(ordered) % 2
                  else statistics.mean(ordered[middle - 1:middle + 1]))

    worst  = _worst_value(values, direction)
    mn     = min(values) if values else None
    mx     = max(values) if values else None

    return AggregatedMetric(
        definition_id   = results_list[0].definition_id,
        direction       = direction,
        n_applicable    = n_applicable,
        n_total         = n_total,
        micro_value     = micro,
        macro_value     = macro,
        worst_value     = worst,
        median_value    = median,
        min_value       = mn,
        max_value       = mx,
        values          = values,
    )


# ---------------------------------------------------------------------------
# Document-level aggregation of all metric groups
# ---------------------------------------------------------------------------

@dataclass
class V2DocumentMetrics:
    """Complete unified metric result for a single (ref, hyp) evaluation."""
    schema_version: str = "evaluation-3.0.0"

    # Per-group results (all are MetricResult dicts via dataclass.to_dict())
    text:           dict = field(default_factory=dict)   # MET-TX01..TX07
    structure:      dict = field(default_factory=dict)
    reading_order:  dict = field(default_factory=dict)
    tables:         dict = field(default_factory=dict)
    critical_data:  dict = field(default_factory=dict)
    integrity:      dict = field(default_factory=dict)

    # Provenance
    evaluator_info: dict = field(default_factory=dict)
    inputs:         dict = field(default_factory=dict)
    operational:    dict = field(default_factory=dict)

    # Evaluation status
    evaluation_status:        str = "ok"  # ok | partial | unavailable
    rank_eligible:            bool = False
    rank_ineligibility_reasons: list[str] = field(default_factory=list)
    applicable_metric_count:  int = 0
    unavailable_metric_count: int = 0

    def _count_metrics(self, group: dict) -> tuple[int, int]:
        applicable = unavailable = 0
        for v in group.values():
            if isinstance(v, dict):
                if "status" not in v or "value" not in v:
                    continue  # Diagnostics are not scalar metrics.
                if _is_applicable_value(v["value"], v["status"]):
                    applicable += 1
                else:
                    unavailable += 1
        return applicable, unavailable

    def finalise(self) -> None:
        """Compute summary counts and eligibility after all groups are filled."""
        app = unav = 0
        for group in (self.text, self.structure, self.reading_order,
                      self.tables, self.critical_data, self.integrity):
            a, u = self._count_metrics(group)
            app += a
            unav += u
        self.applicable_metric_count  = app
        self.unavailable_metric_count = unav

    def to_dict(self) -> dict:
        self.finalise()
        return {
            "schema_version": self.schema_version,
            "evaluator":      self.evaluator_info,
            "inputs":         self.inputs,
            "quality": {
                "text":           self.text,
                "structure":      self.structure,
                "reading_order":  self.reading_order,
                "tables":         self.tables,
                "critical_data":  self.critical_data,
            },
            "integrity":      self.integrity,
            "operational":    self.operational,
            "evaluation": {
                "status":                  self.evaluation_status,
                "rank_eligible":           self.rank_eligible,
                "rank_ineligibility_reasons": self.rank_ineligibility_reasons,
                "applicable_metric_count":  self.applicable_metric_count,
                "unavailable_metric_count": self.unavailable_metric_count,
            },
        }


# ---------------------------------------------------------------------------
# Comparability guard
# ---------------------------------------------------------------------------

def check_comparability(
    result_a: dict,
    result_b: dict,
    *,
    strict: bool = True,
) -> tuple[bool, list[str]]:
    """Compare canonical v3 inputs and evaluator policies, failing closed.

    API and CLI use the same validator. Outcomes never change comparability;
    missing/malformed provenance, legacy schemas and raw-hash fallbacks fail.
    ``strict`` is retained for call compatibility: False does not bypass checks.
    Diagnostic CLI bypass remains explicit via --skip-validation.
    """
    reasons = validate_comparison([("a", result_a), ("b", result_b)])
    return not reasons, reasons


# ---------------------------------------------------------------------------
# Ranking eligibility rules
# ---------------------------------------------------------------------------

_P0_GATES: list[tuple[str, str]] = [
    # (group_path, metric_key) that must NOT be status=invalid_input
]


def evaluate_rank_eligibility(doc: V2DocumentMetrics) -> tuple[bool, list[str]]:
    """Determine whether this result is eligible for ranking.

    Returns (eligible: bool, reasons: list[str]).
    """
    reasons: list[str] = []

    # Must have at least some applicable metrics
    if doc.applicable_metric_count == 0:
        reasons.append("no_applicable_metrics")

    # Evaluation status must not be unavailable
    if doc.evaluation_status == "unavailable":
        reasons.append("evaluation_unavailable")

    # Integrity: invalid Markdown blocks ranking
    integrity = doc.integrity
    md_val = integrity.get("markdown_validity", {})
    if md_val.get("validity_status") == "invalid":
        reasons.append("invalid_markdown_in_hypothesis")

    eligible = len(reasons) == 0
    return eligible, reasons
