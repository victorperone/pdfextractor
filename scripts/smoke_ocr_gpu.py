#!/usr/bin/env python3
"""Prove CUDA inference on a synthetic image PDF, without running the corpus.

Run with the selected engine's Python. A requested GPU or an installed CUDA
provider alone never passes this check. Reports and fixtures cannot be reused.
OCR content checks are reported separately from execution device acceptance.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from structured_pdf_text import ExtractorConfig, PdfTextExtractor
from structured_pdf_text.ocr.factory import build_ocr_pipeline
from structured_pdf_text.renderers.markdown import render_markdown


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _fixture(directory: Path, font_path: Path) -> Path:
    image = Image.new("RGB", (2200, 520), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 72)
    for index, text in enumerate(("AÇÃO: São Paulo, número 12345",
                                  "R$ 42,90 | ID AB-009 | órgão público",
                                  "Teste de execução em GPU")):
        draw.text((64, 55 + index * 140), text, font=font, fill="black")
    path = directory / "synthetic.pdf"
    image.save(directory / "synthetic.png")
    image.save(path, "PDF", resolution=150.0)
    return path


def _instrument(engine: str, directory: Path, stack: ExitStack) -> list[dict]:
    paddle_predictors = []
    if engine == "paddle":
        import paddle.inference as inference
        create = inference.create_predictor
        def tracked(config):
            gpu = bool(config.use_gpu())
            predictor = create(config)
            paddle_predictors.append({"use_gpu": gpu, "created": True})
            return predictor
        stack.enter_context(patch.object(inference, "create_predictor", tracked))
    elif engine == "rapidocr":
        from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
        original = OrtInferSession._init_sess_opts
        sequence = 0
        def options(config):
            nonlocal sequence
            sequence += 1
            result = original(config)
            result.enable_profiling = True
            result.profile_file_prefix = str(directory / f"ort-session-{sequence}")
            return result
        stack.enter_context(patch.object(OrtInferSession, "_init_sess_opts", staticmethod(options)))
    return paddle_predictors


def _evidence(engine: str, backend, paddle_predictors: list[dict]) -> dict:
    if engine == "easyocr":
        import torch
        models = {}
        for name in ("detector", "recognizer"):
            devices = sorted({str(parameter.device) for parameter in getattr(backend._reader, name).parameters()})
            models[name] = devices
        torch.cuda.synchronize()
        return {"model_parameter_devices": models,
                "cuda_memory_allocated_bytes": torch.cuda.memory_allocated(),
                "gpu_name": torch.cuda.get_device_name(0)}
    if engine == "paddle":
        import paddle
        paddle.device.cuda.synchronize()
        return {"predictors": paddle_predictors,
                "cuda_memory_allocated_bytes": paddle.device.cuda.memory_allocated()}
    stages = {}
    for name, attribute in (("detector", "text_det"), ("recognizer", "text_rec")):
        session = getattr(backend._engine, attribute).session.session
        profile = Path(session.end_profiling())
        events = json.loads(profile.read_text())
        nodes = [event for event in events if event.get("cat") == "Node"]
        counts = {}
        for node in nodes:
            provider = node.get("args", {}).get("provider")
            if provider:
                counts[provider] = counts.get(provider, 0) + 1
        stages[name] = {"providers": session.get_providers(), "node_events": counts,
                        "automatic_ep_fallback_enabled": getattr(session, "_enable_fallback", None),
                        "profile": str(profile), "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest()}
    return {"stages": stages}


def _accepted(engine: str, identity: dict, evidence: dict) -> bool:
    if identity.get("device") != "cuda" or identity.get("extra", {}).get("gpu_fallback_reason"):
        return False
    if engine == "easyocr":
        return all(evidence["model_parameter_devices"].get(stage) and
                   all(device.startswith("cuda:") for device in evidence["model_parameter_devices"][stage])
                   for stage in ("detector", "recognizer"))
    if engine == "paddle":
        return bool(evidence["predictors"]) and all(item["use_gpu"] and item["created"] for item in evidence["predictors"])
    return all(evidence["stages"][stage]["node_events"].get("CUDAExecutionProvider", 0) > 0
               for stage in ("detector", "recognizer"))


def run(engine: str, directory: Path, cache_home: Path, font: Path, *, size: str = "medium") -> dict:
    # Reserve the whole namespace before constructing models or probing CUDA.
    directory.mkdir(parents=True, exist_ok=False)
    report = {"engine": engine, "python": sys.executable, "gpu_requested": True,
              "gpu_execution_accepted": False, "quality_accepted": False,
              "release_accepted": False, "corpus_executed": False}
    started = time.perf_counter()
    try:
        pdf = _fixture(directory, font)
        report["fixture_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
        config = ExtractorConfig(mode="ocr", ocr_engine=engine, use_gpu=True,
                                 ocr_provider="onnxruntime" if engine == "rapidocr" else None,
                                 rapidocr_model_size=size if engine == "rapidocr" else None,
                                 ocr_cache_home=cache_home, language="pt-BR", num_threads=2,
                                 ocr_quality_policy="baseline", ocr_quality_variants=False)
        with ExitStack() as stack:
            # RapidOCR creates ORT sessions in its constructor. Profiling must
            # be enabled before composition, otherwise end_profiling is empty.
            predictors = _instrument(engine, directory, stack) if engine == "rapidocr" else []
            pipeline = build_ocr_pipeline(config)
            try:
                # Paddle applies its local runtime policy during composition,
                # but defers creating predictors until the first real request.
                if engine != "rapidocr":
                    predictors = _instrument(engine, directory, stack)
                with PdfTextExtractor(config, ocr_pipeline=pipeline) as extractor:
                    document = extractor.extract(pdf)
                    backend = pipeline.raw_engine
                    identity = backend.identity
                    report["identity"] = _plain({"engine": identity.engine, "runtime": identity.runtime,
                        "device": identity.device, "profile": identity.profile,
                        "package_versions": identity.package_versions,
                        "artifact_hashes": identity.artifact_hashes, "extra": identity.extra})
                    text = render_markdown(document)
                    (directory / "extracted.md").write_text(text, encoding="utf-8")
                    report["document_status"] = document.diagnostics.status.value
                    report["ocr_tokens_added"] = sum(page.diagnostics.ocr_tokens_added for page in document.pages)
                    report["evidence"] = _evidence(engine, backend, predictors)
                    report["gpu_execution_accepted"] = (report["document_status"] == "success" and
                        report["ocr_tokens_added"] > 0 and _accepted(engine, report["identity"], report["evidence"]))
                    normalized = text.casefold()
                    report["content_checks"] = {"portuguese": "ação" in normalized and "são paulo" in normalized,
                        "number": "12345" in normalized, "amount": "r$ 42,90" in normalized,
                        "identifier": "ab-009" in normalized}
                    report["synthetic_content_accepted"] = all(report["content_checks"].values())
            finally:
                pipeline.close()
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["cause"] = str(exc.__cause__) if exc.__cause__ else None
    finally:
        report["elapsed_s"] = time.perf_counter() - started
        (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("easyocr", "paddle", "rapidocr"), required=True)
    parser.add_argument("--cache-home", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rapidocr-model-size", choices=("small", "medium"), default="medium")
    parser.add_argument("--font", type=Path, default=Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    args = parser.parse_args()
    result = run(args.engine, args.output_dir.resolve(), args.cache_home.resolve(), args.font,
                 size=args.rapidocr_model_size)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0 if result["gpu_execution_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
