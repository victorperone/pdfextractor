"""Comparação prática v5 vs v6 — Fase 2.

Extrai o mesmo PDF com perfil v5 (padrão) e com o perfil v6 escolhido,
salva os dois textos em arquivos e imprime as diferenças de conteúdo.

Uso:
    python scripts/eval_v6/compare_v5_v6.py [pdf] [--v6-cache caminho]

    # Padrão — usa corpus/Document_AI_V2.pdf:
    python scripts/eval_v6/compare_v5_v6.py

    # Outro documento:
    python scripts/eval_v6/compare_v5_v6.py meu_documento.pdf

    # Perfil small em vez de medium:
    python scripts/eval_v6/compare_v5_v6.py --profile pt-v6-small

    # Cache v6 em caminho diferente do padrão:
    python scripts/eval_v6/compare_v5_v6.py \\
        --v6-cache C:/Users/victor/.cache/pdfextractor/paddlex-v6-eval

Os arquivos de saída ficam em /tmp/compare_v5_v6/ (ou --output-dir).
"""
from __future__ import annotations

import argparse
import difflib
import os
import sys
import time
from pathlib import Path


def _add_src() -> None:
    src = Path(__file__).parent.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def extract(pdf: Path, profile: str, cache_home: str) -> str:
    _add_src()
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(cache_home).expanduser().resolve())

    from structured_pdf_text import PdfTextExtractor
    from structured_pdf_text.config import ExtractorConfig, ExtractionMode

    config = ExtractorConfig(mode=ExtractionMode.BALANCED, language=profile)
    t0 = time.perf_counter()
    doc = PdfTextExtractor(config).extract(pdf)
    elapsed = time.perf_counter() - t0

    pages = doc.pages
    text = "\n\n--- Página {} ---\n\n".join(
        p.text or "" for p in pages
    )
    print(
        f"  [{profile}] {len(pages)} páginas | "
        f"{len(text):,} chars | {elapsed:.1f}s"
    )
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default="corpus/Document_AI_V2.pdf", type=Path)
    ap.add_argument(
        "--profile",
        default="pt-v6-medium",
        choices=["pt-v6-medium", "pt-v6-small"],
    )
    ap.add_argument(
        "--v5-cache",
        default=str(Path.home() / ".cache/pdfextractor/paddlex"),
    )
    ap.add_argument(
        "--v6-cache",
        default=str(Path.home() / ".cache/pdfextractor/paddlex-v6-eval"),
    )
    ap.add_argument("--output-dir", type=Path, default=Path("/tmp/compare_v5_v6"))
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"PDF não encontrado: {args.pdf}")
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"PDF: {args.pdf}")
    print(f"Saída: {args.output_dir}")
    print()

    print("Extraindo com v5 (pt)...")
    text_v5 = extract(args.pdf, "pt", args.v5_cache)

    print(f"Extraindo com v6 ({args.profile})...")
    text_v6 = extract(args.pdf, args.profile, args.v6_cache)

    out_v5 = args.output_dir / "v5.txt"
    out_v6 = args.output_dir / f"v6_{args.profile}.txt"
    out_v5.write_text(text_v5, encoding="utf-8")
    out_v6.write_text(text_v6, encoding="utf-8")

    print(f"\nSalvo: {out_v5}")
    print(f"Salvo: {out_v6}")

    lines_v5 = text_v5.splitlines()
    lines_v6 = text_v6.splitlines()
    diff = list(difflib.unified_diff(
        lines_v5, lines_v6,
        fromfile="v5", tofile=args.profile,
        lineterm="", n=2,
    ))

    if not diff:
        print("\n[=] Saída idêntica — nenhuma diferença de conteúdo.")
    else:
        diff_path = args.output_dir / "diff.txt"
        diff_path.write_text("\n".join(diff), encoding="utf-8")
        changed = sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
        print(f"\n[≠] {changed} linhas diferentes — diff salvo em: {diff_path}")
        # Mostrar primeiros 40 chunks para visão rápida
        for line in diff[:80]:
            print(line)
        if len(diff) > 80:
            print(f"... ({len(diff) - 80} linhas omitidas — ver diff.txt)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
