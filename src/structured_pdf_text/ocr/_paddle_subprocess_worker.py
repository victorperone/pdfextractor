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
    {"status": "ok"|"error", "tokens": [...], "error": null|str}
    Tokens: [{"text": str, "confidence": float, "bbox": [x0,y0,x1,y1]}, ...]
"""
from __future__ import annotations

import base64
import io
import json
import os
import platform
import sys
from pathlib import Path

# Allow running as a standalone script: add src/ to path
_SRC = Path(__file__).parent.parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Disable oneDNN on Windows (CF-1) before any paddle import
if platform.system() == "Windows":
    os.environ.setdefault("FLAGS_enable_pir_api", "0")


def _make_engine(config: dict):
    from structured_pdf_text.ocr.paddle import PaddleOcrEngine

    return PaddleOcrEngine(
        language=config.get("language", "pt"),
        num_threads=config.get("num_threads", -1),
        ocr_batch_size=config.get("ocr_batch_size", 1),
        quality_variants=config.get("quality_variants", False),
        quality_policy=config.get("quality_policy", "fast"),
        quality_thresholds=config.get("quality_thresholds", {}),
        enable_mkldnn=config.get("mkldnn", False),
    )


def _tokens_to_json(tokens) -> list[dict]:
    out = []
    for t in tokens:
        out.append(
            {
                "text": t.text,
                "confidence": float(t.confidence),
                "bbox": [t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1],
            }
        )
    return out


def main() -> None:
    # Use line-buffered stdout so each response is flushed immediately
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)

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

        method = req.get("method", "")

        try:
            if method == "init":
                init_config = req
                engine = _make_engine(req)
                _reply({"status": "ok", "tokens": [], "error": None})

            elif method == "healthcheck":
                if engine is None:
                    _reply({"status": "error", "tokens": [], "error": "not initialized"})
                else:
                    _reply({"status": "ok", "tokens": [], "error": None})

            elif method in ("recognize_page", "recognize_region"):
                if engine is None:
                    engine = _make_engine(init_config)

                image_bytes = base64.b64decode(req["image_b64"])
                from PIL import Image

                image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                page_index = int(req.get("page_index", 0))

                if method == "recognize_page":
                    tokens = engine.recognize_page(image, page_index)
                else:
                    from structured_pdf_text.geometry import BBox

                    coords = req["region_bbox"]
                    bbox = BBox(*coords)
                    tokens = engine.recognize_region(image, page_index, bbox)

                _reply({"status": "ok", "tokens": _tokens_to_json(tokens), "error": None})

            else:
                _reply({"status": "error", "tokens": [], "error": f"unknown method: {method!r}"})

        except Exception as exc:
            _reply({"status": "error", "tokens": [], "error": str(exc)})


def _reply(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
