"""Central evaluation API for pre-existing Markdown; no extraction or OCR."""
from __future__ import annotations

import hashlib
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregation import AGGREGATION_POLICY, V2DocumentMetrics, evaluate_rank_eligibility
from .comparability import COMPARABILITY_POLICY
from .block_alignment import (
    AlignmentSummary, align_blocks, AlignmentResourceLimit,
    DEFAULT_MAX_ALIGNMENT_PAIRS, MAX_SIMILARITY_TEXT_PRODUCT,
)
from .edit_distance import DEFAULT_MAX_EDIT_CELLS, EDIT_DISTANCE_POLICY
from .critical_data import CriticalDataMetrics, compute_critical_data_metrics
from .integrity_metrics import IntegrityMetrics, compute_integrity_metrics
from .markdown_parser import PARSER_VERSION, parse_page
from .markdown_syntax import PAGE_SYNTAX_POLICY
from .paragraph_boundaries import PARAGRAPH_POLICY
from .html_tables import HTML_TABLE_POLICY
from .models import MarkdownNode
from .reporting import REPORTING_POLICY, corpus_page_metadata, run_page_metadata
from .validation import VALIDATION_POLICY, validate_result
from .page_scope import build_page_scope
from .reference_hash import REFERENCE_HASH_PROTOCOL, compute_reference_effective_hash
from .reference_inputs import document_pages, page_number, pages_markdown, selected_pages_from_run
from .structure_metrics import StructureMetrics, compute_structure_metrics
from .table_metrics import TableMetrics, compute_table_metrics
from .text_metrics import (
    TextMetrics, compute_text_metrics, page_anchored_aligned_cer, page_anchored_edit_rate,
    text_coverage_precision, text_coverage_recall,
)
from .canonical_document import CanonicalDocument
from .models import NormProfile
from .normalization import normalize


@dataclass(frozen=True)
class EvaluationRequest:
    """Markdown and provenance for one evaluation.

    Text fields are the evaluated content. Paths, when supplied, identify the
    source artifacts. Explicit scope/PDF/mode override run-manifest metadata;
    absent scope selects every reference page. No files are written by the API.
    """

    ref_text: str
    hyp_text: str
    ref_path: Path | None = None
    hyp_path: Path | None = None
    manifesto_path: Path | None = None
    engine: str = "unknown"
    run_id: str = "unknown"
    pdf_sha256: str | None = None
    mode: str | None = None
    selected_page_scope: list[int] | None = None
    run_metadata: dict[str, Any] = field(default_factory=dict)
    max_text_edit_cells: int = DEFAULT_MAX_EDIT_CELLS
    max_alignment_pairs: int = DEFAULT_MAX_ALIGNMENT_PAIRS


def _sha256_path(path: Path) -> str | None:
    from .reference_hash import compute_reference_source_hash
    return compute_reference_source_hash(path)


def _parse_scoped_pages(pages: dict[int, str], origin: str) -> dict[int, list[MarkdownNode]]:
    """Nodes per page; order indices are global, in the given page order."""
    by_page: dict[int, list[MarkdownNode]] = {}
    seq = 0
    for number, body in pages.items():
        by_page[number] = parse_page(body, number, origin, start_seq=seq)
        seq += len(by_page[number])
    return by_page


def _evaluate_single_page(args: tuple) -> dict:
    """Worker: computes _populate_quality for a single page.

    Top-level so ProcessPoolExecutor can pickle it on Windows (spawn method).
    Receives the page's parsed nodes (document-wide IDs); returns a plain dict
    plus the page-local alignment pairs, which the document view reuses.
    """
    (page, ref_text, hyp_text, page_in_hyp, ref_nodes, hyp_nodes,
     max_text_edit_cells, max_alignment_pairs) = args
    req = EvaluationRequest(
        ref_text=ref_text,
        hyp_text=hyp_text,
        max_text_edit_cells=max_text_edit_cells,
        max_alignment_pairs=max_alignment_pairs,
    )
    page_doc = V2DocumentMetrics()
    pairs = _populate_quality(
        page_doc, req,
        {page: ref_text},
        {page: hyp_text} if page_in_hyp else {},
        ref_nodes, hyp_nodes,
    )
    result = page_doc.to_dict()
    for group in (*result["quality"].values(), result["integrity"]):
        for metric in group.values():
            if isinstance(metric, dict) and "definition_id" in metric:
                metric["scope"] = "page"
    counts = page_doc.text["cer_sequential_visible"]["counts"]
    return {
        "page": page,
        "result": result,
        "ref_chars": counts["ref_chars"],
        "hyp_chars": counts["hyp_chars"],
        "pairs": pairs,
    }


