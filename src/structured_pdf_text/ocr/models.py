"""OCR model profiles.

Single source of truth for model names used by runtime, setup-models and
models-status. Adding a new language or model variant requires only a new entry
in PROFILES; all consumers derive their configuration from here automatically.

Supported profiles
------------------
- "pt"           : Portuguese / Latin-script documents (PP-OCRv5 server det + mobile rec).
                   Default production profile. Stable, validated on Windows Server 2025.
- "pt-v6-medium" : PP-OCRv6 medium models — experimental evaluation only.
                   Higher accuracy target; same CPU throughput as v5-server.
                   Requires models in a separate cache directory from v5.
                   Select via: --ocr-model-profile pt-v6-medium --cache-home <v6-cache>
- "pt-v6-small"  : PP-OCRv6 small models — experimental evaluation only.
                   Lower memory footprint, ~2.6× faster than v5-server on CPU.
                   Select via: --ocr-model-profile pt-v6-small --cache-home <v6-cache>

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
    # ── Stable production profiles ──────────────────────────────────────────
    "pt": OcrModelProfile(
        language="pt",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv5_server_det",
        recognition="latin_PP-OCRv5_mobile_rec",
    ),
    # ── Experimental evaluation profiles (PP-OCRv6) ─────────────────────────
    # These profiles require a separate model cache from the v5 production
    # models.  Always pass --cache-home pointing to the v6 evaluation cache
    # when using these profiles (e.g. ~/.cache/pdfextractor/paddlex-v6-eval).
    # Do NOT point them at the v5 production cache.
    "pt-v6-medium": OcrModelProfile(
        language="pt-v6-medium",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv6_medium_det",
        recognition="PP-OCRv6_medium_rec",
    ),
    "pt-v6-small": OcrModelProfile(
        language="pt-v6-small",
        doc_orientation="PP-LCNet_x1_0_doc_ori",
        textline_orientation="PP-LCNet_x1_0_textline_ori",
        detection="PP-OCRv6_small_det",
        recognition="PP-OCRv6_small_rec",
    ),
}

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
            f"Use --ocr-model-profile to select an experimental profile."
        )
    return profile
