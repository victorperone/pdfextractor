"""OCR backend registry.

Maps engine names to their metadata so that ``factory.py`` and the CLI can
validate engine names and report setup instructions without importing the
actual backend classes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendEntry:
    """Metadata for one registered OCR backend."""

    name: str
    display_name: str
    default_runtime: str
    default_profile: str
    extras: str | None
    setup_hint: str


REGISTRY: dict[str, BackendEntry] = {
    "paddle": BackendEntry(
        name="paddle",
        display_name="PaddleOCR PP-OCRv6 medium",
        default_runtime="paddle_static",
        default_profile="pt",
        extras="ocr",
        setup_hint="pdftext setup-models --language pt",
    ),
    "rapidocr": BackendEntry(
        name="rapidocr",
        display_name="RapidOCR",
        default_runtime="onnxruntime",
        default_profile="latin/pt-compatible",
        extras="ocr-rapidocr-onnx",
        setup_hint='pip install "structured-pdf-text[ocr-rapidocr-onnx]"',
    ),
    "rapidocr-onnx": BackendEntry(
        name="rapidocr-onnx",
        display_name="RapidOCR + ONNX Runtime",
        default_runtime="onnxruntime",
        default_profile="latin/pt-compatible",
        extras="ocr-rapidocr-onnx",
        setup_hint='pip install "structured-pdf-text[ocr-rapidocr-onnx]"',
    ),
    "rapidocr-openvino": BackendEntry(
        name="rapidocr-openvino",
        display_name="RapidOCR + OpenVINO",
        default_runtime="openvino",
        default_profile="latin/pt-compatible",
        extras="ocr-rapidocr-openvino",
        setup_hint='pip install "structured-pdf-text[ocr-rapidocr-openvino]"',
    ),
    "tesseract": BackendEntry(
        name="tesseract",
        display_name="Tesseract 5 + português",
        default_runtime="tesseract-cli",
        default_profile="por-best",
        extras=None,
        setup_hint="Install Tesseract 5 system package and download por.traineddata (tessdata_best).",
    ),
    "easyocr": BackendEntry(
        name="easyocr",
        display_name="EasyOCR + português (CPU)",
        default_runtime="torch-cpu",
        default_profile="latin-g2-pt",
        extras="ocr-easyocr",
        setup_hint='pip install "structured-pdf-text[ocr-easyocr]"',
    ),
}


def get_entry(engine: str) -> BackendEntry:
    """Return the registry entry for an engine name.

    Raises ``KeyError`` for unknown names.  Callers that want a softer error
    should check ``engine in REGISTRY`` first.
    """
    if engine not in REGISTRY:
        raise KeyError(
            f"Unknown OCR engine: {engine!r}. "
            f"Available: {', '.join(REGISTRY)}"
        )
    return REGISTRY[engine]
