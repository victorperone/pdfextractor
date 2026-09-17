from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import PdfTextExtractor
from .config import ExtractionMode, ExtractorConfig, OcrQualityPolicy, best_extraction_config
from .diagnostics.overlay import render_overlay
from .diagnostics.report import document_report
from .diagnostics.dump import dump_native_page_json
from .diagnostics.corpus import corpus_report
from .diagnostics.compare import compare_extractors
from .errors import FatalExtractionError
from .ocr.models import get_profile
from .ocr.paddle import (
    PaddleOcrUnavailable,
    _local_model_root,
    validate_local_ocr_models,
)
from .renderers.json import render_json
from .renderers.markdown import render_markdown


EXHAUSTIVE_OCR_WARNING = (
    "WARNING: OCR quality policy 'exhaustive' runs all eligible OCR quality\n"
    "variants and may substantially increase runtime and peak memory usage.\n"
    "In internal reference benchmarks, workloads of this class have shown\n"
    "costs on the order of ~50% more peak memory and ~4x wall-clock time\n"
    "compared with a lighter OCR path. Actual impact depends on document\n"
    "content, page size, OCR coverage, and enabled refinements. No automatic\n"
    "quality reduction will be applied."
)


def _warn_if_exhaustive(policy: str, *, emitted: bool = False) -> bool:
    if emitted or str(policy).lower() != OcrQualityPolicy.EXHAUSTIVE.value:
        return emitted
    print(EXHAUSTIVE_OCR_WARNING, file=sys.stderr)
    return True


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
        "--ocr-quality-policy",
        choices=[policy.value for policy in OcrQualityPolicy],
        default=OcrQualityPolicy.ADAPTIVE.value,
        help="OCR quality strategy: baseline, adaptive or exhaustive",
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
    extract_parser.add_argument(
        "--output-file",
        "-o",
        type=Path,
        default=None,
        metavar="FILE",
        help="Save output to FILE instead of printing to stdout",
    )
    extract_parser.add_argument(
        "--progress",
        action="store_true",
        help="Print page progress to stderr during extraction",
    )
    extract_parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="CPU threads for OCR engine (0 = auto-detect, -1 = PaddlePaddle default)",
    )

    inspect_parser = subparsers.add_parser("inspect", help="Print page diagnostics")
    inspect_parser.add_argument("pdf", type=Path)
    inspect_parser.add_argument("--page", type=int, default=None, help="1-based page number")
    inspect_parser.add_argument("--mode", choices=[mode.value for mode in ExtractionMode], default=ExtractionMode.NATIVE.value)
    inspect_parser.add_argument("--language", default="pt")
    inspect_parser.add_argument(
        "--ocr-quality-policy",
        choices=[policy.value for policy in OcrQualityPolicy],
        default=OcrQualityPolicy.ADAPTIVE.value,
    )
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
        "--ocr-quality-policy",
        choices=[policy.value for policy in OcrQualityPolicy],
        default=OcrQualityPolicy.ADAPTIVE.value,
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
    report_parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="CPU threads for OCR engine (0 = auto-detect, -1 = PaddlePaddle default)",
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

    setup_models_parser = subparsers.add_parser(
        "setup-models",
        help="Download OCR model weights (requires internet). Idempotent.",
    )
    setup_models_parser.add_argument("--language", default="pt")
    setup_models_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )

    models_status_parser = subparsers.add_parser(
        "models-status",
        help="Check offline OCR readiness without loading models or accessing the network.",
    )
    models_status_parser.add_argument("--language", default="pt")
    models_status_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
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
                ocr_quality_policy=args.ocr_quality_policy,
                preserve_headers_footers=not args.omit_repeated_headers_footers,
                merge_cross_page_tables=args.merge_cross_page_tables,
                num_threads=args.threads,
            )
        _warn_if_exhaustive(args.ocr_quality_policy)
        if _mode_requires_ocr(config.mode):
            try:
                validate_local_ocr_models(language=config.language)
            except PaddleOcrUnavailable as exc:
                print(str(exc), file=sys.stderr)
                print(
                    "Run: python -m structured_pdf_text.cli setup-models",
                    file=sys.stderr,
                )
                return 1

        def _progress(current: int, total: int) -> None:
            print(f"\rExtraindo página {current}/{total}...", end="", file=sys.stderr, flush=True)

        callback = _progress if args.progress else None
        try:
            document = PdfTextExtractor(config).extract(
                args.pdf,
                progress_callback=callback,
            )
        except FatalExtractionError as exc:
            if args.progress:
                print(file=sys.stderr)
            _print_fatal_extraction_error(exc)
            return 1
        if args.progress:
            print(file=sys.stderr)
        for warning in document.diagnostics.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        if args.output == "raw":
            result = document.raw_text
        elif args.output == "json":
            result = render_json(document)
        elif args.output == "markdown":
            result = render_markdown(document)
        else:
            result = document.reading_text
        if args.output_file is not None:
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_text(result, encoding="utf-8")
        else:
            print(result)
        return 0

    if args.command == "inspect":
        if args.page is not None and args.page < 1:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        config = ExtractorConfig(
            mode=args.mode,
            language=args.language,
            retain_native_evidence=args.raw_page_json,
            ocr_quality_policy=args.ocr_quality_policy,
            page_indices=(args.page - 1,) if args.page is not None else None,
        )
        _warn_if_exhaustive(args.ocr_quality_policy)
        if _mode_requires_ocr(config.mode):
            try:
                validate_local_ocr_models(language=config.language)
            except PaddleOcrUnavailable as exc:
                print(str(exc), file=sys.stderr)
                print(
                    "Run: python -m structured_pdf_text.cli setup-models",
                    file=sys.stderr,
                )
                return 1
        try:
            document = PdfTextExtractor(config).extract(args.pdf)
        except FatalExtractionError as exc:
            _print_fatal_extraction_error(exc)
            return 1
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
            if page.diagnostics.warnings:
                print("warnings:")
                for warning in page.diagnostics.warnings:
                    print(f"  - {warning}")
        else:
            print(document_report(document))
        return 0

    if args.command == "overlay":
        index = args.page - 1
        if index < 0:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        # overlay always uses BALANCED which can trigger OCR.
        try:
            validate_local_ocr_models(language="pt")
        except PaddleOcrUnavailable as exc:
            print(str(exc), file=sys.stderr)
            print(
                "Run: python -m structured_pdf_text.cli setup-models",
                file=sys.stderr,
            )
            return 1
        try:
            document = PdfTextExtractor(
                ExtractorConfig(
                    mode=ExtractionMode.BALANCED,
                    page_indices=(index,),
                )
            ).extract(args.pdf)
        except FatalExtractionError as exc:
            _print_fatal_extraction_error(exc)
            return 1
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
            ocr_quality_policy=args.ocr_quality_policy,
            merge_cross_page_tables=args.merge_cross_page_tables,
            num_threads=args.threads,
        )
        _warn_if_exhaustive(args.ocr_quality_policy)
        if args.workers < 1:
            print("--workers must be at least 1", file=sys.stderr)
            return 2
        if _mode_requires_ocr(config.mode):
            try:
                validate_local_ocr_models(language=config.language)
            except PaddleOcrUnavailable as exc:
                print(str(exc), file=sys.stderr)
                print(
                    "Run: python -m structured_pdf_text.cli setup-models",
                    file=sys.stderr,
                )
                return 1
        try:
            report = corpus_report(args.pdfs, config, workers=args.workers)
        except FatalExtractionError as exc:
            _print_fatal_extraction_error(exc)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "compare":
        ocr_adapters = {"structured-balanced"}
        if any(adapter in ocr_adapters for adapter in args.adapters):
            try:
                validate_local_ocr_models(language=args.language)
            except PaddleOcrUnavailable as exc:
                print(str(exc), file=sys.stderr)
                print(
                    "Run: python -m structured_pdf_text.cli setup-models",
                    file=sys.stderr,
                )
                return 1
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

    if args.command == "setup-models":
        return _cmd_setup_models(args.language, args.cache_home)

    if args.command == "models-status":
        return _cmd_models_status(args.language, args.cache_home)

    return 2


