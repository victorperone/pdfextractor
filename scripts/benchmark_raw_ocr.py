"""RAW OCR benchmark — Fase 3 do comparativo de engines.

Renders each page of a PDF to pixels (congelado), runs one OCR engine,
saves canonical OCRResult per page, and optionally computes CER/WER against
a reference Markdown file.  Generates a run-manifest JSON (see §39 of
Plano_Comparativo_Paddle.md).

Usage (Windows PowerShell — servidor):
    python scripts\\benchmark_raw_ocr.py corpus\\Document_AI_V3.pdf ^
        --engine paddle ^
        --reference corpus\\Corpus_Integrado_PDF_OCR_TableMagic_V3_REFERENCIA.md ^
        --manifesto corpus\\Corpus_Integrado_PDF_OCR_TableMagic_V3_MANIFESTO.json ^
        --output-dir output\\benchmark_raw

Usage (Linux / WSL):
    python scripts/benchmark_raw_ocr.py corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf \\
        --engine paddle \\
        --reference corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3_REFERENCIA.md \\
        --manifesto corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3_MANIFESTO.json \\
        --output-dir output/benchmark_raw

Benchmark rules (Plano_Comparativo_Paddle.md §60):
  - Mesmos pixels para todas as engines (--render-scale fixado por run).
  - Falhas permanecem no denominador.
  - Sem downloads durante execução.
  - Versões e hashes registrados no manifesto.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_info() -> dict[str, object]:
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
        return {"git_sha": sha, "git_dirty": dirty}
    except Exception:
        return {"git_sha": "unknown", "git_dirty": None}


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

def _render_page(pdf_path: Path, page_index: int, scale: float) -> tuple[object, bytes]:
    """Render one page and return (PIL.Image, png_bytes)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[page_index]
    image = page.render(scale=scale).to_pil().convert("RGB")
    import io
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return image, buf.getvalue()


# ---------------------------------------------------------------------------
# Reference markdown parsing
# ---------------------------------------------------------------------------

