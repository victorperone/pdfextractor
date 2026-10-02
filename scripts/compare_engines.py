#!/usr/bin/env python3
"""
Compare engines.

Reads multiple metrics JSON files (one per engine) and generates a
comparison table in Markdown.

Before building the table the script validates that all runs are comparable:
same PDF SHA-256 (when available), same reference SHA-256 (when available),
same selected page set, same extraction mode, and no invalid/partial runs
unless --allow-partial is passed.

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
    # Group 4 — Order and Integrity
    ("Reading Order Acc.",    "grupo4_ordem_integridade",     "reading_order_accuracy"),
    ("Duplicate Content",     "grupo4_ordem_integridade",     "duplicate_content_rate"),
    ("Header Leakage",        "grupo4_ordem_integridade",     "header_leakage_rate"),
    ("Footer Leakage",        "grupo4_ordem_integridade",     "footer_leakage_rate"),
    ("Page# Leakage",         "grupo4_ordem_integridade",     "page_number_leakage_rate"),
    ("Failure Rate",          "grupo4_ordem_integridade",     "failure_rate"),
    ("Invalid Markdown",      "grupo4_ordem_integridade",     "invalid_markdown_rate"),
    # Group 5 — Critical Data
    ("Numeric Exact Match",   "grupo5_dados_criticos",        "numeric_exact_match"),
    ("Date Exact Match",      "grupo5_dados_criticos",        "date_exact_match"),
    ("Currency Exact Match",  "grupo5_dados_criticos",        "currency_exact_match"),
    ("Identifier Exact Match","grupo5_dados_criticos",        "identifier_exact_match"),
]

# Metrics where lower is better (errors)
_LOWER_IS_BETTER = {
    "cer_raw", "cer_normalized", "cer_text_only", "wer", "substitution_rate",
    "deletion_rate", "insertion_rate", "omission_rate", "heading_text_cer",
    "cell_cer", "duplicate_content_rate", "header_leakage_rate",
    "footer_leakage_rate", "page_number_leakage_rate", "failure_rate",
    "invalid_markdown_rate",
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
    """Extract the fields that must be identical across all compared runs."""
    # pages_selected_but_missing is a list in schema v2; sort for stable comparison.
    missing = data.get("pages_selected_but_missing", [])
    selected = data.get("pages_selected", data.get("pages_reference"))
    return {
        "pages_reference": data.get("pages_reference"),
        "pages_selected": selected,
        "pages_selected_but_missing": sorted(missing) if isinstance(missing, list) else missing,
        "missing_pages_penalised": data.get("missing_pages_penalised"),
    }


def _validate_comparability(
    all_data: list[tuple[str, dict]],
    *,
    allow_partial: bool,
) -> list[str]:
    """Check that all runs are comparable. Return a list of error strings (empty = OK)."""
    errors: list[str] = []

    # Check benchmark_status — partial/invalid runs must be excluded from ranking
    # unless --allow-partial is explicitly set.
    if not allow_partial:
        for eng, data in all_data:
            bstatus = data.get("benchmark_status")
            if bstatus is not None and bstatus != "valid":
                errors.append(
                    f"Run '{eng}' has benchmark_status={bstatus!r}. "
                    "Exclude it from ranking or re-run with --allow-partial."
                )

    # Check that all runs share the same comparability key.
    if len(all_data) > 1:
        keys = [(eng, _comparability_key(data)) for eng, data in all_data]
        ref_eng, ref_key = keys[0]
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
        "# Comparativo de Engines OCR — Fase 8",
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
        "| Engine | Run ID | Páginas avaliadas | Selecionadas ausentes | benchmark_status | Missing penalizado |",
        "|---|---|---|---|---|---|",
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
        penalised = "sim" if data.get("missing_pages_penalised") else "não"
        lines.append(
            f"| `{eng}` | {run_id} | {n_eval} | {n_missing} | {bstatus} | {penalised} |"
        )

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
            "Include runs with benchmark_status=partial in the comparison. "
            "Without this flag, partial/invalid runs cause an error exit. "
            "Comparability field mismatches are still reported as errors."
        ),
    )
    ap.add_argument(
        "--skip-validation",
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
    for pattern in args.metrics:
        if "*" in pattern or "?" in pattern:
            matched = [Path(m) for m in glob.glob(pattern)]
            paths.extend(m for m in matched if m.exists())
        else:
            p = Path(pattern)
            if p.exists():
                paths.append(p)
    paths = sorted(set(paths))

    if not paths:
        print("ERROR: no metrics files found", file=sys.stderr)
        return 1

    all_data: list[tuple[str, dict]] = []
    for p in paths:
        try:
            data = _load_metrics(p)
            engine = data.get("engine", p.stem)
            all_data.append((engine, data))
            if not args.quiet:
                print(f"  Loaded: {p.name} ({engine})")
        except Exception as exc:
            print(f"  WARNING: skipping {p}: {exc}", file=sys.stderr)

    if not all_data:
        print("ERROR: no valid metrics files loaded", file=sys.stderr)
        return 1

    # --- Comparability validation (F07) ---
    if not args.skip_validation:
        errors = _validate_comparability(all_data, allow_partial=args.allow_partial)
        if errors:
            print("ERROR: runs are not comparable:", file=sys.stderr)
            for err in errors:
                print(f"  • {err}", file=sys.stderr)
            print(
                "\nUse --skip-validation to bypass checks (diagnostic only) "
                "or --allow-partial to include partial runs.",
                file=sys.stderr,
            )
            return 1
    elif not args.quiet:
        print("  WARNING: comparability validation skipped (--skip-validation)")

    table = _render_comparison_table(all_data)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(table, encoding="utf-8")

    if not args.quiet:
        print(f"\n  Tabela comparativa: {args.output}")
        print(f"  Engines: {', '.join(e for e, _ in all_data)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
