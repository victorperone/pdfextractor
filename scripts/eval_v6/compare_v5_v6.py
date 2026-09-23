"""Comparação prática v5 vs v6 — Fase 2.

Extrai o mesmo PDF com perfil v5 e com o perfil v6 escolhido via CLI,
salva os dois markdowns e exibe as diferenças de conteúdo.

Uso:
    python scripts/eval_v6/compare_v5_v6.py

    python scripts/eval_v6/compare_v5_v6.py corpus/Document_AI_V2.pdf

    python scripts/eval_v6/compare_v5_v6.py --profile pt-v6-small

    python scripts/eval_v6/compare_v5_v6.py \\
        --v6-cache ~/.cache/pdfextractor/paddlex-v6-eval

    # Windows:
    python scripts\\eval_v6\\compare_v5_v6.py `
        --v6-cache "$env:USERPROFILE\\.cache\\pdfextractor\\paddlex-v6-eval"

Saída: output/compare_v5_v6/  (ou --output-dir)
"""
from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
import time
from pathlib import Path


def run_extract(
    pdf: Path,
    profile: str,
    cache_home: str,
    out_file: Path,
) -> float:
    cmd = [
        sys.executable, "-m", "structured_pdf_text.cli",
        "extract",
        str(pdf),
        "--output", "markdown",
        "--output-file", str(out_file),
        "--mode", "balanced",
        "--ocr-model-profile", profile,
        "--cache-home", cache_home,
    ]
    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0

    if result.returncode != 0:
        print(f"  [FAIL] perfil '{profile}'")
        print(result.stderr[-1000:] if result.stderr else "(sem stderr)")
        raise SystemExit(1)

    size = out_file.stat().st_size if out_file.exists() else 0
    print(f"  [{profile}] {size:,} bytes | {elapsed:.1f}s → {out_file.name}")
    return elapsed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", default="corpus/Document_AI_V2.pdf", type=Path)
    ap.add_argument("--profile", default="pt-v6-medium", choices=["pt-v6-medium", "pt-v6-small"])
    ap.add_argument("--v5-cache", default=str(Path.home() / ".cache/pdfextractor/paddlex"))
    ap.add_argument("--v6-cache", default=str(Path.home() / ".cache/pdfextractor/paddlex-v6-eval"))
    ap.add_argument("--output-dir", type=Path, default=Path("output/compare_v5_v6"))
    args = ap.parse_args()

    pdf = args.pdf if args.pdf.is_absolute() else Path.cwd() / args.pdf
    if not pdf.exists():
        print(f"PDF não encontrado: {pdf}")
        return 1

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    out_v5 = out / "v5.md"
    out_v6 = out / f"v6_{args.profile}.md"

    print(f"PDF   : {pdf}")
    print(f"Saída : {out.resolve()}")
    print()

    print("Extraindo com v5 (pt)...")
    run_extract(pdf, "pt", args.v5_cache, out_v5)

    print(f"Extraindo com v6 ({args.profile})...")
    run_extract(pdf, args.profile, args.v6_cache, out_v6)

    # Diff
    lines_v5 = out_v5.read_text(encoding="utf-8").splitlines()
    lines_v6 = out_v6.read_text(encoding="utf-8").splitlines()

    diff = list(difflib.unified_diff(
        lines_v5, lines_v6,
        fromfile=f"v5  ({len(lines_v5)} linhas)",
        tofile=f"{args.profile}  ({len(lines_v6)} linhas)",
        lineterm="", n=3,
    ))

    print()
    if not diff:
        print("[=] Saída idêntica — nenhuma diferença de conteúdo.")
    else:
        diff_path = out / "diff.txt"
        diff_path.write_text("\n".join(diff), encoding="utf-8")
        added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        print(f"[≠] +{added} / -{removed} linhas  →  diff salvo em: {diff_path}")
        print()
        for line in diff[:100]:
            print(line)
        if len(diff) > 100:
            print(f"\n... ({len(diff) - 100} linhas omitidas — abra {diff_path})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