def _load_reference_pages(ref_path: Path) -> dict[int, str]:
    from structured_pdf_text.diagnostics.ocr_metrics import parse_reference_pages
    return parse_reference_pages(ref_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Corpus manifesto loader
# ---------------------------------------------------------------------------

def _load_manifesto(manifesto_path: Path) -> dict:
    with manifesto_path.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-page result
# ---------------------------------------------------------------------------

def _ocr_tokens_to_text(tokens: tuple) -> str:
    return " ".join(t.text for t in tokens if t.text.strip())


def _compute_metrics(
    hyp_text: str,
    ref_pages: dict[int, str],
    page_num: int,
) -> dict[str, object] | None:
    if not ref_pages or page_num not in ref_pages:
        return None
    from structured_pdf_text.diagnostics.ocr_metrics import cer as compute_cer, wer as compute_wer
    ref = ref_pages[page_num]
    return {
        "cer_strict": round(compute_cer(hyp_text, ref), 6),
        "wer_strict": round(compute_wer(hyp_text, ref), 6),
        "reference_chars": len(ref),
        "hypothesis_chars": len(hyp_text),
    }


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    pdf_path: Path,
    engine_name: str,
    language: str,
    render_scale: float,
    output_dir: Path,
    run_id: str,
    page_range: tuple[int, int] | None = None,
    ref_pages: dict[int, str] | None = None,
    manifesto: dict | None = None,
    cache_home: str | None = None,
    verbose: bool = True,
) -> dict:
    import pypdfium2 as pdfium
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.factory import build_ocr_backend
    from structured_pdf_text.ocr.contracts import OCRRequest

    # --- set cache home early (must be before any Paddle import) ---
    if cache_home:
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(
            Path(cache_home).expanduser().resolve()
        )

    config = ExtractorConfig(language=language, ocr_engine=engine_name)
    backend = build_ocr_backend(config)

    health = backend.healthcheck()
    if health != "ready":
        print(f"[FAIL] OCR backend healthcheck: {health}", file=sys.stderr)
        sys.exit(1)

    identity = backend.identity
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(doc)
    p_start, p_end = (page_range or (1, total_pages))
    p_start = max(1, p_start)
    p_end = min(total_pages, p_end)

    # Build page metadata index from manifesto
    manifesto_pages: dict[int, dict] = {}
    if manifesto and "pages" in manifesto:
        for pm in manifesto["pages"]:
            manifesto_pages[pm["page"]] = pm

    pages_out: list[dict] = []
    elapsed_total = 0.0
    errors = 0

    if verbose:
        print(f"[INFO] run_id      : {run_id}")
        print(f"[INFO] engine      : {identity.engine}")
        print(f"[INFO] runtime     : {identity.runtime}")
        print(f"[INFO] language    : {identity.language}")
        print(f"[INFO] pages       : {p_start}–{p_end} of {total_pages}")
        print(f"[INFO] render_scale: {render_scale}  (~{int(72 * render_scale)} DPI)")
        print()

    for page_num in range(p_start, p_end + 1):
        page_index = page_num - 1
        meta = manifesto_pages.get(page_num, {})
        mode = meta.get("mode", "unknown")
        family = meta.get("family", "unknown")
        page_id = meta.get("id", f"P{page_num:03d}")
        expected_control = meta.get("expected_page_control", True)

        t0 = time.perf_counter()
        try:
            pil_image, png_bytes = _render_page(pdf_path, page_index, render_scale)
            image_hash = _sha256_bytes(png_bytes)

            request = OCRRequest(
                image=pil_image,
                image_sha256=image_hash,
                document_id=pdf_path.stem,
                page_index=page_index,
                input_kind="page",
                language=language,
                dpi=int(72 * render_scale),
            )
            result = backend.recognize(request)
            status = result.status
            hyp_text = _ocr_tokens_to_text(result.tokens)
            token_count = len(result.tokens)
            warnings = list(result.warnings)
            if result.status != "ok" and result.status != "no_text":
                errors += 1
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            status = "runtime_error"
            hyp_text = ""
            token_count = 0
            warnings = [str(exc)]
            errors += 1
            if verbose:
                print(f"  [FAIL] page {page_num}: {exc}", file=sys.stderr)
        else:
            elapsed = time.perf_counter() - t0

        elapsed_total += elapsed
        metrics = _compute_metrics(hyp_text, ref_pages or {}, page_num)

        page_entry: dict = {
            "page": page_num,
            "id": page_id,
            "mode": mode,
            "family": family,
            "expected_page_control": expected_control,
            "status": status,
            "elapsed_s": round(elapsed, 4),
            "token_count": token_count,
        }
        if metrics:
            page_entry["metrics"] = metrics
        if warnings:
            page_entry["warnings"] = warnings

        pages_out.append(page_entry)

        if verbose:
            cer_str = f"  CER={metrics['cer_strict']:.4f}" if metrics else ""
            print(f"  p{page_num:04d}  {page_id}  {mode:<20s}  {status:<16s}  {elapsed:.2f}s{cer_str}")

    # --- summary metrics ---
    summary_metrics: dict[str, object] = {}
    pages_with_metrics = [p for p in pages_out if "metrics" in p]
    if pages_with_metrics:
        cer_vals = [p["metrics"]["cer_strict"] for p in pages_with_metrics]
        wer_vals = [p["metrics"]["wer_strict"] for p in pages_with_metrics]
        summary_metrics = {
            "pages_evaluated": len(pages_with_metrics),
            "cer_strict_mean": round(sum(cer_vals) / len(cer_vals), 6),
            "cer_strict_median": round(sorted(cer_vals)[len(cer_vals) // 2], 6),
            "wer_strict_mean": round(sum(wer_vals) / len(wer_vals), 6),
            "wer_strict_median": round(sorted(wer_vals)[len(wer_vals) // 2], 6),
        }

    pages_ok = sum(1 for p in pages_out if p["status"] in ("ok", "no_text"))
    pages_failed = sum(1 for p in pages_out if p["status"] not in ("ok", "no_text"))

    # --- run manifest (§39) ---
    git = _git_info()
    manifest_out: dict = {
        "schema_version": "1.0",
        "run_id": run_id,
        "git_sha": git["git_sha"],
        "git_dirty": git["git_dirty"],
        "engine": identity.engine,
        "runtime": identity.runtime,
        "profile": identity.profile,
        "language": identity.language,
        "device": identity.device,
        "package_versions": identity.package_versions,
        "model_artifacts": identity.artifact_hashes,
        "input": {
            "pdf_path": str(pdf_path.resolve()),
            "pdf_sha256": _sha256_file(pdf_path),
            "corpus_manifesto_id": manifesto.get("corpus_id") if manifesto else None,
            "corpus_schema_version": manifesto.get("schema_version") if manifesto else None,
        },
        "settings": {
            "render_scale": render_scale,
            "dpi": int(72 * render_scale),
            "pages": f"{p_start}-{p_end}",
        },
        "summary": {
            "pages_total": p_end - p_start + 1,
            "pages_ok": pages_ok,
            "pages_failed": pages_failed,
            "elapsed_total_s": round(elapsed_total, 2),
            "accuracy": summary_metrics,
        },
        "pages": pages_out,
    }

    manifest_file = output_dir / f"{run_id}_manifest.json"
    manifest_file.write_text(
        json.dumps(manifest_out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if verbose:
        print()
        print(f"[DONE] {pages_ok}/{p_end - p_start + 1} pages OK  |  {errors} errors  |  {elapsed_total:.1f}s total")
        if summary_metrics:
            print(f"[METR] CER mean={summary_metrics['cer_strict_mean']:.4f}  WER mean={summary_metrics['wer_strict_mean']:.4f}")
        print(f"[OUT ] {manifest_file}")

    return manifest_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_page_range(s: str) -> tuple[int, int]:
    if "-" in s:
        parts = s.split("-", 1)
        return int(parts[0]), int(parts[1])
    n = int(s)
    return n, n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="RAW OCR benchmark — Fase 3 do comparativo de engines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pdf", type=Path, help="PDF a processar")
    parser.add_argument(
        "--engine", default="paddle",
        help="OCR engine: paddle (padrão). Fases 4-7 adicionam rapidocr-onnx, etc.",
    )
    parser.add_argument("--language", default="pt", help="Idioma OCR (default: pt)")
    parser.add_argument(
        "--render-scale", type=float, default=2.0,
        help="Escala de renderização (default: 2.0 ≈ 144 DPI). "
             "Todos os engines devem usar a mesma escala na mesma run.",
    )
    parser.add_argument(
        "--pages", default=None,
        help="Intervalo de páginas (ex: 72-103 para só OCR puro). "
             "Padrão: todas as páginas.",
    )
    parser.add_argument(
        "--reference", type=Path, default=None,
        help="Gabarito Markdown para CER/WER (ex: ...REFERENCIA.md)",
    )
    parser.add_argument(
        "--manifesto", type=Path, default=None,
        help="Manifesto JSON do corpus (ex: ...MANIFESTO.json)",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Diretório de saída para o manifesto de execução",
    )
    parser.add_argument(
        "--run-id", default=None,
        help="ID da run (default: YYYYMMDD-HHMMSS-ENGINE)",
    )
    parser.add_argument(
        "--cache-home", default=None,
        help="Diretório de cache de modelos OCR",
    )
    parser.add_argument("--quiet", action="store_true", help="Suprime progresso por página")

    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"[FAIL] PDF não encontrado: {args.pdf}", file=sys.stderr)
        return 1

    run_id = args.run_id or (
        datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.engine}"
    )

    page_range = _parse_page_range(args.pages) if args.pages else None

    ref_pages: dict[int, str] = {}
    if args.reference:
        if not args.reference.exists():
            print(f"[FAIL] Referência não encontrada: {args.reference}", file=sys.stderr)
            return 1
        ref_pages = _load_reference_pages(args.reference)
        print(f"[INFO] Referência carregada: {len(ref_pages)} páginas")

    manifesto: dict | None = None
    if args.manifesto:
        if not args.manifesto.exists():
            print(f"[WARN] Manifesto não encontrado: {args.manifesto}; continuando sem metadados de página",
                  file=sys.stderr)
        else:
            manifesto = _load_manifesto(args.manifesto)
            print(f"[INFO] Manifesto: {manifesto.get('corpus_id', '?')}  ({manifesto.get('page_count', '?')} páginas)")

    try:
        run_benchmark(
            pdf_path=args.pdf,
            engine_name=args.engine,
            language=args.language,
            render_scale=args.render_scale,
            output_dir=args.output_dir,
            run_id=run_id,
            page_range=page_range,
            ref_pages=ref_pages if ref_pages else None,
            manifesto=manifesto,
            cache_home=args.cache_home,
            verbose=not args.quiet,
        )
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