def evaluate(request: EvaluationRequest) -> dict[str, Any]:
    """Return schema 3.0.0; invalid input/scope raises instead of grading it.

    Extra hypothesis pages are recorded in inputs, outside scoped quality and
    integrity. An absent selected page remains part of the evaluated reference.
    """
    return evaluate_document(request).to_dict()


def evaluate_document(request: EvaluationRequest) -> V2DocumentMetrics:
    """Object form retained for callers that generate diagnostic reports."""
    doc = V2DocumentMetrics()
    for name in ("max_text_edit_cells", "max_alignment_pairs"):
        value = getattr(request, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    # --- Page scope ---
    ref_pages = document_pages(request.ref_text)
    hyp_pages = document_pages(request.hyp_text)
    selected = request.selected_page_scope
    if selected is None:
        selected = selected_pages_from_run(request.run_metadata)
    if selected is not None:
        if not isinstance(selected, (list, tuple, set)) or not selected:
            raise ValueError("Selected page scope cannot be empty")
        selected = {page_number(p) for p in selected}
    scope = build_page_scope(set(ref_pages), set(hyp_pages), selected)
    if scope.selected_without_ref:
        raise ValueError(f"Selected pages without ground truth: {sorted(scope.selected_without_ref)}")
    effective_selected_pages = sorted(scope.selected_reference)
    # Reference order follows page IDs, as does reference-effective-v1. Keep
    # the hypothesis' observed order so inversions remain measurable.
    ref_scoped = {p: ref_pages[p] for p in sorted(scope.selected_reference)}
    hyp_scoped = {p: text for p, text in hyp_pages.items() if p in scope.selected_reference}

    # --- Reference effective hash (protocol reference-effective-v1) ---
    eff_digest, eff_meta = compute_reference_effective_hash(
        ref_pages,
        selected_pages=effective_selected_pages,
    )

    # --- Provenance ---
    doc.evaluator_info = {
        "implementation_commit": _get_git_sha(),
        "metric_definitions_version": "3.1.0",
        "normalization_policy": "unicode_layout",
        "parser": PARSER_VERSION,
        "page_syntax_policy": PAGE_SYNTAX_POLICY,
        "paragraph_policy": PARAGRAPH_POLICY,
        "alignment_policy": "content-match-v3-exact-first-layered",
        "entity_policy": "entities-ptbr-v2",
        "text_projection_policy": "parser-visible-leaves-v3-page-anchored-micro",
        "html_table_policy": HTML_TABLE_POLICY,
        "table_metric_policy": "all-cell-anchors-v3-token-dice",
        "aggregation_policy": AGGREGATION_POLICY,
        "comparability_policy": COMPARABILITY_POLICY,
        "edit_distance_policy": EDIT_DISTANCE_POLICY,
        "resource_policy": {
            "max_text_edit_cells": request.max_text_edit_cells,
            "max_table_edit_cells": request.max_text_edit_cells,
            "max_structure_edit_cells": request.max_text_edit_cells,
            "max_alignment_pairs": request.max_alignment_pairs,
            "max_similarity_text_product": MAX_SIMILARITY_TEXT_PRODUCT,
        },
        "reporting_policy": REPORTING_POLICY,
        "validation_policy": VALIDATION_POLICY,
        "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
    }
    doc.inputs = {
        "reference_effective_sha256": eff_digest,
        "reference_serialization_version": REFERENCE_HASH_PROTOCOL,
        "reference_source_sha256": _sha256_path(request.ref_path) if request.ref_path else None,
        "hypothesis_sha256": _sha256_path(request.hyp_path) if request.hyp_path else hashlib.sha256(request.hyp_text.encode("utf-8")).hexdigest(),
        "manifest_sha256": _sha256_path(request.manifesto_path) if request.manifesto_path else None,
        "pdf_sha256": request.pdf_sha256 if request.pdf_sha256 is not None else request.run_metadata.get("pdf_sha256"),
        "mode": request.mode if request.mode is not None else request.run_metadata.get("mode"),
        "engine": request.engine,
        "run_id": request.run_id,
        "selected_pages": effective_selected_pages,
        "total_pages_in_reference": eff_meta.get("total_pages_in_reference"),
    }

    doc.inputs["selected_missing_pages"] = sorted(scope.selected_missing)
    doc.inputs["extra_pages_out_of_scope"] = sorted(scope.selected_extra)

    # --- Operational ---
    doc.operational = {
        "benchmark_status": None,
        "fallback_rate": None,
        "elapsed_s": None,
        "memory_mb": None,
    }

    doc.operational.update({
        key: deepcopy(value) for key, value in request.run_metadata.items()
        if key not in {"pages", "selected_pages", "alignment_summary"}
    })
    doc.operational["selected_pages"] = effective_selected_pages
    # Operational time/recovery cover the extraction's scope, even when this
    # evaluation explicitly selects a smaller subset of its output.
    doc.operational["run_selected_pages"] = selected_pages_from_run(request.run_metadata)
    metadata = corpus_page_metadata(request.manifesto_path)
    run_pages = run_page_metadata(request.run_metadata)
    ref_by_page = _parse_scoped_pages(ref_scoped, "reference")
    hyp_by_page = _parse_scoped_pages(hyp_scoped, "hypothesis")

    page_args = [
        (
            page,
            ref_scoped[page],
            hyp_scoped.get(page, ""),
            page in hyp_scoped,
            ref_by_page[page],
            hyp_by_page.get(page, []),
            request.max_text_edit_cells,
            request.max_alignment_pairs,
        )
        for page in effective_selected_pages
    ]
    try:
        _hw_cores = len(os.sched_getaffinity(0))
    except AttributeError:
        _hw_cores = os.cpu_count() or 1
    n_workers = min(len(page_args), _hw_cores)
    page_results: dict[int, dict] = {}
    if n_workers > 1 and len(page_args) > 1:
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_evaluate_single_page, arg): arg[0] for arg in page_args}
            for future in as_completed(futures):
                r = future.result()
                page_results[r["page"]] = r
    else:
        for arg in page_args:
            r = _evaluate_single_page(arg)
            page_results[r["page"]] = r

    _populate_document(doc, request, ref_scoped, hyp_scoped, ref_by_page, hyp_by_page,
                       [page_results[page] for page in effective_selected_pages])

    for page in effective_selected_pages:
        r = page_results[page]
        result = r["result"]
        doc.per_page.append({
            "page": page,
            "family": metadata.get(page, {}).get("family"),
            "conditions": metadata.get(page, {}).get("conditions", []),
            "selected_but_missing": page not in hyp_scoped,
            "reference_empty": r["ref_chars"] == 0,
            "hypothesis_empty": r["hyp_chars"] == 0,
            "quality": result["quality"],
            "integrity": result["integrity"],
            "evaluation": result["evaluation"],
            "operational": run_pages.get(page, {}),
        })

    validate_result(doc.to_dict())
    return doc


