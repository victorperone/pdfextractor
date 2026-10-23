#!/usr/bin/env python3
"""Regrade existing hypothesis Markdown using evaluation-3.0.0.

Does NOT invoke OCR; works purely on pre-existing Markdown artefacts.

Usage
-----
    python scripts/regrade_existing.py \\
        --hypothesis output/extracted_easyocr.md \\
        --reference  corpus/REFERENCIA.md \\
        --engine easyocr \\
        --output-dir output/evaluation_v2

    python scripts/regrade_existing.py \\
        --hypothesis output/extracted.md \\
        --manifesto  corpus/MANIFESTO.json \\
        --engine easyocr \\
        --output-dir output/evaluation_v2

Outputs (default run ID: random 12-hex identifier)
-------
    output/evaluation_v2/metrics_{engine}_{run_id}.json
    output/evaluation_v2/errors_{engine}_{run_id}.md
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.evaluation.cli import REGRADE_PROFILE, main as cli_main


def main(argv: list[str] | None = None) -> int:
    return cli_main(REGRADE_PROFILE, argv)


if __name__ == "__main__":
    sys.exit(main())
