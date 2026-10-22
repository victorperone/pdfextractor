"""EasyOCR-owned immutable settings and quality policies."""
import os
from dataclasses import replace

from structured_pdf_text.ocr.pipeline.contracts import FrozenMap
from structured_pdf_text.ocr.pipeline.settings import EasyOCRSettings, _common, _path


def resolve_settings(config, entry=None) -> EasyOCRSettings:
    return EasyOCRSettings(
        **_common(config, "easyocr", None),
        cache_home=_path(config.ocr_cache_home),
        num_threads=config.num_threads,
        max_quality=config.max_quality,
        tiling=config.ocr_tiling,
        tile_rows=config.ocr_tile_rows,
        tile_columns=config.ocr_tile_columns,
        tile_overlap=float(config.ocr_tile_overlap),
        environment=FrozenMap(tuple(
            (name, os.environ[name])
            for name in sorted(os.environ)
            if name.startswith("EASYOCR_")
        )),
    )


def resolve_quality_thresholds(config, entry=None):
    return replace(
        config.ocr_quality_thresholds,
        strong_mean_confidence=0.82,
        strong_lower_quartile=0.65,
        max_low_confidence_char_ratio=0.20,
        severe_mean_confidence=0.55,
        severe_low_confidence_char_ratio=0.45,
        low_confidence_threshold=0.60,
    )
