#!/usr/bin/env python3
"""
Compare engines.

Reads multiple metrics JSON files (one per engine) and generates a
comparison table in Markdown.

Before building the table the script validates that all runs are comparable:
valid matching PDF, effective-reference and manifesto SHA-256 identities,
the exact selected page set, mode, benchmark protocol and evaluator policies.
Run status and missing-page counts are outcomes; they do not reject otherwise
comparable runs. API and CLI share the canonical validation contract.

Usage (Windows server):
    python scripts\\compare_engines.py ^
        output\\fase8\\metrics_paddle_*.json ^
        output\\fase8\\metrics_tesseract_*.json ^
        output\\fase8\\metrics_rapidocr_onnx_*.json ^
        output\\fase8\\metrics_rapidocr_openvino_*.json ^
        output\\fase8\\metrics_easyocr_*.json ^
        --output output\\fase8\\comparison_table.md

Or passing all at once:
    python scripts\\compare_engines.py output\\fase8\\metrics_*.json ^
        --output output\\fase8\\comparison_table.md

Output:
    output/fase8/comparison_table.md
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path


# Metric groups to display, in order
_METRICS: list[tuple[str, str, str]] = [
    # (display_name, group_key, metric_key)
    # Group 1 — Text
    ("CER Raw",               "grupo1_texto",                 "cer_raw"),
    ("CER Normalized",        "grupo1_texto",                 "cer_normalized"),
    ("CER Text Only",         "grupo1_texto",                 "cer_text_only"),
    ("WER",                   "grupo1_texto",                 "wer"),
    ("Word Accuracy",         "grupo1_texto",                 "word_accuracy"),
    ("Substitution Rate",     "grupo1_texto",                 "substitution_rate"),
    ("Deletion Rate",         "grupo1_texto",                 "deletion_rate"),
    ("Insertion Rate",        "grupo1_texto",                 "insertion_rate"),
    ("Omission Rate",         "grupo1_texto",                 "omission_rate"),
    # Group 2 — Markdown Structure
    ("Heading F1",            "grupo2_estrutura_markdown",    "heading_f1"),
    ("Heading Level Acc.",    "grupo2_estrutura_markdown",    "heading_level_accuracy"),
    ("Heading Text CER",      "grupo2_estrutura_markdown",    "heading_text_cer"),
    ("Block F1",              "grupo2_estrutura_markdown",    "block_f1"),
    ("Paragraph Boundary F1", "grupo2_estrutura_markdown",    "paragraph_boundary_f1"),
    ("List Detection F1",     "grupo2_estrutura_markdown",    "list_detection_f1"),
    ("Markdown AST Sim.",     "grupo2_estrutura_markdown",    "markdown_ast_similarity"),
    # Group 3 — Tables
    ("Table F1",              "grupo3_tabelas",               "table_f1"),
    ("Row F1",                "grupo3_tabelas",               "row_f1"),
    ("Column F1",             "grupo3_tabelas",               "column_f1"),
    ("Table Dim. Accuracy",   "grupo3_tabelas",               "table_dimension_accuracy"),
    ("Cell Exact Match",      "grupo3_tabelas",               "cell_exact_match"),
    ("Cell CER",              "grupo3_tabelas",               "cell_cer"),
    ("Cell Alignment Acc.",   "grupo3_tabelas",               "cell_alignment_accuracy"),
    ("Table Structure Sim.",  "grupo3_tabelas",               "table_structure_similarity"),
    ("Table Content F1",      "grupo3_tabelas",               "table_content_f1"),
    # Group 4 — Order and Integrity
    ("Reading Order Acc.",    "grupo4_ordem_integridade",     "reading_order_accuracy"),
    ("Duplicate Content",     "grupo4_ordem_integridade",     "duplicate_content_rate"),
    ("Header Leakage",        "grupo4_ordem_integridade",     "header_leakage_rate"),
    ("Footer Leakage",        "grupo4_ordem_integridade",     "footer_leakage_rate"),
    ("Page# Leakage",         "grupo4_ordem_integridade",     "page_number_leakage_rate"),
    ("Failure Rate",          "grupo4_ordem_integridade",     "failure_rate"),
    ("Invalid Markdown",      "grupo4_ordem_integridade",     "invalid_markdown_rate"),
    # Group 5 — Critical Data
    # *_exact_match == recall; show Precision, Recall and F1 for each category
    # so that a model that copies all reference values but also invents extras
    # cannot score as "perfect" on a single recall-only column.
    ("Numeric Recall",        "grupo5_dados_criticos",        "numeric_exact_match"),
    ("Numeric Precision",     "grupo5_dados_criticos",        "numeric_precision"),
    ("Numeric F1",            "grupo5_dados_criticos",        "numeric_f1"),
    ("Date Recall",           "grupo5_dados_criticos",        "date_exact_match"),
    ("Date Precision",        "grupo5_dados_criticos",        "date_precision"),
    ("Date F1",               "grupo5_dados_criticos",        "date_f1"),
    ("Currency Recall",       "grupo5_dados_criticos",        "currency_exact_match"),
    ("Currency Precision",    "grupo5_dados_criticos",        "currency_precision"),
    ("Currency F1",           "grupo5_dados_criticos",        "currency_f1"),
    ("Identifier Recall",     "grupo5_dados_criticos",        "identifier_exact_match"),
    ("Identifier Precision",  "grupo5_dados_criticos",        "identifier_precision"),
    ("Identifier F1",         "grupo5_dados_criticos",        "identifier_f1"),
]

# Metrics where lower is better (errors)
_LOWER_IS_BETTER = {
    "cer_raw", "cer_normalized", "cer_text_only", "wer", "substitution_rate",
    "deletion_rate", "insertion_rate", "omission_rate", "heading_text_cer",
    "cell_cer", "duplicate_content_rate", "duplicate_block_rate",  # MET-025 fix
    "header_leakage_rate",
    "footer_leakage_rate", "page_number_leakage_rate", "failure_rate",
    "invalid_markdown_rate",
    "missing_page_rate", "easyocr_fallback_rate",
}


def _load_metrics(path: Path) -> dict:
    """Load a metrics JSON file produced by compute_metrics.py."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _get_value(data: dict, group: str, key: str) -> float | None:
    """Safely retrieve a nested metric value from a metrics dict, returning None if absent."""
    group_data = data.get(group, {})
    val = group_data.get(key)
    if val is None:
        return None
    return float(val)


