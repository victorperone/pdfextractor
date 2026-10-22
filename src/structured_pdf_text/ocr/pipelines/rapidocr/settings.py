"""RapidOCR-only settings and model selection."""
from __future__ import annotations

import os

from structured_pdf_text.ocr.env import env_float
from structured_pdf_text.ocr.pipeline.contracts import FrozenMap
from structured_pdf_text.ocr.pipeline.settings import RapidOCRSettings, _common, _path
from .models import selected_paths


def resolve_settings(config, entry=None) -> RapidOCRSettings:
    provider = config.ocr_provider or (entry.default_provider if entry is not None else "onnxruntime")
    names = (
        "RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL",
        "RAPIDOCR_UNCLIP_RATIO", "RAPIDOCR_BOX_THRESH", "RAPIDOCR_DET_THRESH",
        "RAPIDOCR_TEXT_SCORE", "RAPIDOCR_ANGLE_CLS",
    )
    values = {name: os.environ.get(name) for name in names}
    if values["RAPIDOCR_ANGLE_CLS"] and values["RAPIDOCR_ANGLE_CLS"].lower() in {"1", "true", "yes"}:
        raise ValueError(
            "RAPIDOCR_ANGLE_CLS is not supported by the pinned v6 profile; "
            "orientation classification is disabled explicitly"
        )
    size = config.rapidocr_model_size or "medium"
    detector, recognizer, dictionary, classifier = selected_paths(size, config.ocr_cache_home)
    return RapidOCRSettings(
        **_common(config, "rapidocr", provider),
        model_size=size,
        cache_home=_path(config.ocr_cache_home),
        num_threads=config.num_threads,
        detector_path=str(detector),
        recognizer_path=str(recognizer),
        dictionary_path=str(dictionary),
        classifier_path=str(classifier),
        unclip_ratio=env_float("RAPIDOCR_UNCLIP_RATIO", 1.8, minimum=0.0),
        box_threshold=env_float("RAPIDOCR_BOX_THRESH", 0.45, minimum=0.0, maximum=1.0),
        detector_threshold=env_float("RAPIDOCR_DET_THRESH", 0.25, minimum=0.0, maximum=1.0),
        text_score=env_float("RAPIDOCR_TEXT_SCORE", 0.5, minimum=0.0, maximum=1.0),
        environment=FrozenMap(tuple((name, value) for name, value in values.items() if value is not None)),
    )
