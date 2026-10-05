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

Engines: paddle | rapidocr (onnxruntime/openvino) | tesseract | easyocr

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
import os
import platform
import re
import subprocess
import sys
import time
import statistics
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


def _git_dirty() -> bool | None:
    """Return dirty state, or None when Git could not determine it."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        )
        return bool(out.strip())
    except Exception:
        return None


def _git_status_error() -> str | None:
    try:
        subprocess.check_output(["git", "status", "--porcelain"], text=True, stderr=subprocess.PIPE)
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


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


def _page_body(content: str) -> str:
    return re.sub(r"^##\s+P[áa]gina\s+[^\n]*\n?", "", content, count=1, flags=re.I).strip()


def _classify_page_status(
    ocr_outcome: str, facts: dict, has_content: bool, ocr_tokens_added: int
) -> tuple[str, str]:
    """Return content/benchmark status and stability as separate dimensions."""
    if "ocr_failed" in facts or ocr_outcome in ("failed", "runtime_error"):
        return "invalid", "failed"
    if facts.get("partial_reasons"):
        return "degraded", "degraded"
    if ocr_outcome == "recovered" and has_content:
        return "ok", "recovered"
    if ocr_outcome in ("degraded", "partial"):
        return "degraded", "degraded"
    if not has_content and ocr_tokens_added == 0:
        return "empty", "unknown"
    return "ok", "clean"


def _classify_document_status(
    doc_status: str, failed_pages: list[int], degraded_pages: list[int]
) -> str:
    if doc_status == "failure" or failed_pages:
        return "invalid"
    if degraded_pages or doc_status == "partial_success":
        return "partial"
    return "valid"


def _diagnostic_reasons(diag) -> tuple[list[str], list[str]]:
    facts = dict(getattr(diag, "facts", {}))
    partial = sorted({str(value) for value in facts.get("partial_reasons", [])})
    complexity = [
        reason.value if hasattr(reason, "value") else str(reason)
        for reason in getattr(diag, "reasons", [])
    ]
    return partial, complexity


def _build_config(engine: str, mode: str, language: str, page_indices: tuple | None, provider: str | None = None):
    """Build an ExtractorConfig for the given engine and extraction mode.

    render_scale is set per-engine via best_ocr_render_scale(). Override with
    the OCR_RENDER_SCALE env var for one-off experiments without code changes.
    """
    import os
    from structured_pdf_text.config import (
        ExtractionMode,
        best_extraction_config,
        best_ocr_render_scale,
    )
    base = best_extraction_config(language=language, preserve_headers=False)
    render_scale_env = os.environ.get("OCR_RENDER_SCALE")
    render_scale = float(render_scale_env) if render_scale_env else best_ocr_render_scale(engine)
    config = replace(base, ocr_engine=engine, ocr_provider=provider, mode=ExtractionMode(mode), ocr_render_scale=render_scale)
    if page_indices is not None:
        config = replace(config, page_indices=page_indices)
    return config


def _parse_pages_arg(pages_arg: str) -> tuple[int, ...]:
    """Convert a page range string ('1-5' or '1,3,5') to a tuple of 0-based page indices."""
    indices: list[int] = []
    for part in pages_arg.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Malformed page range: {pages_arg!r}")
        if "-" in part:
            lo, hi = part.split("-", 1)
            start, end = int(lo), int(hi)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range {part!r}; expected 1 <= start <= end")
            indices.extend(range(start - 1, end))
        else:
            page = int(part)
            if page < 1:
                raise ValueError(f"Page numbers are 1-based and must be positive: {part!r}")
            indices.append(page - 1)
    return tuple(sorted(set(indices)))


def main() -> int:
    """Entry point: extract a PDF with one OCR engine and save Markdown + run manifest."""
    ap = argparse.ArgumentParser(
        description="Run pdfextractor E2E pipeline and save output (Fase 8)."
    )
    ap.add_argument("pdf", type=Path, help="PDF corpus to extract")
    ap.add_argument(
        "--engine",
        default="easyocr",
        choices=["easyocr", "paddle", "rapidocr", "tesseract"],
        help="OCR family (default: easyocr); choose RapidOCR provider with --provider",
    )
    ap.add_argument("--provider", choices=["onnxruntime", "openvino"], default=None)
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
        config = _build_config(args.engine, args.mode, args.language, page_indices, args.provider)
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

    # Per-page timing tracking.
    # on_progress(current, total) fires when page `current` *begins*.
    # The interval [on_progress(N) → on_progress(N+1)] is the wall-clock time
    # spent processing page N (render + OCR + assembly combined — not broken
    # down further without pipeline instrumentation).
    # The last page interval is closed immediately when extract() returns so
    # it excludes post-extraction work (markdown rendering, manifest writing).
    page_times: list[float] = []
    _tick: list[float] = [time.perf_counter()]
    _total_pages: list[int] = [0]

    def on_progress(current: int, total: int) -> None:
        now = time.perf_counter()
        _total_pages[0] = total
        if current > 1:
            page_times.append(now - _tick[0])
        _tick[0] = now
        if not args.quiet:
            print(f"  Processando página {current}/{total}...", end="\r")

    t0 = time.perf_counter()
    document = None
    engine_identity: dict = {}
    error_msg: str | None = None
    error_details: dict = {}
    extractor = PdfTextExtractor(config)
    from structured_pdf_text.memory import ProcessTreeMemorySampler
    memory_sampler = ProcessTreeMemorySampler()

    memory_sampler.__enter__()
    try:
        with extractor:
            document = extractor.extract(args.pdf, progress_callback=on_progress)
            # PdfTextExtractor closes its backend on context exit and clears
            # ``ocr_engine``. Snapshot the effective runtime identity while
            # the backend is still available so the run manifest retains its
            # package versions, model hashes, provider and profile.
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
                        "extra": dict(getattr(ident, "extra", {})),
                    }
                except Exception:
                    pass
            # Close the last page interval immediately — before any post-processing.
            page_times.append(time.perf_counter() - _tick[0])
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
    finally:
        memory_sampler.__exit__(None, None, None)

    elapsed_s = time.perf_counter() - t0

    # --- Save error manifest if extraction failed ---
    if document is None:
        from structured_pdf_text.benchmark_protocol import protocol_manifest
        manifest = {
            "schema_version": "2.0",
            "run_id": run_id,
            "git_sha": _git_sha(),
            "git_dirty": _git_dirty(),
            "git_dirty_error": _git_status_error(),
            "engine": args.engine,
            "mode": args.mode,
            "language": args.language,
            **protocol_manifest(
                language=config.language,
                mode=config.normalized_mode().value,
                quality_policy=config.effective_ocr_quality_policy().value,
            ),
            "pdf_path": str(args.pdf),
            "pdf_sha256": _sha256_file(args.pdf),
            "status": "error",
            "error": error_msg,
            "error_details": error_details,
            "elapsed_s": round(elapsed_s, 3),
            "memory": memory_sampler.snapshot(),
            "page_count": 0,
            "pages": [],
        }
        from structured_pdf_text.atomic_io import atomic_write_json
        atomic_write_json(manifest_path, manifest)
        print(f"\nERROR manifest: {manifest_path}")
        return 1

    # --- Render markdown and save ---
    from structured_pdf_text.renderers.markdown import render_markdown
    # This artifact is for page-aligned diagnostics and structural metrics.
    md_text = render_markdown(document, diagnostic=True)
    from structured_pdf_text.atomic_io import atomic_write_text
    atomic_write_text(md_path, md_text)

    # --- Build per-page entries (schema v2) ---
    page_entries = []
    degraded_pages: list[int] = []
    failed_ocr_pages: list[int] = []
    recovered_pages: list[int] = []

    for i, page in enumerate(document.pages):
        page_num = page.page_index + 1
        elapsed_page = page_times[i] if i < len(page_times) else 0.0
        diag = page.diagnostics
        page_warnings: list[str] = list(getattr(diag, "warnings", []))
        facts: dict = dict(getattr(diag, "facts", {}))
        has_page_content = bool(page.reading_text.strip())
        partial_reasons, complexity_reasons = _diagnostic_reasons(diag)

        # Derive ocr_outcome from diagnostics facts and token counts.
        ocr_tokens_added: int = getattr(diag, "ocr_tokens_added", 0)
        ocr_outcome = facts.get("ocr_outcome", None)
        if ocr_outcome is None:
            if ocr_tokens_added > 0:
                ocr_outcome = "success"
            elif has_page_content:
                ocr_outcome = "not_requested"
            else:
                ocr_outcome = "unknown"

        # Classify page-level benchmark status.
        page_bstatus, stability_status = _classify_page_status(
            str(ocr_outcome), facts, has_page_content, ocr_tokens_added
        )
        if page_bstatus == "invalid":
            failed_ocr_pages.append(page_num)
        elif stability_status == "recovered":
            recovered_pages.append(page_num)
        elif stability_status == "degraded":
            degraded_pages.append(page_num)

        page_timings = dict(facts.get("timings_ms", {}))
        render_s = sum(page_timings.get(k, 0.0) for k in ("render_lowres_ms", "render_ocr_ms")) / 1000
        ocr_s = sum(page_timings.get(k, 0.0) for k in ("ocr_ms", "ocr_figure_ms", "ocr_targeted_refinement_ms")) / 1000
        assembly_s = max(0.0, elapsed_page - render_s - ocr_s)

        page_entries.append({
            "page": page_num,
            "content_status": (
                "invalid" if page_bstatus == "invalid"
                else "empty" if not has_page_content
                else "partial" if page_bstatus == "degraded"
                else "valid"
            ),
            "benchmark_page_status": page_bstatus,
            "stability_status": stability_status,
            "ocr_outcome": ocr_outcome,
            "partial_reasons": partial_reasons,
            "complexity_reasons": complexity_reasons,
            "warnings": page_warnings,
            "elapsed_s": round(elapsed_page, 4),
            "render_s": round(render_s, 4),
            "ocr_s": round(ocr_s, 4),
            "assembly_s": round(assembly_s, 4),
            "timings_ms": page_timings,
            "easyocr_calls": int(facts.get("easyocr_calls", 0)),
            "easyocr_fallback_count": int(facts.get("easyocr_fallback_count", 0)),
            "easyocr_fallback_rate": float(facts.get("easyocr_fallback_rate", 0.0)),
            "easyocr_fallback_reasons": list(facts.get("easyocr_fallback_reasons", [])),
            "char_count": len(page.reading_text),
            "ocr_tokens_added": ocr_tokens_added,
            "strategy": getattr(getattr(diag, "strategy", None), "value", None) or "unknown",
        })

    # --- Document-level benchmark status ---
    doc_diag = document.diagnostics
    _doc_status = getattr(doc_diag, "status", None)
    doc_status_raw: str = _doc_status.value if hasattr(_doc_status, "value") else str(_doc_status or "unknown")
    doc_warnings: list[str] = list(getattr(doc_diag, "warnings", []))

    benchmark_status = _classify_document_status(
        doc_status_raw, failed_ocr_pages, degraded_pages
    )

    # --- Memory snapshot (set by api.py after extraction completes) ---
    doc_facts: dict = dict(getattr(doc_diag, "facts", {}))
    memory_stats: dict = dict(doc_facts.get("memory", {}))
    memory_stats.update(memory_sampler.as_dict())

    from structured_pdf_text.benchmark_protocol import protocol_manifest
    manifest = {
        "schema_version": "2.0",
        "run_id": run_id,
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "git_dirty_error": _git_status_error(),
        "engine": args.engine,
        "engine_identity": engine_identity,
        "mode": args.mode,
        "language": args.language,
        **protocol_manifest(
            language=config.language,
            mode=config.normalized_mode().value,
            quality_policy=config.effective_ocr_quality_policy().value,
        ),
        "pdf_path": str(args.pdf),
        "pdf_sha256": _sha256_file(args.pdf),
        "document_status": doc_status_raw,
        "benchmark_status": benchmark_status,
        "document_warnings": doc_warnings,
        "degraded_pages": degraded_pages,
        "failed_ocr_pages": failed_ocr_pages,
        "recovered_pages": recovered_pages,
        "recovery_count": sum(p["easyocr_fallback_count"] for p in page_entries),
        "fallback_rate": (
            sum(p["easyocr_fallback_count"] for p in page_entries)
            / max(1, sum(p["easyocr_calls"] for p in page_entries))
        ),
        "stability_status": (
            "failed" if benchmark_status == "invalid"
            else "degraded" if degraded_pages or benchmark_status == "partial"
            else "recovered" if recovered_pages
            else "clean"
        ),
        "warnings_count": len(doc_warnings),
        "elapsed_s": round(elapsed_s, 3),
        "timings": {
            key: round(sum(page[key] for page in page_entries), 4)
            for key in ("render_s", "ocr_s", "assembly_s")
        },
        "page_count": len(document.pages),
        "memory": memory_stats,
        "runtime_environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cpu_count": os.cpu_count(),
            "requested_threads": getattr(config, "num_threads", None),
            "ocr_provider": getattr(config, "ocr_provider", None),
            "render_scale": getattr(config, "ocr_render_scale", None),
            "thread_environment": {
                key: os.environ.get(key)
                for key in ("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
            },
        },
        "startup_and_throughput": {
            "first_page_latency_s": round(page_times[0], 4) if page_times else None,
            "warm_page_median_s": round(statistics.median(page_times[1:]), 4) if len(page_times) > 1 else None,
            "warm_page_p95_s": round(sorted(page_times[1:])[int(0.95 * (len(page_times[1:]) - 1))], 4) if len(page_times) > 2 else None,
        },
        "extracted_markdown": str(md_path),
        "timing_note": (
            "per-page elapsed_s is the wall-clock interval between progress callbacks "
            "(render + OCR + assembly combined). Last page interval is closed immediately "
            "after extract() returns, excluding markdown rendering and manifest writing."
        ),
        "pages": page_entries,
    }

    from structured_pdf_text.atomic_io import atomic_write_json
    atomic_write_json(manifest_path, manifest)

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
        print(f"    --manifesto <CORPUS_MANIFESTO.json> \\")
        print(f"    --run-manifest {manifest_path} \\")
        print(f"    --engine {args.engine} \\")
        print(f"    --output-dir {args.output_dir}")
        print(f"  # Replace <CORPUS_MANIFESTO.json> with the corpus reference manifesto path.")

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
