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


# Each entry: (display_label, engine, provider, cli_key)
# cli_key must match the argparse choices below; used in re-exec and --configuration filter.
CONFIGURATIONS = (
    ("paddle", "paddle", None, "paddle"),
    ("rapidocr [onnxruntime]", "rapidocr", "onnxruntime", "rapidocr-onnxruntime"),
    ("rapidocr [openvino]", "rapidocr", "openvino", "rapidocr-openvino"),
    ("easyocr", "easyocr", None, "easyocr"),
    ("tesseract", "tesseract", None, "tesseract"),
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
      - CRAFT + recognizer baseline (probe_deep with plain EasyOCR config, no DBNet requirement)
      - Full exhaustive pipeline via recognize_page(..., quality_policy="exhaustive")
      - Direct recognition path via recognize_direct()
      - Candidate planner diagnostics via consume_page_diagnostics()
      - DBNet18 weights present on disk
      - DBNet18 runtime functional
      - multiple_detectors capability flag

    Returns a dict with keys:
      baseline_ready, baseline_reason,
      exhaustive_planner_ready, exhaustive_planner_reason,
      direct_recognition_ready, direct_recognition_reason,
      dbnet_weights_available, dbnet_runtime_available, dbnet_failure_reason,
      multiple_detectors, max_quality_status, max_quality_reason
    """
    from PIL import Image, ImageDraw

    from structured_pdf_text.config import ExtractorConfig, ExtractionMode, max_quality_extraction_config
    from structured_pdf_text.ocr.factory import build_ocr_backend
    from structured_pdf_text.ocr.readiness import _load_smoke_font

    report: dict[str, Any] = {
        "baseline_ready": False,
        "baseline_reason": None,
        "exhaustive_planner_ready": False,
        "exhaustive_planner_reason": None,
        "direct_recognition_ready": False,
        "direct_recognition_reason": None,
        "dbnet_weights_available": False,
        "dbnet_runtime_available": False,
        "dbnet_failure_reason": None,
        "multiple_detectors": False,
        "max_quality_status": "unknown",
        "max_quality_reason": None,
    }

    # Step 1: baseline deep smoke using plain EasyOCR config (no DBNet requirement).
    # Must NOT use max_quality_extraction_config() here — probe_static for easyocr
    # returns DEGRADED when max_quality=True and DBNet weights are absent, which would
    # make the baseline fail even when CRAFT + recognizer are fully functional.
    try:
        baseline_config = ExtractorConfig(
            mode=ExtractionMode.OCR,
            language=language,
            ocr_engine="easyocr",
        )
        baseline = probe_deep(baseline_config)
        report["baseline_ready"] = baseline.status == ReadinessStatus.READY
        report["baseline_reason"] = baseline.reason_code
    except Exception as exc:
        report["baseline_reason"] = f"probe_error: {type(exc).__name__}: {exc}"
        report["max_quality_status"] = "unknown"
        report["max_quality_reason"] = "baseline_probe_failed"
        return report

    if not report["baseline_ready"]:
        report["max_quality_status"] = baseline.status.value
        report["max_quality_reason"] = baseline.reason_code or "baseline_not_ready"
        return report

    # Build a smoke image large enough for CRAFT to detect text at font size 42.
    # Use _load_smoke_font to get the same deterministic font used by deep probes.
    smoke_image = Image.new("RGB", (640, 120), "white")
    draw = ImageDraw.Draw(smoke_image)
    _smoke_font = _load_smoke_font(42)
    draw.text((10, 10), "Texto de teste OCR 1234", fill="black", font=_smoke_font)

    # Use the full smoke image as the crop for recognize_direct.
    smoke_crop = smoke_image

    # Step 2: build max-quality backend and run real exhaustive pipeline + direct path.
    backend = None
    try:
        mq_config = max_quality_extraction_config(language=language)
        backend = build_ocr_backend(mq_config)

        # 2a: run recognize_page with exhaustive quality policy
        try:
            tokens = backend.recognize_page(smoke_image, 0, quality_policy="exhaustive")
            if not tokens:
                report["exhaustive_planner_reason"] = "exhaustive_planner_returned_no_tokens"
            else:
                report["exhaustive_planner_ready"] = True
        except Exception as exc:
            report["exhaustive_planner_reason"] = f"{type(exc).__name__}: {exc}"

        # 2b: collect candidate planner diagnostics
        diag = backend.consume_page_diagnostics()
        report["dbnet_weights_available"] = bool(diag.get("dbnet_weights_available"))
        report["dbnet_runtime_available"] = bool(diag.get("dbnet_runtime_available"))
        report["dbnet_failure_reason"] = diag.get("dbnet_failure_reason")

        # 2c: run recognize_direct
        try:
            direct_tokens = backend.recognize_direct(smoke_crop, 0)
            if not direct_tokens:
                report["direct_recognition_reason"] = "direct_recognition_returned_no_tokens"
            else:
                report["direct_recognition_ready"] = True
        except Exception as exc:
            report["direct_recognition_reason"] = f"{type(exc).__name__}: {exc}"

        # 2d: read capability flag (no probe triggered here, just reads cached state)
        report["multiple_detectors"] = backend.capabilities.multiple_detectors

    except Exception as exc:
        report["exhaustive_planner_reason"] = (
            report["exhaustive_planner_reason"] or f"backend_error: {type(exc).__name__}: {exc}"
        )
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    # Step 3: determine overall max-quality status.
    # Mandatory: baseline + exhaustive planner + direct recognition.
    # DBNet18: optional — INCOMPLETE when baseline passes but DBNet unavailable.
    if not report["exhaustive_planner_ready"]:
        report["max_quality_status"] = "incomplete"
        report["max_quality_reason"] = report["exhaustive_planner_reason"] or "exhaustive_planner_failed"
    elif not report["direct_recognition_ready"]:
        report["max_quality_status"] = "incomplete"
        report["max_quality_reason"] = report["direct_recognition_reason"] or "direct_recognition_failed"
    elif report["dbnet_runtime_available"]:
        report["max_quality_status"] = "ready"
        report["max_quality_reason"] = None
    else:
        # CRAFT and direct path both work — max-quality extraction runs, but without
        # DBNet18 as an additional candidate.
        report["max_quality_status"] = "incomplete"
        if report["dbnet_weights_available"]:
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

    if args.max_quality and args.configuration is not None and args.configuration != "easyocr":
        print(
            f"Error: --max-quality is only valid for EasyOCR, not --configuration {args.configuration!r}",
            file=sys.stderr,
        )
        return 2

    if args.max_quality:
        # Max-quality mode: run only the EasyOCR max-quality check.
        # Other engines are not subject to the exhaustive candidate requirement.
        print(f"\nMax-quality EasyOCR preflight  (language={args.language})")
        print("-" * 60)
        mq = _check_max_quality_easyocr(args.language)

        _yes_no = lambda v: "yes" if v else "no"
        _ok_fail = lambda v: "OK" if v else "FAIL"
        print(f"  Baseline (CRAFT + recognizer): {_ok_fail(mq['baseline_ready'])}"
              + (f"  [{mq['baseline_reason']}]" if mq["baseline_reason"] else ""))
        print(f"  Exhaustive planner:            {_ok_fail(mq['exhaustive_planner_ready'])}"
              + (f"  [{mq['exhaustive_planner_reason']}]" if mq["exhaustive_planner_reason"] else ""))
        print(f"  Direct recognition:            {_ok_fail(mq['direct_recognition_ready'])}"
              + (f"  [{mq['direct_recognition_reason']}]" if mq["direct_recognition_reason"] else ""))
        print(f"  DBNet18 weights on disk:       {_yes_no(mq['dbnet_weights_available'])}")
        print(f"  DBNet18 runtime functional:    {_yes_no(mq['dbnet_runtime_available'])}"
              + (f"  [{mq['dbnet_failure_reason']}]" if mq["dbnet_failure_reason"] else ""))
        print(f"  multiple_detectors capability: {mq['multiple_detectors']}")
        print()

        mq_status = mq["max_quality_status"]
        mq_reason = mq["max_quality_reason"] or ""
        if mq_status == "ready":
            print("Max-quality EasyOCR: READY")
            return 0
        else:
            print(f"Max-quality EasyOCR: {mq_status.upper()}"
                  + (f"  [{mq_reason}]" if mq_reason else ""))
            if mq["baseline_ready"] and mq["exhaustive_planner_ready"]:
                print("  Note: baseline EasyOCR (CRAFT) and exhaustive planner are READY — "
                      "extraction will work but the full candidate set is not available.")
            return 1

    if args.deep_smoke and args.configuration is None:
        # Paddle and EasyOCR load different native runtimes (Paddle and
        # PyTorch). Keeping every smoke in one Python process can corrupt
        # runtime state or segfault after a valid Paddle result. Re-exec each
        # profile so the check matches the benchmark's process isolation.
        all_ready = True
        script = str(Path(__file__).resolve())
        for _label, _, _, _cli_key in CONFIGURATIONS:
            result = subprocess.run(
                [sys.executable, script, "--language", args.language,
                 "--deep-smoke", "--configuration", _cli_key],
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
            if item[3] == args.configuration
        )

    name_width = max(len(label) for label, _, _, _ in configurations) + 2
    print(f"\n{'Configuration':<{name_width}}  {'Status':<12}  Reason / details")
    print("-" * 100)
    all_ready = True
    for label, engine, provider, _ in configurations:
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