def _mode_requires_ocr(mode: ExtractionMode | str) -> bool:
    """Return True when the extraction mode can trigger OCR."""
    ocr_modes = {ExtractionMode.BALANCED, ExtractionMode.OCR, "balanced", "ocr"}
    return mode in ocr_modes


def _print_fatal_extraction_error(exc: FatalExtractionError) -> None:
    """Print a concise fatal error without exposing a normal-flow traceback."""
    print("FATAL EXTRACTION ERROR", file=sys.stderr)
    print(f"code: {exc.code}", file=sys.stderr)
    if exc.page_index is not None:
        print(f"page: {exc.page_index + 1}", file=sys.stderr)
    if exc.stage:
        print(f"stage: {exc.stage}", file=sys.stderr)
    print(f"message: {exc}", file=sys.stderr)
    cause_type = exc.details.get("cause_type")
    cause_message = exc.details.get("cause_message")
    if cause_type or cause_message:
        print(f"cause: {cause_type}: {cause_message}", file=sys.stderr)
    print(
        "Extraction aborted. The document was not completed.",
        file=sys.stderr,
    )


_WEIGHT_EXTENSIONS = frozenset({
    ".pdmodel", ".pdiparams", ".pdparams", ".pdiparams.info",
    ".nb", ".onnx", ".bin", ".pt",
})