def _fmt(val: float | None) -> str:
    """Format a float metric value to 4 decimal places, or '—' if None."""
    if val is None:
        return "—"
    return f"{val:.4f}"


def _best_engines(values: dict[str, float | None], key: str, tol: float = 1e-6) -> set[str]:
    """Return all engine names tied for best value for this metric.

    Ties within `tol` are treated as equal so that e.g. 0.511000 and 0.511000
    from two RapidOCR runtimes both show as winners rather than only the first.
    Returns an empty set when no values are available.
    """
    valid = {eng: v for eng, v in values.items() if v is not None}
    if not valid:
        return set()
    if key in _LOWER_IS_BETTER:
        best_val = min(valid.values())
        return {eng for eng, v in valid.items() if v <= best_val + tol}
    else:
        best_val = max(valid.values())
        return {eng for eng, v in valid.items() if v >= best_val - tol}


# ---------------------------------------------------------------------------
# Comparability validation (F07)
# ---------------------------------------------------------------------------

def _comparability_key(data: dict) -> dict:
    """Extract the fields that must be identical across all compared runs.

    Only experiment inputs belong here. Missing pages and run status are
    engine outcomes and must remain visible as metrics instead of making a run
    incomparable. A missing exact page list is represented as None; callers
    cannot silently downgrade to matching page counts.
    """
    run_block = data.get("run") or {}
    exact_pages = run_block.get("selected_pages")
    if not isinstance(exact_pages, list):
        exact_pages = data.get("selected_pages")
    try:
        selected = tuple(sorted(int(page) for page in exact_pages))
    except (TypeError, ValueError):
        selected = None
    return {
        "pdf_sha256": data.get("pdf_sha256"),
        "reference_sha256": data.get("reference_sha256"),
        "manifest_sha256": data.get("manifest_sha256"),
        "mode": data.get("mode"),
        "selected_pages": selected,
        "benchmark_protocol_id": (data.get("run") or {}).get(
            "benchmark_protocol_id", data.get("benchmark_protocol_id")
        ),
    }


