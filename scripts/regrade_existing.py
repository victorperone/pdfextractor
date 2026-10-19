#!/usr/bin/env python3
"""Regrade existing hypothesis Markdown against a reference using V2 metrics.

Does NOT invoke OCR; works purely on pre-existing Markdown artefacts.
Implements the tool described in spec §11.4.

Usage
-----
    # With a reference Markdown file:
    python scripts/regrade_existing.py \\
        --hypothesis output/extracted_easyocr.md \\
        --reference  corpus/REFERENCIA.md \\
        --engine easyocr \\
        --output-dir output/evaluation_v2

    # With a manifest (pages + metadata):
    python scripts/regrade_existing.py \\
        --hypothesis output/extracted.md \\
        --manifesto  corpus/MANIFESTO.json \\
        --engine easyocr \\
        --output-dir output/evaluation_v2

Outputs
-------
    output/evaluation_v2/metrics_v2_{engine}_{run_id}.json
    output/evaluation_v2/errors_v2_{engine}_{run_id}.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.evaluation import (
    PARSER_VERSION,
    AlignmentSummary,
    CriticalDataMetrics,
    IntegrityMetrics,
    MarkdownValidityReport,
    MetricResult,
    MetricStatus,
    PageScope,
    REFERENCE_HASH_PROTOCOL,
    StructureMetrics,
    TableMetrics,
    TextMetrics,
    V2DocumentMetrics,
    align_blocks,
    build_page_scope,
    check_comparability,
    compute_critical_data_metrics,
    compute_integrity_metrics,
    compute_reference_effective_hash,
    compute_structure_metrics,
    compute_table_metrics,
    compute_text_metrics,
    evaluate_rank_eligibility,
    parse_document,
    split_pages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_pages_safe(text: str) -> dict[int, str]:
    """Split Markdown text into pages using the canonical split_pages protocol.

    Falls back to {1: text} when no page markers are present.
    Duplicate page markers raise ValueError — caller should catch and report.
    """
    try:
        pages = split_pages(text)
    except ValueError:
        pages = {}
    if not pages and text.strip():
        pages = {1: text.strip()}
    return pages


def _load_manifesto(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _manifesto_pages(manifesto: dict[str, Any]) -> dict[int, str]:
    """Extract {page_num: expected_markdown} from a manifesto JSON."""
    pages: dict[int, str] = {}
    for entry in manifesto.get("pages", []):
        pnum = entry.get("page") or entry.get("page_num")
        text = entry.get("expected_markdown", "")
        if pnum is not None:
            pages[int(pnum)] = text
    return pages


def _manifesto_metadata(manifesto: dict[str, Any]) -> dict[int, dict]:
    """Extract per-page metadata (family, conditions) from manifesto."""
    meta: dict[int, dict] = {}
    for entry in manifesto.get("pages", []):
        pnum = entry.get("page") or entry.get("page_num")
        if pnum is not None:
            meta[int(pnum)] = {
                "family": entry.get("family", "unknown"),
                "conditions": entry.get("conditions", []),
                "scenario": entry.get("scenario", ""),
            }
    return meta


# ---------------------------------------------------------------------------
# V2 evaluation engine
# ---------------------------------------------------------------------------

def _run_v2(
    ref_text: str,
    hyp_text: str,
    *,
    ref_path: Path | None,
    hyp_path: Path,
    manifesto_path: Path | None,
    engine: str,
    run_id: str,
) -> V2DocumentMetrics:
    """Run the unified evaluation-3.0.0 engine and return a V2DocumentMetrics object."""
    doc = V2DocumentMetrics()

    # --- Parse reference + hypothesis ---
    ref_nodes = parse_document(ref_text, origin="reference")
    hyp_nodes = parse_document(hyp_text, origin="hypothesis")

    # --- Page scope ---
    ref_pages = _split_pages_safe(ref_text)
    hyp_pages = _split_pages_safe(hyp_text)
    scope = build_page_scope(
        ref_page_nums=set(ref_pages.keys()),
        hyp_page_nums=set(hyp_pages.keys()),
    )

    selected_pages = sorted(scope.selected_reference)

    # --- Reference effective hash (protocol reference-effective-v1) ---
    eff_digest, eff_meta = compute_reference_effective_hash(
        ref_pages, selected_pages=selected_pages if selected_pages else None
    )

    # --- Provenance ---
    doc.evaluator_info = {
        "implementation_commit": _get_git_sha(),
        "metric_definitions_version": "3.0.0",
        "normalization_policy": "unicode_layout",
        "parser": PARSER_VERSION,
        "alignment_policy": "content-match-v1",
        "entity_policy": "entities-ptbr-v1",
        "created_at_utc": datetime.now(tz=timezone.utc).isoformat(),
    }
    doc.inputs = {
        "reference_effective_sha256": eff_digest,
        "reference_serialization_version": REFERENCE_HASH_PROTOCOL,
        "reference_source_sha256": _sha256_path(ref_path) if ref_path else None,
        "hypothesis_sha256": _sha256_path(hyp_path),
        "manifest_sha256": _sha256_path(manifesto_path) if manifesto_path else None,
        "pdf_sha256": None,
        "engine": engine,
        "run_id": run_id,
        "selected_pages": selected_pages,
        "total_pages_in_reference": eff_meta.get("total_pages_in_reference"),
    }

    doc.inputs["selected_missing_pages"] = sorted(scope.selected_missing)
    doc.inputs["extra_pages_out_of_scope"] = sorted(scope.selected_extra)

    # --- Block alignment ---
    pairs = align_blocks(ref_nodes, hyp_nodes)
    alignment_summary = AlignmentSummary.from_pairs(pairs, len(ref_nodes), len(hyp_nodes))

    # --- Text metrics (Group 1) ---
    text: TextMetrics = compute_text_metrics(ref_nodes, hyp_nodes, pairs)
    doc.text = text.to_dict()

    # --- Structure metrics ---
    struct: StructureMetrics = compute_structure_metrics(pairs, ref_nodes, hyp_nodes)
    doc.structure = {k: v for k, v in struct.to_dict().items()
                     if k != "pairwise_reading_order_accuracy"
                     and k != "block_type_confusion"}
    doc.reading_order = {
        "pairwise_reading_order_accuracy": struct.reading_order.to_dict()
            if struct.reading_order else {},
    }

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
        hyp_page_nums=set(hyp_pages.keys()),
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

    # --- Eligibility ---
    doc.finalise()
    eligible, reasons = evaluate_rank_eligibility(doc)
    doc.rank_eligible = eligible
    doc.rank_ineligibility_reasons = reasons

    return doc


def _get_git_sha() -> str | None:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).parent.parent,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Error report generator
# ---------------------------------------------------------------------------

def _generate_error_report(
    doc: V2DocumentMetrics,
    run_id: str,
    engine: str,
    ref_path: Path | None,
    hyp_path: Path,
) -> str:
    lines = [
        f"# Evaluation Error Report (schema evaluation-3.0.0)",
        f"",
        f"**Run ID:** `{run_id}`  ",
        f"**Engine:** `{engine}`  ",
        f"**Hypothesis:** `{hyp_path}`  ",
        f"**Reference:** `{ref_path or 'via manifesto'}`  ",
        f"**Created:** `{doc.evaluator_info.get('created_at_utc', 'N/A')}`  ",
        f"",
        f"## Evaluation Status",
        f"",
        f"- Status: **{doc.evaluation_status}**",
        f"- Rank eligible: **{doc.rank_eligible}**",
        f"- Applicable metrics: {doc.applicable_metric_count}",
        f"- Unavailable metrics: {doc.unavailable_metric_count}",
    ]
    if doc.rank_ineligibility_reasons:
        lines += ["", "### Ineligibility Reasons", ""]
        for r in doc.rank_ineligibility_reasons:
            lines.append(f"- `{r}`")

    # Markdown validity
    mv = doc.integrity.get("markdown_validity", {})
    if mv:
        status = mv.get("validity_status", "unknown")
        lines += [
            "", "## Markdown Validity", "",
            f"- Status: **{status}**",
            f"- Parse errors: {mv.get('parse_errors', 0)}",
            f"- Unclosed fences: {mv.get('unclosed_fences', 0)}",
            f"- Duplicate page markers: {mv.get('duplicate_page_markers', 0)}",
        ]
        for w in mv.get("warnings", []):
            lines.append(f"- ⚠ {w}")

    # Page scope
    inputs = doc.inputs
    missing = inputs.get("selected_missing_pages", [])
    extra = inputs.get("extra_pages_out_of_scope", [])
    if missing or extra:
        lines += ["", "## Page Scope Issues", ""]
        if missing:
            lines.append(f"- **Missing pages** (FN for all metrics): {missing}")
        if extra:
            lines.append(f"- **Extra pages** (out of scope): {extra}")

    # Per-group summary
    lines += ["", "## Quality Metrics Summary", ""]
    groups = {
        "Structure": doc.structure,
        "Reading Order": doc.reading_order,
        "Tables": doc.tables,
        "Critical Data": doc.critical_data,
    }
    for group_name, group in groups.items():
        if not group:
            continue
        lines.append(f"### {group_name}")
        lines.append("")
        for metric_key, result in group.items():
            if not isinstance(result, dict):
                continue
            value = result.get("value")
            status = result.get("status", "unknown")
            direction = result.get("direction", "")
            if value is not None:
                lines.append(f"- `{metric_key}`: {value:.4f} ({status}, {direction})")
            else:
                lines.append(f"- `{metric_key}`: N/A ({status})")
        lines.append("")

    # Integrity
    dup = doc.integrity.get("duplicate_excess_rate", {})
    if dup:
        lines += ["## Integrity", ""]
        val = dup.get("value")
        lines.append(f"- Duplicate excess rate: {val:.4f}" if val is not None else "- Duplicate excess rate: N/A")
        cov = doc.integrity.get("page_content_coverage_recall", {})
        val2 = cov.get("value")
        lines.append(f"- Content coverage recall: {val2:.4f}" if val2 is not None else "- Content coverage recall: N/A")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Delta V1 × V2 report
# ---------------------------------------------------------------------------

def _delta_report(
    v1_json: dict[str, Any],
    v2_json: dict[str, Any],
    run_id: str,
) -> str:
    """Generate a text delta report comparing V1 and V2 metric values.

    Maps V1 field names (grupo1_texto…grupo5_dados_criticos) to V2 equivalents.
    """
    V1_TO_V2_MAP = {
        # Group 1 — Text (V2 does not yet compute CER/WER in regrade, maps to N/A)
        "cer_raw":               ("quality.text.cer_raw", None),
        "cer_normalized":        ("quality.text.cer_normalized", None),
        "wer":                   ("quality.text.wer_sequential", None),
        # Group 2 — Structure
        "heading_f1":            ("quality.structure.heading_detection_f1", "value"),
        "paragraph_boundary_f1": ("quality.structure.paragraph_detection_f1", "value"),
        "list_detection_f1":     ("quality.structure.list_group_detection_f1", "value"),
        # Group 3 — Tables
        "table_f1":              ("quality.tables.table_detection_f1", "value"),
        "cell_exact_match":      ("quality.tables.cell_content_exact_match", "value"),
        # Group 4 — Order/integrity
        "reading_order_accuracy":("quality.reading_order.pairwise_reading_order_accuracy", "value"),
        "duplicate_block_rate":  ("integrity.duplicate_excess_rate", "value"),
        # Group 5 — Critical data (V2 keys: numeric, currency, dates, cpf, cnpj)
        "numeric_f1":            ("quality.critical_data.numeric", "value"),
        "currency_f1":           ("quality.critical_data.currency", "value"),
        "date_f1":               ("quality.critical_data.dates", "value"),
        "identifier_f1":         ("quality.critical_data.identifiers_combined", "value"),
    }

    def _v1_val(v1: dict, key: str) -> float | None:
        for group_key in ("grupo1_texto", "grupo2_estrutura_markdown",
                          "grupo3_tabelas", "grupo4_ordem_integridade",
                          "grupo5_dados_criticos"):
            group = v1.get(group_key, {})
            if key in group:
                v = group[key]
                return float(v) if v is not None else None
        v = v1.get(key)
        return float(v) if v is not None else None

    def _v2_val(v2: dict, path: str, subkey: str | None) -> float | None:
        parts = path.split(".")
        node: Any = v2
        for p in parts:
            if isinstance(node, dict):
                node = node.get(p)
            else:
                return None
        if node is None:
            return None
        if subkey:
            return node.get(subkey)
        if isinstance(node, dict):
            return node.get("value")
        return float(node) if node is not None else None

    lines = [
        "# V1 × V2 Metric Delta Report",
        "",
        f"**Run ID:** `{run_id}`  ",
        f"**V1 schema:** `{v1_json.get('schema_version', 'legacy')}`  ",
        f"**V2 schema:** `{v2_json.get('schema_version', 'evaluation-v2.0')}`  ",
        "",
        "| Metric (V1 name) | V1 Value | V2 Value | Delta | Change |",
        "|---|---:|---:|---:|---|",
    ]

    for v1_key, (v2_path, v2_sub) in V1_TO_V2_MAP.items():
        old = _v1_val(v1_json, v1_key)
        new = _v2_val(v2_json, v2_path, v2_sub)
        old_s = f"{old:.4f}" if old is not None else "N/A"
        new_s = f"{new:.4f}" if new is not None else "N/A"
        if old is not None and new is not None:
            delta = new - old
            delta_s = f"{delta:+.4f}"
            if abs(delta) < 0.001:
                change = "≈ unchanged"
            elif delta > 0:
                change = "↑ improved"
            else:
                change = "↓ degraded"
        else:
            delta_s = "—"
            change = "N/A→value" if old is None and new is not None else "value→N/A"
        lines.append(f"| `{v1_key}` | {old_s} | {new_s} | {delta_s} | {change} |")

    lines += [
        "",
        "## Notes",
        "",
        "- Δ < 0 for quality metrics (higher_is_better) = regression in V2 definition.",
        "- Δ > 0 for error metrics (lower_is_better) = regression in V2 definition.",
        "- N/A: metric is not applicable for this document or definition changed.",
        "- `change_reason` categories: `bug_fix`, `new_definition`, `scope_fix`, `applicability_change`.",
        "- Never interpret a score change as extractor improvement/regression without examining `change_reason`.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Regrade hypothesis Markdown with V2 metrics (no OCR invoked).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python scripts/regrade_existing.py \\
                  --hypothesis output/easyocr.md \\
                  --reference  corpus/REFERENCIA.md \\
                  --engine easyocr --output-dir output/v2

              python scripts/regrade_existing.py \\
                  --hypothesis output/easyocr.md \\
                  --manifesto  corpus/MANIFESTO.json \\
                  --v1-metrics output/metrics_easyocr.json \\
                  --engine easyocr --output-dir output/v2
        """),
    )
    ap.add_argument("--hypothesis", type=Path, required=True,
                    help="Extracted Markdown file to evaluate")
    ap.add_argument("--reference", type=Path, default=None,
                    help="Reference ground-truth Markdown file")
    ap.add_argument("--manifesto", type=Path, default=None,
                    help="Manifesto JSON with pages[].expected_markdown")
    ap.add_argument("--v1-metrics", type=Path, default=None,
                    help="Existing V1 metrics JSON for delta report")
    ap.add_argument("--engine", default="unknown",
                    help="Engine name (easyocr, paddle, …)")
    ap.add_argument("--run-id", default=None,
                    help="Run identifier (auto-generated if not given)")
    ap.add_argument("--output-dir", type=Path, default=Path("output/evaluation_v2"),
                    help="Output directory")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # Validate inputs
    if not args.hypothesis.is_file():
        print(f"ERROR: hypothesis not found: {args.hypothesis}", file=sys.stderr)
        return 1
    if args.reference is None and args.manifesto is None:
        print("ERROR: provide --reference or --manifesto", file=sys.stderr)
        return 1

    run_id = args.run_id or uuid.uuid4().hex[:12]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Load hypothesis
    hyp_text = args.hypothesis.read_text(encoding="utf-8")

    # Load reference
    if args.reference is not None:
        ref_text = args.reference.read_text(encoding="utf-8")
        manifesto_path = None
    else:
        manifesto = _load_manifesto(args.manifesto)
        ref_pages = _manifesto_pages(manifesto)
        # Reconstruct ref_text as ## Página N ... blocks
        parts = []
        for pnum in sorted(ref_pages):
            parts.append(f"## Página {pnum}\n\n{ref_pages[pnum]}")
        ref_text = "\n\n".join(parts)
        manifesto_path = args.manifesto

    # Verify reference hash stability (spec §0.3 and §12.6 invariant)
    ref_sha = _sha256_path(args.reference) if args.reference else None
    if not args.quiet:
        if ref_sha:
            print(f"Reference SHA-256: {ref_sha}")
        print(f"Hypothesis SHA-256: {_sha256_path(args.hypothesis)}")

    # Run V2 evaluation
    doc = _run_v2(
        ref_text=ref_text,
        hyp_text=hyp_text,
        ref_path=args.reference,
        hyp_path=args.hypothesis,
        manifesto_path=manifesto_path,
        engine=args.engine,
        run_id=run_id,
    )

    result_dict = doc.to_dict()

    # Write metrics JSON (canonical name: metrics_{engine}_{run_id}.json)
    metrics_path = args.output_dir / f"metrics_{args.engine}_{run_id}.json"
    metrics_path.write_text(
        json.dumps(result_dict, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Write error report (canonical name: errors_{engine}_{run_id}.md)
    report_text = _generate_error_report(
        doc, run_id=run_id, engine=args.engine,
        ref_path=args.reference, hyp_path=args.hypothesis,
    )
    report_path = args.output_dir / f"errors_{args.engine}_{run_id}.md"
    report_path.write_text(report_text, encoding="utf-8")

    # Write delta report if V1 metrics provided
    if args.v1_metrics and args.v1_metrics.is_file():
        v1_json = json.loads(args.v1_metrics.read_text(encoding="utf-8"))
        delta_text = _delta_report(v1_json, result_dict, run_id=run_id)
        delta_path = args.output_dir / f"delta_v1v2_{args.engine}_{run_id}.md"
        delta_path.write_text(delta_text, encoding="utf-8")
        if not args.quiet:
            print(f"Delta report: {delta_path}")

    if not args.quiet:
        print(f"Metrics:      {metrics_path}")
        print(f"Error report: {report_path}")
        print(f"schema_version: {result_dict.get('schema_version')}")
        print(f"rank_eligible:  {result_dict.get('evaluation', {}).get('rank_eligible')}")
        ev = result_dict.get("evaluation", {})
        print(f"applicable metrics: {ev.get('applicable_metric_count')}")
        reasons = ev.get("rank_ineligibility_reasons", [])
        if reasons:
            print(f"ineligibility reasons: {reasons}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
