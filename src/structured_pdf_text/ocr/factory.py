"""OCR backend factory.

The single point where an ``ExtractorConfig`` is mapped to a concrete backend
instance.  ``api.py`` depends only on this function; it never imports backend
classes directly.  Adding a new engine in the future only requires a new
branch in ``build_ocr_backend`` and a new entry in ``registry.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from structured_pdf_text.ocr.contracts import OCRBackend, UnsupportedOCREngine
from structured_pdf_text.ocr.registry import REGISTRY

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig


def build_ocr_backend(config: "ExtractorConfig") -> OCRBackend:
    """Instantiate the correct OCR backend for ``config.ocr_engine``.

    The returned object satisfies both ``OCRBackend`` (benchmarking contract)
    and ``OcrEngine`` (pipeline contract consumed by recovery.py).

    Raises:
        UnsupportedOCREngine: when ``config.ocr_engine`` is not registered.
    """
    engine = getattr(config, "ocr_engine", "paddle")

    if engine not in REGISTRY:
        raise UnsupportedOCREngine(
            f"Unknown OCR engine: {engine!r}. "
            f"Available: {', '.join(REGISTRY)}"
        )

    match engine:
        case "paddle":
            from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
            return PaddleOCRBackend(config)

        case "rapidocr-onnx" | "rapidocr-openvino":
            raise UnsupportedOCREngine(
                f"Engine {engine!r} is planned for Phase 4/5 of the OCR "
                "comparison roadmap. Run the P4-pre ONNX export validation "
                "first (see Plano_Comparativo_Paddle.md §68.2)."
            )

        case "tesseract":
            raise UnsupportedOCREngine(
                "Engine 'tesseract' is planned for Phase 6. "
                "See Plano_Comparativo_Paddle.md §19."
            )

        case "easyocr":
            raise UnsupportedOCREngine(
                "Engine 'easyocr' is planned for Phase 7. "
                "See Plano_Comparativo_Paddle.md §20."
            )

        case _:
            raise UnsupportedOCREngine(
                f"Unknown OCR engine: {engine!r}. "
                f"Available: {', '.join(REGISTRY)}"
            )
