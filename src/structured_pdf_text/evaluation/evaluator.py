"""Central evaluation API for pre-existing Markdown; no extraction or OCR."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregation import V2DocumentMetrics, evaluate_rank_eligibility
from .block_alignment import (
    AlignmentSummary, align_blocks, AlignmentResourceLimit,
    DEFAULT_MAX_ALIGNMENT_PAIRS, MAX_SIMILARITY_TEXT_PRODUCT,
)
from .edit_distance import DEFAULT_MAX_EDIT_CELLS, EDIT_DISTANCE_POLICY
from .critical_data import CriticalDataMetrics, compute_critical_data_metrics
from .integrity_metrics import IntegrityMetrics, compute_integrity_metrics
from .markdown_parser import PARSER_VERSION, parse_page
from .models import MarkdownNode
from .page_scope import build_page_scope
from .reference_hash import REFERENCE_HASH_PROTOCOL, compute_reference_effective_hash
from .reference_inputs import document_pages, page_number, pages_markdown, selected_pages_from_run
from .structure_metrics import StructureMetrics, compute_structure_metrics
from .table_metrics import TableMetrics, compute_table_metrics
from .text_metrics import TextMetrics, compute_text_metrics


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


def _parse_scoped_pages(pages: dict[int, str], origin: str) -> list[MarkdownNode]:
    nodes: list[MarkdownNode] = []
    for number, body in pages.items():
        nodes.extend(parse_page(body, number, origin, start_seq=len(nodes)))
    return nodes


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
    ref_nodes = _parse_scoped_pages(ref_scoped, "reference")
    hyp_nodes = _parse_scoped_pages(hyp_scoped, "hypothesis")
    ref_text = pages_markdown(ref_scoped)
    hyp_text = pages_markdown(hyp_scoped)

    # --- Reference effective hash (protocol reference-effective-v1) ---
    eff_digest, eff_meta = compute_reference_effective_hash(
        ref_pages,
        selected_pages=effective_selected_pages,
    )

    # --- Provenance ---
    doc.evaluator_info = {
        "implementation_commit": _get_git_sha(),
        "metric_definitions_version": "3.0.1",
        "normalization_policy": "unicode_layout",
        "parser": PARSER_VERSION,
        "alignment_policy": "content-match-v1",
        "entity_policy": "entities-ptbr-v1",
        "text_projection_policy": "parser-visible-leaves-v2",
        "edit_distance_policy": EDIT_DISTANCE_POLICY,
        "resource_policy": {
            "max_text_edit_cells": request.max_text_edit_cells,
            "max_alignment_pairs": request.max_alignment_pairs,
            "max_similarity_text_product": MAX_SIMILARITY_TEXT_PRODUCT,
        },
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
        compute_structure_metrics(pairs, ref_nodes, hyp_nodes)
        if alignment_error is None else compute_structure_metrics([], [], [])
    )
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
                              counts={"ref_nodes": len(ref_nodes), "hyp_nodes": len(hyp_nodes)},
                              warnings=[alignment_error])

    # --- Table metrics ---
    tables: TableMetrics = compute_table_metrics(ref_nodes, hyp_nodes)
    doc.tables = tables.to_dict()

    # --- Critical data metrics ---
    critical: CriticalDataMetrics = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    doc.critical_data = critical.to_dict()

    # --- Integrity metrics ---
    integrity: IntegrityMetrics = compute_integrity_metrics(
        ref_nodes, hyp_nodes,
        selected_pages=scope.selected_reference,
        selected_reference=scope.selected_reference,
        hyp_page_nums=set(hyp_scoped),
        hyp_raw_text=hyp_text,
        ref_raw_text=ref_text,
    )
    doc.integrity = integrity.to_dict()

    # --- Operational ---
    doc.operational = {
        "benchmark_status": "valid",
        "fallback_rate": None,
        "elapsed_s": None,
        "memory_mb": None,
        "alignment_summary": alignment_summary.to_dict(),
    }

    doc.operational.update({
        key: value for key, value in request.run_metadata.items()
        if key not in {"pages", "selected_pages", "alignment_summary"}
    })
    doc.operational["selected_pages"] = effective_selected_pages
    if alignment_error is not None:
        # Empty pairs above are an unavailable computation, never an extraction
        # with zero matches. Do not publish its empty summary as observed data.
        doc.operational["alignment_summary"] = {
            "status": "resource_limit", "reason": alignment_error,
            "total_ref": len(ref_nodes), "total_hyp": len(hyp_nodes),
        }

    # --- Eligibility ---
    doc.finalise()
    eligible, reasons = evaluate_rank_eligibility(doc)
    if any(metric.get("status") == "resource_limit"
           for group in (doc.text, doc.structure, doc.reading_order)
           for metric in group.values()):
        doc.evaluation_status = "partial"
        eligible = False
        reasons.append("evaluation_resource_limit")
    doc.rank_eligible = eligible
    doc.rank_ineligibility_reasons = reasons

    return doc


def _get_git_sha() -> str | None:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parents[3],
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
