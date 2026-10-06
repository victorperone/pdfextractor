#!/usr/bin/env python3
"""Check static OCR readiness or run deep pt-BR smoke tests.

Static mode inspects packages, executables, model files and language data
without constructing heavy inference runtimes. Pass ``--deep-smoke`` to load
each deployment configuration and OCR a generated pt-BR sample.

Pass ``--max-quality`` (requires ``--deep-smoke`` and ``--configuration easyocr``)
to verify the full exhaustive candidate set, including DBNet18 runtime availability.
When CRAFT and recognizer pass but DBNet18 is unavailable the result is reported as
INCOMPLETE/degraded rather than silently claiming max-quality is fully ready.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from structured_pdf_text.config import ExtractorConfig, ExtractionMode
from structured_pdf_text.ocr.readiness import ReadinessResult, ReadinessStatus, probe_deep, probe_static


CONFIGURATIONS = (
    ("paddle", "paddle", None),
    ("rapidocr [onnxruntime]", "rapidocr", "onnxruntime"),
    ("rapidocr [openvino]", "rapidocr", "openvino"),
    ("easyocr", "easyocr", None),
    ("tesseract", "tesseract", None),
)


def _check(engine: str, provider: str | None, language: str, deep: bool) -> ReadinessResult:
    try:
        config = ExtractorConfig(
            mode=ExtractionMode.OCR,
            language=language,
            ocr_engine=engine,
            ocr_provider=provider,
        )
        return probe_deep(config) if deep else probe_static(config)
    except Exception as exc:
        return ReadinessResult(ReadinessStatus.UNKNOWN, "configuration_error", {"message": str(exc)})


def _check_max_quality_easyocr(language: str) -> dict[str, Any]:
    """Run a max-quality preflight for EasyOCR.

    Verifies:
      - CRAFT + recognizer runtime (via existing probe_deep)
      - DBNet18 weights present on disk
      - DBNet18 runtime functional (via _probe_dbnet18_runtime)
      - multiple_detectors capability flag

    Returns a dict with keys:
      baseline_status, baseline_reason, dbnet_weights, dbnet_runtime,
      dbnet_failure_reason, multiple_detectors, max_quality_status,
      max_quality_reason
    """
    from structured_pdf_text.config import max_quality_extraction_config
    from structured_pdf_text.ocr.factory import build_ocr_backend

    report: dict[str, Any] = {
        "baseline_status": "unknown",
        "baseline_reason": None,
        "dbnet_weights": False,
        "dbnet_runtime": False,
        "dbnet_failure_reason": None,
        "multiple_detectors": False,
        "max_quality_status": "unknown",
        "max_quality_reason": None,
    }

    # Step 1: baseline deep smoke (CRAFT + recognizer)
    try:
        config = max_quality_extraction_config(language=language)
        baseline = probe_deep(config)
        report["baseline_status"] = baseline.status.value
        report["baseline_reason"] = baseline.reason_code
    except Exception as exc:
        report["baseline_status"] = "unknown"
        report["baseline_reason"] = f"probe_error: {exc}"
        report["max_quality_status"] = "unknown"
        report["max_quality_reason"] = "baseline_probe_failed"
        return report

    if baseline.status != ReadinessStatus.READY:
        report["max_quality_status"] = baseline.status.value
        report["max_quality_reason"] = baseline.reason_code or "baseline_not_ready"
        return report

    # Step 2: DBNet18 runtime probe — reuse cached result
    backend = None
    try:
        backend = build_ocr_backend(config)
        from structured_pdf_text.ocr.backends.easyocr import (
            _dbnet18_weights_available,
            _probe_dbnet18_runtime,
            _model_cache_dir,
        )
        cache_dir = _model_cache_dir()
        report["dbnet_weights"] = _dbnet18_weights_available(cache_dir)
        dbnet_ok, dbnet_fail = _probe_dbnet18_runtime(
            getattr(backend, "_reader", None), cache_dir
        )
        report["dbnet_runtime"] = dbnet_ok
        report["dbnet_failure_reason"] = dbnet_fail
        report["multiple_detectors"] = backend.capabilities.multiple_detectors
    except Exception as exc:
        report["dbnet_failure_reason"] = f"probe_error: {type(exc).__name__}: {exc}"
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    # Step 3: determine max-quality overall status
    if report["dbnet_runtime"]:
        report["max_quality_status"] = "ready"
        report["max_quality_reason"] = None
    else:
        # CRAFT is fine but DBNet18 is unavailable — degraded, not broken
        report["max_quality_status"] = "incomplete"
        if report["dbnet_weights"]:
            report["max_quality_reason"] = "dbnet18_runtime_unavailable"
        else:
            report["max_quality_reason"] = "dbnet18_weights_missing"

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--deep-smoke", action="store_true", help="Load each backend and OCR a generated pt-BR sample")
    parser.add_argument(
        "--max-quality",
        action="store_true",
        help=(
            "Verify the full max-quality EasyOCR candidate set including DBNet18 runtime. "
            "Requires --deep-smoke and --configuration easyocr (or no --configuration). "
            "Reports INCOMPLETE when CRAFT is ready but DBNet18 is unavailable."
        ),
    )
    parser.add_argument(
        "--configuration",
        choices=("paddle", "rapidocr-onnxruntime", "rapidocr-openvino", "easyocr", "tesseract"),
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.max_quality and not args.deep_smoke:
        print("Error: --max-quality requires --deep-smoke", file=sys.stderr)
        return 2

    if args.max_quality:
        # Max-quality mode: run only the EasyOCR max-quality check
        # (other engines are not subject to the exhaustive candidate requirement)
        print(f"\nMax-quality EasyOCR preflight  (language={args.language})")
        print("-" * 60)
        mq = _check_max_quality_easyocr(args.language)

        print(f"  Baseline (CRAFT + recognizer): {mq['baseline_status']}"
              + (f"  [{mq['baseline_reason']}]" if mq["baseline_reason"] else ""))
        print(f"  DBNet18 weights on disk:       {'yes' if mq['dbnet_weights'] else 'no'}")
        print(f"  DBNet18 runtime functional:    {'yes' if mq['dbnet_runtime'] else 'no'}"
              + (f"  [{mq['dbnet_failure_reason']}]" if mq["dbnet_failure_reason"] else ""))
        print(f"  multiple_detectors capability: {mq['multiple_detectors']}")
        print()

        mq_status = mq["max_quality_status"]
        mq_reason = mq["max_quality_reason"] or ""
        if mq_status == "ready":
            print(f"Max-quality EasyOCR: READY")
            return 0
        else:
            print(f"Max-quality EasyOCR: {mq_status.upper()}"
                  + (f"  [{mq_reason}]" if mq_reason else ""))
            if mq["baseline_status"] == "ready":
                print("  Note: baseline EasyOCR (CRAFT) is READY — extraction will work "
                      "but the full exhaustive candidate set is not available.")
            return 1

    if args.deep_smoke and args.configuration is None:
        # Paddle and EasyOCR load different native runtimes (Paddle and
        # PyTorch). Keeping every smoke in one Python process can corrupt
        # runtime state or segfault after a valid Paddle result. Re-exec each
        # profile so the check matches the benchmark's process isolation.
        all_ready = True
        script = str(Path(__file__).resolve())
        for name, _, _ in CONFIGURATIONS:
            result = subprocess.run(
                [sys.executable, script, "--language", args.language,
                 "--deep-smoke", "--configuration", name],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            if result.returncode:
                all_ready = False
        if all_ready:
            print("\nAll selected OCR configurations: READY")
            return 0
        print("\nOne or more OCR configurations are not ready.")
        return 1

    configurations = CONFIGURATIONS
    if args.configuration:
        configurations = tuple(
            item for item in CONFIGURATIONS
            if item[0].replace(" [onnxruntime]", "").replace(" [openvino]", "").replace(" ", "-")
            == args.configuration
        )

    name_width = max(len(label) for label, _, _ in configurations) + 2
    print(f"\n{'Configuration':<{name_width}}  {'Status':<12}  Reason / details")
    print("-" * 100)
    all_ready = True
    for label, engine, provider in configurations:
        result = _check(engine, provider, args.language, args.deep_smoke)
        if result.status.value != "ready":
            all_ready = False
        extra = result.reason_code or ""
        if result.details:
            extra = (extra + " " if extra else "") + str(result.details)
        print(f"{'[ok]' if result.status.value == 'ready' else '[!!]'}  {label:<{name_width}}  {result.status.value:<12}  {extra}")

    if all_ready:
        print("\nAll selected OCR configurations: READY")
        return 0
    print("\nOne or more OCR configurations are not ready.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
