from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import PdfTextExtractor
from .config import ExtractionMode, ExtractorConfig, best_extraction_config
from .diagnostics.overlay import render_overlay
from .diagnostics.report import document_report
from .diagnostics.dump import dump_native_page_json
from .diagnostics.corpus import corpus_report
from .diagnostics.compare import compare_extractors
from .renderers.json import render_json
from .renderers.markdown import render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdftext")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract text or structure from a PDF")
    extract_parser.add_argument("pdf", type=Path)
    extract_parser.add_argument("--output", choices=["reading", "raw", "json", "markdown"], default="reading")
    extract_parser.add_argument("--mode", choices=[mode.value for mode in ExtractionMode], default=ExtractionMode.NATIVE.value)
    extract_parser.add_argument("--language", default="pt")
    extract_parser.add_argument(
        "--ocr-batch-size",
        type=int,
        default=3,
        help="Number of OCR quality variants sent per model batch",
    )
    extract_parser.add_argument(
        "--no-ocr-quality-variants",
        action="store_true",
        help="Use one OCR pass per page/region instead of enhancement variants",
    )
    extract_parser.add_argument(
        "--omit-repeated-headers-footers",
        action="store_true",
        help="Remove repeated edge regions from reading output while preserving raw output",
    )
    extract_parser.add_argument(
        "--merge-cross-page-tables",
        action="store_true",
        help="Merge table fragments that continue across page boundaries",
    )
    extract_parser.add_argument(
        "--best",
        action="store_true",
        help=(
            "Use the best full-extraction profile: BALANCED mode, tables enabled, "
            "cross-page merging, headers removed, quality OCR variants. "
            "Overrides --mode and other flags."
        ),
    )

    inspect_parser = subparsers.add_parser("inspect", help="Print page diagnostics")
    inspect_parser.add_argument("pdf", type=Path)
    inspect_parser.add_argument("--page", type=int, default=None, help="1-based page number")
    inspect_parser.add_argument("--mode", choices=[mode.value for mode in ExtractionMode], default=ExtractionMode.NATIVE.value)
    inspect_parser.add_argument(
        "--raw-page-json",
        action="store_true",
        help="Include the immutable PDFium page evidence as JSON",
    )

    overlay_parser = subparsers.add_parser("overlay", help="Render a diagnostic overlay")
    overlay_parser.add_argument("pdf", type=Path)
    overlay_parser.add_argument("--page", type=int, required=True, help="1-based page number")
    overlay_parser.add_argument("--out", type=Path, required=True)
    overlay_parser.add_argument("--scale", type=float, default=2.0)

    report_parser = subparsers.add_parser(
        "report",
        help="Run real extraction over one or more PDFs and print corpus diagnostics",
    )
    report_parser.add_argument("pdfs", nargs="+", type=Path)
    report_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ExtractionMode],
        default=ExtractionMode.NATIVE.value,
    )
    report_parser.add_argument("--language", default="pt")
    report_parser.add_argument(
        "--ocr-batch-size",
        type=int,
        default=3,
        help="Number of OCR quality variants sent per model batch",
    )
    report_parser.add_argument(
        "--no-ocr-quality-variants",
        action="store_true",
        help="Use one OCR pass per page/region instead of enhancement variants",
    )
    report_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel document workers; keep at 1 for OCR unless memory is ample",
    )
    report_parser.add_argument(
        "--merge-cross-page-tables",
        action="store_true",
        help="Merge compatible table fragments across adjacent pages",
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare local extractor views without treating a baseline as ground truth",
    )
    compare_parser.add_argument("pdf", type=Path)
    compare_parser.add_argument(
        "--adapters",
        nargs="+",
        choices=("structured-native", "structured-balanced", "pdfium-raw", "pymupdf"),
        default=("structured-native", "pdfium-raw", "pymupdf"),
    )
    compare_parser.add_argument(
        "--reference",
        choices=("structured-native", "structured-balanced", "pdfium-raw", "pymupdf"),
        default="pymupdf",
    )
    compare_parser.add_argument("--language", default="pt")
    compare_parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include full raw and reading text in the JSON report",
    )

    args = parser.parse_args(argv)
    if args.command == "extract":
        if args.ocr_batch_size < 1:
            print("--ocr-batch-size must be at least 1", file=sys.stderr)
            return 2
        if args.best:
            config = best_extraction_config(language=args.language)
        else:
            config = ExtractorConfig(
                mode=args.mode,
                language=args.language,
                ocr_batch_size=args.ocr_batch_size,
                ocr_quality_variants=not args.no_ocr_quality_variants,
                preserve_headers_footers=not args.omit_repeated_headers_footers,
                merge_cross_page_tables=args.merge_cross_page_tables,
            )
        document = PdfTextExtractor(config).extract(args.pdf)
        if args.output == "raw":
            print(document.raw_text)
        elif args.output == "json":
            print(render_json(document))
        elif args.output == "markdown":
            print(render_markdown(document))
        else:
            print(document.reading_text)
        return 0

    if args.command == "inspect":
        if args.page is not None and args.page < 1:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        config = ExtractorConfig(
            mode=args.mode,
            retain_native_evidence=args.raw_page_json,
            page_indices=(args.page - 1,) if args.page is not None else None,
        )
        try:
            document = PdfTextExtractor(config).extract(args.pdf)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.page is not None:
            page = document.pages[0]
            if args.raw_page_json and page.native_evidence is not None:
                print(dump_native_page_json(page.native_evidence))
                return 0
            reasons = ",".join(reason.value for reason in page.diagnostics.reasons) or "none"
            print(
                f"page={args.page} strategy={page.diagnostics.strategy.value} "
                f"reasons={reasons} native_chars={page.diagnostics.native_chars} "
                f"text_len={page.diagnostics.native_text_length} "
                f"native_score={page.diagnostics.facts.get('native_text_score', '?')} "
                f"facts={page.diagnostics.facts}"
            )
        else:
            print(document_report(document))
        return 0

    if args.command == "overlay":
        index = args.page - 1
        if index < 0:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        try:
            document = PdfTextExtractor(
                ExtractorConfig(
                    mode=ExtractionMode.BALANCED,
                    page_indices=(index,),
                )
            ).extract(args.pdf)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        out = render_overlay(args.pdf, document.pages[0], args.out, scale=args.scale)
        print(out)
        return 0

    if args.command == "report":
        if args.ocr_batch_size < 1:
            print("--ocr-batch-size must be at least 1", file=sys.stderr)
            return 2
        config = ExtractorConfig(
            mode=args.mode,
            language=args.language,
            ocr_batch_size=args.ocr_batch_size,
            ocr_quality_variants=not args.no_ocr_quality_variants,
            merge_cross_page_tables=args.merge_cross_page_tables,
        )
        if args.workers < 1:
            print("--workers must be at least 1", file=sys.stderr)
            return 2
        print(
            json.dumps(
                corpus_report(args.pdfs, config, workers=args.workers),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "compare":
        try:
            comparison = compare_extractors(
                args.pdf,
                args.adapters,
                reference=args.reference,
                language=args.language,
                include_text=args.include_text,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
