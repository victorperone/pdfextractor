#!/usr/bin/env python3
"""
Compare engines.

Reads canonical evaluation-3.0.0 metrics JSON files (one per run) and writes a
comparison table in Markdown.

Before building the table the script validates that all runs are comparable:
valid matching PDF, effective-reference and manifesto SHA-256 identities,
the exact selected page set, mode, benchmark protocol and evaluator policies.
Run status and missing-page counts are outcomes; they do not reject otherwise
comparable runs. API and CLI share the canonical validation contract. Other
schemas are rejected; regrade them with regrade_existing.py.

Usage:
    python scripts/compare_engines.py output/fase8/metrics_v3_*.json \
        --output output/fase8/comparison_table.md
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.evaluation.comparability import comparability_key, validate_comparison
from structured_pdf_text.evaluation.report_renderer import cell as escape_cell, render_run_sections, unique_run_labels
from structured_pdf_text.evaluation.reporting import finite_number, metric_value
from structured_pdf_text.evaluation.validation import EvaluationValidationError, validate_result


def _load_metrics(path: Path) -> dict:
    """Load a metrics JSON file produced by compute_metrics.py or regrade_existing.py."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt(val: float | None) -> str:
    """Format a float metric value to 4 decimal places, or '—' if None."""
    if val is None:
        return "—"
    return f"{val:.4f}"


# ---------------------------------------------------------------------------
# Canonical schema evaluation-3.0.0
# ---------------------------------------------------------------------------

_V3_SCHEMA = "evaluation-3.0.0"

# Canonical metrics: (display_name, dot-path relative to 'quality' or 'integrity', direction)
# Path format: "section.key"  (the value is always at result["value"])
_METRICS: list[tuple[str, str, str]] = [
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
_INTEGRITY_METRICS: list[tuple[str, str, str]] = [
    ("Duplicate Excess Rate",     "integrity.duplicate_excess_rate",              "lower"),
    ("Missing Page Rate",         "integrity.missing_page_rate",                  "lower"),
    ("Content Coverage Recall",   "integrity.page_content_coverage_recall",       "higher"),
]


def _validate_schema_homogeneity(all_data: list[tuple[str, dict]]) -> list[str]:
    """Return one error per run whose schema is not evaluation-3.0.0.

    Older results must be re-evaluated with regrade_existing.py. Duplicate
    labels are not collapsed, so no run can hide behind another.
    """
    return [
        f"Engine '{eng}': schema version '{data.get('schema_version', 'missing')}' is not accepted. "
        f"Only {_V3_SCHEMA} is supported. Re-evaluate with regrade_existing.py."
        for eng, data in all_data if data.get("schema_version") != _V3_SCHEMA
    ]


def _comparability_key(data: dict) -> dict:
    return comparability_key(data)


def _validate_comparability(all_data: list[tuple[str, dict]]) -> list[str]:
    """Use the same fail-closed rules as public check_comparability()."""
    return validate_comparison(all_data)


def _get_value(data: dict, dot_path: str) -> float | None:
    """Finite, applicable value at 'section.key' (quality) or 'integrity.key'."""
    return metric_value(data, dot_path)


def _best_engines(values: dict[str, float | None], direction: str, tol: float = 1e-6) -> set[str]:
    """All runs tied (within tol) for the best value; empty when none is available."""
    valid = {eng: v for eng, v in values.items() if finite_number(v)}
    if not valid:
        return set()
    if direction == "lower":
        best_val = min(valid.values())
        return {eng for eng, v in valid.items() if v <= best_val + tol}
    best_val = max(valid.values())
    return {eng for eng, v in valid.items() if v >= best_val - tol}


def _render_comparison_table(all_data: list[tuple[str, dict]]) -> str:
    """Render canonical metrics and the operational/page diagnostic sections."""
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

    all_metric_rows = list(_METRICS) + [
        (dn, path, d) for dn, path, d in _INTEGRITY_METRICS
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
            eng: _get_value(data, dot_path) for eng, data in all_data
        }
        best_set = _best_engines(values, direction)
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
            engine = (data.get("inputs") or {}).get("engine") or p.stem
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

    # --- Schema check: only canonical results can be rendered ---
    schema_errors = _validate_schema_homogeneity(all_data)
    if schema_errors:
        print("ERROR: unsupported schema versions:", file=sys.stderr)
        for err in schema_errors:
            print(f"  • {err}", file=sys.stderr)
        return 1

    all_data = unique_run_labels(all_data)
    if not args.skip_validation:
        for label, data in all_data:
            try:
                validate_result(data)
            except EvaluationValidationError as exc:
                print(f"ERROR: invalid canonical result '{label}': {exc}. Re-evaluate with regrade_existing.py.", file=sys.stderr)
                return 1

        # --- Comparability validation ---
        errors = _validate_comparability(all_data)
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