def _populate_quality(
    doc: V2DocumentMetrics, request: EvaluationRequest,
    ref_scoped: dict[int, str], hyp_scoped: dict[int, str],
    ref_nodes: list[MarkdownNode], hyp_nodes: list[MarkdownNode],
) -> list | None:
    """Full metric pipeline for one page, with its own budgets.

    Returns the page-local alignment pairs, or None when the alignment hit
    its resource limit. The document view reuses them (see _populate_document).
    """
    ref_text = pages_markdown(ref_scoped)
    hyp_text = pages_markdown(hyp_scoped)
    # --- Block alignment ---
    alignment_error: str | None = None
    try:
        pairs = align_blocks(ref_nodes, hyp_nodes, max_candidate_pairs=request.max_alignment_pairs)
    except AlignmentResourceLimit as exc:
        pairs = []
        alignment_error = str(exc)
    alignment_summary = AlignmentSummary.from_pairs(pairs, len(ref_nodes), len(hyp_nodes))

    # --- Text metrics (Group 1) ---
    text: TextMetrics = compute_text_metrics(
        ref_nodes, hyp_nodes, pairs if alignment_error is None else None,
        ref_markdown="\n\n".join(ref_scoped.values()),
        hyp_markdown="\n\n".join(hyp_scoped.values()),
        max_edit_cells=request.max_text_edit_cells,
        max_alignment_pairs=request.max_alignment_pairs,
    )
    doc.text = text.to_dict()

    # --- Structure metrics ---
    struct: StructureMetrics = (
        compute_structure_metrics(pairs, ref_nodes, hyp_nodes, max_edit_cells=request.max_text_edit_cells)
        if alignment_error is None else compute_structure_metrics([], [], [])
    )
    _set_structure(doc, struct, alignment_error, len(ref_nodes), len(hyp_nodes))

    # --- Table metrics ---
    tables: TableMetrics = compute_table_metrics(
        ref_nodes, hyp_nodes, max_edit_cells=request.max_text_edit_cells)
    doc.tables = tables.to_dict()

    # --- Critical data metrics ---
    critical: CriticalDataMetrics = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    doc.critical_data = critical.to_dict()

    # --- Integrity metrics ---
    integrity: IntegrityMetrics = compute_integrity_metrics(
        ref_nodes, hyp_nodes,
        selected_pages=set(ref_scoped),
        selected_reference=set(ref_scoped),
        hyp_page_nums=set(hyp_scoped),
        hyp_raw_text=hyp_text,
        ref_raw_text=ref_text,
        text_recall=text.coverage_recall,
    )
    doc.integrity = integrity.to_dict()
    _finish(doc, alignment_summary, alignment_error, len(ref_nodes), len(hyp_nodes))
    return pairs if alignment_error is None else None


