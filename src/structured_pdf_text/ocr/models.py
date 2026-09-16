"""OCR model profiles.

Single source of truth for model names used by runtime, setup-models and
models-status. Adding a new language requires only a new entry in PROFILES;
all consumers derive their configuration from here automatically.

Supported profiles
------------------
- "pt"  : Portuguese / Latin-script documents (PP-OCRv5 server det + mobile rec).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OcrModelProfile:
    """Names of the four PaddleOCR models that make up one language profile."""

    language: str
    doc_orientation: str
    textline_orientation: str
    detection: str
    recognition: str

    @property
    def model_names(self) -> dict[str, str]:
        """Map PaddleOCR kwarg suffix → model name (for *_model_dir kwargs)."""
        return {
            "doc_orientation_classify": self.doc_orientation,
            "textline_orientation": self.textline_orientation,
            "text_detection": self.detection,
            "text_recognition": self.recognition,
        }

    @property
    def dir_kwargs(self) -> dict[str, str]:
        """Map full PaddleOCR *_model_dir kwarg name → model directory name."""
        return {
            f"{key}_model_dir": name
            for key, name in self.model_names.items()
        }

    @property
    def name_kwargs(self) -> dict[str, str]:
        """Map full PaddleOCR *_model_name kwarg name → model name (for setup)."""
        return {
            f"{key}_model_name": name
            for key, name in self.model_names.items()
        }


PROFILES: dict[str, OcrModelProfile] = {
    "pt": OcrModelProfile(
        language="pt",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv5_server_det",
        recognition="latin_PP-OCRv5_mobile_rec",
    ),
}

SUPPORTED_LANGUAGES = frozenset(PROFILES)


def get_profile(language: str) -> OcrModelProfile:
    """Return the OCR profile for the given language code.

    Raises ValueError for unsupported languages with a clear message so callers
    know exactly what to do instead of getting a cryptic KeyError.
    """
    profile = PROFILES.get(language)
    if profile is None:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise ValueError(
            f"No local OCR profile configured for language '{language}'. "
            f"Supported: {supported}."
        )
    return profile
