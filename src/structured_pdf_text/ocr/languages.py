"""Public BCP 47 language tags and explicit OCR-engine aliases."""
from __future__ import annotations

from structured_pdf_text.errors import ConfigurationError


_TAGS = {
    "pt": "pt-BR", "pt-br": "pt-BR", "por": "pt-BR",
    "en": "en", "en-us": "en", "eng": "en",
}


def canonical_language(value: str) -> str:
    try:
        return _TAGS[value.strip().casefold()]
    except (AttributeError, KeyError):
        raise ConfigurationError(f"Unsupported OCR language {value!r}; supported: pt-BR, en") from None


def backend_language(value: str, engine: str) -> str:
    language = canonical_language(value)
    if language == "pt-BR":
        return "por" if engine == "tesseract" else "pt"
    return "eng" if engine == "tesseract" else "en"
