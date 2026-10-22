#!/usr/bin/env python3
"""
Paddle OCR subprocess worker — CF-4 DLL isolation (Windows).

Spawned by PaddleOCRBackend when torch is detected in the same venv.
Runs in a clean process where PaddlePaddle DLLs load without conflicts.

Protocol: JSONL version 5 over stdin/stdout (one JSON object per line).

  Request:
    {
      "method": "init" | "recognize_page" | "recognize_region" | "healthcheck",
      "image_path": "<temporary PNG path>", # recognize_* only
      "page_index": int,                  # recognize_* only
      "region_bbox": [x0,y0,x1,y1]|null, # recognize_region only
      "language": str,                    # init only
      "num_threads": int,                 # init only
      "ocr_batch_size": int,              # init only
      "quality_variants": bool,           # init only
      "quality_policy": str,              # init only
      "quality_thresholds": dict,         # init only
      "mkldnn": bool,                     # init only
      "disable_pir_api": bool             # init only
    }
    OR the literal string "QUIT" to shut down.

  Response:
    {"status": "ok"|"error", "tokens": [...], "error": null|str, "request_id": int|null}
    Tokens: [{"text": str, "confidence": float|null, "bbox": [x0,y0,x1,y1],
               "source": str, "language": str|null, "rotation": int, "provenance": str|null}, ...]
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import BinaryIO

# Module-level response pipe; set by main() before any _reply() call.
_response_pipe: BinaryIO = sys.stdout.buffer


def _redirect_protocol_stdout() -> None:
    """Reserve stdout for protocol messages and route fd 1 noise to stderr."""
    global _response_pipe
    stdout_fd = sys.stdout.fileno()
    response_fd = os.dup(stdout_fd)
    _response_pipe = os.fdopen(response_fd, "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), stdout_fd)
    sys.stdout = io.TextIOWrapper(
        os.fdopen(os.dup(sys.stderr.fileno()), "wb", buffering=0),
        line_buffering=True,
    )

# Add src/ to path so structured_pdf_text is importable when the package
# is not installed in the venv (dev mode via sys.path in the parent script).
_SRC = Path(__file__).parent.parent.parent  # .../src/
# A worker launched by filename puts ocr/ first on sys.path. Its compatibility
# paddle.py must never shadow the third-party paddle package in that process.
_WORKER_DIR = Path(__file__).resolve().parent
sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != _WORKER_DIR]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.ocr.runtime_policy import (
    PaddleRuntimePolicy,
    apply_paddle_runtime_policy,
)
from structured_pdf_text.ocr.pipelines.paddle.wire import PROTOCOL_VERSION, serialize_tokens
from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector

# Apply the parent's resolved policy before importing Paddle/PaddleOCR.


def _make_engine(config: dict):
    apply_paddle_runtime_policy(
        PaddleRuntimePolicy(
            enable_mkldnn=bool(config.get("mkldnn", False)),
            disable_pir_api=bool(config.get("disable_pir_api", False)),
            reason="resolved by parent process",
        )
    )
    from structured_pdf_text.ocr.paddle import PaddleOcrEngine
    from structured_pdf_text.config import OcrQualityThresholds

    qt_dict = config.get("quality_thresholds") or {}
    quality_thresholds = OcrQualityThresholds(**qt_dict) if qt_dict else OcrQualityThresholds()

    return PaddleOcrEngine(
        language=config.get("language", "pt"),
        num_threads=config.get("num_threads", -1),
        model_profile=config.get("model_profile", "pt"),
        ocr_batch_size=config.get("ocr_batch_size", 1),
        quality_variants=config.get("quality_variants", False),
        quality_policy=config.get("quality_policy", "fast"),
        quality_thresholds=quality_thresholds,
        enable_mkldnn=config.get("mkldnn", False),
        cache_home=config.get("cache_home"),
        use_gpu=config.get("use_gpu", False),
    )


def _tokens_to_json(tokens) -> list[dict]:
    return serialize_tokens(tokens)


def main() -> None:
    _redirect_protocol_stdout()

    engine = None
    init_config: dict = {}

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line or line == "QUIT":
            break

        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply({"status": "error", "tokens": [], "error": f"bad JSON: {exc}"})
            continue

        # Echo request_id so the parent can verify response routing.
        req_id: int | None = req.get("request_id")
        method = req.get("method", "")

        try:
            protocol_version = req.get("protocol_version")
            if protocol_version != PROTOCOL_VERSION:
                raise ValueError(f"unsupported protocol_version: {protocol_version!r}")
            if method == "init":
                init_config = req
                engine = _make_engine(req)
                _reply({"status": "ok", "tokens": [], "error": None,
                        "protocol_version": PROTOCOL_VERSION, "execution": _execution(engine)}, req_id)

            elif method == "healthcheck":
                if engine is None:
                    _reply({"status": "error", "tokens": [], "error": "not initialized"}, req_id)
                else:
                    _reply({"status": "ok", "tokens": [], "error": None}, req_id)

            elif method in ("recognize_page", "recognize_region"):
                if engine is None:
                    engine = _make_engine(init_config)

                from PIL import Image
                image = Image.open(req["image_path"]).convert("RGB")
                page_index = int(req.get("page_index", 0))

                if method == "recognize_page":
                    from structured_pdf_text.geometry import BBox

                    raw_bbox = req.get("page_bbox")
                    page_bbox = BBox(*raw_bbox) if raw_bbox is not None else None
                    tokens = engine.recognize_page(
                        image, page_index, page_bbox,
                        quality_variants=req.get("quality_variants"),
                        quality_policy=req.get("quality_policy"),
                        page_rotation=req.get("page_rotation", 0),
                    )
                else:
                    from structured_pdf_text.geometry import BBox

                    coords = req["region_bbox"]
                    bbox = BBox(*coords)
                    tokens = engine.recognize_region(image, page_index, bbox)

                _reply({"status": "ok", "tokens": _tokens_to_json(tokens), "error": None,
                        "diagnostics": LegacyDiagnosticsProjector.snapshot_attributes(engine),
                        "execution": _execution(engine)}, req_id)

            else:
                _reply({"status": "error", "tokens": [], "error": f"unknown method: {method!r}"}, req_id)

        except Exception as exc:
            import errno
            from structured_pdf_text.errors import FatalExtractionError
            fatal = isinstance(exc, FatalExtractionError)
            _reply({
                "status": "error", "tokens": [], "error": str(exc),
                "error_type": type(exc).__name__,
                "errno": exc.errno if isinstance(exc, OSError) else None,
                "fatal": fatal or isinstance(exc, MemoryError) or (isinstance(exc, OSError) and exc.errno == errno.ENOMEM),
                "fatal_code": getattr(exc, "code", None) if fatal else None,
                "stage": getattr(exc, "stage", None) or method,
                "page_index": getattr(exc, "page_index", None),
                "details": getattr(exc, "details", {}) if fatal else {},
                "diagnostics": LegacyDiagnosticsProjector.snapshot_attributes(engine) if engine is not None else {},
                "execution": _execution(engine),
            }, req_id)


def _execution(engine) -> dict:
    return {"device": getattr(engine, "effective_device", "unknown"),
            "gpu_fallback_reason": getattr(engine, "gpu_fallback_reason", None)}


def _reply(obj: dict, request_id: int | None = None) -> None:
    if request_id is not None:
        obj = {**obj, "request_id": request_id}
    _response_pipe.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
    _response_pipe.flush()


if __name__ == "__main__":
    main()
