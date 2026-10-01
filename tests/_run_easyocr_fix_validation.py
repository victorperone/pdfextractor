"""Standalone validation runner for EasyOCR fix tests.

Run with: python3 tests/_run_easyocr_fix_validation.py
Requires only: numpy (no pytest, no package install needed).
"""
from __future__ import annotations

import os
import sys
import types
import importlib
import importlib.util
from typing import Any
from unittest.mock import MagicMock, patch

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


# ---------------------------------------------------------------------------
# Stub out all package-level imports so easyocr.py can be loaded standalone
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__getattr__ = lambda attr: MagicMock()  # type: ignore[method-assign]
    return m


_STUBS = [
    "structured_pdf_text",
    "structured_pdf_text.document",
    "structured_pdf_text.geometry",
    "structured_pdf_text.ocr",
    "structured_pdf_text.ocr.backends",
    "structured_pdf_text.ocr.backends._parser_utils",
    "structured_pdf_text.ocr.contracts",
    "structured_pdf_text.config",
]
for _s in _STUBS:
    if _s not in sys.modules:
        sys.modules[_s] = _make_stub(_s)

_doc_mod = sys.modules["structured_pdf_text.document"]
_doc_mod.OcrToken = MagicMock  # type: ignore[attr-defined]
_doc_mod.SourceKind = MagicMock  # type: ignore[attr-defined]
_geo_mod = sys.modules["structured_pdf_text.geometry"]
_geo_mod.BBox = MagicMock  # type: ignore[attr-defined]
_parser_mod = sys.modules["structured_pdf_text.ocr.backends._parser_utils"]
_parser_mod.finite_confidence = MagicMock  # type: ignore[attr-defined]
_parser_mod.quadrilateral_geometry = MagicMock  # type: ignore[attr-defined]
_contracts_mod = sys.modules["structured_pdf_text.ocr.contracts"]
for _attr in ("OCRBackendIdentity", "OCRCapabilities", "OCRRequest", "OCRResult", "OCRToken",
              "UnsupportedOCREngine"):
    setattr(_contracts_mod, _attr, MagicMock)

_BACKEND_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "src", "structured_pdf_text", "ocr", "backends", "easyocr.py",
)
spec = importlib.util.spec_from_file_location("easyocr_backend", _BACKEND_PATH)
assert spec is not None and spec.loader is not None
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)  # type: ignore[attr-defined]
_run_easyocr = _mod._run_easyocr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reader(*, detect_raises: bool = False, recognize_raises: bool = False) -> MagicMock:
    reader = MagicMock()
    if detect_raises:
        reader.detect.side_effect = RuntimeError("detect failed")
    else:
        reader.detect.return_value = ([[10, 90, 10, 90]], [[]])
    if recognize_raises:
        reader.recognize.side_effect = RuntimeError("recognize failed")
    else:
        reader.recognize.return_value = [
            ([[10, 10], [90, 10], [90, 30], [10, 30]], "Hello World", 0.95)
        ]
    reader.readtext.return_value = [
        ([[10, 10], [90, 10], [90, 30], [10, 30]], "Hello World", 0.95)
    ]
    return reader


def _img(h: int = 100, w: int = 120) -> Any:
    import numpy as np
    return np.full((h, w, 3), 128, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def run(name: str):
    def decorator(fn):
        try:
            fn()
            _results.append((name, True, ""))
            print(f"  {PASS}  {name}")
        except Exception as exc:
            _results.append((name, False, str(exc)))
            print(f"  {FAIL}  {name}")
            print(f"         {exc}")
        return fn
    return decorator


print("\n=== EasyOCR fix validation ===\n")
print("FIX 1: reader.recognize() must receive 3-channel (H,W,3) image\n")


@run("recognize() gets 3-channel array (not 2D grayscale)")
def _():
    import numpy as np
    reader = _make_reader()
    _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    img_arg = reader.recognize.call_args[0][0]
    assert isinstance(img_arg, np.ndarray), f"expected ndarray, got {type(img_arg)}"
    assert img_arg.ndim == 3, f"BUG: ndim={img_arg.ndim} (should be 3, not 2)"
    assert img_arg.shape[2] == 3, f"BUG: shape={img_arg.shape} (last dim must be 3)"


@run("recognize() image shape matches original input shape")
def _():
    import numpy as np
    img = _img(h=200, w=150)
    reader = _make_reader()
    _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    img_arg = reader.recognize.call_args[0][0]
    assert img_arg.shape == img.shape, (
        f"BUG: shape changed from {img.shape} to {img_arg.shape} "
        "(grayscale conversion must NOT happen)"
    )


@run("fallback readtext() when detect fails — still 3-channel")
def _():
    import numpy as np
    reader = _make_reader(detect_raises=True)
    _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    img_arg = reader.readtext.call_args[0][0]
    assert img_arg.ndim == 3, f"BUG: readtext fallback got ndim={img_arg.ndim}"


@run("fallback readtext() when recognize fails — still 3-channel")
def _():
    import numpy as np
    reader = _make_reader(recognize_raises=True)
    _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    img_arg = reader.readtext.call_args[0][0]
    assert img_arg.ndim == 3, f"BUG: recognize-fail readtext got ndim={img_arg.ndim}"


print("\nFIX 2: adjust_contrast must be 0.5 (was 1.0)\n")


@run("adjust_contrast=0.5 is forwarded to reader.recognize()")
def _():
    reader = _make_reader()
    _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    kw = reader.recognize.call_args[1]
    assert kw.get("adjust_contrast") == 0.5, (
        f"BUG: adjust_contrast={kw.get('adjust_contrast')} (expected 0.5)"
    )


@run("adjust_contrast=1.0 is the old broken value — must NOT appear as default")
def _():
    reader = _make_reader()
    _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                 allowlist=None, blocklist=None, workers=0)
    kw = reader.recognize.call_args[1]
    assert kw.get("adjust_contrast") != 1.0, (
        "BUG: adjust_contrast=1.0 (old aggressive value) is still the default"
    )


