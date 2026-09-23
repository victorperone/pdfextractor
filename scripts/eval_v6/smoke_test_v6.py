"""Smoke test de compatibilidade PP-OCRv6 — Fase 1.

Verifica se os modelos v6 inicializam e executam inferência offline em CPU,
sem qualquer tentativa de download de rede.

Uso:
    # Ativar o venv de avaliação primeiro:
    source .venv-paddle-v6-eval/bin/activate

    python scripts/eval_v6/smoke_test_v6.py \\
        --cache-home ~/.cache/pdfextractor/paddlex-v6-eval \\
        --profile pt-v6-medium

    # Para testar com o corpus real:
    python scripts/eval_v6/smoke_test_v6.py \\
        --cache-home ~/.cache/pdfextractor/paddlex-v6-eval \\
        --profile pt-v6-medium \\
        --pdf corpus/Document_AI_V2.pdf \\
        --pages 1
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _set_offline_mode(cache_home: str) -> None:
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = cache_home


def _build_model_dirs(cache_home: str, profile_name: str) -> dict[str, str]:
    """Resolve model dirs for the given profile from the v6 eval cache."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
    from structured_pdf_text.ocr.models import get_profile

    profile = get_profile(profile_name)
    official_models = Path(cache_home) / "official_models"
    return {
        "doc_orientation_classify_model_dir": str(official_models / profile.doc_orientation),
        "doc_orientation_classify_model_name": profile.doc_orientation,
        "textline_orientation_model_dir": str(official_models / profile.textline_orientation),
        "textline_orientation_model_name": profile.textline_orientation,
        "text_detection_model_dir": str(official_models / profile.detection),
        "text_detection_model_name": profile.detection,
        "text_recognition_model_dir": str(official_models / profile.recognition),
        "text_recognition_model_name": profile.recognition,
    }


def _check_model_dirs(model_kwargs: dict[str, str]) -> list[str]:
    """Return list of missing model directories."""
    missing = []
    for key, path in model_kwargs.items():
        if not key.endswith("_model_dir"):
            continue
        p = Path(path)
        if not p.exists():
            missing.append(str(p))
        elif not any(p.glob("*")):
            missing.append(f"{p} (vazio)")
    return missing


def smoke_test_image(cache_home: str, profile_name: str) -> bool:
    """Run OCR on a synthetic white 200x200 image."""
    import numpy as np
    from paddleocr import PaddleOCR

    model_kwargs = _build_model_dirs(cache_home, profile_name)
    missing = _check_model_dirs(model_kwargs)
    if missing:
        print(f"[FAIL] Modelos ausentes para '{profile_name}':")
        for m in missing:
            print(f"  - {m}")
        return False

    print(f"[OK] Todos os diretórios de modelo encontrados para '{profile_name}'.")

    print("[...] Inicializando PaddleOCR (CPU, offline)...")
    t0 = time.perf_counter()
    ocr = PaddleOCR(
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        enable_mkldnn=False,
        device="cpu",
        **model_kwargs,
    )
    init_ms = (time.perf_counter() - t0) * 1000
    print(f"[OK] PaddleOCR inicializado em {init_ms:.0f} ms.")

    img = np.ones((200, 200, 3), dtype=np.uint8) * 240
    print("[...] Inferência em imagem sintética 200×200...")
    t1 = time.perf_counter()
    result = list(ocr.predict(input=img))
    infer_ms = (time.perf_counter() - t1) * 1000
    print(f"[OK] Inferência concluída em {infer_ms:.0f} ms. Resultados: {len(result)} item(s).")
    return True


def smoke_test_pdf(cache_home: str, profile_name: str, pdf_path: Path, pages: int) -> bool:
    """Extract first N pages of a PDF via the project's extractor."""
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
    import os as _os
    _os.environ["PADDLE_PDX_CACHE_HOME"] = cache_home

    from structured_pdf_text import PdfTextExtractor
    from structured_pdf_text.config import ExtractorConfig, ExtractionMode

    print(f"[...] Extraindo {pages} página(s) de {pdf_path.name} com perfil '{profile_name}'...")
    config = ExtractorConfig(mode=ExtractionMode.BALANCED, language=profile_name)
    t0 = time.perf_counter()
    doc = PdfTextExtractor(config).extract(pdf_path)
    elapsed = (time.perf_counter() - t0) * 1000

    extracted_pages = len(doc.pages)
    total_chars = sum(len(p.text) for p in doc.pages)
    print(f"[OK] Extração concluída: {extracted_pages} páginas, {total_chars} chars, {elapsed:.0f} ms.")
    print(f"     Status: {doc.diagnostics.status.value}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke test PP-OCRv6 offline+CPU")
    ap.add_argument(
        "--cache-home",
        default=str(Path.home() / ".cache/pdfextractor/paddlex-v6-eval"),
        help="Diretório raiz do cache de modelos v6",
    )
    ap.add_argument(
        "--profile",
        default="pt-v6-medium",
        choices=["pt-v6-medium", "pt-v6-small"],
        help="Perfil OCR a testar",
    )
    ap.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="PDF para teste real (opcional). Ex: corpus/Document_AI_V2.pdf",
    )
    ap.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Número de páginas do PDF a processar (padrão: 1)",
    )
    args = ap.parse_args()

    cache_home = str(Path(args.cache_home).expanduser().resolve())
    print("=" * 60)
    print(f"Smoke test PP-OCRv6 — perfil '{args.profile}'")
    print(f"Cache : {cache_home}")
    print("=" * 60)

    _set_offline_mode(cache_home)

    ok = smoke_test_image(cache_home, args.profile)
    if not ok:
        return 1

    if args.pdf:
        if not args.pdf.exists():
            print(f"[FAIL] PDF não encontrado: {args.pdf}")
            return 1
        ok = smoke_test_pdf(cache_home, args.profile, args.pdf, args.pages)
        if not ok:
            return 1

    print("")
    print("[PASS] Smoke test concluído sem erros.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