def _validate_comparability(
    all_data: list[tuple[str, dict]],
    *,
    allow_partial: bool,
) -> list[str]:
    """Check that all runs are comparable. Return a list of error strings (empty = OK)."""
    errors: list[str] = []
    # Kept as a call/CLI compatibility argument. Partial, recovered, and
    # invalid statuses are run outcomes; usable metric artifacts still compare.
    _ = allow_partial

    # Check that all runs share the same comparability key.
    if len(all_data) > 1:
        keys = [(eng, _comparability_key(data)) for eng, data in all_data]
        ref_eng, ref_key = keys[0]
        for eng, key in keys:
            if key["selected_pages"] is None:
                errors.append(
                    f"Run '{eng}' does not contain an exact selected page list. "
                    "Regenerate its metrics before comparing runs."
                )
            if key["benchmark_protocol_id"] is None:
                errors.append(
                    f"Run '{eng}' has no benchmark_protocol_id. Regenerate its metrics "
                    "with the versioned benchmark protocol before comparing runs."
                )
        for eng, key in keys[1:]:
            for field, ref_val in ref_key.items():
                val = key.get(field)
                if ref_val != val:
                    errors.append(
                        f"Comparability mismatch on '{field}': "
                        f"'{ref_eng}'={ref_val!r} vs '{eng}'={val!r}. "
                        "Runs are not comparable."
                    )

    return errors


