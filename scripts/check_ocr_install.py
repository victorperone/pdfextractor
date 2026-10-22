#!/usr/bin/env python3
"""Check dependency consistency, OpenCV ownership and isolated OCR imports."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess
import sys


OPENCV_DISTRIBUTIONS = {
    "opencv-python", "opencv-python-headless",
    "opencv-contrib-python", "opencv-contrib-python-headless",
}

# Deployment profiles, not an inference dispatch table. Keep native runtimes in
# distinct processes even when inspecting the historical shared environment.
PROFILE_MODULES = {
    "tesseract": ("cv2",),
    "easyocr": ("cv2", "torch", "torchvision", "easyocr"),
    "paddle": ("cv2", "paddle", "paddleocr", "paddlex"),
    "rapidocr-onnxruntime": ("cv2", "rapidocr", "onnxruntime"),
    "rapidocr-openvino": ("cv2", "rapidocr", "openvino"),
}
PROFILE_OPENCV = {
    "tesseract": "opencv-python-headless",
    "easyocr": "opencv-python-headless",
    "paddle": "opencv-contrib-python",
    "rapidocr-onnxruntime": "opencv-python",
    "rapidocr-openvino": "opencv-python",
}


def inspect_install(configuration: str | None, *, imports: bool = False, gpu: bool = False) -> dict:
    distributions = list(metadata.distributions())
    names = {re.sub(r"[-_.]+", "-", dist.metadata.get("Name", "")).lower() for dist in distributions}
    conflicts = [list(pair) for pair in (("paddlepaddle", "paddlepaddle-gpu"),
                                       ("onnxruntime", "onnxruntime-gpu")) if set(pair) <= names]
    required_gpu = {"paddle": "paddlepaddle-gpu", "rapidocr-onnxruntime": "onnxruntime-gpu"}.get(configuration)
    runtime_valid = not conflicts and (not gpu or required_gpu is None or required_gpu in names)
    installed = sorted([
        {"name": re.sub(r"[-_.]+", "-", dist.metadata.get("Name", "")).lower(),
         "version": dist.version}
        for dist in distributions
        if re.sub(r"[-_.]+", "-", dist.metadata.get("Name", "")).lower() in OPENCV_DISTRIBUTIONS
    ], key=lambda item: (item["name"], item["version"]))
    unique = len(installed) == 1
    expected = PROFILE_OPENCV.get(configuration)
    ownership = unique and (expected is None or installed[0]["name"] == expected)
    # One cv2 wheel alone does not imply that upstream requirements are met.
    check = subprocess.run([sys.executable, "-I", "-m", "pip", "check"],
                           capture_output=True, text=True, errors="replace")
    probes = []
    if imports and ownership and runtime_valid and check.returncode == 0:
        for profile in ((configuration,) if configuration else PROFILE_MODULES):
            modules = PROFILE_MODULES[profile]
            code = "import importlib; " + "; ".join(
                f"importlib.import_module({name!r})" for name in modules)
            if gpu:
                code += "; " + {
                    "easyocr": "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print(torch.cuda.get_device_name(0))",
                    "paddle": "import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0, 'CUDA is unavailable'",
                    "rapidocr-onnxruntime": "import onnxruntime as ort; ort.preload_dlls(directory=''); assert 'CUDAExecutionProvider' in ort.get_available_providers(), 'CUDA provider is unavailable'",
                }[profile]
            result = subprocess.run([sys.executable, "-I", "-c", code],
                                    capture_output=True, text=True, errors="replace")
            probes.append({"configuration": profile, "modules": list(modules),
                           "returncode": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr})
    return {
        "configuration": configuration, "python": sys.executable,
        "opencv_distributions": installed, "expected_opencv": expected,
        "opencv_ownership_valid": ownership,
        "gpu_requested": gpu, "runtime_namespace_conflicts": conflicts,
        "runtime_distributions_valid": runtime_valid,
        "pip_check": {"returncode": check.returncode, "stdout": check.stdout, "stderr": check.stderr},
        "imports_requested": imports, "import_probes": probes,
        "passed": ownership and runtime_valid and check.returncode == 0 and (
            not imports or bool(probes) and all(p["returncode"] == 0 for p in probes)),
        "models_checked": False, "quality_accepted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", choices=tuple(PROFILE_MODULES))
    parser.add_argument("--gpu", action="store_true", help="Check GPU distributions and CUDA availability for the selected profile")
    parser.add_argument("--imports", action="store_true", help="Import each selected profile in its own process")
    parser.add_argument("--report", type=Path, help="Save a JSON report without replacing an existing file")
    args = parser.parse_args()
    if args.gpu and args.configuration not in {"easyocr", "paddle", "rapidocr-onnxruntime"}:
        parser.error("--gpu requires an EasyOCR, Paddle or RapidOCR/ONNX configuration")
    # Reserve the report before starting probes, including for failing installs.
    report_file = args.report.open("x", encoding="utf-8") if args.report else None
    try:
        report = inspect_install(args.configuration, imports=args.imports, gpu=args.gpu)
        encoded = json.dumps(report, indent=2, ensure_ascii=False)
        if report_file:
            report_file.write(encoded + "\n")
        print(encoded)
        return 0 if report["passed"] else 1
    finally:
        if report_file:
            report_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