@run("EASYOCR_ADJUST_CONTRAST env var is read by __init__")
def _():
    with patch.dict(os.environ, {"EASYOCR_ADJUST_CONTRAST": "0.3"}):
        value = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
    assert value == 0.3, f"env var not read, got {value}"


@run("EASYOCR_ADJUST_CONTRAST absent → default 0.5")
def _():
    env = {k: v for k, v in os.environ.items() if k != "EASYOCR_ADJUST_CONTRAST"}
    with patch.dict(os.environ, env, clear=True):
        value = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
    assert value == 0.5, f"default should be 0.5, got {value}"


print("\nFIX 3: mag_ratio must be 1.2 (was 1.5)\n")


@run("detect() called with mag_ratio=1.2 (default, no env var)")
def _():
    env = {k: v for k, v in os.environ.items() if k != "EASYOCR_MAG_RATIO"}
    with patch.dict(os.environ, env, clear=True):
        reader = _make_reader()
        _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)
    kw = reader.detect.call_args[1]
    assert kw.get("mag_ratio") == 1.2, (
        f"BUG: mag_ratio={kw.get('mag_ratio')} (expected 1.2)"
    )


@run("mag_ratio=1.5 must NOT be the default (old over-magnification)")
def _():
    env = {k: v for k, v in os.environ.items() if k != "EASYOCR_MAG_RATIO"}
    with patch.dict(os.environ, env, clear=True):
        reader = _make_reader()
        _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)
    kw = reader.detect.call_args[1]
    assert kw.get("mag_ratio") != 1.5, (
        "BUG: mag_ratio=1.5 is the old value and must no longer be the default"
    )


@run("EASYOCR_MAG_RATIO=1.8 propagates to reader.detect()")
def _():
    with patch.dict(os.environ, {"EASYOCR_MAG_RATIO": "1.8"}):
        reader = _make_reader()
        _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)
    kw = reader.detect.call_args[1]
    assert kw.get("mag_ratio") == 1.8, (
        f"env override not respected, got mag_ratio={kw.get('mag_ratio')}"
    )


@run("mag_ratio consistent in readtext() fallback when detect raises")
def _():
    with patch.dict(os.environ, {"EASYOCR_MAG_RATIO": "1.3"}):
        reader = _make_reader(detect_raises=True)
        _run_easyocr(reader, _img(), beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)
    kw = reader.readtext.call_args[1]
    assert kw.get("mag_ratio") == 1.3, (
        f"BUG: readtext fallback got mag_ratio={kw.get('mag_ratio')} instead of 1.3"
    )


print("\nINTEGRATION: all fixes + golden-path\n")


@run("golden path: detect+recognize succeed, all corrected defaults applied")
def _():
    import numpy as np
    env = {k: v for k, v in os.environ.items()
           if k not in ("EASYOCR_MAG_RATIO", "EASYOCR_ADJUST_CONTRAST")}
    with patch.dict(os.environ, env, clear=True):
        reader = _make_reader()
        img = _img(h=200, w=150)
        result = _run_easyocr(reader, img, beamwidth=10, adjust_contrast=0.5,
                              allowlist=None, blocklist=None, workers=0)

    # Fix 1
    rec_img = reader.recognize.call_args[0][0]
    assert rec_img.ndim == 3 and rec_img.shape[2] == 3, "Fix 1 failed in integration"
    # Fix 2
    assert reader.recognize.call_args[1]["adjust_contrast"] == 0.5, "Fix 2 failed"
    # Fix 3
    assert reader.detect.call_args[1]["mag_ratio"] == 1.2, "Fix 3 failed"
    # canvas_size
    assert reader.detect.call_args[1]["canvas_size"] == 200, "canvas_size should be max(h,w)"
    # result
    assert isinstance(result, list), "result must be a list"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

passed = sum(1 for _, ok, _ in _results if ok)
failed = sum(1 for _, ok, _ in _results if not ok)
total = len(_results)

print(f"\n{'='*40}")
print(f"Results: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED)")
    sys.exit(1)
else:
    print("  — all OK")
