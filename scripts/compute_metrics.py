#!/usr/bin/env python3
"""Compute canonical E2E metrics (schema evaluation-3.0.0) for extracted Markdown.

Usage:
    python scripts/compute_metrics.py \\
        --hypothesis output/fase8/extracted_paddle_20261001.md \\
        --manifesto corpus/V4/Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json \\
        --run-manifest output/fase8/manifesto_e2e_paddle_20261001.json \\
        --engine paddle --output-dir output/fase8

Outputs (default run ID: hypothesis file stem):
    {output-dir}/metrics_v3_{engine}_{run_id}.json
    {output-dir}/errors_v3_{engine}_{run_id}.md

Next step: python scripts/compare_engines.py metrics_v3_*.json --output comparison_table.md
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.evaluation.cli import COMPUTE_PROFILE, main as cli_main


def main(argv: list[str] | None = None) -> int:
    return cli_main(COMPUTE_PROFILE, argv)


if __name__ == "__main__":
    sys.exit(main())
