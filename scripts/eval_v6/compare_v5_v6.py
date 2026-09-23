"""Comparação isolada v5 vs v6 — Fase 2.

Extrai o mesmo PDF (corpus/Document_AI_V2.pdf por padrão) com os dois perfis
e gera um relatório lado a lado: caracteres extraídos, tempo, diferenças por página.

Uso:
    # Ativar o venv de avaliação:
    source .venv-paddle-v6-eval/bin/activate

    # Comparação básica (usa corpus/Document_AI_V2.pdf):
    python scripts/eval_v6/compare_v5_v6.py

    # Comparação com PDF personalizado:
    python scripts/eval_v6/compare_v5_v6.py --pdf outro_documento.pdf

    # Salvar relatório JSON:
    python scripts/eval_v6/compare_v5_v6.py --output relatorio_v5_v6.json

    # Limitar número de páginas:
    python scripts/eval_v6/compare_v5_v6.py --max-pages 10

Requisitos:
    - Modelos v5 em ~/.cache/pdfextractor/paddlex/
    - Modelos v6 em ~/.cache/pdfextractor/paddlex-v6-eval/
    - Ambos os ambientes configurados via setup_v6_env.sh
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _add_src_to_path() -> None:
    src = Path(__file__).parent.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


@dataclass
class PageResult:
    page_index: int
    profile: str
    cache_home: str
    char_count: int
    word_count: int
    text_preview: str
    elapsed_ms: float
    ocr_pages: int
    native_pages: int
    status: str


@dataclass
class ComparisonReport:
    pdf_path: str
    max_pages: int
    profiles_tested: list[str]
    results: list[PageResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def extract_with_profile(
    pdf_path: Path,
    profile: str,
    cache_home: str,
    max_pages: int,
) -> list[PageResult]:
    _add_src_to_path()

    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(cache_home).expanduser().resolve())

    from structured_pdf_text import PdfTextExtractor
    from structured_pdf_text.config import ExtractorConfig, ExtractionMode

    config = ExtractorConfig(mode=ExtractionMode.BALANCED, language=profile)
    extractor = PdfTextExtractor(config)

    results: list[PageResult] = []
    t0 = time.perf_counter()
    doc = extractor.extract(pdf_path)
    total_ms = (time.perf_counter() - t0) * 1000

    pages = doc.pages[:max_pages] if max_pages else doc.pages
    per_page_ms = total_ms / max(len(pages), 1)

    for i, page in enumerate(pages):
        text = page.text or ""
        results.append(PageResult(
            page_index=i,
            profile=profile,
            cache_home=cache_home,
            char_count=len(text),
            word_count=len(text.split()),
            text_preview=text[:200].replace("\n", " "),
            elapsed_ms=per_page_ms,
            ocr_pages=getattr(doc.diagnostics, "ocr_page_count", 0),
            native_pages=getattr(doc.diagnostics, "native_page_count", 0),
            status=doc.diagnostics.status.value,
        ))

    return results


def build_summary(report: ComparisonReport) -> dict[str, Any]:
    by_profile: dict[str, dict[str, Any]] = {}
    for r in report.results:
        if r.profile not in by_profile:
            by_profile[r.profile] = {
                "total_chars": 0,
                "total_words": 0,
                "total_ms": 0.0,
                "page_count": 0,
            }
        by_profile[r.profile]["total_chars"] += r.char_count
        by_profile[r.profile]["total_words"] += r.word_count
        by_profile[r.profile]["total_ms"] += r.elapsed_ms
        by_profile[r.profile]["page_count"] += 1

    for p, d in by_profile.items():
        d["avg_ms_per_page"] = d["total_ms"] / max(d["page_count"], 1)

    # Diff chars per page between profiles
    pages_by_idx: dict[int, dict[str, int]] = {}
    for r in report.results:
        if r.page_index not in pages_by_idx:
            pages_by_idx[r.page_index] = {}
        pages_by_idx[r.page_index][r.profile] = r.char_count

    profiles = report.profiles_tested
    char_diffs: list[dict[str, Any]] = []
    if len(profiles) == 2:
        p0, p1 = profiles[0], profiles[1]
        for idx, by_prof in sorted(pages_by_idx.items()):
            c0 = by_prof.get(p0, 0)
            c1 = by_prof.get(p1, 0)
            char_diffs.append({
                "page_index": idx,
                p0 + "_chars": c0,
                p1 + "_chars": c1,
                "delta_chars": c1 - c0,
                "delta_pct": round((c1 - c0) / max(c0, 1) * 100, 1),
            })

    return {
        "by_profile": by_profile,
        "char_diffs_per_page": char_diffs,
    }


def print_report(report: ComparisonReport) -> None:
    print("\n" + "=" * 70)
    print(f"COMPARAÇÃO v5 vs v6 — {Path(report.pdf_path).name}")
    print("=" * 70)

    for profile, stats in report.summary.get("by_profile", {}).items():
        print(f"\n  Perfil: {profile}")
        print(f"    Páginas    : {stats['page_count']}")
        print(f"    Total chars: {stats['total_chars']:,}")
        print(f"    Total words: {stats['total_words']:,}")
        print(f"    Tempo médio: {stats['avg_ms_per_page']:.0f} ms/página")

    diffs = report.summary.get("char_diffs_per_page", [])
    if diffs:
        profiles = report.profiles_tested
        if len(profiles) == 2:
            p0, p1 = profiles[0], profiles[1]
            print(f"\n  Delta caracteres por página ({p0} → {p1}):")
            print(f"  {'Pág':>4}  {p0:>20}  {p1:>20}  {'Δ chars':>10}  {'Δ %':>8}")
            for d in diffs:
                flag = "⚠" if abs(d["delta_pct"]) > 10 else " "
                print(
                    f"  {d['page_index']:>4}  "
                    f"{d[p0 + '_chars']:>20,}  "
                    f"{d[p1 + '_chars']:>20,}  "
                    f"{d['delta_chars']:>+10,}  "
                    f"{d['delta_pct']:>+7.1f}%  {flag}"
                )

    print("\n  Legenda: ⚠ = diferença > 10% em chars (investigar)")
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser(description="Comparação v5 vs v6 em PDF real")
    ap.add_argument(
        "--pdf",
        type=Path,
        default=Path("corpus/Document_AI_V2.pdf"),
        help="PDF para comparação (padrão: corpus/Document_AI_V2.pdf)",
    )
    ap.add_argument(
        "--v5-cache",
        default=str(Path.home() / ".cache/pdfextractor/paddlex"),
        help="Cache de modelos v5 (produção)",
    )
    ap.add_argument(
        "--v6-cache",
        default=str(Path.home() / ".cache/pdfextractor/paddlex-v6-eval"),
        help="Cache de modelos v6 (avaliação)",
    )
    ap.add_argument(
        "--v6-profile",
        default="pt-v6-medium",
        choices=["pt-v6-medium", "pt-v6-small"],
        help="Perfil v6 a comparar (padrão: pt-v6-medium)",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Máximo de páginas a processar (0 = todas)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Salvar relatório JSON neste caminho",
    )
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"[FAIL] PDF não encontrado: {args.pdf}")
        print("       Use --pdf para especificar um caminho válido.")
        return 1

    profiles_to_test = [
        ("pt", args.v5_cache),
        (args.v6_profile, args.v6_cache),
    ]

    report = ComparisonReport(
        pdf_path=str(args.pdf.resolve()),
        max_pages=args.max_pages,
        profiles_tested=[p for p, _ in profiles_to_test],
    )

    for profile, cache in profiles_to_test:
        print(f"\n[...] Extraindo com perfil '{profile}' (cache: {cache})...")
        try:
            results = extract_with_profile(args.pdf, profile, cache, args.max_pages)
            report.results.extend(results)
            total_chars = sum(r.char_count for r in results)
            print(f"[OK] {len(results)} páginas, {total_chars:,} chars total.")
        except Exception as exc:
            print(f"[FAIL] Erro com perfil '{profile}': {type(exc).__name__}: {exc}")
            return 1

    report.summary = build_summary(report)
    print_report(report)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as fh:
            json.dump(asdict(report), fh, ensure_ascii=False, indent=2)
        print(f"\n[OK] Relatório JSON salvo em: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
