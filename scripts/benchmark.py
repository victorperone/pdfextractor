from __future__ import annotations

import argparse
import time
from pathlib import Path

from structured_pdf_text import PdfTextExtractor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    extractor = PdfTextExtractor()
    for pdf in sorted(args.corpus.rglob("*.pdf")):
        start = time.perf_counter()
        try:
            result = extractor.extract(pdf)
            status = result.diagnostics.status.value
            pages = result.diagnostics.page_count
        except Exception as exc:
            status = f"error:{type(exc).__name__}"
            pages = 0
        elapsed = (time.perf_counter() - start) * 1000
        print(f"{pdf}\t{status}\tpages={pages}\tms={elapsed:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
