"""Hybrid 3-level evaluation: native text → OCR → PP-TableMagic.

For each page and each profile (v5, v6):
  1. Extract native PDF text layer (pypdfium2).
       if char_count >= min_native_chars → use native text, skip OCR.
  2. Run PaddleOCR (v5 or v6 models) → get rec_texts + avg_confidence.
       if avg_confidence >= confidence_threshold → use OCR output.
  3. If avg_confidence < confidence_threshold AND text was detected:
       activate TableRecognitionPipelineV2 (use_layout_detection=True).
       if tables detected  → use TableMagic text output.
       if no tables found  → keep OCR output, record 'ocr_tablemagic_no_table'.
  4. Record everything in a single evaluation.json (no per-page directories or images).

Required models per cache directory:
  v5 OCR  : PP-OCRv5_server_det + latin_PP-OCRv5_mobile_rec
  v6 OCR  : PP-OCRv6_medium_det + PP-OCRv6_medium_rec
  Table   : PP-LCNet_x1_0_table_cls, SLANeXt_wired/wireless,
            RT-DETR-L_wired/wireless_table_cell_det
  Layout  : PP-DocLayout_plus-L  (same cache, or override with --layout-model-dir)

First-run download (requires internet once per cache):
    python scripts/eval_v6/compare_hybrid.py corpus/doc.pdf --check-only
    # MISS for PP-DocLayout_plus-L → run without DISABLE check to auto-download:
    python scripts/eval_v6/compare_hybrid.py corpus/doc.pdf --check-only --allow-download

Subsequent runs (100% offline):
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True python scripts/eval_v6/compare_hybrid.py ...

Usage:
    python scripts/eval_v6/compare_hybrid.py corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf \\
        --v5-cache ~/.cache/pdfextractor/paddlex \\
        --v6-cache ~/.cache/pdfextractor/paddlex-v6-eval \\
        --confidence-threshold 0.70 \\
        --output-dir output/compare_hybrid
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HYBRID_PROFILES = ("v5", "v6")
LAYOUT_MODEL = "PP-DocLayout_plus-L"
DEFAULT_THRESHOLD = 0.85

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
    _loaded_model_names,
)

# Maps user-facing profile name to the tm-* key used by eval_tablemagic helpers
_TM_PROFILE = {"v5": "tm-v5", "v6": "tm-v6"}


# ---------------------------------------------------------------------------
# Helpers shared with compare_tablemagic / compare_v5_v6
# ---------------------------------------------------------------------------

def git_identity() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    return {"branch": run("branch", "--show-current"), "sha": run("rev-parse", "HEAD")}


def _count_pdf_pages(pdf_path: Path) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    n = len(doc)
    doc.close()
    return n


def _rasterize_page(pdf_path: Path, page_number: int, scale: float = 2.0):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_number - 1]
        bitmap = page.render(scale=scale, rotation=0)
        return bitmap.to_pil().convert("RGB")
    finally:
        doc.close()


def _extract_native_text(pdf_path: Path, page_number: int) -> tuple[str, int]:
    """Extract native PDF text layer. Returns (text, char_count)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_number - 1]
        textpage = page.get_textpage()
        text = textpage.get_text_bounded()
        textpage.close()
        text = text.strip()
        return text, len(text)
    finally:
        doc.close()


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = (int(x) for x in part.split("-", 1))
            if left < 1 or right < left:
                raise ValueError("page ranges must be positive and ascending")
            pages.extend(range(left, right + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError("pages are 1-based")
            pages.append(page)
    if not pages:
        raise ValueError("at least one page required")
    return sorted(set(pages))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Layout model inventory
# ---------------------------------------------------------------------------

def _layout_inventory(cache: Path, layout_dir: Path | None) -> dict[str, Any]:
    directory = layout_dir if layout_dir else (cache / "official_models" / LAYOUT_MODEL)
    files = list(directory.rglob("*")) if directory.is_dir() else []
    weights = [
        p for p in files
        if p.suffix.lower() in {".pdiparams", ".pdparams", ".onnx", ".bin", ".nb", ".pt"}
        or p.name.endswith(".pdiparams.info")
    ]
    meta = [p for p in files if p.suffix.lower() in {".json", ".yml", ".yaml"}]
    return {
        "name": LAYOUT_MODEL,
        "role": "layout_detection",
        "directory": str(directory),
        "exists": directory.is_dir(),
        "has_weight": bool(weights),
        "has_metadata": bool(meta),
    }


def _print_inventory(profile: str, cache: Path, layout_dir: Path | None) -> bool:
    tm_profile = _TM_PROFILE[profile]
    inventory = model_inventory(cache, tm_profile)
    layout_inv = _layout_inventory(cache, layout_dir)
    all_ok = True
    print(f"=== Inventário: {profile}  (cache: {cache}) ===")
    for item in inventory:
        ok = item["exists"] and item.get("has_weight") and item.get("has_metadata")
        tag = "OK  " if ok else "MISS"
        print(f"  [{tag}] {item['role']:40s} {item['name']}")
        if not ok:
            all_ok = False
    ok = layout_inv["exists"] and layout_inv.get("has_weight") and layout_inv.get("has_metadata")
    tag = "OK  " if ok else "MISS"
    print(f"  [{tag}] {'layout_detection':40s} {layout_inv['name']}")
    if not ok:
        print(
            f"         Para baixar: execute sem --check-only e sem "
            f"PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True (auto-download na primeira vez).\n"
            f"         Diretório esperado: {layout_inv['directory']}"
        )
        all_ok = False
    print()
    return all_ok


# ---------------------------------------------------------------------------
# OCR phase
# ---------------------------------------------------------------------------

def _build_ocr_pipeline(cache: Path, profile: str) -> Any:
    from paddleocr import PaddleOCR
    tm_profile = _TM_PROFILE[profile]
    names = model_names(tm_profile)
    root = cache / "official_models"
    return PaddleOCR(
        text_detection_model_name=names["text_detection"],
        text_detection_model_dir=str(root / names["text_detection"]),
        text_recognition_model_name=names["text_recognition"],
        text_recognition_model_dir=str(root / names["text_recognition"]),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        enable_mkldnn=False,
    )


def _ocr_page(img_path: Path, ocr_pipeline: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        results = list(ocr_pipeline.predict(str(img_path)))
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "rec_texts": [],
            "rec_scores": [],
            "avg_confidence": None,
            "text_count": 0,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    all_texts: list[str] = []
    all_scores: list[float] = []
    for res in results:
        raw = _result_json(res)
        all_texts.extend(raw.get("rec_texts") or [])
        all_scores.extend(float(s) for s in (raw.get("rec_scores") or []))

    avg_conf = round(sum(all_scores) / len(all_scores), 4) if all_scores else None
    return {
        "status": "success",
        "rec_texts": all_texts,
        "rec_scores": [round(s, 4) for s in all_scores],
        "avg_confidence": avg_conf,
        "text_count": len(all_texts),
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# TableMagic phase (lazy init)
# ---------------------------------------------------------------------------

def _build_tm_pipeline(cache: Path, profile: str, layout_dir: Path | None) -> Any:
    from paddleocr import TableRecognitionPipelineV2
    tm_profile = _TM_PROFILE[profile]
    kwargs = pipeline_kwargs(cache, tm_profile)
    # Override: enable layout detection (Opção B)
    kwargs["use_layout_detection"] = True
    root = cache / "official_models"
    ld = layout_dir if layout_dir else (root / LAYOUT_MODEL)
    kwargs["layout_detection_model_name"] = LAYOUT_MODEL
    kwargs["layout_detection_model_dir"] = str(ld)
    return TableRecognitionPipelineV2(**kwargs)


def _tablemagic_page(img_path: Path, tm_pipeline: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        output = list(tm_pipeline.predict(
            str(img_path),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_layout_detection=True,
            use_ocr_model=True,
            use_table_orientation_classify=False,
        ))
    except Exception as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "table_count": 0,
            "tables": [],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    tables: list[dict[str, Any]] = []
    for item in output:
        raw = _result_json(item)
        for table in raw.get("table_res_list", []):
            if not isinstance(table, dict):
                continue
            scores = table.get("table_ocr_pred", {}).get("rec_scores", [])
            texts = table.get("table_ocr_pred", {}).get("rec_texts", [])
            tables.append({
                "pred_html": table.get("pred_html", ""),
                "cell_count": len(table.get("cell_box_list", [])),
                "rec_texts": texts,
                "rec_scores": [round(float(s), 4) for s in scores],
                "avg_score": round(sum(scores) / max(len(scores), 1), 4),
            })

    return {
        "status": "success",
        "table_count": len(tables),
        "tables": tables,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


# ---------------------------------------------------------------------------
# Per-profile runner
# ---------------------------------------------------------------------------

def run_profile(
    pdf: Path,
    page_list: list[int],
    cache: Path,
    profile: str,
    threshold: float,
    layout_dir: Path | None,
    scale: float,
    min_native_chars: int,
) -> dict[str, Any]:
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache)

    print(f"=== Executando: {profile} ===")

    # OCR and TableMagic pipelines are lazily initialized
    ocr_pipeline: Any = None
    ocr_init_ms: float | None = None
    tm_pipeline: Any = None
    tm_init_ms: float | None = None

    def _get_ocr() -> Any:
        nonlocal ocr_pipeline, ocr_init_ms
        if ocr_pipeline is None:
            t = time.perf_counter()
            ocr_pipeline = _build_ocr_pipeline(cache, profile)
            ocr_init_ms = round((time.perf_counter() - t) * 1000, 1)
            print(f"  [{profile}] PaddleOCR pronto em {ocr_init_ms:.0f}ms")
        return ocr_pipeline

    def _get_tm() -> Any:
        nonlocal tm_pipeline, tm_init_ms
        if tm_pipeline is None:
            t = time.perf_counter()
            tm_pipeline = _build_tm_pipeline(cache, profile, layout_dir)
            tm_init_ms = round((time.perf_counter() - t) * 1000, 1)
            print(f"  [{profile}] TableMagic pronto em {tm_init_ms:.0f}ms")
        return tm_pipeline

    page_results: list[dict[str, Any]] = []
    tmpdir = Path(tempfile.mkdtemp(prefix=f"hybrid_{profile}_"))

    try:
        for page_num in page_list:
            t_page = time.perf_counter()

            # --- Nível 1: texto nativo ---
            native_text, native_chars = _extract_native_text(pdf, page_num)
            if native_chars >= min_native_chars:
                elapsed = round((time.perf_counter() - t_page) * 1000, 1)
                page_results.append({
                    "page": page_num,
                    "native_chars": native_chars,
                    "ocr": None,
                    "tablemagic": None,
                    "mode": "native",
                    "confidence_trigger": False,
                    "final_texts": [native_text],
                    "final_avg_confidence": None,
                    "total_elapsed_ms": elapsed,
                })
                print(f"  [{profile}] pág {page_num:3d}: native={native_chars} chars  mode=native")
                continue

            # --- Nível 2: OCR ---
            img_path = tmpdir / f"p{page_num:04d}.png"
            _rasterize_page(pdf, page_num, scale).save(img_path)

            ocr = _ocr_page(img_path, _get_ocr())

            conf = ocr.get("avg_confidence")
            trigger = (
                ocr["status"] == "success"
                and conf is not None
                and conf < threshold
                and ocr["text_count"] > 0
            )

            record: dict[str, Any] = {
                "page": page_num,
                "native_chars": native_chars,
                "ocr": {
                    "avg_confidence": conf,
                    "text_count": ocr["text_count"],
                    "rec_texts": ocr["rec_texts"],
                    "elapsed_ms": ocr["elapsed_ms"],
                    "status": ocr["status"],
                },
                "tablemagic": None,
                "mode": "ocr",
                "confidence_trigger": trigger,
                "final_texts": ocr["rec_texts"],
                "final_avg_confidence": conf,
                "total_elapsed_ms": ocr["elapsed_ms"],
            }

            # --- Nível 3: TableMagic ---
            if trigger:
                tm = _tablemagic_page(img_path, _get_tm())
                record["tablemagic"] = {
                    "table_count": tm["table_count"],
                    "elapsed_ms": tm["elapsed_ms"],
                    "status": tm["status"],
                }
                record["total_elapsed_ms"] = round(
                    ocr["elapsed_ms"] + tm["elapsed_ms"], 1
                )

                if tm["status"] == "success" and tm["table_count"] > 0:
                    record["mode"] = "tablemagic"
                    all_tm_texts = [
                        text
                        for tbl in tm["tables"]
                        for text in tbl.get("rec_texts", [])
                    ]
                    all_tm_scores = [
                        s
                        for tbl in tm["tables"]
                        for s in tbl.get("rec_scores", [])
                    ]
                    record["final_texts"] = all_tm_texts
                    record["final_avg_confidence"] = (
                        round(sum(all_tm_scores) / len(all_tm_scores), 4)
                        if all_tm_scores else None
                    )
                else:
                    record["mode"] = "ocr_tablemagic_no_table"

            img_path.unlink(missing_ok=True)
            page_results.append(record)

            conf_str = f"{conf:.3f}" if conf is not None else " n/a "
            print(
                f"  [{profile}] pág {page_num:3d}: native={native_chars:3d}  "
                f"conf={conf_str}  trigger={'YES' if trigger else ' no'}  mode={record['mode']}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    native_pages = [r["page"] for r in page_results if r["mode"] == "native"]
    ocr_pages    = [r["page"] for r in page_results if r["mode"] == "ocr"]
    tm_pages     = [r["page"] for r in page_results if r["mode"] == "tablemagic"]
    no_tbl       = [r["page"] for r in page_results if r["mode"] == "ocr_tablemagic_no_table"]
    errors       = [r["page"] for r in page_results if (r.get("ocr") or {}).get("status") == "error"]

    label = "OK" if not errors else f"PARCIAL ({len(errors)} erro(s))"
    print(
        f"[{label}] {profile}: "
        f"native={len(native_pages)}  ocr={len(ocr_pages)}  "
        f"tablemagic={len(tm_pages)}  no_table={len(no_tbl)}  errors={len(errors)}\n"
    )

    return {
        "profile": profile,
        "ocr_init_ms": ocr_init_ms,
        "tm_init_ms": tm_init_ms,
        "confidence_threshold": threshold,
        "min_native_chars": min_native_chars,
        "pages_total": len(page_list),
        "pages_native": len(native_pages),
        "pages_ocr_only": len(ocr_pages),
        "pages_tablemagic_activated": len(tm_pages),
        "pages_tablemagic_no_table": len(no_tbl),
        "pages_error": len(errors),
        "tablemagic_page_numbers": tm_pages,
        "page_results": page_results,
    }


# ---------------------------------------------------------------------------
# Comparison builder
# ---------------------------------------------------------------------------

def _compare_pages(
    v5_result: dict[str, Any],
    v6_result: dict[str, Any],
) -> list[dict[str, Any]]:
    by_v5 = {r["page"]: r for r in v5_result["page_results"]}
    by_v6 = {r["page"]: r for r in v6_result["page_results"]}
    comparison = []
    for page in sorted(set(by_v5) | set(by_v6)):
        r5 = by_v5.get(page)
        r6 = by_v6.get(page)
        comparison.append({
            "page": page,
            "native_chars": r5.get("native_chars") if r5 else (r6.get("native_chars") if r6 else None),
            "v5_mode": r5["mode"] if r5 else "not_run",
            "v6_mode": r6["mode"] if r6 else "not_run",
            "v5_ocr_confidence": r5["ocr"]["avg_confidence"] if r5 and r5.get("ocr") else None,
            "v6_ocr_confidence": r6["ocr"]["avg_confidence"] if r6 and r6.get("ocr") else None,
            "v5_final_confidence": r5["final_avg_confidence"] if r5 else None,
            "v6_final_confidence": r6["final_avg_confidence"] if r6 else None,
            "v5_text_count": r5["ocr"]["text_count"] if r5 and r5.get("ocr") else None,
            "v6_text_count": r6["ocr"]["text_count"] if r6 and r6.get("ocr") else None,
            "v5_elapsed_ms": r5["total_elapsed_ms"] if r5 else None,
            "v6_elapsed_ms": r6["total_elapsed_ms"] if r6 else None,
        })
    return comparison


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="PDF a avaliar")
    parser.add_argument("--v5-cache", type=Path,
                        default=Path.home() / ".cache/pdfextractor/paddlex")
    parser.add_argument("--v6-cache", type=Path,
                        default=Path.home() / ".cache/pdfextractor/paddlex-v6-eval")
    parser.add_argument(
        "--layout-model-dir", type=Path, default=None,
        help=(
            f"Diretório local do {LAYOUT_MODEL}. Se omitido, usa "
            f"<cache>/official_models/{LAYOUT_MODEL}."
        ),
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=DEFAULT_THRESHOLD,
        help="Confiança mínima de OCR abaixo da qual o TableMagic é ativado (padrão: 0.70).",
    )
    parser.add_argument("--pages", help="Páginas 1-based, ex: 1,3-5,7")
    parser.add_argument("--output-dir", type=Path, default=Path("output/compare_hybrid"))
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument(
        "--min-native-chars", type=int, default=50,
        help="Mínimo de caracteres da camada nativa para usar texto nativo (padrão: 50).",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Verificar inventário de modelos sem executar inferência.",
    )
    parser.add_argument(
        "--allow-download", action="store_true",
        help="Permite download automático de modelos ausentes (necessário na primeira vez).",
    )
    args = parser.parse_args(argv)

    pdf = args.pdf.expanduser().resolve()
    if not pdf.is_file():
        print(f"[FAIL] PDF não encontrado: {pdf}", file=sys.stderr)
        return 1

    if not args.allow_download:
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

    total_pages = _count_pdf_pages(pdf)
    try:
        pages = parse_pages(args.pages) if args.pages else list(range(1, total_pages + 1))
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    invalid = [p for p in pages if p > total_pages]
    if invalid:
        parser.error(f"Páginas fora do intervalo (PDF tem {total_pages} páginas): {invalid}")

    cache_by_profile: dict[str, Path] = {
        "v5": args.v5_cache.expanduser().resolve(),
        "v6": args.v6_cache.expanduser().resolve(),
    }
    layout_dir = args.layout_model_dir.expanduser().resolve() if args.layout_model_dir else None
    threshold = args.confidence_threshold

    print(f"PDF    : {pdf} ({total_pages} páginas)")
    print(f"Páginas: {pages}")
    print(f"Threshold de confiança: {threshold:.2f}")
    print(f"Min chars nativo: {args.min_native_chars}")
    print(f"Layout model: {LAYOUT_MODEL}")
    print()

    all_ok = True
    for profile in HYBRID_PROFILES:
        ok = _print_inventory(profile, cache_by_profile[profile], layout_dir)
        all_ok = all_ok and ok

    if not all_ok and not args.allow_download:
        print(
            "[FAIL] Um ou mais modelos ausentes. "
            "Execute com --allow-download na primeira vez para baixar automaticamente.",
            file=sys.stderr,
        )
        return 1

    if not all_ok and args.allow_download:
        print("[INFO] Modelos ausentes serão baixados automaticamente durante a execução.\n")

    if args.check_only:
        if all_ok:
            print("[OK] Todos os modelos encontrados. Execute sem --check-only para rodar.")
        else:
            print("[INFO] Modelos ausentes serão baixados na primeira execução (--allow-download).")
        return 0

    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] = {
        "schema": "structured-pdf-text.hybrid-ocr-tablemagic.v2",
        "design": "native-ocr-tablemagic-3level",
        "min_native_chars": args.min_native_chars,
        "pdf": str(pdf),
        "pdf_sha256": sha256_file(pdf),
        "total_pdf_pages": total_pages,
        "pages_human_1_based": pages,
        "confidence_threshold": threshold,
        "layout_model": LAYOUT_MODEL,
        "use_layout_detection": True,
        "git": git_identity(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": sys.version,
        "packages": package_versions(),
        "render_scale": args.scale,
        "results": {},
        "comparison": [],
    }

    failures = 0
    for profile in HYBRID_PROFILES:
        cache = cache_by_profile[profile]
        try:
            result = run_profile(pdf, pages, cache, profile, threshold, layout_dir, args.scale, args.min_native_chars)
            metadata["results"][profile] = result
        except Exception as exc:
            print(f"[FAIL] {profile}: {type(exc).__name__}: {exc}", file=sys.stderr)
            metadata["results"][profile] = {"status": "failed", "error": str(exc)}
            failures += 1

    r = metadata["results"]
    if "v5" in r and "page_results" in r["v5"] and "v6" in r and "page_results" in r["v6"]:
        metadata["comparison"] = _compare_pages(r["v5"], r["v6"])

    manifest_path = out / "evaluation.json"
    _write_json(manifest_path, metadata)
    print(f"[INFO] Manifesto: {manifest_path}")

    if failures:
        print(f"[FAIL] {failures} de {len(HYBRID_PROFILES)} perfis falharam", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