def _populate_document(
    doc: V2DocumentMetrics, request: EvaluationRequest,
    ref_scoped: dict[int, str], hyp_scoped: dict[int, str],
    ref_by_page: dict[int, list[MarkdownNode]], hyp_by_page: dict[int, list[MarkdownNode]],
    pages: list[dict],
) -> None:
    """Document view, page-anchored (policy page-anchored-micro-v1).

    Sequential and aligned text rates sum the pages' S/D/I counts. Structure
    and reading order use the union of the page-local alignments with global
    order indices, so inversions between pages remain measurable. Tables,
    critical data, token coverage and integrity compare the whole document.
    Budgets apply per page; document-level recomputation gets one per page.
    """
    ref_nodes = [n for nodes in ref_by_page.values() for n in nodes]
    hyp_nodes = [n for nodes in hyp_by_page.values() for n in nodes]
    budget = request.max_text_edit_cells * max(len(pages), 1)
    failed = [page["page"] for page in pages if page["pairs"] is None]
    alignment_error = (f"Block alignment exceeded its budget on pages {failed}" if failed else None)
    pairs = [] if failed else [pair for page in pages for pair in page["pairs"]]
    alignment_summary = AlignmentSummary.from_pairs(pairs, len(ref_nodes), len(hyp_nodes))

    # --- Text metrics (Group 1) ---
    page_text = [page["result"]["quality"]["text"] for page in pages]
    ref_tokens = Counter(normalize(CanonicalDocument.from_nodes(ref_nodes).visible_text).split())
    hyp_tokens = Counter(normalize(CanonicalDocument.from_nodes(hyp_nodes).visible_text).split())
    normalized = page_anchored_edit_rate([t["cer_sequential_normalized"] for t in page_text],
                                         "MET-TX02", NormProfile.UNICODE_LAYOUT, "chars")
    text = TextMetrics(
        cer_raw=page_anchored_edit_rate([t["cer_markdown_raw"] for t in page_text],
                                        "MET-TX01", NormProfile.RAW, "chars"),
        cer_normalized=normalized,
        cer_visible=replace(normalized, definition_id="MET-TX03",
                            normalization_profile=NormProfile.VISIBLE_TEXT.value,
                            counts=dict(normalized.counts), warnings=list(normalized.warnings)),
        wer_sequential=page_anchored_edit_rate([t["wer_sequential"] for t in page_text],
                                               "MET-TX04", NormProfile.UNICODE_LAYOUT, "words"),
        aligned_block_text_cer=page_anchored_aligned_cer([t["aligned_block_text_cer"] for t in page_text]),
        coverage_recall=text_coverage_recall(ref_nodes, hyp_nodes, ref_tokens=ref_tokens, hyp_tokens=hyp_tokens),
        coverage_precision=text_coverage_precision(ref_nodes, hyp_nodes,
                                                   ref_tokens=ref_tokens, hyp_tokens=hyp_tokens),
    )
    doc.text = text.to_dict()

    # --- Structure and reading order over the union of page alignments ---
    _set_structure(doc, compute_structure_metrics(pairs, ref_nodes, hyp_nodes, max_edit_cells=budget)
                   if alignment_error is None else compute_structure_metrics([], [], []),
                   alignment_error, len(ref_nodes), len(hyp_nodes))

    doc.tables = compute_table_metrics(ref_nodes, hyp_nodes, max_edit_cells=budget).to_dict()
    doc.critical_data = compute_critical_data_metrics(ref_nodes, hyp_nodes).to_dict()
    doc.integrity = compute_integrity_metrics(
        ref_nodes, hyp_nodes,
        selected_pages=set(ref_scoped),
        selected_reference=set(ref_scoped),
        hyp_page_nums=set(hyp_scoped),
        hyp_raw_text=pages_markdown(hyp_scoped),
        ref_raw_text=pages_markdown(ref_scoped),
        text_recall=text.coverage_recall,
    ).to_dict()
    _finish(doc, alignment_summary, alignment_error, len(ref_nodes), len(hyp_nodes))


