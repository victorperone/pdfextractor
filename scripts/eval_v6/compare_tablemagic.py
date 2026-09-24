"""Controlled PP-TableMagic comparison: tm-v5 versus tm-v6 across full PDF pages (Desenho A).

Each arm runs TableRecognitionPipelineV2 with its own internal OCR pair:
  tm-v5: PP-OCRv5_server_det + latin_PP-OCRv5_mobile_rec
  tm-v6: PP-OCRv6_medium_det + PP-OCRv6_medium_rec

All other table components (structure, cells, classifier) are fixed between arms
and must be present in each cache directory.

Required cache layout:
  v5-cache/official_models/{PP-OCRv5 OCR models} + {common table models}
  v6-cache/official_models/{PP-OCRv6 OCR models} + {common table models}

Use --check-only to verify the inventory without running inference.

Usage:
    python scripts/eval_v6/compare_tablemagic.py corpus/Document_AI_V2.pdf \\
        --v5-cache ~/.cache/pdfextractor/paddlex \\
        --v6-cache ~/.cache/pdfextractor/paddlex-v6-eval \\
        --pages 3,7,12 \\
        --output-dir output/compare_tablemagic
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TM_PROFILES = ("tm-v5", "tm-v6")

# Import shared utilities from eval_tablemagic (same directory)
_EVAL_DIR = Path(__file__).parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))
from eval_tablemagic import (  # noqa: E402
    TABLE_PROFILES,
    COMMON_TABLE_MODELS,
    model_names,
    model_inventory,
    validate_model_inventory,
    pipeline_kwargs,
    sha256_file,
    package_versions,
    _result_json,
    _save_result_artifacts,
    _loaded_model_names,
)


def git_identity() -> dict[str, str | None]:
    import subprocess

    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    return {
        "branch": run("branch", "--show-current"),
        "sha": run("rev-parse", "HEAD"),
        "status": run("status", "--short"),
    }


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = (int(item.strip()) for item in part.split("-", 1))
            if left < 1 or right < left:
                raise ValueError("page ranges must be positive and ascending")
            pages.extend(range(left, right + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError("pages are human-facing and 1-based")
            pages.append(page)
    if not pages:
        raise ValueError("at least one page is required")
    return sorted(set(pages))


def _count_pdf_pages(pdf_path: Path) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    n = len(doc)
    doc.close()
    return n


def _rasterize_page(pdf_path: Path, page_number: int, scale: float = 2.0):
    """Rasterize one PDF page (1-based) and return a PIL Image (RGB)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_number - 1]
        bitmap = page.render(scale=scale, rotation=0)
        return bitmap.to_pil().convert("RGB")
    finally:
        doc.close()


def _check_pipeline_signature(cls: type) -> None:
    required = {
        "table_classification_model_dir",
        "wired_table_structure_recognition_model_dir",
        "wireless_table_structure_recognition_model_dir",
        "wired_table_cells_detection_model_dir",
        "wireless_table_cells_detection_model_dir",
        "text_detection_model_dir",
        "text_recognition_model_dir",
    }
    sig = inspect.signature(cls)
    missing = required - set(sig.parameters)
    if missing:
        raise RuntimeError(
            "installed TableRecognitionPipelineV2 cannot express the explicit "
            f"configuration: {sorted(missing)}"
        )


