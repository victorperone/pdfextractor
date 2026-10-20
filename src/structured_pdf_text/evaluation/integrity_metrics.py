"""Integrity and operational quality metrics for V2 (spec §8, MET-I*).

Metrics
-------
MET-I01  duplicate_excess_rate      fraction of content blocks exceeding ref multiplicity
MET-I02  missing_page_rate          fraction of selected pages absent from hypothesis
MET-I03  page_content_coverage_recall  multiset token recall of canonical visible text
MET-I04  markdown_validity          parse error and construct counts (descriptive)
MET-I05  header_footer_heuristic    edge-content repetition (diagnostic only, not in score)

Design notes
------------
- duplicate_excess compares per-fingerprint multiplicity against ref (spec §8.4):
    excess(fp) = max(0, hyp_count - ref_count)
- header/footer/page-number detection is tagged *_heuristic_v1; never enters score.
- markdown_validity uses the canonical parser; counts errors by category.
- All rates use exact integer numerators/denominators; no max(N,1) shims.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from structured_pdf_text.evaluation.models import (
    MarkdownNode,
    MetricResult,
    MetricStatus,
    NodeKind,
    NormProfile,
)
from structured_pdf_text.evaluation.normalization import normalize
from .markdown_syntax import page_markers, split_page_sources, unclosed_fence_count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return normalize(text, NormProfile.UNICODE_LAYOUT).lower().strip()


def _make_result(
    value: float | None,
    status: MetricStatus,
    definition_id: str,
    counts: dict,
    direction: str = "lower_is_better",
) -> MetricResult:
    return MetricResult(
        value         = round(value, 4) if value is not None else None,
        status        = status,
        direction     = direction,
        unit          = "ratio",
        definition_id = definition_id,
        counts        = counts,
        scope         = "document",
    )


# ---------------------------------------------------------------------------
# MET-I01: Duplicate excess rate
# ---------------------------------------------------------------------------

def duplicate_excess_rate(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *,
    min_text_len: int = 15,
) -> MetricResult:
    """MET-I01: Fraction of content blocks in hyp exceeding ref multiplicity.

    For each content fingerprint (normalised visible text ≥ min_text_len chars),
    excess = max(0, hyp_count − ref_count).

    rate = Σ excess / total_hyp_blocks_evaluated
    """
    def _fingerprints(nodes: Sequence[MarkdownNode]) -> list[str]:
        from .canonical_document import CanonicalDocument
        return [
            _norm(n.visible_text)
            for n in CanonicalDocument.from_nodes(nodes).leaf_nodes
            if len(_norm(n.visible_text)) >= min_text_len
        ]

    ref_fps = _fingerprints(ref_nodes)
    hyp_fps = _fingerprints(hyp_nodes)

    ref_counter = Counter(ref_fps)
    hyp_counter = Counter(hyp_fps)

    total_hyp = len(hyp_fps)
    if total_hyp == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-I01",
                            {"total_hyp_blocks": 0, "excess_blocks": 0})

    excess = sum(
        max(0, hyp_counter[fp] - ref_counter[fp])
        for fp in hyp_counter
    )
    rate = excess / total_hyp

    return _make_result(
        rate, MetricStatus.OK, "MET-I01",
        {"excess_blocks": excess, "total_hyp_blocks": total_hyp,
         "unique_fingerprints_hyp": len(hyp_counter),
         "unique_fingerprints_ref": len(ref_counter)},
    )


# ---------------------------------------------------------------------------
# MET-I02: Missing page rate
# ---------------------------------------------------------------------------

def missing_page_rate(
    selected_pages: set[int],
    selected_reference: set[int],
    hyp_page_nums: set[int],
) -> MetricResult:
    """MET-I02: Fraction of selected+reference pages absent from hypothesis.

    denominator = |selected_reference|  (pages that should be evaluated)
    numerator   = |selected_reference − hyp_page_nums|
    """
    denom = len(selected_reference)
    if denom == 0:
        return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-I02",
                            {"selected_reference": 0, "missing": 0})

    missing = len(selected_reference - hyp_page_nums)
    rate = missing / denom
    return _make_result(
        rate, MetricStatus.OK, "MET-I02",
        {"missing": missing, "selected_reference": denom,
         "selected": len(selected_pages), "hyp_pages": len(hyp_page_nums)},
    )


# ---------------------------------------------------------------------------
# MET-I03: Page content coverage recall
# ---------------------------------------------------------------------------

def page_content_coverage_recall(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
) -> MetricResult:
    """MET-I03: Multiset word-token recall of canonical visible text.

    Includes literal code and counts each terminal text unit once. Text is
    normalized and lowercased; token multiplicity matters, order does not.
    Character counts are diagnostic; recall = recovered tokens / reference tokens.
    """
    from .canonical_document import CanonicalDocument
    ref_text = _norm(CanonicalDocument.from_nodes(ref_nodes).visible_text)
    hyp_text = _norm(CanonicalDocument.from_nodes(hyp_nodes).visible_text)

    ref_len = len(ref_text)
    if ref_len == 0:
        if len(hyp_text) == 0:
            return _make_result(None, MetricStatus.NOT_APPLICABLE, "MET-I03",
                                {"ref_chars": 0, "hyp_chars": 0, "correct_absence": True}, direction="higher_is_better")
        return _make_result(0.0, MetricStatus.FALSE_POSITIVE_ONLY, "MET-I03",
                            {"ref_chars": 0, "hyp_chars": len(hyp_text)}, direction="higher_is_better")

    # Token-level multiset recall (word tokens)
    ref_tokens = Counter(ref_text.split())
    hyp_tokens = Counter(hyp_text.split())
    tp_tokens = sum((ref_tokens & hyp_tokens).values())
    total_ref_tokens = sum(ref_tokens.values())

    recall = tp_tokens / total_ref_tokens if total_ref_tokens > 0 else None
    status = MetricStatus.OK if total_ref_tokens > 0 else MetricStatus.NOT_APPLICABLE

    return _make_result(
        recall, status, "MET-I03",
        {"ref_chars": ref_len, "hyp_chars": len(hyp_text),
         "ref_tokens": total_ref_tokens, "tp_tokens": tp_tokens},
        direction="higher_is_better",
    )


# ---------------------------------------------------------------------------
# MET-I04: Markdown validity (descriptive)
# ---------------------------------------------------------------------------

@dataclass
class MarkdownValidityReport:
    """Diagnostic counts for Markdown structural validity."""
    parse_errors:            int = 0
    unclosed_fences:         int = 0
    page_marker_errors:      int = 0
    duplicate_page_markers:  int = 0
    unsupported_constructs:  int = 0
    validity_status:         str = "valid"   # "valid" | "warnings" | "invalid"
    warnings:                list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "parse_errors":           self.parse_errors,
            "unclosed_fences":        self.unclosed_fences,
            "page_marker_errors":     self.page_marker_errors,
            "duplicate_page_markers": self.duplicate_page_markers,
            "unsupported_constructs": self.unsupported_constructs,
            "validity_status":        self.validity_status,
            "warnings":               self.warnings,
        }


def markdown_validity_report(text: str) -> MarkdownValidityReport:
    """MET-I04: Analyse Markdown text for structural validity issues.

    This uses grammar-based checks, not heuristics on pipe counts.
    """
    report = MarkdownValidityReport()

    report.unclosed_fences = unclosed_fence_count(text)
    if report.unclosed_fences:
        report.parse_errors += report.unclosed_fences
        report.warnings.append(f"unclosed_fence: {report.unclosed_fences} unterminated blocks")

    # Duplicate page markers
    page_counter = Counter(marker.page for marker in page_markers(text))
    dups = {p: c for p, c in page_counter.items() if c > 1}
    if dups:
        report.duplicate_page_markers = sum(c - 1 for c in dups.values())
        report.parse_errors += report.duplicate_page_markers
        for p, c in dups.items():
            report.warnings.append(f"duplicate_page_marker: '## Página {p}' appears {c}×")

    # Determine validity_status
    if report.parse_errors > 0:
        report.validity_status = "invalid"
    elif report.warnings:
        report.validity_status = "warnings"
    else:
        report.validity_status = "valid"

    return report


# ---------------------------------------------------------------------------
# MET-I05: Header/footer/page-number heuristic (diagnostic only)
# ---------------------------------------------------------------------------

_ISOLATED_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)


@dataclass
class EdgeContentDiagnostic:
    """Heuristic edge-content diagnostic — NOT included in quality score."""
    suspected_header_lines: int = 0
    suspected_footer_lines: int = 0
    suspected_page_number_lines: int = 0
    policy: str = "report_only"
    note: str = "heuristic_v1 — do not use as quality metric"

    def to_dict(self) -> dict:
        return {
            "suspected_header_lines":       self.suspected_header_lines,
            "suspected_footer_lines":       self.suspected_footer_lines,
            "suspected_page_number_lines":  self.suspected_page_number_lines,
            "policy":                       self.policy,
            "note":                         self.note,
        }


def edge_content_diagnostic(
    hyp_text: str,
    ref_text: str,
    *,
    edge_lines: int = 3,
    repetition_threshold: float = 0.30,
) -> EdgeContentDiagnostic:
    """MET-I05 (heuristic): Count lines at document edges that repeat across pages.

    This is a *diagnostic only* — never contributes to quality score.
    Legitimate repetition present in the reference is not flagged.
    """
    diag = EdgeContentDiagnostic()

    hyp_lines  = [ln.strip() for ln in hyp_text.split("\n") if ln.strip()]
    ref_lines  = set(ln.strip().lower() for ln in ref_text.split("\n") if ln.strip())

    if not hyp_lines:
        return diag

    # Use the same page grammar as quality and validity, including code/HTML.
    pages = [body for body, _ in split_page_sources(hyp_text).values()]
    header_candidates: Counter = Counter()
    footer_candidates: Counter = Counter()

    for page_text in pages:
        page_lines = [ln.strip() for ln in page_text.split("\n") if ln.strip()]
        for ln in page_lines[:edge_lines]:
            header_candidates[ln.lower()] += 1
        for ln in page_lines[-edge_lines:]:
            footer_candidates[ln.lower()] += 1

    n_pages = max(len(pages), 1)

    for line, count in header_candidates.items():
        if count / n_pages >= repetition_threshold and line not in ref_lines:
            diag.suspected_header_lines += 1
    for line, count in footer_candidates.items():
        if count / n_pages >= repetition_threshold and line not in ref_lines:
            diag.suspected_footer_lines += 1

    # Isolated numbers (potential page numbers): present in hyp but absent from ref
    hyp_nums = set(_ISOLATED_NUM_RE.findall(hyp_text))
    ref_nums  = set(_ISOLATED_NUM_RE.findall(ref_text))
    diag.suspected_page_number_lines = len(hyp_nums - ref_nums)

    return diag


# ---------------------------------------------------------------------------
# Convenience: compute all integrity metrics
# ---------------------------------------------------------------------------

@dataclass
class IntegrityMetrics:
    """All integrity metrics for one evaluation run."""
    duplicate_excess:          MetricResult | None = None
    missing_page:              MetricResult | None = None
    content_coverage_recall:   MetricResult | None = None
    markdown_validity:         MarkdownValidityReport | None = None
    edge_content_diagnostic:   EdgeContentDiagnostic | None = None

    def to_dict(self) -> dict:
        result: dict = {}
        if self.duplicate_excess:
            result["duplicate_excess_rate"] = self.duplicate_excess.to_dict()
        if self.missing_page:
            result["missing_page_rate"] = self.missing_page.to_dict()
        if self.content_coverage_recall:
            result["page_content_coverage_recall"] = self.content_coverage_recall.to_dict()
        if self.markdown_validity:
            result["markdown_validity"] = self.markdown_validity.to_dict()
        if self.edge_content_diagnostic:
            result["edge_content_heuristic_v1"] = self.edge_content_diagnostic.to_dict()
        return result


def compute_integrity_metrics(
    ref_nodes: Sequence[MarkdownNode],
    hyp_nodes: Sequence[MarkdownNode],
    *,
    selected_pages: set[int] | None = None,
    selected_reference: set[int] | None = None,
    hyp_page_nums: set[int] | None = None,
    hyp_raw_text: str | None = None,
    ref_raw_text: str | None = None,
) -> IntegrityMetrics:
    """Compute all integrity metrics.

    Parameters
    ----------
    ref_nodes           : Reference MarkdownNode list.
    hyp_nodes           : Hypothesis MarkdownNode list.
    selected_pages      : Pages the engine was asked to produce.
    selected_reference  : selected ∩ reference pages.
    hyp_page_nums       : Pages present in hypothesis output.
    hyp_raw_text        : Raw hypothesis Markdown (for validity/edge checks).
    ref_raw_text        : Raw reference Markdown (for edge-content comparison).
    """
    dup = duplicate_excess_rate(ref_nodes, hyp_nodes)

    miss: MetricResult | None = None
    if selected_reference is not None and hyp_page_nums is not None:
        miss = missing_page_rate(
            selected_pages or set(), selected_reference, hyp_page_nums
        )

    cov = page_content_coverage_recall(ref_nodes, hyp_nodes)

    validity: MarkdownValidityReport | None = None
    if hyp_raw_text is not None:
        validity = markdown_validity_report(hyp_raw_text)

    edge: EdgeContentDiagnostic | None = None
    if hyp_raw_text is not None and ref_raw_text is not None:
        edge = edge_content_diagnostic(hyp_raw_text, ref_raw_text)

    return IntegrityMetrics(
        duplicate_excess        = dup,
        missing_page            = miss,
        content_coverage_recall = cov,
        markdown_validity       = validity,
        edge_content_diagnostic = edge,
    )
