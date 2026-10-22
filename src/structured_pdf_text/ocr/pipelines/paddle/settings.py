"""PaddleOCR-owned immutable settings and quality policies."""
import os

from structured_pdf_text.ocr.pipeline.contracts import FrozenMap
from structured_pdf_text.ocr.pipeline.settings import PaddleSettings, _common, _path


def resolve_settings(config, entry=None) -> PaddleSettings:
    return PaddleSettings(
        **_common(config, "paddle", None),
        model_profile=config.paddle_model_profile,
        cache_home=_path(config.ocr_cache_home),
        num_threads=config.num_threads,
        batch_size=config.ocr_batch_size,
        environment=FrozenMap(tuple(
            (name, os.environ[name])
            for name in sorted(os.environ)
            if name.startswith("PADDLE_")
        )),
    )


def resolve_quality_thresholds(config, entry=None):
    return config.ocr_quality_thresholds
