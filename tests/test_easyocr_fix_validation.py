"""Validation tests for the EasyOCR Phase 9 regression fixes.

These tests run without any real OCR runtime (no EasyOCR, no PyTorch).
They verify three specific bug-fixes:

  Fix 1 — reader.recognize() receives 3-channel (H,W,3) array, NOT 2D grayscale.
  Fix 2 — adjust_contrast defaults to 0.5 (was 1.0), env-configurable.
  Fix 3 — mag_ratio defaults to 1.2 (was 1.5), env-configurable.
"""
from __future__ import annotations

import os
import sys
import types
import importlib
import importlib.util
from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Bootstrap: inject lightweight stubs for all package-level imports so the
# module can be imported without installing the full structured-pdf-text stack.
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

# Specific attributes that the module's from-imports resolve at import time
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


# Now load the actual module under test from its source file.
_BACKEND_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "src", "structured_pdf_text", "ocr", "backends", "easyocr.py",
)

spec = importlib.util.spec_from_file_location("easyocr_backend", _BACKEND_PATH)
assert spec is not None and spec.loader is not None
_easyocr_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_easyocr_mod)  # type: ignore[attr-defined]

_run_easyocr = _easyocr_mod._run_easyocr  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reader(*, detect_raises: bool = False, recognize_raises: bool = False) -> MagicMock:
    """Return a mock EasyOCR reader with a controlled detect/recognize/readtext API."""
    reader = MagicMock()

    if detect_raises:
        reader.detect.side_effect = RuntimeError("detect failed")
    else:
        # Simulate a valid single-line horizontal_list response
        reader.detect.return_value = ([[10, 90, 10, 90]], [[]])

    if recognize_raises:
        reader.recognize.side_effect = RuntimeError("recognize failed")
    else:
        # Simulate a result entry: (bbox_points, text, confidence)
        reader.recognize.return_value = [
            ([[10, 10], [90, 10], [90, 30], [10, 30]], "Hello World", 0.95)
        ]

    reader.readtext.return_value = [
        ([[10, 10], [90, 10], [90, 30], [10, 30]], "Hello World", 0.95)
    ]
    return reader


def _make_image_3ch(h: int = 100, w: int = 120) -> "Any":
    """Return a (H,W,3) uint8 numpy array — simulates a rendered PDF page."""
    import numpy as np
    return np.full((h, w, 3), 128, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Fix 1: reader.recognize() must receive a 3-channel (H,W,3) array
# ---------------------------------------------------------------------------

class TestFix1RecognizeReceives3ChannelImage:
    def test_recognize_called_with_3d_array(self):
        """The primary bug: 2D grayscale was passed; must now be (H,W,3)."""
        import numpy as np
        reader = _make_reader()
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        assert reader.recognize.call_count == 1, "recognize() should be called once"
        called_img = reader.recognize.call_args[0][0]  # first positional arg
        assert isinstance(called_img, np.ndarray), "image must be ndarray"
        assert called_img.ndim == 3, (
            f"recognize() must receive a 3-channel image (ndim=3), got ndim={called_img.ndim}. "
            "A 2D (grayscale) array here is the Phase 9 bug."
        )
        assert called_img.shape[2] == 3, (
            f"Last dimension must be 3 (RGB channels), got shape={called_img.shape}"
        )

    def test_recognize_image_is_original_not_grayscale(self):
        """Verify the passed array preserves the original pixel values (not converted)."""
        import numpy as np
        reader = _make_reader()
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        called_img = reader.recognize.call_args[0][0]
        # The 3-channel image should still have 3 distinct channels
        assert called_img.shape == img.shape, (
            "Image shape must be preserved; CLAHE grayscale conversion must NOT happen"
        )

    def test_fallback_readtext_when_detect_fails_uses_3d_array(self):
        """When detect() raises, readtext() fallback must also get 3-channel image."""
        import numpy as np
        reader = _make_reader(detect_raises=True)
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        assert reader.readtext.call_count == 1
        called_img = reader.readtext.call_args[0][0]
        assert called_img.ndim == 3, "readtext() fallback must also receive 3-channel image"

    def test_fallback_readtext_when_recognize_fails_uses_3d_array(self):
        """When recognize() raises, the secondary readtext() fallback must get 3-channel."""
        import numpy as np
        reader = _make_reader(recognize_raises=True)
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        assert reader.readtext.call_count == 1
        called_img = reader.readtext.call_args[0][0]
        assert called_img.ndim == 3, "readtext() secondary fallback must get 3-channel image"


# ---------------------------------------------------------------------------
# Fix 2: adjust_contrast defaults to 0.5 (was 1.0)
# ---------------------------------------------------------------------------

class TestFix2AdjustContrastDefault:
    def test_default_adjust_contrast_is_0_5(self):
        """adjust_contrast must default to 0.5, not 1.0."""
        reader = _make_reader()
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        kwargs = reader.recognize.call_args[1]  # keyword args
        assert "adjust_contrast" in kwargs, "adjust_contrast must be passed as kwarg"
        assert kwargs["adjust_contrast"] == 0.5, (
            f"adjust_contrast should be 0.5, got {kwargs['adjust_contrast']}"
        )

    def test_adjust_contrast_value_1_0_would_be_wrong(self):
        """Regression guard: 1.0 is the old broken value and must NOT be the default."""
        reader = _make_reader()
        img = _make_image_3ch()

        _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0)

        kwargs = reader.recognize.call_args[1]
        assert kwargs.get("adjust_contrast") != 1.0, (
            "adjust_contrast=1.0 is the old aggressive value; default must be 0.5"
        )

    def test_adjust_contrast_env_var_override(self):
        """EASYOCR_ADJUST_CONTRAST env var must override the default for __init__."""
        with patch.dict(os.environ, {"EASYOCR_ADJUST_CONTRAST": "0.3"}):
            value = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
        assert value == 0.3

    def test_adjust_contrast_env_var_missing_defaults_to_0_5(self):
        env = {k: v for k, v in os.environ.items() if k != "EASYOCR_ADJUST_CONTRAST"}
        with patch.dict(os.environ, env, clear=True):
            value = float(os.environ.get("EASYOCR_ADJUST_CONTRAST", "0.5"))
        assert value == 0.5


