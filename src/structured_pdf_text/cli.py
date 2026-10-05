from __future__ import annotations

import argparse
import json
import os
import tempfile
import getpass
import sys
from pathlib import Path

from .api import PdfTextExtractor
from .config import (
    ExtractionMode,
    ExtractorConfig,
    OcrQualityPolicy,
    best_extraction_config,
    effective_ocr_quality_policy,
    max_quality_extraction_config,
)
from .diagnostics.overlay import render_overlay
from .diagnostics.report import document_report
from .diagnostics.dump import dump_native_page_json
from .diagnostics.corpus import corpus_report
from .diagnostics.compare import compare_extractors
from .errors import ConfigurationError, FatalExtractionError
from .ocr.models import (
    PADDLE_OCR_FEATURE_DEFAULTS,
    UV_DOC_MODEL,
    get_profile,
    model_directory_is_ready,
    required_model_directories,
)
from .ocr.runtime_policy import apply_paddle_runtime_policy, resolve_paddle_runtime_policy
from .ocr.registry import PUBLIC_ENGINES
from .ocr.readiness import probe_static
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
    try:
        return _main_impl(argv)
    except (ConfigurationError, FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _main_impl(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pdftext",
        description="PDF text extraction. CPU OCR defaults to oneDNN disabled; set PADDLE_ENABLE_MKLDNN=1 to opt in.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="Extract text or structure from a PDF")
    extract_parser.add_argument("pdf", type=Path)
    extract_parser.add_argument("--output", choices=["reading", "raw", "json", "markdown"], default="reading")
    extract_parser.add_argument("--mode", choices=[mode.value for mode in ExtractionMode], default=ExtractionMode.NATIVE.value)
    extract_parser.add_argument(
        "--language", default="pt-BR",
        help="OCR language tag (default: pt-BR)",
    )
    extract_parser.add_argument(
        "--paddle-model-profile", "--ocr-model-profile",
        dest="paddle_model_profile",
        default=None,
        metavar="PROFILE",
        help=(
            "Paddle model profile to use (e.g. pt-v6-medium). "
            "PP-OCRv6 is the default profile; PP-OCRv5 remains available as pt-v5."
        ),
    )
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
        "--max-quality",
        action="store_true",
        help="Use exhaustive OCR, multi-scale tiling, regional recovery, table-cell and critical-data refinement.",
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
    password_group = extract_parser.add_mutually_exclusive_group()
    password_group.add_argument("--password-stdin", action="store_true", help="Read the PDF password from stdin")
    password_group.add_argument("--ask-password", action="store_true", help="Prompt securely for the PDF password")
    extract_parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="CPU threads for OCR engine (0 = auto-detect, -1 = the engine's own thread default)",
    )
    extract_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )
    extract_parser.add_argument(
        "--ocr-engine",
        default=None,
        metavar="ENGINE",
        choices=PUBLIC_ENGINES,
        help=(
            "OCR engine family: easyocr (default), paddle, rapidocr, tesseract. "
            "RapidOCR runtime selection uses --ocr-provider."
        ),
    )
    extract_parser.add_argument("--ocr-provider", choices=["onnxruntime", "openvino"], default=None,
                                help="Inference provider when --ocr-engine rapidocr is selected")

    inspect_parser = subparsers.add_parser("inspect", help="Print page diagnostics")
    inspect_parser.add_argument("pdf", type=Path)
    inspect_parser.add_argument("--page", type=int, default=None, help="1-based page number")
    inspect_parser.add_argument("--mode", choices=[mode.value for mode in ExtractionMode], default=ExtractionMode.NATIVE.value)
    inspect_parser.add_argument(
        "--language", default="pt-BR",
        help="OCR language tag (default: pt-BR)",
    )
    inspect_parser.add_argument(
        "--paddle-model-profile", "--ocr-model-profile",
        dest="paddle_model_profile",
        default=None,
        metavar="PROFILE",
        help="Paddle model profile (default: pt).",
    )
    inspect_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )
    inspect_parser.add_argument("--ocr-engine", choices=PUBLIC_ENGINES, default="easyocr")
    inspect_parser.add_argument("--ocr-provider", choices=("onnxruntime", "openvino"), default=None)
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
    overlay_parser.add_argument(
        "--mode", choices=("native", "balanced", "ocr"), default="native",
        help="Extraction mode; native is the default and does not use OCR",
    )
    overlay_parser.add_argument(
        "--ocr-model-profile", default=None, metavar="PROFILE",
        help="Paddle model profile (default: pt)",
    )
    overlay_parser.add_argument("--language", default="pt-BR", help="OCR language tag")
    overlay_parser.add_argument("--ocr-engine", choices=PUBLIC_ENGINES, default="easyocr")
    overlay_parser.add_argument("--ocr-provider", choices=("onnxruntime", "openvino"), default=None)
    overlay_parser.add_argument(
        "--cache-home", default=None, metavar="DIR",
        help="Override the OCR model cache directory",
    )

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
    report_parser.add_argument(
        "--language", default="pt-BR", help="OCR language tag (default: pt-BR)",
    )
    report_parser.add_argument(
        "--paddle-model-profile", "--ocr-model-profile",
        dest="paddle_model_profile",
        default=None,
        metavar="PROFILE",
        help="Paddle model profile (default: pt).",
    )
    report_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )
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
        help="CPU threads for OCR engine (0 = auto-detect, -1 = the engine's own thread default)",
    )
    report_parser.add_argument("--ocr-engine", choices=PUBLIC_ENGINES, default="easyocr")
    report_parser.add_argument("--ocr-provider", choices=("onnxruntime", "openvino"), default=None)

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
    compare_parser.add_argument("--language", default=None, help="Explicit OCR profile when an OCR adapter is selected")
    compare_parser.add_argument(
        "--ocr-model-profile", default=None, metavar="PROFILE",
        help="Explicit OCR profile required when structured-balanced is selected",
    )
    compare_parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include full raw and reading text in the JSON report",
    )

    setup_models_parser = subparsers.add_parser(
        "setup-paddle-models", aliases=["setup-models"],
        help="Download PaddleOCR model weights (requires internet). Idempotent.",
    )
    setup_models_parser.add_argument("--language", default="pt")
    setup_models_parser.add_argument(
        "--paddle-model-profile", "--ocr-model-profile",
        dest="paddle_model_profile",
        default=None,
        metavar="PROFILE",
        help=(
            "OCR model profile to download (e.g. pt). "
            "When omitted, uses the profile matching --language. "
            "Use --cache-home to store v6 models in a separate directory from v5."
        ),
    )
    setup_models_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )

    setup_easyocr_parser = subparsers.add_parser(
        "setup-easyocr-models",
        help="Download EasyOCR CRAFT, Portuguese recognizer and optional DBNet18 weights.",
    )
    setup_easyocr_parser.add_argument("--language", default="pt-BR")
    setup_easyocr_parser.add_argument("--cache-home", default=None, metavar="DIR")
    setup_easyocr_parser.add_argument(
        "--include-dbnet", action="store_true",
        help="Also download the DBNet18 detector required for maximum-quality mode.",
    )

    models_status_parser = subparsers.add_parser(
        "paddle-models-status", aliases=["models-status"],
        help="Check PaddleOCR model readiness without loading models or accessing the network.",
    )
    models_status_parser.add_argument("--language", default="pt")
    models_status_parser.add_argument(
        "--paddle-model-profile", "--ocr-model-profile",
        dest="paddle_model_profile",
        default=None,
        metavar="PROFILE",
        help="OCR model profile to check (e.g. pt). When omitted, uses --language.",
    )
    models_status_parser.add_argument(
        "--cache-home",
        default=None,
        metavar="DIR",
        help="Override the OCR model cache directory",
    )

    args = parser.parse_args(argv)
    if args.command == "extract":
        if args.max_quality and args.best:
            print("--max-quality and --best select different extraction profiles; choose one", file=sys.stderr)
            return 2
        if args.ocr_batch_size < 1:
            print("--ocr-batch-size must be at least 1", file=sys.stderr)
            return 2
        language = args.language or "pt-BR"
        profile_name = args.paddle_model_profile or "pt"
        # Resolve effective engine: explicit --ocr-engine wins; default is "easyocr".
        effective_engine: str = args.ocr_engine or "easyocr"
        if args.max_quality:
            config = max_quality_extraction_config(language=language)
            if args.ocr_engine is not None or args.ocr_provider is not None:
                from dataclasses import replace as _replace
                config = _replace(
                    config,
                    ocr_engine=args.ocr_engine or config.ocr_engine,
                    ocr_provider=args.ocr_provider,
                    paddle_model_profile=profile_name,
                    ocr_cache_home=args.cache_home,
                )
        elif args.best:
            config = best_extraction_config(language=language)
            # F05: --best must not silently ignore an explicit --ocr-engine.
            if args.ocr_engine is not None or args.ocr_provider is not None:
                from dataclasses import replace as _replace
                config = _replace(
                    config,
                    ocr_engine=args.ocr_engine or config.ocr_engine,
                    ocr_provider=args.ocr_provider,
                    paddle_model_profile=profile_name,
                    ocr_cache_home=args.cache_home,
                )
        else:
            config = ExtractorConfig(
                mode=args.mode,
                language=language,
                ocr_batch_size=args.ocr_batch_size,
                ocr_quality_variants=not args.no_ocr_quality_variants,
                ocr_quality_policy=args.ocr_quality_policy,
                preserve_headers_footers=not args.omit_repeated_headers_footers,
                merge_cross_page_tables=args.merge_cross_page_tables,
                num_threads=args.threads,
                ocr_engine=effective_engine,
                ocr_provider=args.ocr_provider,
                paddle_model_profile=profile_name,
                ocr_cache_home=args.cache_home,
            )
        if args.cache_home:
            from dataclasses import replace as _replace
            config = _replace(config, ocr_cache_home=args.cache_home)
        _warn_if_exhaustive(effective_ocr_quality_policy(config).value)
        # F04: Paddle model preflight only runs when the selected engine is Paddle.
        if _mode_requires_ocr(config.mode):
            readiness = probe_static(config, cache_home=args.cache_home)
            if readiness.status.value == "degraded":
                print(
                    f"OCR backend is available in degraded mode: {readiness.reason_code}: {readiness.details}",
                    file=sys.stderr,
                )
            elif readiness.status.value != "ready":
                print(
                    f"OCR backend is not ready before extraction: {readiness.status.value} "
                    f"({readiness.reason_code or 'unknown'}): {readiness.details}",
                    file=sys.stderr,
                )
                if config.ocr_engine == "paddle":
                    print(
                        f"Run: pdftext setup-paddle-models --ocr-model-profile {config.paddle_model_profile}"
                        + (f" --cache-home {args.cache_home}" if args.cache_home else ""),
                        file=sys.stderr,
                    )
                return 1

        def _progress(current: int, total: int) -> None:
            print(f"\rExtraindo página {current}/{total}...", end="", file=sys.stderr, flush=True)

        callback = _progress if args.progress else None
        password = None
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        elif args.ask_password:
            password = getpass.getpass("PDF password: ")
        if args.cache_home:
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(
                Path(args.cache_home).expanduser().resolve()
            )
        try:
            with PdfTextExtractor(config) as extractor:
                document = extractor.extract(
                    args.pdf,
                    password=password,
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
            try:
                args.output_file.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_text(args.output_file, result)
            except OSError as exc:
                print(f"Could not write output file: {exc}", file=sys.stderr)
                return 1
        else:
            print(result)
        return 1 if document.diagnostics.status.value != "success" else 0

    if args.command == "inspect":
        if args.page is not None and args.page < 1:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        profile_name = args.paddle_model_profile or "pt"
        config = ExtractorConfig(
            mode=args.mode,
            language=args.language,
            paddle_model_profile=profile_name,
            retain_native_evidence=args.raw_page_json,
            ocr_quality_policy=args.ocr_quality_policy,
            page_indices=(args.page - 1,) if args.page is not None else None,
            ocr_engine=args.ocr_engine,
            ocr_provider=args.ocr_provider,
        )
        _warn_if_exhaustive(effective_ocr_quality_policy(config).value)
        if _mode_requires_ocr(config.mode):
            readiness = probe_static(config, cache_home=args.cache_home)
            if readiness.status.value != "ready":
                print(f"OCR backend is not ready: {readiness.status.value} ({readiness.reason_code})", file=sys.stderr)
                return 1
        if args.cache_home:
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(
                Path(args.cache_home).expanduser().resolve()
            )
        try:
            with PdfTextExtractor(config) as extractor:
                document = extractor.extract(args.pdf)
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
                return 1 if document.diagnostics.status.value != "success" else 0
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
        return 1 if document.diagnostics.status.value != "success" else 0

    if args.command == "overlay":
        index = args.page - 1
        if index < 0:
            print(f"Invalid page: {args.page}", file=sys.stderr)
            return 2
        mode = ExtractionMode(args.mode)
        if mode == ExtractionMode.NATIVE and args.ocr_model_profile is not None:
            print("--ocr-model-profile cannot be used with --mode native", file=sys.stderr)
            return 2
        profile_name = args.ocr_model_profile or "pt"
        config = ExtractorConfig(
            mode=mode,
            language=args.language,
            paddle_model_profile=profile_name,
            ocr_engine=args.ocr_engine,
            ocr_provider=args.ocr_provider,
            page_indices=(index,),
        )
        if mode != ExtractionMode.NATIVE:
            readiness = probe_static(config, cache_home=args.cache_home)
            if readiness.status.value != "ready":
                print(f"OCR backend is not ready: {readiness.status.value} ({readiness.reason_code})", file=sys.stderr)
                return 1
        try:
            with PdfTextExtractor(
                config
            ) as extractor:
                document = extractor.extract(args.pdf)
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
        profile_name = args.paddle_model_profile or "pt"
        config = ExtractorConfig(
            mode=args.mode,
            language=args.language,
            paddle_model_profile=profile_name,
            ocr_batch_size=args.ocr_batch_size,
            ocr_quality_variants=not args.no_ocr_quality_variants,
            ocr_quality_policy=args.ocr_quality_policy,
            merge_cross_page_tables=args.merge_cross_page_tables,
            num_threads=args.threads,
            ocr_engine=args.ocr_engine,
            ocr_provider=args.ocr_provider,
        )
        _warn_if_exhaustive(effective_ocr_quality_policy(config).value)
        if args.workers < 1:
            print("--workers must be at least 1", file=sys.stderr)
            return 2
        if _mode_requires_ocr(config.mode):
            readiness = probe_static(config, cache_home=args.cache_home)
            if readiness.status.value != "ready":
                print(f"OCR backend is not ready: {readiness.status.value} ({readiness.reason_code})", file=sys.stderr)
                return 1
        if args.cache_home:
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(
                Path(args.cache_home).expanduser().resolve()
            )
        try:
            report = corpus_report(args.pdfs, config, workers=args.workers)
        except FatalExtractionError as exc:
            _print_fatal_extraction_error(exc)
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if any(
            document.get("status") != "success"
            for document in report.get("documents", [])
        ) else 0

    if args.command == "compare":
        ocr_adapters = {"structured-balanced"}
        if any(adapter in ocr_adapters for adapter in args.adapters):
            if args.ocr_model_profile is None and args.language is None:
                print("An explicit --ocr-model-profile or --language is required when structured-balanced is selected", file=sys.stderr)
                return 2
            profile_name = args.ocr_model_profile or args.language or "pt"
            try:
                get_profile(profile_name)
                validate_local_ocr_models(language=profile_name)
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            except PaddleOcrUnavailable as exc:
                print(str(exc), file=sys.stderr)
                print(
                    f"Run: pdftext setup-paddle-models --paddle-model-profile {profile_name}",
                    file=sys.stderr,
                )
                return 1
        try:
            comparison = compare_extractors(
                args.pdf,
                args.adapters,
                reference=args.reference,
                language=args.ocr_model_profile or args.language or "pt",
                include_text=args.include_text,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(comparison, ensure_ascii=False, indent=2))
        return 0 if comparison.get("status") == "success" else 1

    if args.command in {"setup-paddle-models", "setup-models"}:
        profile_name = args.paddle_model_profile or args.language or "pt"
        return _cmd_setup_models(profile_name, args.cache_home)

    if args.command == "setup-easyocr-models":
        return _cmd_setup_easyocr_models(args.language, args.cache_home, args.include_dbnet)

    if args.command in {"paddle-models-status", "models-status"}:
        profile_name = args.paddle_model_profile or args.language or "pt"
        return _cmd_models_status(profile_name, args.cache_home)

    return 2


def _mode_requires_ocr(mode: ExtractionMode | str) -> bool:
    """Return True when the extraction mode can trigger OCR."""
    ocr_modes = {ExtractionMode.BALANCED, ExtractionMode.OCR, "balanced", "ocr"}
    return mode in ocr_modes


def _cmd_setup_easyocr_models(language: str, cache_home: str | None, include_dbnet: bool) -> int:
    """Provision EasyOCR weights explicitly; inference remains offline-only."""
    try:
        import easyocr
        from .ocr.languages import backend_language
        cache = Path(cache_home).expanduser() / "easyocr" if cache_home else Path.home() / ".cache" / "pdfextractor" / "easyocr"
        cache.mkdir(parents=True, exist_ok=True)
        langs = [backend_language(language, "easyocr")]
        easyocr.Reader(langs, gpu=False, model_storage_directory=str(cache), download_enabled=True)
        if include_dbnet:
            easyocr.Reader(
                langs, gpu=False, detect_network="dbnet18",
                model_storage_directory=str(cache), download_enabled=True,
            )
        print(json.dumps({"status": "ready", "cache": str(cache), "dbnet18_requested": include_dbnet}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"EasyOCR model setup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


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


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a result file atomically using a temporary sibling file."""
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _model_is_ready(model_dir: "Path") -> bool:
    """Compatibility wrapper around the shared model readiness check."""
    return model_directory_is_ready(model_dir)


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
    required_models = required_model_directories(
        profile, **PADDLE_OCR_FEATURE_DEFAULTS
    ).values()
    for model_name in required_models:
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
        hint = f"Run: pdftext setup-paddle-models --paddle-model-profile {language}"
        if cache_home:
            hint += f" --cache-home {cache_home}"
        print(hint)
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
    print(f"Profile: {language}")
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
    policy = resolve_paddle_runtime_policy()
    apply_paddle_runtime_policy(policy)
    options: dict = {
        **PADDLE_OCR_FEATURE_DEFAULTS,
        "enable_mkldnn": policy.enable_mkldnn,
        **profile.name_kwargs,
        "doc_unwarping_model_name": UV_DOC_MODEL,
    }

    try:
        PaddleOCR(**options)
    except Exception as exc:
        print(f"Error during model initialization: {exc}", file=sys.stderr)
        return 1

    print("\nVerifying installed models...")
    all_ok = True
    required_models = required_model_directories(
        profile, **PADDLE_OCR_FEATURE_DEFAULTS
    ).values()
    for model_name in required_models:
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
            "\nSome models are still missing. Re-run setup-paddle-models or check connectivity.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