def _set_structure(doc: V2DocumentMetrics, struct: StructureMetrics, alignment_error: str | None,
                   ref_count: int, hyp_count: int) -> None:
    doc.structure = {k: v for k, v in struct.to_dict().items()
                     if k != "pairwise_reading_order_accuracy"
                     and k != "block_type_confusion"}
    doc.reading_order = {
        "pairwise_reading_order_accuracy": struct.reading_order.to_dict()
            if struct.reading_order else {},
    }
    if alignment_error is not None:
        for group in (doc.structure, doc.reading_order):
            for metric in group.values():
                metric.update(value=None, status="resource_limit", coverage=None,
                              counts={"ref_nodes": ref_count, "hyp_nodes": hyp_count},
                              warnings=[alignment_error])


def _finish(doc: V2DocumentMetrics, alignment_summary: AlignmentSummary, alignment_error: str | None,
            ref_count: int, hyp_count: int) -> None:
    doc.operational["alignment_summary"] = alignment_summary.to_dict()
    if alignment_error is not None:
        # Empty pairs above are an unavailable computation, never an extraction
        # with zero matches. Do not publish its empty summary as observed data.
        doc.operational["alignment_summary"] = {
            "status": "resource_limit", "reason": alignment_error,
            "total_ref": ref_count, "total_hyp": hyp_count,
        }

    # --- Eligibility ---
    doc.finalise()
    eligible, reasons = evaluate_rank_eligibility(doc)
    if any(metric.get("status") == "resource_limit"
           for group in (doc.text, doc.structure, doc.reading_order, doc.tables)
           for metric in group.values()):
        doc.evaluation_status = "partial"
        eligible = False
        reasons.append("evaluation_resource_limit")
    doc.rank_eligible = eligible
    doc.rank_ineligibility_reasons = reasons


def _get_git_sha() -> str | None:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[3],
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
