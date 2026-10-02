#!/usr/bin/env python3
"""
Paddle OCR subprocess worker — CF-4 DLL isolation (Windows).

Spawned by PaddleOCRBackend when torch is detected in the same venv.
Runs in a clean process where PaddlePaddle DLLs load without conflicts.

Protocol: JSONL over stdin/stdout (one JSON object per line).

  Request:
    {
      "method": "init" | "recognize_page" | "recognize_region" | "healthcheck",
      "image_b64": "<base64 PNG>",        # recognize_* only
      "page_index": int,                  # recognize_* only
      "region_bbox": [x0,y0,x1,y1]|null, # recognize_region only
      "language": str,                    # init only
      "num_threads": int,                 # init only
      "ocr_batch_size": int,              # init only
      "quality_variants": bool,           # init only
      "quality_policy": str,              # init only
      "quality_thresholds": dict,         # init only
      "mkldnn": bool                      # init only
    }
    OR the literal string "QUIT" to shut down.

  Response:
    {"status": "ok"|"error", "tokens": [...], "error": null|str, "request_id": int|null}
    Tokens: [{"text": str, "confidence": float|null, "bbox": [x0,y0,x1,y1],
               "source": str, "language": str|null, "rotation": int, "provenance": str|null}, ...]
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from typing import BinaryIO

# Module-level response pipe; set by main() before any _reply() call.
_response_pipe: BinaryIO = sys.stdout.buffer

# Add src/ to path so structured_pdf_text is importable when the package
# is not installed in the venv (dev mode via sys.path in the parent script).
_SRC = Path(__file__).parent.parent.parent  # .../src/
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from structured_pdf_text.ocr.runtime_policy import (
    PaddleRuntimePolicy,
    apply_paddle_runtime_policy,
)

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
        ocr_batch_size=config.get("ocr_batch_size", 1),
        quality_variants=config.get("quality_variants", False),
        quality_policy=config.get("quality_policy", "fast"),
        quality_thresholds=quality_thresholds,
        enable_mkldnn=config.get("mkldnn", False),
    )


def _tokens_to_json(tokens) -> list[dict]:
    out = []
    for t in tokens:
        source = getattr(t, "source", None)
        out.append(
            {
                "text": t.text,
                "confidence": float(t.confidence) if t.confidence is not None else None,
                "bbox": [t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1],
                "source": source.value if hasattr(source, "value") else str(source or "ocr_page"),
                "language": getattr(t, "language", None),
                "rotation": getattr(t, "rotation", 0),
                "provenance": getattr(t, "provenance", None),
            }
        )
    return out


def main() -> None:
    global _response_pipe

    # Capture the raw stdout binary pipe before redirecting sys.stdout.
    # All JSONL responses are written directly here so that any print()
    # calls from PaddleOCR, PIL, or third-party code never contaminate
    # the framing pipe used by the parent process.
    _response_pipe = sys.stdout.buffer

    # Redirect sys.stdout → sys.stderr so print() calls from paddle / PIL
    # go to the parent's stderr (visible in logs) instead of the JSONL pipe.
    sys.stdout = io.TextIOWrapper(sys.stderr.buffer, line_buffering=True)

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
            if method == "init":
                init_config = req
                engine = _make_engine(req)
                _reply({"status": "ok", "tokens": [], "error": None}, req_id)

            elif method == "healthcheck":
                if engine is None:
                    _reply({"status": "error", "tokens": [], "error": "not initialized"}, req_id)
                else:
                    _reply({"status": "ok", "tokens": [], "error": None}, req_id)

            elif method in ("recognize_page", "recognize_region"):
                if engine is None:
                    engine = _make_engine(init_config)

                image_bytes = base64.b64decode(req["image_b64"])
                from PIL import Image

                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                page_index = int(req.get("page_index", 0))

                if method == "recognize_page":
                    tokens = engine.recognize_page(
                        image, page_index, quality_policy=req.get("quality_policy")
                    )
                else:
                    from structured_pdf_text.geometry import BBox

                    coords = req["region_bbox"]
                    bbox = BBox(*coords)
                    tokens = engine.recognize_region(image, page_index, bbox)

                _reply({"status": "ok", "tokens": _tokens_to_json(tokens), "error": None}, req_id)

            else:
                _reply({"status": "error", "tokens": [], "error": f"unknown method: {method!r}"}, req_id)

        except Exception as exc:
            _reply({"status": "error", "tokens": [], "error": str(exc)}, req_id)


def _reply(obj: dict, request_id: int | None = None) -> None:
    if request_id is not None:
        obj = {**obj, "request_id": request_id}
    _response_pipe.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
    _response_pipe.flush()


if __name__ == "__main__":
    main()
