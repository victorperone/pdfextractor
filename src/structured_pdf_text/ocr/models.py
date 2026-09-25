"""OCR model profiles.

Single source of truth for model names used by runtime, setup-models and
models-status. Adding a new language or model variant requires only a new entry
in PROFILES; all consumers derive their configuration from here automatically.

Supported profiles
------------------
- "pt"           : Portuguese / Latin-script documents (PP-OCRv6 medium).
                   Default profile for commands that permit implicit selection.
- "pt-v6-medium" : Explicit alias for the same PP-OCRv6 medium profile.
- "pt-v5"        : PP-OCRv5 rollback profile, only when explicitly selected.
- "pt-v6-small"  : PP-OCRv6 small profile.

Notes
-----
Profile names are intentionally distinct from language codes so that --language pt
can be kept as "Portuguese" while --ocr-model-profile selects the actual model set.
The "pt" profile continues to be the default when --ocr-model-profile is absent.
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
    # ── Production profile (PP-OCRv6 medium — default since 2026-09-24) ─────
    # Cache: ~/.cache/pdfextractor/paddlex  (promoted from paddlex-v6-eval)
    "pt": OcrModelProfile(
        language="pt",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv6_medium_det",
        recognition="PP-OCRv6_medium_rec",
    ),
    # ── Explicit rollback profile (PP-OCRv5 server) ─────────────────────────
    # Use --ocr-model-profile pt-v5 to select v5 explicitly.
    "pt-v5": OcrModelProfile(
        language="pt-v5",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv5_server_det",
        recognition="latin_PP-OCRv5_mobile_rec",
    ),
    # ── PP-OCRv6 small profile ───────────────────────────────────────────────
    "pt-v6-small": OcrModelProfile(
        language="pt-v6-small",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv6_small_det",
        recognition="PP-OCRv6_small_rec",
    ),
}

# Public identifier for the default v6 medium profile. This is an alias to
# the same immutable configuration, so model names and cache paths cannot drift.
PROFILES["pt-v6-medium"] = PROFILES["pt"]
SUPPORTED_LANGUAGES = frozenset(PROFILES)


def get_profile(language: str) -> OcrModelProfile:
    """Return the OCR model profile for the given profile name.

    The ``language`` parameter doubles as the profile selector when
    ``--ocr-model-profile`` is not specified.  When the CLI flag is used,
    its value is passed here directly (e.g. ``"pt-v6-medium"``).

    Raises ValueError for unsupported profile names with a clear message so
    callers know exactly what to do instead of getting a cryptic KeyError.
    """
    profile = PROFILES.get(language)
    if profile is None:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise ValueError(
            f"No local OCR profile configured for '{language}'. "
            f"Supported profiles: {supported}. "
            f"Choose one of the listed profiles explicitly with --ocr-model-profile."
        )
    return profile