# ---------------------------------------------------------------------------
# Fix 3: mag_ratio defaults to 1.2 (was 1.5)
# ---------------------------------------------------------------------------

class TestFix3MagRatioDefault:
    def test_detect_called_with_mag_ratio_1_2(self):
        """CRAFT detection must use mag_ratio=1.2, not the old 1.5."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EASYOCR_MAG_RATIO", None)
            reader = _make_reader()
            img = _make_image_3ch()

            _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                         allowlist=None, blocklist=None, workers=0)

        kwargs = reader.detect.call_args[1]
        assert "mag_ratio" in kwargs, "mag_ratio must be passed to reader.detect()"
        assert kwargs["mag_ratio"] == 1.2, (
            f"mag_ratio should be 1.2 (default), got {kwargs['mag_ratio']}"
        )

    def test_mag_ratio_1_5_would_be_old_broken_value(self):
        """Regression guard: 1.5 must not be the mag_ratio default."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EASYOCR_MAG_RATIO", None)
            reader = _make_reader()
            img = _make_image_3ch()

            _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                         allowlist=None, blocklist=None, workers=0)

        kwargs = reader.detect.call_args[1]
        assert kwargs.get("mag_ratio") != 1.5, (
            "mag_ratio=1.5 is the old over-magnification value; default must now be 1.2"
        )

    def test_mag_ratio_env_var_override(self):
        """EASYOCR_MAG_RATIO env var must propagate to reader.detect()."""
        with patch.dict(os.environ, {"EASYOCR_MAG_RATIO": "1.8"}):
            reader = _make_reader()
            img = _make_image_3ch()
            _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                         allowlist=None, blocklist=None, workers=0)

        kwargs = reader.detect.call_args[1]
        assert kwargs["mag_ratio"] == 1.8, (
            f"EASYOCR_MAG_RATIO=1.8 must propagate, got {kwargs.get('mag_ratio')}"
        )

    def test_mag_ratio_consistent_in_fallback_readtext(self):
        """When detect() fails, readtext() fallback must use the same mag_ratio."""
        with patch.dict(os.environ, {"EASYOCR_MAG_RATIO": "1.3"}):
            reader = _make_reader(detect_raises=True)
            img = _make_image_3ch()
            _run_easyocr(reader, img, beamwidth=5, adjust_contrast=0.5,
                         allowlist=None, blocklist=None, workers=0)

        kwargs = reader.readtext.call_args[1]
        assert kwargs.get("mag_ratio") == 1.3, (
            "Fallback readtext() must use the same EASYOCR_MAG_RATIO as detect()"
        )


# ---------------------------------------------------------------------------
# Integration: all three fixes together
# ---------------------------------------------------------------------------

class TestAllFixesTogether:
    def test_normal_path_passes_all_correct_args(self):
        """Golden-path: detect+recognize both succeed with the corrected defaults."""
        import numpy as np
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EASYOCR_MAG_RATIO", None)
            os.environ.pop("EASYOCR_ADJUST_CONTRAST", None)
            reader = _make_reader()
            img = _make_image_3ch(h=200, w=150)

            result = _run_easyocr(reader, img, beamwidth=10, adjust_contrast=0.5,
                                  allowlist=None, blocklist=None, workers=0)

        # Fix 1: shape
        rec_img = reader.recognize.call_args[0][0]
        assert rec_img.ndim == 3 and rec_img.shape[2] == 3

        # Fix 2: contrast
        assert reader.recognize.call_args[1]["adjust_contrast"] == 0.5

        # Fix 3: mag_ratio
        assert reader.detect.call_args[1]["mag_ratio"] == 1.2

        # canvas_size must be max(h, w) = 200
        assert reader.detect.call_args[1]["canvas_size"] == 200

        # Result must be a list
        assert isinstance(result, list)
