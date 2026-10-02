"""Contract tests for the EasyOCR backend's detect/recognize split.

These tests verify the correct upstream contract as described in the F01/F02
audit findings.  No real EasyOCR or PyTorch installation is required — all
external calls are replaced with monkeypatched fakes at runtime, not at
module-collection time.

Key contracts verified
----------------------
- Nominal path: detect() called once, recognize() called once, readtext() never.
- detect() output is unwrapped with [0] before passing to recognize().
- recognize() receives the grayscale image from reformat_input(), not the
  3-channel colour array.
- Fallback to readtext() is explicit: emits RuntimeWarning and returns
  fallback_info dict (not None).
- decoder param propagates correctly (greedy / beamsearch).
- mag_ratio and adjust_contrast propagate correctly.
"""
from __future__ import annotations

import sys
import types
import warnings
from typing import Any
from unittest.mock import MagicMock, call

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image(h: int = 100, w: int = 120, channels: int = 3) -> np.ndarray:
    if channels == 1:
        return np.full((h, w), 128, dtype=np.uint8)
    return np.full((h, w, channels), 128, dtype=np.uint8)


def _make_reader(
    *,
    detect_raises: bool = False,
    recognize_raises: bool = False,
) -> MagicMock:
    """Return a fake EasyOCR Reader whose detect/recognize/readtext are mocks.

    detect() returns the aggregate structure EasyOCR uses:
        ([[x0, x1, y0, y1]], [[]])  — a list-of-lists, one entry per image.
    The backend must unwrap [0] before calling recognize().
    """
    reader = MagicMock()

    if detect_raises:
        reader.detect.side_effect = RuntimeError("detect failed")
    else:
        # Aggregate output: one entry per image in the batch.
        reader.detect.return_value = (
            [[[10, 90, 10, 30]]],   # horizontal_list — list of lists
            [[]],                    # free_list — list of lists
        )

    if recognize_raises:
        reader.recognize.side_effect = RuntimeError("recognize failed")
    else:
        reader.recognize.return_value = [
            ([[10, 10], [90, 10], [90, 30], [10, 30]], "Hello", 0.95)
        ]

    reader.readtext.return_value = [
        ([[10, 10], [90, 10], [90, 30], [10, 30]], "Fallback", 0.80)
    ]
    return reader


def _make_reformat_input(img_color: np.ndarray, img_gray: np.ndarray):
    """Return a fake reformat_input() that returns predetermined arrays."""
    def _reformat(arr):  # noqa: ANN001
        return img_color, img_gray
    return _reformat


# ---------------------------------------------------------------------------
# Fixture: import _run_easyocr from the real source with easyocr.utils patched
# ---------------------------------------------------------------------------

@pytest.fixture()
def run_easyocr(monkeypatch):
    """Import _run_easyocr and patch easyocr.utils.reformat_input per-test.

    Returns a factory: call_run(reader, img, **kwargs) → (raw, fallback_info).
    The factory automatically injects colour/gray arrays via a patched
    reformat_input so tests control exactly what the backend sees.
    """
    from structured_pdf_text.ocr.backends.easyocr import _run_easyocr

    img_color = _make_image(100, 120, 3)
    img_gray = _make_image(100, 120, 1)

    # Patch easyocr.utils.reformat_input inside the backend's module namespace.
    fake_easyocr_utils = types.SimpleNamespace(
        reformat_input=_make_reformat_input(img_color, img_gray)
    )
    fake_easyocr = types.SimpleNamespace(utils=fake_easyocr_utils)

    # The backend does `from easyocr.utils import reformat_input` inside _run_easyocr.
    # We patch sys.modules so that import resolves to our fake.
    original_easyocr = sys.modules.get("easyocr")
    original_easyocr_utils = sys.modules.get("easyocr.utils")

    monkeypatch.setitem(sys.modules, "easyocr", fake_easyocr)
    monkeypatch.setitem(sys.modules, "easyocr.utils", fake_easyocr_utils)

    def call_run(reader, img, *, decoder="greedy", beamwidth=5,
                 adjust_contrast=0.5, allowlist=None, blocklist=None, workers=0):
        return _run_easyocr(
            reader, img,
            decoder=decoder,
            beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
        )

    return call_run, img_color, img_gray


# ---------------------------------------------------------------------------
# Nominal path — detect once, recognize once, readtext never
# ---------------------------------------------------------------------------

