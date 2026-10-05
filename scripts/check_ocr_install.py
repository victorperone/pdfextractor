#!/usr/bin/env python3
"""Verify the all-OCR Python environment has one OpenCV wheel and imports."""
from __future__ import annotations

import argparse
from importlib import metadata
import sys


OPENCV_DISTRIBUTIONS = {
    "opencv-python", "opencv-python-headless",
    "opencv-contrib-python", "opencv-contrib-python-headless",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--imports", action="store_true", help="Import every configured OCR package/provider")
    args = parser.parse_args()
    installed = sorted(
        dist.metadata["Name"]
        for dist in metadata.distributions()
        if (dist.metadata.get("Name") or "").casefold() in OPENCV_DISTRIBUTIONS
    )
    if len(installed) != 1:
        print(f"Expected exactly one OpenCV distribution, found {installed or 'none'}", file=sys.stderr)
        return 1
    print(f"OpenCV distribution: {installed[0]} ({metadata.version(installed[0])})")
    if args.imports:
        modules = (
            "cv2", "paddle", "paddleocr", "rapidocr", "onnxruntime",
            "openvino", "easyocr", "torch", "torchvision",
        )
        for name in modules:
            try:
                __import__(name)
            except Exception as exc:
                print(f"Import failed: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"Import ready: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
