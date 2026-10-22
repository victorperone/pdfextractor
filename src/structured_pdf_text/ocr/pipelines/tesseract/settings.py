"""Tesseract-owned immutable executable, tessdata and page settings."""
from __future__ import annotations

import os

from structured_pdf_text.ocr.pipeline.contracts import FrozenMap
from structured_pdf_text.ocr.pipeline.settings import TesseractSettings, _common


def resolve_settings(config, entry=None) -> TesseractSettings:
    names = (
        "TESSERACT_CMD", "TESSERACT_TESSDATA_DIR", "TESSERACT_LANG",
        "TESSERACT_DPI", "TESSERACT_PSM", "TESSERACT_OEM",
    )
    values = {name: os.environ.get(name) for name in names}

    def as_int(name: str) -> int | None:
        value = values[name]
        if value is None:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc

    language = values["TESSERACT_LANG"] or ("por" if config.language == "pt-BR" else "eng")
    return TesseractSettings(
        **_common(config, "tesseract", None),
        executable=values["TESSERACT_CMD"],
        tessdata_dir=values["TESSERACT_TESSDATA_DIR"],
        language_code=language,
        dpi=as_int("TESSERACT_DPI"),
        psm=as_int("TESSERACT_PSM"),
        oem=as_int("TESSERACT_OEM"),
        environment=FrozenMap(tuple(
            (name, value) for name, value in values.items() if value is not None
        )),
    )