def _model_is_ready(model_dir: "Path") -> bool:
    """Return True when model_dir looks like a properly installed PaddleOCR model.

    A directory that exists but contains only README or JSON files is NOT ready.
    At least one weight file (by extension) must be present.
    """
    if not model_dir.is_dir():
        return False
    for entry in model_dir.iterdir():
        suffix = entry.suffix.lower()
        # .pdiparams.info has two suffixes; check the full name too.
        if suffix in _WEIGHT_EXTENSIONS or entry.name.endswith(".pdiparams.info"):
            return True
    return False


def _cmd_models_status(language: str, cache_home: str | None) -> int:
    """Print offline OCR readiness without loading models or accessing the network."""
    import os
    from pathlib import Path

    try:
        profile = get_profile(language)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    effective_cache = cache_home or os.environ.get(
        "PADDLE_PDX_CACHE_HOME",
        str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
    )
    root = _local_model_root(effective_cache)
    print(f"OCR model home:\n  {root}\n")

    all_ok = True
    for model_name in profile.dir_kwargs.values():
        model_dir = root / model_name
        if _model_is_ready(model_dir):
            print(f"[ok] {model_name}")
        else:
            print(f"[missing] {model_name}")
            all_ok = False

    print()
    if all_ok:
        print("Offline OCR readiness: READY")
        return 0
    else:
        print("Offline OCR readiness: NOT READY")
        print("Run: python -m structured_pdf_text.cli setup-models")
        return 1


def _cmd_setup_models(language: str, cache_home: str | None) -> int:
    """Download OCR model weights using PaddleOCR (requires internet). Idempotent."""
    import os
    from pathlib import Path

    try:
        profile = get_profile(language)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    effective_cache = cache_home or os.environ.get(
        "PADDLE_PDX_CACHE_HOME",
        str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
    )

    resolved_cache = str(Path(effective_cache).expanduser().resolve())
    os.environ["PADDLE_PDX_CACHE_HOME"] = resolved_cache
    # Allow remote model resolution during setup.
    os.environ.pop("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", None)

    print(f"OCR model cache: {resolved_cache}")
    print(f"Language profile: {language}")
    print("Initializing models (this may download weights if not already cached)...\n")

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        print(
            "Error: PaddleOCR is not installed.\n"
            "Install OCR dependencies first:\n"
            "  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    root = _local_model_root(resolved_cache)
    options: dict = {
        "use_doc_orientation_classify": True,
        "use_doc_unwarping": False,
        "use_textline_orientation": True,
        "enable_mkldnn": False,
        **profile.name_kwargs,
    }

    try:
        PaddleOCR(**options)
    except Exception as exc:
        print(f"Error during model initialization: {exc}", file=sys.stderr)
        return 1

    print("\nVerifying installed models...")
    all_ok = True
    for model_name in profile.dir_kwargs.values():
        model_dir = root / model_name
        if _model_is_ready(model_dir):
            print(f"  [ok] {model_dir}")
        else:
            print(f"  [missing] {model_dir}", file=sys.stderr)
            all_ok = False

    if all_ok:
        print("\nAll models installed. Runtime is ready for offline extraction.")
        return 0
    else:
        print(
            "\nSome models are still missing. Re-run setup-models or check connectivity.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