def run_profile(
    pdf: Path,
    page_list: list[int],
    cache: Path,
    profile: str,
    out_dir: Path,
    scale: float,
) -> dict[str, Any]:
    """Process all pages for one TableMagic profile.

    The pipeline is initialized once and reused across pages.
    """
    from paddleocr import TableRecognitionPipelineV2

    inventory = model_inventory(cache, profile)
    validate_model_inventory(inventory)
    _check_pipeline_signature(TableRecognitionPipelineV2)

    kwargs = pipeline_kwargs(cache, profile)
    t0 = time.perf_counter()
    pipeline = TableRecognitionPipelineV2(**kwargs)
    init_ms = round((time.perf_counter() - t0) * 1000, 1)
    loaded_models = _loaded_model_names(pipeline)
    print(f"  [{profile}] pronto em {init_ms:.0f}ms | modelos observados: {loaded_models}")

    page_results: list[dict[str, Any]] = []
    for page_num in page_list:
        t_page = time.perf_counter()

        page_dir = out_dir / f"page_{page_num:04d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        img_path = page_dir / "input.png"
        _rasterize_page(pdf, page_num, scale).save(img_path)

        try:
            output = list(
                pipeline.predict(
                    str(img_path),
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_layout_detection=False,
                    use_ocr_model=True,
                    use_table_orientation_classify=False,
                )
            )
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - t_page) * 1000, 1)
            page_results.append({
                "page": page_num,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "table_count": 0,
                "tables": [],
                "elapsed_ms": elapsed_ms,
            })
            print(f"  [{profile}] página {page_num}: ERRO — {type(exc).__name__}: {exc}")
            continue

        tables: list[dict[str, Any]] = []
        for item_index, item in enumerate(output):
            raw = _result_json(item)
            artifact_dir = page_dir / f"result_{item_index:03d}"
            _save_result_artifacts(item, raw, artifact_dir)
            for table in raw.get("table_res_list", []):
                if not isinstance(table, dict):
                    continue
                scores = table.get("table_ocr_pred", {}).get("rec_scores", [])
                tables.append({
                    "pred_html": table.get("pred_html", ""),
                    "cell_box_list": table.get("cell_box_list", []),
                    "cell_count": len(table.get("cell_box_list", [])),
                    "text_count": len(table.get("table_ocr_pred", {}).get("rec_texts", [])),
                    "avg_score": round(sum(scores) / max(len(scores), 1), 4),
                })

        elapsed_ms = round((time.perf_counter() - t_page) * 1000, 1)
        page_result = {
            "page": page_num,
            "status": "success",
            "table_count": len(tables),
            "tables": tables,
            "elapsed_ms": elapsed_ms,
        }
        page_results.append(page_result)
        (page_dir / "page_result.json").write_text(
            json.dumps(page_result, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"  [{profile}] página {page_num}: {len(tables)} tabela(s) | {elapsed_ms:.0f}ms")

    return {
        "profile": profile,
        "model_inventory": inventory,
        "loaded_model_names_observed": loaded_models,
        "init_ms": init_ms,
        "page_results": page_results,
        "total_tables": sum(r.get("table_count", 0) for r in page_results),
        "error_count": sum(1 for r in page_results if r.get("status") == "error"),
    }


def _compare_pages(
    v5_pages: list[dict[str, Any]],
    v6_pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_v5 = {r["page"]: r for r in v5_pages}
    by_v6 = {r["page"]: r for r in v6_pages}
    comparison = []
    for page in sorted(set(by_v5) | set(by_v6)):
        r5 = by_v5.get(page)
        r6 = by_v6.get(page)
        c5 = r5.get("table_count", 0) if r5 else None
        c6 = r6.get("table_count", 0) if r6 else None
        comparison.append({
            "page": page,
            "tm_v5_status": r5["status"] if r5 else "not_run",
            "tm_v6_status": r6["status"] if r6 else "not_run",
            "tm_v5_tables": c5,
            "tm_v6_tables": c6,
            "table_count_delta": (c6 - c5) if c5 is not None and c6 is not None else None,
            "tm_v5_elapsed_ms": r5.get("elapsed_ms") if r5 else None,
            "tm_v6_elapsed_ms": r6.get("elapsed_ms") if r6 else None,
        })
    return comparison


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _print_inventory(profile: str, cache: Path) -> bool:
    inventory = model_inventory(cache, profile)
    all_ok = True
    print(f"=== Inventário: {profile}  (cache: {cache}) ===")
    for item in inventory:
        ok = item["exists"] and item.get("has_weight") and item.get("has_metadata")
        tag = "OK  " if ok else "MISS"
        print(f"  [{tag}] {item['role']:35s} {item['name']}")
        if not item["exists"]:
            print(f"           ausente: {item['directory']}")
            all_ok = False
        elif not item.get("has_weight"):
            print(f"           sem arquivo de pesos em: {item['directory']}")
            all_ok = False
    print()
    return all_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="PDF a avaliar; todas as páginas por padrão")
    parser.add_argument("--v5-cache", type=Path, default=Path.home() / ".cache/pdfextractor/paddlex")
    parser.add_argument("--v6-cache", type=Path, default=Path.home() / ".cache/pdfextractor/paddlex-v6-eval")
    parser.add_argument("--pages", help="Páginas humanas 1-based, ex: 1,3-5,7")
    parser.add_argument("--output-dir", type=Path, default=Path("output/compare_tablemagic"))
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verificar inventário de modelos sem executar inferência",
    )
    args = parser.parse_args(argv)

    pdf = args.pdf.expanduser().resolve()
    if not pdf.is_file():
        print(f"[FAIL] PDF não encontrado: {pdf}", file=sys.stderr)
        return 1

    total_pages = _count_pdf_pages(pdf)
    try:
        pages = parse_pages(args.pages) if args.pages else list(range(1, total_pages + 1))
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    invalid = [p for p in pages if p < 1 or p > total_pages]
    if invalid:
        parser.error(f"Páginas fora do intervalo (PDF tem {total_pages} páginas): {invalid}")

    cache_by_profile: dict[str, Path] = {
        "tm-v5": args.v5_cache.expanduser().resolve(),
        "tm-v6": args.v6_cache.expanduser().resolve(),
    }

    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

    print(f"PDF    : {pdf} ({total_pages} páginas)")
    print(f"Páginas: {pages}")
    print()

    # Inventory check for both profiles
    all_ok = True
    for profile in TM_PROFILES:
        ok = _print_inventory(profile, cache_by_profile[profile])
        all_ok = all_ok and ok

    if not all_ok:
        print(
            "[FAIL] Um ou mais modelos estão ausentes. Baixe-os antes de executar.\n"
            "       Use eval_tablemagic.py para verificar o setup individual de cada perfil.",
            file=sys.stderr,
        )
        return 1

    if args.check_only:
        print("[OK] Todos os modelos encontrados. Execute sem --check-only para rodar a inferência.")
        return 0

    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] = {
        "schema": "structured-pdf-text.tablemagic-comparison.v1",
        "design": "A",
        "pdf": str(pdf),
        "pdf_sha256": sha256_file(pdf),
        "total_pdf_pages": total_pages,
        "pages_human_1_based": pages,
        "git": git_identity(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": sys.version,
        "packages": package_versions(),
        "render_scale": args.scale,
        "pipeline_flags": {
            "use_ocr_model": True,
            "use_ocr_results_with_table_cells": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_layout_detection": False,
            "use_table_orientation_classify": False,
        },
        "results": {},
        "comparison": [],
    }

    failures = 0
    for profile in TM_PROFILES:
        cache = cache_by_profile[profile]
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)
        print(f"=== Executando: {profile} ===")
        profile_out = out / profile
        profile_out.mkdir(parents=True, exist_ok=True)
        try:
            result = run_profile(pdf, pages, cache, profile, profile_out, args.scale)
        except Exception as exc:
            print(f"[FAIL] {profile}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
            metadata["results"][profile] = {"status": "failed", "error": str(exc)}
            continue

        metadata["results"][profile] = result
        n_err = result["error_count"]
        label = "OK" if n_err == 0 else f"PARCIAL ({n_err} erro(s))"
        print(f"[{label}] {profile}: {result['total_tables']} tabelas em {len(pages)} página(s)\n")

    # Build paired comparison
    r = metadata["results"]
    if (
        "tm-v5" in r and "page_results" in r.get("tm-v5", {})
        and "tm-v6" in r and "page_results" in r.get("tm-v6", {})
    ):
        metadata["comparison"] = _compare_pages(
            r["tm-v5"]["page_results"],
            r["tm-v6"]["page_results"],
        )

    manifest_path = out / "evaluation.json"
    _write_json(manifest_path, metadata)
    print(f"[INFO] Manifesto: {manifest_path}")

    if failures:
        print(f"[FAIL] {failures} de {len(TM_PROFILES)} perfis falharam", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
