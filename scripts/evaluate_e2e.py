#!/usr/bin/env python3
"""
E2E benchmark runner.

Runs PdfTextExtractor with the specified OCR engine on a corpus PDF,
saves the extracted Markdown and a run manifest (schema v2).

Usage (Windows server):
    python scripts\\evaluate_e2e.py ^
        corpus\\Corpus_Stress_OCR_Markdown_V4.pdf ^
        --engine paddle ^
        --output-dir output\\fase8

    python scripts\\evaluate_e2e.py ^
        corpus\\Corpus_Stress_OCR_Markdown_V4.pdf ^
        --engine tesseract ^
        --output-dir output\\fase8 ^
        --allow-partial

Engines: paddle | rapidocr-onnx | rapidocr-openvino | tesseract | easyocr

Output:
    output/fase8/extracted_{engine}_{run_id}.md
    output/fase8/manifesto_e2e_{engine}_{run_id}.json

Schema v2 changes vs v1
-----------------------
- document_status: the real ExtractionStatus from document.diagnostics.status
  ("success" | "partial_success" | "failure")
- benchmark_status: "valid" | "partial" | "invalid" — derived from document_status
  A "partial" or "invalid" run is NOT eligible for standard ranking.
- degraded_pages: list of page numbers where OCR or assembly was degraded
- failed_ocr_pages: list of page numbers where OCR failed entirely
- Per-page entries now include ocr_outcome, partial_reasons, warnings
- --allow-partial: if absent and document_status != "success", exits with code 2
  (run saved, but signalled as ineligible for automatic ranking)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _git_sha() -> str:
    """Return the current git commit SHA, or 'unknown' on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    """Return True if the working tree has uncommitted changes."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        )
        return bool(out.strip())
    except Exception:
        return False


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_page_sections(md_text: str) -> dict[int, str]:
    """Split markdown into per-page sections.

    Handles both formats:
      '## Página 1'          (extractor output)
      '## Página 001 | Title' (reference format)
    Returns {1-based page number: page text (including header line)}.
    """
    pattern = re.compile(
        r"^##\s+P[áa]gina\s+0*(\d+)",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(pattern.finditer(md_text))
    if not matches:
        return {}
    pages: dict[int, str] = {}
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        pages[page_num] = md_text[start:end].strip()
    return pages


def _build_config(engine: str, mode: str, language: str, page_indices: tuple | None):
    """Build an ExtractorConfig for the given engine and extraction mode."""
    from structured_pdf_text.config import (
        ExtractionMode,
        best_extraction_config,
    )
    base = best_extraction_config(language=language, preserve_headers=False)
    config = replace(base, ocr_engine=engine, mode=ExtractionMode(mode))
    if page_indices is not None:
        config = replace(config, page_indices=page_indices)
    return config


def _parse_pages_arg(pages_arg: str) -> tuple[int, ...]:
    """Convert a page range string ('1-5' or '1,3,5') to a tuple of 0-based page indices."""
    indices: list[int] = []
    for part in pages_arg.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.extend(range(int(lo) - 1, int(hi)))  # 1-based → 0-based
        else:
            indices.append(int(part) - 1)
    return tuple(sorted(set(indices)))


def main() -> int:
    """Entry point: extract a PDF with one OCR engine and save Markdown + run manifest."""
    ap = argparse.ArgumentParser(
        description="Run pdfextractor E2E pipeline and save output (Fase 8)."
    )
    ap.add_argument("pdf", type=Path, help="PDF corpus to extract")
    ap.add_argument(
        "--engine",
        default="paddle",
        choices=["paddle", "rapidocr-onnx", "rapidocr-openvino", "tesseract", "easyocr"],
        help="OCR engine (default: paddle)",
    )
    ap.add_argument(
        "--mode",
        default="balanced",
        choices=["balanced", "native", "ocr"],
        help="Extraction mode (default: balanced)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/fase8"),
    )
    ap.add_argument("--run-id", default=None, help="Run ID (auto-generated if omitted)")
    ap.add_argument("--pages", default=None, help="Page range: '1-10' or '1,5,10'")
    ap.add_argument("--language", default="pt")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help=(
            "Save and exit 0 even when document_status is partial_success. "
            "Without this flag a partial run exits with code 2 to signal the "
            "benchmark orchestrator that the run is ineligible for ranking."
        ),
    )
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"{ts}-{args.engine}-e2e-001"
    engine_slug = args.engine.replace("-", "_")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.output_dir / f"extracted_{engine_slug}_{run_id}.md"
    manifest_path = args.output_dir / f"manifesto_e2e_{engine_slug}_{run_id}.json"

    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    try:
        from structured_pdf_text.api import PdfTextExtractor
        from structured_pdf_text.renderers.markdown import render_markdown
    except ImportError as exc:
        print(f"ERROR: cannot import structured_pdf_text: {exc}", file=sys.stderr)
        return 1

    page_indices = _parse_pages_arg(args.pages) if args.pages else None

    try:
        config = _build_config(args.engine, args.mode, args.language, page_indices)
    except Exception as exc:
        print(f"ERROR: cannot build config: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"=== E2E Benchmark — {args.engine} ===")
        print(f"  PDF    : {args.pdf}")
        print(f"  Run ID : {run_id}")
        print(f"  Mode   : {args.mode}")
        print(f"  Output : {args.output_dir}")
        print()

    # Per-page timing tracking
    page_times: list[float] = []
    _tick: list[float] = [time.perf_counter()]

    def on_progress(current: int, total: int) -> None:
        now = time.perf_counter()
        if current > 1:
            page_times.append(now - _tick[0])
        _tick[0] = now
        if not args.quiet:
            print(f"  Processando página {current}/{total}...", end="\r")

    t0 = time.perf_counter()
    document = None
    error_msg: str | None = None
    error_details: dict = {}

    try:
        extractor = PdfTextExtractor(config)
        document = extractor.extract(args.pdf, progress_callback=on_progress)
        page_times.append(time.perf_counter() - _tick[0])  # last page
    except Exception as exc:
        error_msg = str(exc)
        if hasattr(exc, "details") and exc.details:
            error_details = dict(exc.details)
        if exc.__cause__ is not None:
            error_details.setdefault("cause_type", type(exc.__cause__).__name__)
            error_details.setdefault("cause_message", str(exc.__cause__))
        if not args.quiet:
            print(f"\n  ERROR: {exc}", file=sys.stderr)
            if error_details:
                for k, v in error_details.items():
                    print(f"  {k}: {v}", file=sys.stderr)

    elapsed_s = time.perf_counter() - t0

    # --- Save error manifest if extraction failed ---
    if document is None:
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "git_sha": _git_sha(),
            "git_dirty": _git_dirty(),
            "engine": args.engine,
            "mode": args.mode,
            "language": args.language,
            "pdf_path": str(args.pdf),
            "pdf_sha256": _sha256_file(args.pdf),
            "status": "error",
            "error": error_msg,
            "error_details": error_details,
            "elapsed_s": round(elapsed_s, 3),
            "page_count": 0,
            "pages": [],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nERROR manifest: {manifest_path}")
        return 1

    # --- Render markdown and save ---
    from structured_pdf_text.renderers.markdown import render_markdown
    md_text = render_markdown(document)
    md_path.write_text(md_text, encoding="utf-8")

    page_sections = _parse_page_sections(md_text)

    # --- Build per-page entries (schema v2) ---
    page_entries = []
    degraded_pages: list[int] = []
    failed_ocr_pages: list[int] = []

    for i, page in enumerate(document.pages):
        page_num = page.page_index + 1
        elapsed_page = page_times[i] if i < len(page_times) else 0.0
        content = page_sections.get(page_num, "")

        diag = page.diagnostics
        page_warnings: list[str] = list(getattr(diag, "warnings", []))
        partial_reasons: list[str] = [str(r) for r in getattr(diag, "reasons", [])]
        facts: dict = dict(getattr(diag, "facts", {}))

        # Derive ocr_outcome from diagnostics facts and token counts.
        ocr_tokens_added: int = getattr(diag, "ocr_tokens_added", 0)
        ocr_outcome = facts.get("ocr_outcome", None)
        if ocr_outcome is None:
            if ocr_tokens_added > 0:
                ocr_outcome = "success"
            elif content.strip():
                ocr_outcome = "not_requested"
            else:
                ocr_outcome = "unknown"

        # Classify page-level benchmark status.
        if "ocr_failed" in facts or ocr_outcome in ("failed", "runtime_error"):
            page_bstatus = "invalid"
            failed_ocr_pages.append(page_num)
        elif ocr_outcome in ("recovered", "degraded", "partial"):
            page_bstatus = "degraded"
            degraded_pages.append(page_num)
        elif not content.strip() and ocr_tokens_added == 0:
            page_bstatus = "empty"
        else:
            page_bstatus = "ok"

        page_entries.append({
            "page": page_num,
            "content_status": "nonempty" if content.strip() else "empty",
            "benchmark_page_status": page_bstatus,
            "ocr_outcome": ocr_outcome,
            "partial_reasons": partial_reasons,
            "warnings": page_warnings,
            "elapsed_s": round(elapsed_page, 4),
            "char_count": len(content),
            "ocr_tokens_added": ocr_tokens_added,
            "strategy": str(getattr(diag, "strategy", "unknown")),
        })

    # --- Document-level benchmark status ---
    doc_diag = document.diagnostics
    doc_status_raw: str = str(getattr(doc_diag, "status", "unknown"))
    doc_warnings: list[str] = list(getattr(doc_diag, "warnings", []))

    if failed_ocr_pages:
        benchmark_status = "invalid"
    elif degraded_pages or doc_status_raw == "partial_success":
        benchmark_status = "partial"
    else:
        benchmark_status = "valid"

    # --- Engine identity ---
    engine_identity: dict = {}
    ocr_eng = getattr(extractor, "ocr_engine", None)
    if ocr_eng is not None and hasattr(ocr_eng, "identity"):
        try:
            ident = ocr_eng.identity
            engine_identity = {
                "engine": ident.engine,
                "runtime": ident.runtime,
                "profile": ident.profile,
                "language": ident.language,
                "device": getattr(ident, "device", None),
                "package_versions": dict(ident.package_versions),
                "artifact_hashes": dict(getattr(ident, "artifact_hashes", {})),
            }
        except Exception:
            pass

    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "engine": args.engine,
        "engine_identity": engine_identity,
        "mode": args.mode,
        "language": args.language,
        "pdf_path": str(args.pdf),
        "pdf_sha256": _sha256_file(args.pdf),
        "document_status": doc_status_raw,
        "benchmark_status": benchmark_status,
        "document_warnings": doc_warnings,
        "degraded_pages": degraded_pages,
        "failed_ocr_pages": failed_ocr_pages,
        "warnings_count": len(doc_warnings),
        "elapsed_s": round(elapsed_s, 3),
        "page_count": len(document.pages),
        "extracted_markdown": str(md_path),
        "pages": page_entries,
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        n_pages = len(document.pages)
        n_empty = sum(1 for e in page_entries if e["content_status"] == "empty")
        s_per_page = elapsed_s / n_pages if n_pages else 0
        print(f"\n  Concluído: {n_pages} páginas em {elapsed_s:.1f}s ({s_per_page:.2f}s/pág)")
        if n_empty:
            print(f"  Atenção: {n_empty} páginas vazias")
        if degraded_pages:
            print(f"  Atenção: {len(degraded_pages)} páginas degradadas: {degraded_pages}")
        if failed_ocr_pages:
            print(f"  ERRO: {len(failed_ocr_pages)} páginas com falha de OCR: {failed_ocr_pages}")
        print(f"  benchmark_status: {benchmark_status}")
        print(f"\n  Markdown  : {md_path}")
        print(f"  Manifesto : {manifest_path}")
        print()
        print("  Próximo passo — calcular métricas:")
        print(f"  python scripts/compute_metrics.py \\")
        print(f"    --hypothesis {md_path} \\")
        print(f"    --manifesto <MANIFESTO.json> \\")
        print(f"    --engine {args.engine} \\")
        print(f"    --output-dir {args.output_dir}")

    # Exit code 2 signals "run saved but ineligible for standard ranking".
    if benchmark_status != "valid" and not args.allow_partial:
        if not args.quiet:
            print(
                f"\n  AVISO: benchmark_status={benchmark_status!r}. "
                "Use --allow-partial para suprimir este exit code.",
                file=sys.stderr,
            )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
