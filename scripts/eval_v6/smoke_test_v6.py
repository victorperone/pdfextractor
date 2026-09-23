"""Smoke test de compatibilidade PP-OCRv6 — Fase 1.

Confirma que os modelos v6 inicializam e rodam offline em CPU,
sem nenhuma tentativa de download de rede.

Uso:
    # Verificação rápida (imagem sintética):
    python scripts/eval_v6/smoke_test_v6.py \\
        --cache-home ~/.cache/pdfextractor/paddlex-v6-eval

    # Com documento real:
    python scripts/eval_v6/smoke_test_v6.py \\
        --cache-home ~/.cache/pdfextractor/paddlex-v6-eval \\
        --pdf corpus/Document_AI_V2.pdf

    # No Windows (PowerShell):
    python scripts/eval_v6/smoke_test_v6.py `
        --cache-home C:/Users/victor/.cache/pdfextractor/paddlex-v6-eval `
        --pdf corpus/Document_AI_V2.pdf
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _add_src() -> None:
    src = Path(__file__).parent.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def check_models(cache_home: str, profile: str) -> bool:
    _add_src()
    from structured_pdf_text.ocr.models import get_profile

    p = get_profile(profile)
    root = Path(cache_home) / "official_models"
    missing = [
        name for name in [p.doc_orientation, p.textline_orientation, p.detection, p.recognition]
        if not (root / name).exists()
    ]
    if missing:
        print("[FAIL] Modelos ausentes:")
        for m in missing:
            print(f"  {root / m}")
        return False
    print(f"[OK] Todos os modelos encontrados em {root}")
    return True


def test_image(cache_home: str, profile: str) -> bool:
    _add_src()
    from structured_pdf_text.ocr.models import get_profile

    import numpy as np
    from paddleocr import PaddleOCR

    p = get_profile(profile)
    root = Path(cache_home) / "official_models"
    kwargs = {
        "doc_orientation_classify_model_dir": str(root / p.doc_orientation),
        "doc_orientation_classify_model_name": p.doc_orientation,
        "textline_orientation_model_dir": str(root / p.textline_orientation),
        "textline_orientation_model_name": p.textline_orientation,
        "text_detection_model_dir": str(root / p.detection),
        "text_detection_model_name": p.detection,
        "text_recognition_model_dir": str(root / p.recognition),
        "text_recognition_model_name": p.recognition,
        "use_doc_orientation_classify": True,
        "use_doc_unwarping": False,
        "use_textline_orientation": True,
        "enable_mkldnn": False,
        "device": "cpu",
    }

    t0 = time.perf_counter()
    ocr = PaddleOCR(**kwargs)
    t1 = time.perf_counter()
    img = np.ones((200, 200, 3), dtype=np.uint8) * 240
    list(ocr.predict(input=img))
    t2 = time.perf_counter()
    print(f"[OK] Init: {(t1-t0)*1000:.0f}ms | Inferência: {(t2-t1)*1000:.0f}ms")
    return True


def test_pdf(pdf: Path, cache_home: str, profile: str) -> bool:
    _add_src()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(cache_home).expanduser().resolve())

    from structured_pdf_text import PdfTextExtractor
    from structured_pdf_text.config import ExtractorConfig, ExtractionMode

    t0 = time.perf_counter()
    doc = PdfTextExtractor(ExtractorConfig(mode=ExtractionMode.BALANCED, language=profile)).extract(pdf)
    elapsed = time.perf_counter() - t0
    chars = sum(len(p.reading_text or "") for p in doc.pages)
    print(f"[OK] {len(doc.pages)} páginas | {chars:,} chars | {elapsed:.1f}s | {doc.diagnostics.status.value}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-home", default=str(Path.home() / ".cache/pdfextractor/paddlex-v6-eval"))
    ap.add_argument("--profile", default="pt-v6-medium", choices=["pt-v6-medium", "pt-v6-small"])
    ap.add_argument("--pdf", type=Path, default=None)
    args = ap.parse_args()

    cache_home = str(Path(args.cache_home).expanduser().resolve())
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = cache_home

    print(f"Perfil : {args.profile}")
    print(f"Cache  : {cache_home}")

    if not check_models(cache_home, args.profile):
        return 1

    print("Testando com imagem sintética...")
    if not test_image(cache_home, args.profile):
        return 1

    if args.pdf:
        print(f"Testando com {args.pdf.name}...")
        if not test_pdf(args.pdf, cache_home, args.profile):
            return 1

    print("\n[PASS] Smoke test OK — modelos v6 funcionando offline em CPU.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
