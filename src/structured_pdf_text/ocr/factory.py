"""OCR backend factory.

The single point where an ``ExtractorConfig`` is mapped to a concrete backend
instance.  ``api.py`` depends only on this function; it never imports backend
classes directly.  Adding a new engine in the future only requires a new
branch in ``build_ocr_backend`` and a new entry in ``registry.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from dataclasses import replace
import warnings

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
    from structured_pdf_text.ocr.languages import backend_language, canonical_language
    canonical_language(config.language)

    if engine not in REGISTRY:
        raise UnsupportedOCREngine(
            f"Unknown OCR engine: {engine!r}. "
            f"Available: {', '.join(REGISTRY)}"
        )

    match engine:
        case "paddle":
            from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend
            return PaddleOCRBackend(replace(config, language=backend_language(config.language, "paddle")))

        case "rapidocr":
            from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend
            provider = config.ocr_provider or (
                config.ocr_runtime if config.ocr_runtime in {"onnxruntime", "openvino"} else "onnxruntime"
            )
            if provider not in {"onnxruntime", "openvino"}:
                raise UnsupportedOCREngine(f"Unsupported RapidOCR provider: {provider!r}")
            return RapidOCRBackend(config, runtime=provider)

        case "rapidocr-onnx":
            warnings.warn("rapidocr-onnx is deprecated; use ocr_engine='rapidocr', ocr_provider='onnxruntime'", DeprecationWarning, stacklevel=2)
            from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend
            return RapidOCRBackend(config, runtime="onnxruntime")

        case "rapidocr-openvino":
            warnings.warn("rapidocr-openvino is deprecated; use ocr_engine='rapidocr', ocr_provider='openvino'", DeprecationWarning, stacklevel=2)
            from structured_pdf_text.ocr.backends.rapidocr import RapidOCRBackend
            return RapidOCRBackend(config, runtime="openvino")

        case "tesseract":
            from structured_pdf_text.ocr.backends.tesseract import TesseractBackend
            return TesseractBackend(config)

        case "easyocr":
            from structured_pdf_text.ocr.backends.easyocr import EasyOCRBackend
            return EasyOCRBackend(config)

        case _:
            raise UnsupportedOCREngine(
                f"Unknown OCR engine: {engine!r}. "
                f"Available: {', '.join(REGISTRY)}"
            )
