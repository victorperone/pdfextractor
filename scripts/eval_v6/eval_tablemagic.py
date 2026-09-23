"""Avaliação PP-TableMagic (TableRecognitionPipelineV2) — Fase 4.

Avalia o pipeline de reconhecimento de tabelas do PaddleOCR em imagens
de páginas rasterizadas, comparando com a saída do pipeline atual do projeto.

Uso:
    # Ativar o venv de avaliação:
    source .venv-paddle-v6-eval/bin/activate

    # Avaliar em uma imagem específica:
    python scripts/eval_v6/eval_tablemagic.py --image pagina_tabela.png

    # Avaliar em PDF real (extrai páginas como imagem):
    python scripts/eval_v6/eval_tablemagic.py \\
        --pdf corpus/Document_AI_V2.pdf \\
        --pages 5,6,7

    # Especificar cache de modelos e diretório de saída:
    python scripts/eval_v6/eval_tablemagic.py \\
        --pdf corpus/Document_AI_V2.pdf \\
        --cache-home ~/.cache/pdfextractor/paddlex \\
        --output-dir output/tablemagic_eval

IMPORTANTE: PP-TableMagic usa modelos v5 internamente (SLANeXt_wired, PP-OCRv5_server_det,
PP-OCRv5_server_rec). O --cache-home deve apontar para o cache v5 de produção.

Pré-condição: Esta avaliação só faz sentido se a Fase 2 (compare_v5_v6.py)
identificou páginas com falhas estruturais de tabela que v6 OCR puro não resolve.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _add_src_to_path() -> None:
    src = Path(__file__).parent.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _set_offline_mode(cache_home: str) -> None:
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(cache_home).expanduser().resolve())


def _pdf_page_to_image(pdf_path: Path, page_idx: int, scale: float = 2.0):
    """Render a PDF page to a PIL Image."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[page_idx]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    return pil_image


def eval_tablemagic_on_image(
    image_path: str,
    cache_home: str,
) -> dict:
    """Run TableRecognitionPipelineV2 on a single image and return structured result."""
    from paddleocr import TableRecognitionPipelineV2

    official_models = Path(cache_home) / "official_models"
    t0 = time.perf_counter()

    pipeline_kwargs: dict = {
        "device": "cpu",
        "enable_mkldnn": False,
    }

    # Wire in local model dirs only if they exist (offline mode)
    wired_dir = official_models / "SLANeXt_wired"
    det_dir = official_models / "PP-OCRv5_server_det"
    rec_dir = official_models / "latin_PP-OCRv5_server_rec"

    if wired_dir.exists():
        pipeline_kwargs["wired_table_structure_recognition_model_dir"] = str(wired_dir)
    if det_dir.exists():
        pipeline_kwargs["text_detection_model_dir"] = str(det_dir)
    if rec_dir.exists():
        pipeline_kwargs["text_recognition_model_dir"] = str(rec_dir)

    pipeline = TableRecognitionPipelineV2(**pipeline_kwargs)
    init_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    output = list(pipeline.predict(image_path))
    infer_ms = (time.perf_counter() - t1) * 1000

    tables = []
    for item in output:
        raw = item.json if hasattr(item, "json") else {}
        for table in raw.get("table_res_list", []):
            html = table.get("pred_html", "")
            cells = table.get("cell_box_list", [])
            texts = table.get("table_ocr_pred", {}).get("rec_texts", [])
            scores = table.get("table_ocr_pred", {}).get("rec_scores", [])
            tables.append({
                "cell_count": len(cells),
                "text_count": len(texts),
                "avg_score": round(sum(scores) / max(len(scores), 1), 4),
                "html_length": len(html),
                "html_preview": html[:300],
                "texts_preview": texts[:10],
            })

    return {
        "image": image_path,
        "init_ms": round(init_ms, 1),
        "infer_ms": round(infer_ms, 1),
        "table_count": len(tables),
        "tables": tables,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Avaliação PP-TableMagic — Fase 4")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="Imagem PNG/JPG de uma página com tabela")
    source.add_argument("--pdf", type=Path, help="PDF para extrair páginas como imagem")
    ap.add_argument(
        "--pages",
        default="",
        help="Páginas do PDF (índice 0-based), separadas por vírgula. Ex: 5,6,7",
    )
    ap.add_argument(
        "--cache-home",
        default=str(Path.home() / ".cache/pdfextractor/paddlex"),
        help="Cache de modelos v5 (deve conter SLANeXt_wired, PP-OCRv5_server_det, etc.)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/tablemagic_eval"),
        help="Diretório de saída para HTMLs e JSON",
    )
    ap.add_argument(
        "--scale",
        type=float,
        default=2.0,
        help="Fator de escala para renderização de páginas PDF (padrão: 2.0 = 144 DPI)",
    )
    args = ap.parse_args()

    _add_src_to_path()
    cache_home = str(Path(args.cache_home).expanduser().resolve())
    _set_offline_mode(cache_home)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    images_to_eval: list[str] = []

    if args.image:
        if not args.image.exists():
            print(f"[FAIL] Imagem não encontrada: {args.image}")
            return 1
        images_to_eval.append(str(args.image.resolve()))
    else:
        if not args.pdf.exists():
            print(f"[FAIL] PDF não encontrado: {args.pdf}")
            return 1

        page_indices: list[int] = []
        if args.pages:
            try:
                page_indices = [int(p.strip()) for p in args.pages.split(",") if p.strip()]
            except ValueError:
                print(f"[FAIL] --pages deve conter números separados por vírgula, ex: 5,6,7")
                return 1
        else:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(args.pdf))
            page_indices = list(range(len(pdf)))

        for idx in page_indices:
            img = _pdf_page_to_image(args.pdf, idx, args.scale)
            out_path = args.output_dir / f"page_{idx:04d}.png"
            img.save(str(out_path))
            images_to_eval.append(str(out_path))
            print(f"[OK] Página {idx} renderizada: {out_path}")

    print(f"\n[...] Avaliando {len(images_to_eval)} imagem(ns) com PP-TableMagic...")
    print(f"      Cache: {cache_home}")
    print(f"      Saída : {args.output_dir}")
    print("")

    all_results = []
    for img_path in images_to_eval:
        print(f"[...] {Path(img_path).name}...")
        try:
            result = eval_tablemagic_on_image(img_path, cache_home)
            all_results.append(result)
            print(
                f"[OK] {result['table_count']} tabela(s) | "
                f"init={result['init_ms']:.0f}ms | "
                f"infer={result['infer_ms']:.0f}ms"
            )
            if result["tables"]:
                t0 = result["tables"][0]
                print(f"     Tabela 0: {t0['cell_count']} células, score médio={t0['avg_score']}")
        except Exception as exc:
            print(f"[FAIL] {Path(img_path).name}: {type(exc).__name__}: {exc}")
            all_results.append({"image": img_path, "error": str(exc)})

    report_path = args.output_dir / "tablemagic_report.json"
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(all_results, fh, ensure_ascii=False, indent=2)
    print(f"\n[OK] Relatório JSON salvo em: {report_path}")

    errors = [r for r in all_results if "error" in r]
    if errors:
        print(f"\n[WARN] {len(errors)} imagem(ns) com falha:")
        for e in errors:
            print(f"  - {e['image']}: {e['error']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
