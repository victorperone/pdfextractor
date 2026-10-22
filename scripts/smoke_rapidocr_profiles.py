#!/usr/bin/env python3
"""Run real Portuguese OCR once for each selected RapidOCR size/provider pair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.factory import build_ocr_backend, resolve_config
from structured_pdf_text.ocr.pipelines.rapidocr.models import verify_artifacts


TEXT_LINES = (
    "AÇÃO: São Paulo, número 12345",
    "R$ 42,90 | ID AB-009 | órgão público",
)


def _font(path: Path | None, size: int):
    candidates = [path] if path else []
    candidates.extend([
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ])
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fixture(font_path: Path | None) -> Image.Image:
    image = Image.new("RGB", (2200, 340), "white")
    draw = ImageDraw.Draw(image)
    font = _font(font_path, 96)
    draw.text((48, 45), TEXT_LINES[0], fill="black", font=font)
    draw.text((48, 180), TEXT_LINES[1], fill="black", font=font)
    return image


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def run(size: str, provider: str, cache_home: Path | None, font_path: Path | None, use_gpu: bool = False) -> dict:
    config = ExtractorConfig(
        mode="ocr", ocr_engine="rapidocr", ocr_provider=provider,
        rapidocr_model_size=size, language="pt-BR", ocr_cache_home=cache_home,
        use_gpu=use_gpu,
    )
    settings = resolve_config(config).settings
    hashes = verify_artifacts(size, (
        settings.detector_path, settings.recognizer_path,
        settings.dictionary_path, settings.classifier_path,
    ))
    backend = build_ocr_backend(config)
    try:
        image = _fixture(font_path)
        tokens = backend.recognize_page(image, 0, BBox(0, 0, image.width, image.height))
        text = _normalize(" ".join(token.text for token in tokens))
        checks = {
            "portuguese_accent": any(char in text for char in "ãáçêó"),
            "number": "12345" in text,
            "amount": "42,90" in text or "42.90" in text,
            "identifier": "ab-009" in text or "ab009" in text,
        }
        if not all(checks.values()):
            raise RuntimeError(f"{size}/{provider} failed OCR content checks: {checks}; text={text!r}")
        identity = backend.identity
        return {
            "size": size,
            "provider": provider,
            "profile": identity.profile,
            "runtime": identity.runtime,
            "language": identity.language,
            "hashes": hashes,
            "checks": checks,
            "tokens": [token.text for token in tokens],
        }
    finally:
        backend.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-home", type=Path)
    parser.add_argument("--size", choices=("medium", "small", "all"), default="all")
    parser.add_argument("--provider", choices=("onnxruntime", "openvino", "all"), default="all")
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    sizes = ("medium", "small") if args.size == "all" else (args.size,)
    providers = ("onnxruntime", "openvino") if args.provider == "all" else (args.provider,)
    results = []
    for size in sizes:
        for provider in providers:
            result = run(size, provider, args.cache_home, args.font, args.gpu)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