class TestNominalPath:
    def test_detect_called_once(self, run_easyocr):
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        img = _make_image()
        call_run(reader, img)
        assert reader.detect.call_count == 1

    def test_recognize_called_once(self, run_easyocr):
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        img = _make_image()
        call_run(reader, img)
        assert reader.recognize.call_count == 1

    def test_readtext_not_called(self, run_easyocr):
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        img = _make_image()
        call_run(reader, img)
        assert reader.readtext.call_count == 0

    def test_fallback_info_is_none_on_nominal_path(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        raw, fallback_info = call_run(reader, _make_image())
        assert fallback_info is None

    def test_result_is_list(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        raw, _ = call_run(reader, _make_image())
        assert isinstance(raw, list)
        assert len(raw) == 1


# ---------------------------------------------------------------------------
# detect() → recognize() unwrapping contract
# ---------------------------------------------------------------------------

class TestDetectRecognizeUnwrap:
    def test_recognize_receives_grayscale_image(self, run_easyocr):
        """recognize() must receive img_gray (2-D), not the 3-channel colour array."""
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        called_img = reader.recognize.call_args[0][0]
        assert np.array_equal(called_img, img_gray), (
            "recognize() must receive the grayscale array from reformat_input, "
            f"not the colour array. Got shape={getattr(called_img, 'shape', '?')}"
        )

    def test_recognize_does_not_receive_colour_image(self, run_easyocr):
        """Regression guard: colour (H,W,3) image must NOT be passed to recognize()."""
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        called_img = reader.recognize.call_args[0][0]
        assert not np.array_equal(called_img, img_color), (
            "recognize() received the colour image — the F01 bug is still present"
        )

    def test_detect_receives_colour_image(self, run_easyocr):
        """detect() must receive the colour (H,W,3) image from reformat_input."""
        call_run, img_color, img_gray = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        called_img = reader.detect.call_args[0][0]
        assert np.array_equal(called_img, img_color), (
            "detect() must receive the colour array from reformat_input"
        )

    def test_horizontal_list_is_unwrapped(self, run_easyocr):
        """recognize() must receive the unwrapped horizontal_list, not the aggregate."""
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        # Positional args to recognize: img_gray, horizontal_list, free_list
        pos_args = reader.recognize.call_args[0]
        horizontal_list = pos_args[1]
        # After [0] unwrap, it should be the inner list, not a list-of-lists
        assert isinstance(horizontal_list, list)
        assert not isinstance(horizontal_list[0], list) or (
            # Inner element is a bounding-box list [x0,x1,y0,y1], not another level
            isinstance(horizontal_list[0], list) and
            not isinstance(horizontal_list[0][0], list)
        ), (
            "horizontal_list passed to recognize() looks like the aggregate "
            "(list-of-lists), not the unwrapped single-image list"
        )

    def test_detect_called_with_reformat_false(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        kwargs = reader.detect.call_args[1]
        assert kwargs.get("reformat") is False

    def test_recognize_called_with_reformat_false(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        kwargs = reader.recognize.call_args[1]
        assert kwargs.get("reformat") is False

    def test_canvas_size_from_reformatted_image(self, run_easyocr):
        """canvas_size must be max(h, w) of the reformatted image (img_color)."""
        call_run, img_color, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        h, w = img_color.shape[:2]
        expected = max(h, w)
        kwargs = reader.detect.call_args[1]
        assert kwargs.get("canvas_size") == expected


# ---------------------------------------------------------------------------
# Decoder propagation
# ---------------------------------------------------------------------------

class TestDecoderPropagation:
    def test_greedy_decoder_passed_to_recognize(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image(), decoder="greedy")
        assert reader.recognize.call_args[1]["decoder"] == "greedy"

    def test_beamsearch_decoder_passed_to_recognize(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image(), decoder="beamsearch")
        assert reader.recognize.call_args[1]["decoder"] == "beamsearch"


# ---------------------------------------------------------------------------
# mag_ratio propagation
# ---------------------------------------------------------------------------

class TestMagRatio:
    def test_default_mag_ratio_1_2(self, run_easyocr, monkeypatch):
        monkeypatch.delenv("EASYOCR_MAG_RATIO", raising=False)
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        assert reader.detect.call_args[1]["mag_ratio"] == pytest.approx(1.2)

    def test_mag_ratio_env_override(self, run_easyocr, monkeypatch):
        monkeypatch.setenv("EASYOCR_MAG_RATIO", "1.8")
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        assert reader.detect.call_args[1]["mag_ratio"] == pytest.approx(1.8)


# ---------------------------------------------------------------------------
# adjust_contrast propagation
# ---------------------------------------------------------------------------

class TestAdjustContrast:
    def test_adjust_contrast_passed_to_recognize(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image(), adjust_contrast=0.5)
        assert reader.recognize.call_args[1]["adjust_contrast"] == pytest.approx(0.5)

    def test_adjust_contrast_0_5_not_1_0(self, run_easyocr):
        """1.0 was the old aggressive default — must never be the default."""
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image(), adjust_contrast=0.5)
        assert reader.recognize.call_args[1]["adjust_contrast"] != pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Fallback path — detect() raises
# ---------------------------------------------------------------------------

class TestFallbackOnDetectFailure:
    def test_readtext_called_once_when_detect_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            call_run(reader, _make_image())
        assert reader.detect.call_count == 1
        assert reader.readtext.call_count == 1
        assert reader.recognize.call_count == 0

    def test_fallback_info_is_not_none_when_detect_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _, fallback_info = call_run(reader, _make_image())
        assert fallback_info is not None
        assert fallback_info.get("fallback_used") is True

    def test_fallback_emits_runtime_warning_when_detect_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            call_run(reader, _make_image())
        runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert runtime_warnings, "A RuntimeWarning must be emitted when falling back"

    def test_fallback_info_contains_primary_error(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _, fallback_info = call_run(reader, _make_image())
        assert "detect_failed" in fallback_info.get("primary_error", "")

    def test_fallback_result_is_readtext_output(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        reader.readtext.return_value = [("bbox", "FallbackText", 0.7)]
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            raw, _ = call_run(reader, _make_image())
        assert raw == [("bbox", "FallbackText", 0.7)]


# ---------------------------------------------------------------------------
# Fallback path — recognize() raises
# ---------------------------------------------------------------------------

class TestFallbackOnRecognizeFailure:
    def test_readtext_called_once_when_recognize_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(recognize_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            call_run(reader, _make_image())
        assert reader.detect.call_count == 1
        assert reader.recognize.call_count == 1
        assert reader.readtext.call_count == 1

    def test_fallback_info_not_none_when_recognize_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(recognize_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _, fallback_info = call_run(reader, _make_image())
        assert fallback_info is not None
        assert fallback_info.get("fallback_used") is True

    def test_fallback_info_contains_recognize_error(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(recognize_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _, fallback_info = call_run(reader, _make_image())
        assert "recognize_failed" in fallback_info.get("primary_error", "")