def _render_comparison_table(all_data: list[tuple[str, dict]]) -> str:
    """Render a Markdown comparison table from a list of (engine_name, metrics_dict) pairs.

    Each metric row highlights the best engine value in bold and indicates
    direction (↑ higher is better / ↓ lower is better).
    """
    engines = [name for name, _ in all_data]
    engine_headers = " | ".join(f"**{e}**" for e in engines)

    sections = {
        "grupo1_texto": "Grupo 1 — Texto",
        "grupo2_estrutura_markdown": "Grupo 2 — Estrutura Markdown",
        "grupo3_tabelas": "Grupo 3 — Tabelas",
        "grupo4_ordem_integridade": "Grupo 4 — Ordem e Integridade",
        "grupo5_dados_criticos": "Grupo 5 — Dados Críticos",
    }

    lines = [
        "# Comparativo de perfis de implantação OCR",
        "",
        "Este relatório compara configurações de implantação completas. Diferenças de família, modelo, provider e pré-processamento fazem parte do perfil registrado.",
        "",
        f"Engines avaliadas: {', '.join(f'`{e}`' for e in engines)}",
        "",
        "Legenda: **negrito** = melhor valor na linha  |  ↓ menor é melhor  |  ↑ maior é melhor",
        "",
    ]

    current_group = ""
    header_written = False

    for display_name, group_key, metric_key in _METRICS:
        # Section header
        if group_key != current_group:
            if header_written:
                lines.append("")
            current_group = group_key
            section_title = sections.get(group_key, group_key)
            lines += [
                f"## {section_title}",
                "",
                f"| Métrica | {engine_headers} |",
                "|---" + "|---" * len(engines) + "|",
            ]
            header_written = True

        # Row values
        values: dict[str, float | None] = {}
        for eng, data in all_data:
            values[eng] = _get_value(data, group_key, metric_key)

        best_set = _best_engines(values, metric_key)
        direction = "↓" if metric_key in _LOWER_IS_BETTER else "↑"

        row_cells = []
        for eng in engines:
            v = values[eng]
            cell = _fmt(v)
            if eng in best_set and v is not None:
                cell = f"**{cell}**"
            row_cells.append(cell)

        lines.append(
            f"| {display_name} {direction} | " + " | ".join(row_cells) + " |"
        )

    lines += [
        "",
        "---",
        "",
        "## Metadados dos Runs",
        "",
        "| Configuração de implantação | Run ID | Páginas avaliadas | Selecionadas ausentes | Content status | Stability | Recoveries | Render s | OCR s | Processing s | Elapsed s | Parent Peak RSS MB | Process Tree Peak RSS MB | Missing penalizado |",
        "|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for eng, data in all_data:
        run_id = data.get("run_id", "—")
        n_eval = data.get("pages_evaluated", "—")
        # schema v2 field; fall back to old field name for backwards compatibility
        n_missing = data.get(
            "pages_selected_but_missing_count",
            data.get("pages_missing_in_hypothesis", "—"),
        )
        bstatus = data.get("benchmark_status", "—")
        stability = data.get("stability_status", "—")
        recoveries = data.get("recovery_count", 0)
        run = data.get("run") or {}
        elapsed = data.get("elapsed_s", run.get("elapsed_s"))
        timings = data.get("timings", {})
        render_s = timings.get("render_s", "—")
        ocr_s = timings.get("ocr_s", "—")
        processing_s = timings.get("assembly_s", "—")
        memory = data.get("memory", {})
        parent_peak = memory.get("peak_rss_bytes")
        tree_peak = memory.get("sampled_process_tree_peak_rss_bytes")
        parent_peak_mb = f"{parent_peak / (1024 * 1024):.1f}" if parent_peak is not None else "—"
        tree_peak_mb = f"{tree_peak / (1024 * 1024):.1f}" if tree_peak is not None else "—"
        penalised = "sim" if data.get("missing_pages_penalised") else "não"
        lines.append(
            f"| `{eng}` | {run_id} | {n_eval} | {n_missing} | {bstatus} | {stability} | {recoveries} | {render_s} | {ocr_s} | {processing_s} | {elapsed if elapsed is not None else '—'} | {parent_peak_mb} | {tree_peak_mb} | {penalised} |"
        )

    lines += ["", "### Perfis efetivos das configurações", "", "| Configuração | Perfil registrado |", "|---|---|"]
    for eng, data in all_data:
        identity = data.get("engine_identity") or (data.get("run") or {}).get("engine_identity", {})
        profile = {
            key: identity.get(key)
            for key in ("runtime", "profile", "language", "device", "extra")
            if identity.get(key) not in (None, {}, "")
        }
        profile_text = json.dumps(profile, ensure_ascii=False, sort_keys=True)
        profile_text = profile_text.replace("|", "\\|") if profile else "—"
        lines.append(f"| `{eng}` | `{profile_text}` |")

    lines += ["", ""]

    # --- By-condition breakdown ---
    # Groups per_page results by the "conditions" tag from the corpus manifest
    # and macro-averages key metrics. Requires per_page to be present in each
    # metrics file (it always is when produced by compute_metrics.py).
    condition_section = _render_by_condition(all_data, "conditions", "Condição")
    if condition_section:
        lines += condition_section
    family_section = _render_by_condition(all_data, "family", "Família")
    if family_section:
        lines += family_section

    return "\n".join(lines)


_BY_CONDITION_METRICS: list[tuple[str, str]] = [
    # (display_name, per_page_key)
    ("CER Text-Only ↓", "cer_text_only"),
    ("WER ↓",           "wer"),
    ("Deletion ↓",      "deletion_rate"),
    ("Currency F1 ↑",   "currency_f1"),
    ("Identifier F1 ↑", "identifier_f1"),
    ("Cell CER ↓",      "cell_cer"),
    ("Failure Rate ↓", "failure_rate"),
    ("Missing Page Rate ↓", "missing_page_rate"),
    ("Recovery Fallback Rate ↓", "easyocr_fallback_rate"),
]


def _render_by_condition(
    all_data: list[tuple[str, dict]], field: str = "conditions", title: str = "Condição"
) -> list[str]:
    """Render a macro-averaged breakdown by a corpus metadata field.

    Returns an empty list when no per_page data is available (old files).
    """
    # Collect all condition labels across all engines.
    all_conditions: set[str] = set()
    engine_by_cond: dict[str, dict[str, list[dict]]] = {}  # engine -> cond -> [page_entries]

    for eng, data in all_data:
        per_page = data.get("per_page", [])
        if not per_page:
            continue
        engine_by_cond[eng] = {}
        for entry in per_page:
            labels = entry.get(field) or ("sem condição" if field == "conditions" else "sem família")
            if isinstance(labels, str):
                labels = [labels]
            for cond in labels:
                all_conditions.add(str(cond))
                engine_by_cond.setdefault(eng, {}).setdefault(str(cond), []).append(entry)

    if not all_conditions:
        return []

    engines = [eng for eng, _ in all_data if eng in engine_by_cond]
    if not engines:
        return []

    engine_headers = " | ".join(f"**{e}**" for e in engines)
    lines: list[str] = [
        "---",
        "",
        f"## Breakdown por {title} do Corpus",
        "",
        f"> Macro-média das páginas agrupadas por `{field}` do manifesto.",
        "",
    ]

    for display_name, key in _BY_CONDITION_METRICS:
        lines += [
            f"### {display_name}",
            "",
            f"| Condição | {engine_headers} |",
            "|---" + "|---" * len(engines) + "|",
        ]
        for cond in sorted(all_conditions):
            row = [f"`{cond}`"]
            best_vals: dict[str, float | None] = {}
            for eng in engines:
                pages = engine_by_cond.get(eng, {}).get(cond, [])
                vals = [p[key] for p in pages if key in p and p[key] is not None]
                best_vals[eng] = sum(vals) / len(vals) if vals else None
            best_set = _best_engines(best_vals, key)
            for eng in engines:
                v = best_vals[eng]
                cell = _fmt(v)
                if eng in best_set and v is not None:
                    cell = f"**{cell}**"
                row.append(cell)
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Schema version support — evaluation-3.0.0 is canonical; V1/V2 are legacy
# ---------------------------------------------------------------------------

_V3_SCHEMA = "evaluation-3.0.0"
_V2_SCHEMA = "evaluation-v2.0"  # legacy — kept for rejection message only

# V3 metrics: (display_name, dot-path relative to 'quality' or 'integrity', direction)
# Path format: "section.key"  (the value is always at result["value"])
_V2_METRICS: list[tuple[str, str, str]] = [
    # Text (MET-TX01–TX07)
    ("CER Markdown Raw",          "text.cer_markdown_raw",            "lower"),
    ("CER Sequential Normalized", "text.cer_sequential_normalized",   "lower"),
    ("CER Sequential Visible",    "text.cer_sequential_visible",      "lower"),
    ("WER Sequential",            "text.wer_sequential",              "lower"),
    ("Aligned Block CER",         "text.aligned_block_text_cer",      "lower"),
    ("Coverage Recall",           "text.text_coverage_recall",        "higher"),
    ("Coverage Precision",        "text.text_coverage_precision",     "higher"),
    # Structure
    ("Heading F1",                "structure.heading_detection_f1",           "higher"),
    ("Heading Text CER",          "structure.heading_text_cer",               "lower"),
    ("Heading Precision",         "structure.heading_precision",              "higher"),
    ("Heading Recall",            "structure.heading_recall",                 "higher"),
    ("Heading Level Match",       "structure.heading_level_match_rate",       "higher"),
    ("Paragraph F1",              "structure.paragraph_detection_f1",         "higher"),
    ("List Group F1",             "structure.list_group_detection_f1",        "higher"),
    ("List Item F1",              "structure.list_item_detection_f1",         "higher"),
    ("List Hierarchy Acc.",       "structure.list_hierarchy_accuracy",        "higher"),
    # Reading order
    ("Reading Order Acc.",        "reading_order.pairwise_reading_order_accuracy", "higher"),
    # Tables
    ("Table Detection F1",        "tables.table_detection_f1",              "higher"),
    ("Table Page Location Acc.",  "tables.table_page_location_accuracy",    "higher"),
    ("Table Row F1",              "tables.table_row_detection_f1",          "higher"),
    ("Col. Count Accuracy",       "tables.table_column_count_accuracy",     "higher"),
    ("Cell Detection F1",         "tables.cell_detection_f1",               "higher"),
    ("Cell Exact Match",          "tables.cell_content_exact_match",        "higher"),
    ("Cell Alignment Acc.",       "tables.cell_alignment_accuracy",         "higher"),
    ("Cell CER",                  "tables.cell_cer",                        "lower"),
    ("Table Content F1",          "tables.table_content_f1",                "higher"),
    # Critical data — document F1 (position-independent)
    ("Numeric Doc F1",            "critical_data.numeric",                  "higher"),
    ("Currency Doc F1",           "critical_data.currency",                 "higher"),
    ("Percent Doc F1",            "critical_data.percent",                  "higher"),
    ("Dates Doc F1",              "critical_data.dates",                    "higher"),
    ("CPF Doc F1",                "critical_data.cpf",                      "higher"),
    ("CNPJ Doc F1",               "critical_data.cnpj",                     "higher"),
    ("Identifiers Doc F1",        "critical_data.identifiers_combined",     "higher"),
    # Critical data — page F1 (penalises cross-page displacements)
    ("Numeric Page F1",           "critical_data.numeric_page",             "higher"),
    ("Currency Page F1",          "critical_data.currency_page",            "higher"),
    ("Percent Page F1",           "critical_data.percent_page",             "higher"),
    ("Dates Page F1",             "critical_data.dates_page",               "higher"),
    ("CPF Page F1",               "critical_data.cpf_page",                 "higher"),
    ("CNPJ Page F1",              "critical_data.cnpj_page",                "higher"),
    ("Identifiers Page F1",       "critical_data.identifiers_combined_page","higher"),
]

# Integrity metrics are one level up (not inside 'quality')
_V2_INTEGRITY_METRICS: list[tuple[str, str, str]] = [
    ("Duplicate Excess Rate",     "integrity.duplicate_excess_rate",              "lower"),
    ("Missing Page Rate",         "integrity.missing_page_rate",                  "lower"),
    ("Content Coverage Recall",   "integrity.page_content_coverage_recall",       "higher"),
]


def _detect_schema(data: dict) -> str:
    """Classify schema: 'v3' (canonical), 'v2' (legacy), or 'v1' (no schema_version)."""
    sv = data.get("schema_version", "")
    if sv == _V3_SCHEMA:
        return "v3"
    if sv == _V2_SCHEMA:
        return "v2"
    return "v1"


def _validate_schema_homogeneity(all_data: list[tuple[str, dict]]) -> list[str]:
    """Return error strings if any run is not schema evaluation-3.0.0.

    V1 and V2 results must be re-evaluated with regrade_existing.py before
    comparing.  Mixing any two schema versions is rejected.
    """
    errors: list[str] = []
    # Do not collapse runs by engine name: duplicate labels can otherwise hide
    # a legacy artifact behind a later canonical artifact.
    for eng, data in all_data:
        s = _detect_schema(data)
        if s != "v3":
            if s == "v2":
                errors.append(
                    f"Engine '{eng}': schema evaluation-v2.0 is not accepted for "
                    f"comparison. Re-evaluate with regrade_existing.py to produce "
                    f"evaluation-3.0.0 output."
                )
            else:
                raw_sv = data.get("schema_version", "missing")
                errors.append(
                    f"Engine '{eng}': schema version '{raw_sv}' "
                    f"is not accepted. Only evaluation-3.0.0 is supported. "
                    f"Re-evaluate with regrade_existing.py."
                )
    return errors


def _comparability_key_v2(data: dict) -> dict:
    """Compatibility wrapper for the package's canonical input/config key."""
    from structured_pdf_text.evaluation.comparability import comparability_key
    return comparability_key(data)


def _validate_comparability_v2(
    all_data: list[tuple[str, dict]],
) -> list[str]:
    """Use the same fail-closed rules as public check_comparability()."""
    from structured_pdf_text.evaluation.comparability import validate_comparison
    return validate_comparison(all_data)


def _get_value_v2(data: dict, dot_path: str) -> float | None:
    """Navigate a dot-path into a V2 JSON document and return the metric value.

    For quality metrics the path is 'section.key' inside data['quality'].
    For integrity metrics the path is 'integrity.key' at the top level.
    The terminal node is a canonical MetricResult dict. Only finite values
    with an applicable status may enter the comparison.
    """
    from structured_pdf_text.evaluation.reporting import metric_value
    return metric_value(data, dot_path)


def _best_engines_v2(
    values: dict[str, float | None],
    direction: str,
    tol: float = 1e-6,
) -> set[str]:
    from structured_pdf_text.evaluation.reporting import finite_number
    valid = {eng: v for eng, v in values.items() if finite_number(v)}
    if not valid:
        return set()
    if direction == "lower":
        best_val = min(valid.values())
        return {eng for eng, v in valid.items() if v <= best_val + tol}
    else:
        best_val = max(valid.values())
        return {eng for eng, v in valid.items() if v >= best_val - tol}


def _render_comparison_table_v2(all_data: list[tuple[str, dict]]) -> str:
    """Render canonical metrics and the operational/page diagnostic sections."""
    from structured_pdf_text.evaluation.report_renderer import cell as escape_cell, render_run_sections, unique_run_labels
    all_data = unique_run_labels(all_data)
    engines = [name for name, _ in all_data]
    engine_headers = " | ".join(f"**{escape_cell(e)}**" for e in engines)

    _SECTIONS = {
        "text":          "Grupo 1 — Texto (MET-TX01–TX07)",
        "structure":     "Grupo 2 — Estrutura Markdown",
        "reading_order": "Grupo 3 — Ordem de Leitura",
        "tables":        "Grupo 4 — Tabelas",
        "critical_data": "Grupo 5 — Dados Críticos",
        "integrity":     "Integridade",
    }

    lines = [
        "# Comparativo de perfis de implantação OCR",
        "",
        f"Métricas do avaliador (`schema_version: {_V3_SCHEMA}`).",
        "",
        f"Engines avaliadas: {', '.join(escape_cell(e) for e in engines)}",
        "",
        "Legenda: **negrito** = melhor valor na linha  |  ↓ menor é melhor  |  ↑ maior é melhor  |  — = N/A ou indisponível; consulte os estados e a cobertura.",
        "",
    ]

    all_metric_rows = list(_V2_METRICS) + [
        (dn, path, d) for dn, path, d in _V2_INTEGRITY_METRICS
    ]

    current_section = ""
    for display_name, dot_path, direction in all_metric_rows:
        section = dot_path.split(".")[0]
        if section != current_section:
            if current_section:
                lines.append("")
            current_section = section
            title = _SECTIONS.get(section, section)
            arrow = "↓" if direction == "lower" else "↑"
            lines += [
                f"## {title}",
                "",
                f"| Métrica | {engine_headers} |",
                "|---" + "|---" * len(engines) + "|",
            ]

        values: dict[str, float | None] = {
            eng: _get_value_v2(data, dot_path) for eng, data in all_data
        }
        best_set = _best_engines_v2(values, direction)
        arrow = "↓" if direction == "lower" else "↑"

        row_cells = []
        for eng in engines:
            v = values[eng]
            cell = _fmt(v)
            if eng in best_set and v is not None:
                cell = f"**{cell}**"
            row_cells.append(cell)

        lines.append(
            f"| {display_name} {arrow} | " + " | ".join(row_cells) + " |"
        )

    lines += ["", "---", ""]
    lines += render_run_sections(all_data)

    lines += ["", ""]
    return "\n".join(lines)


def main() -> int:
    """Entry point: aggregate multiple metrics JSON files and write a side-by-side comparison table."""
    ap = argparse.ArgumentParser(
        description="Generate comparison table from engine metrics JSONs."
    )
    ap.add_argument(
        "metrics",
        nargs="+",
        help="Metrics JSON files (one per engine). Supports glob patterns.",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=Path("output/fase8/comparison_table.md"),
    )
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Deprecated compatibility option. Partial runs are included by "
            "default and their status is shown in the comparison metadata."
        ),
    )
    ap.add_argument(
        "--skip-validation", "--allow-incompatible",
        action="store_true",
        help=(
            "Skip all comparability checks and generate the table regardless. "
            "Use only for diagnostic inspection of heterogeneous run sets."
        ),
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # Expand globs using the glob module, which handles both relative and absolute
    # paths (Path.glob() rejects absolute patterns in Python ≥ 3.12).
    paths: list[Path] = []
    input_errors: list[str] = []
    for pattern in args.metrics:
        if "*" in pattern or "?" in pattern:
            matched = [Path(m) for m in glob.glob(pattern) if Path(m).is_file()]
            paths.extend(matched)
            if not matched:
                input_errors.append(f"no metrics files match {pattern!r}")
        else:
            p = Path(pattern)
            if p.is_file():
                paths.append(p)
            else:
                input_errors.append(f"metrics file does not exist: {p}")
    paths = sorted(set(paths))

    if input_errors:
        for error in input_errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if not paths:
        print("ERROR: no metrics files found", file=sys.stderr)
        return 1

    all_data: list[tuple[str, dict]] = []
    load_errors: list[str] = []
    for p in paths:
        try:
            data = _load_metrics(p)
            if not isinstance(data, dict):
                raise ValueError("metrics JSON must be an object")
            # V2 stores engine under evaluator.engine; V1 stores it at top level.
            engine = (
                (data.get("inputs") or {}).get("engine")
                or data.get("engine")
                or (data.get("evaluator") or {}).get("engine")
                or p.stem
            )
            all_data.append((engine, data))
            if not args.quiet:
                print(f"  Loaded: {p.name} ({engine})")
        except Exception as exc:
            load_errors.append(f"ERROR: cannot load metrics {p}: {exc}")

    if load_errors:
        for error in load_errors:
            print(error, file=sys.stderr)
        return 1

    if not all_data:
        print("ERROR: no valid metrics files loaded", file=sys.stderr)
        return 1

    # --- Schema detection and homogeneity check ---
    schema_errors = _validate_schema_homogeneity(all_data)
    if schema_errors and not args.skip_validation:
        print("ERROR: mixed schema versions:", file=sys.stderr)
        for err in schema_errors:
            print(f"  • {err}", file=sys.stderr)
        return 1
    elif schema_errors and not args.quiet:
        print("  WARNING: mixed schema versions (--skip-validation active)")

    is_v3 = all(_detect_schema(data) == "v3" for _, data in all_data)
    if is_v3:
        from structured_pdf_text.evaluation.report_renderer import unique_run_labels
        all_data = unique_run_labels(all_data)
        if not args.skip_validation:
            from structured_pdf_text.evaluation.validation import validate_result, EvaluationValidationError
            for label, data in all_data:
                try:
                    validate_result(data)
                except EvaluationValidationError as exc:
                    print(f"ERROR: invalid canonical result '{label}': {exc}. Re-evaluate with regrade_existing.py.", file=sys.stderr)
                    return 1

    # --- Comparability validation ---
    if not args.skip_validation:
        if is_v3:
            errors = _validate_comparability_v2(all_data)
        else:
            errors = _validate_comparability(all_data, allow_partial=args.allow_partial)
        if errors:
            print("ERROR: runs are not comparable:", file=sys.stderr)
            for err in errors:
                print(f"  • {err}", file=sys.stderr)
            print(
                "\nUse --skip-validation to bypass input checks (diagnostic only). "
                "Run status and missing-page penalties remain included in the report.",
                file=sys.stderr,
            )
            return 1
    elif not args.quiet:
        print("  WARNING: comparability validation skipped (--skip-validation)")

    if is_v3:
        table = _render_comparison_table_v2(all_data)
    else:
        table = _render_comparison_table(all_data)
    if args.skip_validation:
        table += (
            "\n> **Heterogeneous comparison:** comparability validation was bypassed. "
            "Do not treat this report as a homogeneous benchmark.\n"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    from structured_pdf_text.atomic_io import atomic_write_text
    atomic_write_text(args.output, table)

    if not args.quiet:
        print(f"\n  Tabela comparativa: {args.output}")
        print(f"  Engines: {', '.join(e for e, _ in all_data)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
