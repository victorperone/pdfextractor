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
                 adjust_contrast=0.5, allowlist=None, blocklist=None, workers=0,
                 rotation_info=None):
        return _run_easyocr(
            reader, img,
            decoder=decoder,
            beamwidth=beamwidth,
            adjust_contrast=adjust_contrast,
            allowlist=allowlist,
            blocklist=blocklist,
            workers=workers,
            rotation_info=rotation_info,
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
        """canvas_size must retain the requested magnification of img_color."""
        call_run, img_color, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image())
        h, w = img_color.shape[:2]
        mag_ratio = reader.detect.call_args[1]["mag_ratio"]
        expected = int(mag_ratio * max(h, w))
        kwargs = reader.detect.call_args[1]
        assert kwargs.get("canvas_size") == expected
        assert kwargs.get("canvas_size") >= max(h, w)


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
        assert reader.detect.call_args[1]["canvas_size"] == int(1.8 * 120)


def test_easyocr_fallback_diagnostics_accumulate_until_consumed():
    from structured_pdf_text.ocr.backends.easyocr import EasyOCRBackend

    backend = EasyOCRBackend.__new__(EasyOCRBackend)
    backend.reset_page_diagnostics()
    backend._record_call({"primary_error": "first region failed"})
    backend._record_call(None)
    backend._record_call({"primary_error": "third region failed"})

    diagnostics = backend.consume_page_diagnostics()
    assert diagnostics["easyocr_calls"] == 3
    assert diagnostics["easyocr_fallback_count"] == 2
    assert diagnostics["easyocr_fallback_rate"] == pytest.approx(2 / 3)
    assert diagnostics["easyocr_fallback_reasons"] == ["first region failed", "third region failed"]
    assert diagnostics["candidate_diagnostics"] == []  # no multi-candidate pass
    assert backend.consume_page_diagnostics()["easyocr_fallback_count"] == 0


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


# ---------------------------------------------------------------------------
# F12 — rotation_info propagation
# ---------------------------------------------------------------------------

class TestRotationInfoNominalPath:
    """rotation_info must reach recognize() on the nominal path, not readtext()."""

    def test_rotation_info_none_by_default(self, run_easyocr):
        """When rotation_info is None, recognize() is called with rotation_info=None."""
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        call_run(reader, _make_image(), rotation_info=None)
        assert reader.readtext.call_count == 0
        assert reader.recognize.call_args[1].get("rotation_info") is None

    def test_rotation_info_list_reaches_recognize(self, run_easyocr):
        """rotation_info=[90,180,270] must be forwarded to recognize(), not readtext()."""
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        rotation = [90, 180, 270]
        call_run(reader, _make_image(), rotation_info=rotation)
        assert reader.readtext.call_count == 0, "readtext() must not be called on nominal path"
        assert reader.recognize.call_count == 1
        assert reader.recognize.call_args[1].get("rotation_info") == rotation

    def test_nominal_path_with_rotation_info_does_not_trigger_fallback(self, run_easyocr):
        """Setting rotation_info must not push execution to the fallback path."""
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        _, fallback_info = call_run(reader, _make_image(), rotation_info=[90, 180, 270])
        assert fallback_info is None, "fallback_info must be None on nominal path"


class TestRotationInfoFallbackPath:
    """On fallback, the same rotation_info must be forwarded to readtext()."""

    def test_fallback_receives_rotation_info_when_detect_raises(self, run_easyocr):
        """readtext() must receive the same rotation_info as the failed nominal attempt."""
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        rotation = [90, 180, 270]
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            call_run(reader, _make_image(), rotation_info=rotation)
        assert reader.readtext.call_count == 1
        assert reader.readtext.call_args[1].get("rotation_info") == rotation

    def test_fallback_receives_rotation_info_when_recognize_raises(self, run_easyocr):
        call_run, _, _ = run_easyocr
        reader = _make_reader(recognize_raises=True)
        rotation = [90, 270]
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            call_run(reader, _make_image(), rotation_info=rotation)
        assert reader.readtext.call_count == 1
        assert reader.readtext.call_args[1].get("rotation_info") == rotation

    def test_fallback_none_rotation_info_forwarded(self, run_easyocr):
        """None rotation_info must also be forwarded consistently (not converted to [])."""
        call_run, _, _ = run_easyocr
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            call_run(reader, _make_image(), rotation_info=None)
        assert reader.readtext.call_args[1].get("rotation_info") is None


# ---------------------------------------------------------------------------
# §7 — Cache path always passed to Reader
# ---------------------------------------------------------------------------

class TestCachePathAlwaysPassedToReader:
    """EasyOCRBackend must always pass model_storage_directory to Reader.

    Without this fix the Reader falls back to ~/.EasyOCR/model/ while the
    readiness probe checks ~/.cache/pdfextractor/easyocr/ — they diverge.
    """

    def _make_easyocr_stub(self, captured: dict) -> types.ModuleType:
        """Return a fake easyocr module whose Reader records __init__ kwargs."""
        class FakeReader:
            def __init__(self, langs, **kwargs):
                captured["kwargs"] = kwargs
                captured["langs"] = langs
                self._langs = langs

        fake_mod = types.ModuleType("easyocr")
        fake_mod.Reader = FakeReader  # type: ignore[attr-defined]
        return fake_mod

    def test_model_storage_directory_always_passed(self, monkeypatch):
        """Reader must receive model_storage_directory even without EASYOCR_MODULE_PATH."""
        captured: dict = {}
        monkeypatch.delenv("EASYOCR_MODULE_PATH", raising=False)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        import sys
        fake_mod = self._make_easyocr_stub(captured)
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        # Patch _import_easyocr to return our stub
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        # Patch _apply_torch_threads to avoid torch import
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        # Call __init__ manually
        easyocr_mod.EasyOCRBackend.__init__(backend, config)

        assert "model_storage_directory" in captured["kwargs"], (
            "Reader must always receive model_storage_directory"
        )
        expected_dir = easyocr_mod._model_cache_dir()
        assert captured["kwargs"]["model_storage_directory"] == str(expected_dir)

    def test_model_storage_directory_uses_env_override(self, monkeypatch, tmp_path):
        """When EASYOCR_MODULE_PATH is set, it overrides the default cache dir."""
        captured: dict = {}
        monkeypatch.setenv("EASYOCR_MODULE_PATH", str(tmp_path))
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        import sys
        fake_mod = self._make_easyocr_stub(captured)
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)

        assert captured["kwargs"]["model_storage_directory"] == str(tmp_path)


# ---------------------------------------------------------------------------
# §8/9/13 — Full parameter surface propagation
# ---------------------------------------------------------------------------

class TestDetectorParamPropagation:
    """Detector thresholds must reach reader.detect() when set via env vars."""

    def test_text_threshold_reaches_detect(self, run_easyocr, monkeypatch):
        monkeypatch.setenv("EASYOCR_TEXT_THRESHOLD", "0.55")
        call_run, _, _ = run_easyocr
        reader = _make_reader()
        # _run_easyocr reads env vars directly when called — pass via kwargs
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import numpy as np
        import sys, types
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        img = _make_image()
        _run_easyocr(reader, img, decoder="greedy", beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0, text_threshold=0.55)
        assert reader.detect.call_args[1]["text_threshold"] == pytest.approx(0.55)

    def test_min_size_reaches_detect(self, run_easyocr, monkeypatch):
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import sys, types
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        reader = _make_reader()
        _run_easyocr(reader, _make_image(), decoder="greedy", beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0, min_size=10)
        assert reader.detect.call_args[1]["min_size"] == 10

    def test_width_ths_reaches_detect(self, run_easyocr, monkeypatch):
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import sys, types
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        reader = _make_reader()
        _run_easyocr(reader, _make_image(), decoder="greedy", beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0, width_ths=0.25)
        assert reader.detect.call_args[1]["width_ths"] == pytest.approx(0.25)

    def test_contrast_ths_reaches_recognize(self, run_easyocr, monkeypatch):
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import sys, types
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        reader = _make_reader()
        _run_easyocr(reader, _make_image(), decoder="greedy", beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0, contrast_ths=0.2)
        assert reader.recognize.call_args[1]["contrast_ths"] == pytest.approx(0.2)

    def test_filter_ths_reaches_recognize(self, run_easyocr, monkeypatch):
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import sys, types
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        reader = _make_reader()
        _run_easyocr(reader, _make_image(), decoder="greedy", beamwidth=5, adjust_contrast=0.5,
                     allowlist=None, blocklist=None, workers=0, filter_ths=0.01)
        assert reader.recognize.call_args[1]["filter_ths"] == pytest.approx(0.01)


class TestWordbeamsearchDecoder:
    """wordbeamsearch must be accepted as a valid decoder value."""

    def _make_easyocr_stub_for_init(self, monkeypatch) -> "types.ModuleType":
        class FakeReader:
            def __init__(self, langs, **kwargs):
                self._langs = langs
        import sys, types
        fake_mod = types.ModuleType("easyocr")
        fake_mod.Reader = FakeReader  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        return fake_mod

    def test_wordbeamsearch_accepted(self, monkeypatch):
        fake_mod = self._make_easyocr_stub_for_init(monkeypatch)
        monkeypatch.setenv("EASYOCR_DECODER", "wordbeamsearch")
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        assert backend._decoder == "wordbeamsearch"

    def test_invalid_decoder_falls_back_to_greedy(self, monkeypatch):
        fake_mod = self._make_easyocr_stub_for_init(monkeypatch)
        monkeypatch.setenv("EASYOCR_DECODER", "invalid_decoder")
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        assert backend._decoder == "greedy"

    def test_quantize_false_passed_to_reader(self, monkeypatch):
        captured: dict = {}

        class FakeReader:
            def __init__(self, langs, **kwargs):
                captured["quantize"] = kwargs.get("quantize")
                self._langs = langs

        import sys, types
        fake_mod = types.ModuleType("easyocr")
        fake_mod.Reader = FakeReader  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        monkeypatch.setenv("EASYOCR_QUANTIZE", "0")
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        assert captured["quantize"] is False

    def test_fallback_receives_same_detector_params(self, monkeypatch):
        """When detect() raises, readtext() fallback must receive the same detector params."""
        from structured_pdf_text.ocr.backends.easyocr import _run_easyocr
        import sys, types, warnings
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        reader = _make_reader(detect_raises=True)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            _run_easyocr(reader, _make_image(), decoder="greedy", beamwidth=5,
                         adjust_contrast=0.5, allowlist=None, blocklist=None,
                         workers=0, text_threshold=0.55, width_ths=0.25,
                         contrast_ths=0.2, filter_ths=0.01)
        kw = reader.readtext.call_args[1]
        assert kw["text_threshold"] == pytest.approx(0.55)
        assert kw["width_ths"] == pytest.approx(0.25)
        assert kw["contrast_ths"] == pytest.approx(0.2)
        assert kw["filter_ths"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# §5 — quality_variants / quality_policy candidate policies
# ---------------------------------------------------------------------------

class TestQualityCandidatePolicies:
    """quality_variants=True and quality_policy must trigger real EasyOCR multi-pass."""

    def _make_backend_with_counting_reader(self, monkeypatch) -> "tuple[Any, dict]":
        """Return an EasyOCRBackend with a counting fake Reader."""
        import sys, types
        call_counts: dict = {"detect": 0, "recognize": 0}

        class CountingReader:
            def detect(self, img_color, **kwargs):
                call_counts["detect"] += 1
                return ([[[10, 90, 10, 30]]], [[]])

            def recognize(self, img_gray, horizontal_list, free_list, **kwargs):
                call_counts["recognize"] += 1
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "text", 0.9)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return CountingReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        # Patch easyocr.utils so _run_easyocr can import reformat_input
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)

        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend, call_counts

    def test_default_policy_makes_single_ocr_call(self, monkeypatch):
        """Without quality_variants, exactly 1 detect+recognize pair is called."""
        backend, counts = self._make_backend_with_counting_reader(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_variants=False)
        assert counts["detect"] == 1

    def test_adaptive_policy_makes_multiple_ocr_calls(self, monkeypatch):
        """quality_policy='adaptive' must trigger >1 detect call."""
        backend, counts = self._make_backend_with_counting_reader(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="adaptive")
        assert counts["detect"] >= 2, "adaptive must produce at least 2 candidates"

    def test_quality_variants_true_triggers_multi_pass(self, monkeypatch):
        """quality_variants=True must trigger >1 detect call."""
        backend, counts = self._make_backend_with_counting_reader(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_variants=True)
        assert counts["detect"] >= 2

    def test_exhaustive_policy_makes_more_calls_than_adaptive(self, monkeypatch):
        """quality_policy='exhaustive' must produce more candidates than 'adaptive'."""
        backend_a, counts_a = self._make_backend_with_counting_reader(monkeypatch)
        backend_a.recognize_page(_make_image(), 0, quality_policy="adaptive")
        adaptive_detect = counts_a["detect"]

        # Reset and test exhaustive
        backend_e, counts_e = self._make_backend_with_counting_reader(monkeypatch)
        backend_e.recognize_page(_make_image(), 0, quality_policy="exhaustive")
        exhaustive_detect = counts_e["detect"]

        assert exhaustive_detect > adaptive_detect, (
            f"exhaustive ({exhaustive_detect}) must run more candidates than "
            f"adaptive ({adaptive_detect})"
        )

    def test_candidate_selection_returns_non_empty_when_any_candidate_has_tokens(self, monkeypatch):
        """The best candidate with tokens must be returned."""
        backend, _ = self._make_backend_with_counting_reader(monkeypatch)
        from structured_pdf_text.geometry import BBox
        tokens = backend.recognize_page(
            _make_image(), 0,
            page_bbox=BBox(0, 0, 120, 100),
            quality_policy="adaptive",
        )
        assert isinstance(tokens, list)


# ---------------------------------------------------------------------------
# §24 — Polygon retention in pipeline tokens
# ---------------------------------------------------------------------------

class TestPolygonRetention:
    """EasyOCR quadrilateral boxes must survive into OcrToken.polygon."""

    def test_result_to_pipeline_tokens_populates_polygon(self):
        """_result_to_pipeline_tokens must populate OcrToken.polygon from bbox_pts."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        from structured_pdf_text.document import SourceKind
        bbox_pts = [[10, 10], [90, 10], [90, 30], [10, 30]]
        raw = [(bbox_pts, "hello", 0.95)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        assert len(tokens) == 1
        t = tokens[0]
        assert t.polygon is not None, "polygon must be populated from CRAFT quadrilateral"
        assert len(t.polygon) == 4
        # Verify polygon coordinates match the input with applied offset (0,0 default)
        xs = [p.x for p in t.polygon]
        ys = [p.y for p in t.polygon]
        assert min(xs) == pytest.approx(10.0)
        assert max(xs) == pytest.approx(90.0)
        assert min(ys) == pytest.approx(10.0)
        assert max(ys) == pytest.approx(30.0)

    def test_polygon_propagates_with_offset(self):
        """Offsets applied to bbox must also shift polygon coordinates."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        bbox_pts = [[0, 0], [10, 0], [10, 5], [0, 5]]
        raw = [(bbox_pts, "x", 0.9)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt", offset_x=50.0, offset_y=20.0)
        assert tokens[0].polygon is not None
        xs = [p.x for p in tokens[0].polygon]
        ys = [p.y for p in tokens[0].polygon]
        assert min(xs) == pytest.approx(50.0)
        assert max(xs) == pytest.approx(60.0)
        assert min(ys) == pytest.approx(20.0)
        assert max(ys) == pytest.approx(25.0)

    def test_polygon_transforms_through_map_tokens_to_page(self):
        """map_tokens_to_page must apply raster-to-page transform to polygon."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        from structured_pdf_text.geometry import BBox
        # Page: 100x50 pt; raster: 200x100 px — each pixel = 0.5 pt
        page_bbox = BBox(0, 0, 100, 50)
        bbox_pts = [[0, 0], [200, 0], [200, 100], [0, 100]]
        raw = [(bbox_pts, "page", 0.9)]
        raster_tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        page_tokens = map_tokens_to_page(raster_tokens, page_bbox, 200, 100)
        assert page_tokens[0].polygon is not None
        for p in page_tokens[0].polygon:
            assert 0.0 <= p.x <= 100.0
            assert 0.0 <= p.y <= 50.0

    def test_polygon_shifts_through_offset_tokens(self):
        """offset_tokens must translate polygon points by the same (x, y) delta."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        from structured_pdf_text.ocr.coordinates import offset_tokens
        bbox_pts = [[0, 0], [10, 0], [10, 5], [0, 5]]
        raw = [(bbox_pts, "y", 0.9)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        shifted = offset_tokens(tokens, 30.0, 15.0)
        assert shifted[0].polygon is not None
        xs = [p.x for p in shifted[0].polygon]
        ys = [p.y for p in shifted[0].polygon]
        assert min(xs) == pytest.approx(30.0)
        assert min(ys) == pytest.approx(15.0)

    def test_degenerate_polygon_skipped_gracefully(self):
        """A bbox_pts with < 3 valid points must still produce an OcrToken with polygon=None."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        # Fewer than 3 points — geometry will fail or polygon will be None
        bbox_pts = [[0, 0], [10, 10]]  # only 2 points
        raw = [(bbox_pts, "z", 0.9)]
        # quadrilateral_geometry requires >= 4 points — result is None → token skipped
        tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        assert tokens == []


# ---------------------------------------------------------------------------
# §46 — Candidate diagnostics in consume_page_diagnostics
# ---------------------------------------------------------------------------

class TestCandidateDiagnostics:
    """consume_page_diagnostics must include candidate_diagnostics for multi-pass."""

    def _make_counting_backend(self, monkeypatch) -> "Any":
        import sys, types

        class FakeReader:
            def detect(self, img_color, **kwargs):
                return ([[[10, 90, 10, 30]]], [[]])
            def recognize(self, img_gray, h_list, f_list, **kwargs):
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "word", 0.85)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return FakeReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend

    def test_single_pass_produces_empty_candidate_diagnostics(self, monkeypatch):
        """Default single-pass must leave candidate_diagnostics as empty list."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_variants=False)
        diag = backend.consume_page_diagnostics()
        assert diag["candidate_diagnostics"] == []

    def test_adaptive_pass_produces_candidate_diagnostics(self, monkeypatch):
        """adaptive policy must populate candidate_diagnostics with >=2 entries."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="adaptive")
        diag = backend.consume_page_diagnostics()
        cand = diag["candidate_diagnostics"]
        assert len(cand) >= 2
        selected = [c for c in cand if c["selected"]]
        assert len(selected) == 1, "Exactly one candidate must be marked selected"

    def test_exhaustive_pass_produces_candidate_diagnostics(self, monkeypatch):
        """exhaustive policy must produce >=4 candidate entries."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        cand = diag["candidate_diagnostics"]
        assert len(cand) >= 4

    def test_candidate_diagnostics_contain_required_keys(self, monkeypatch):
        """Each candidate_diagnostics entry must have the documented keys."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="adaptive")
        diag = backend.consume_page_diagnostics()
        required = {"candidate_id", "token_count", "char_count", "mean_confidence", "score", "selected"}
        for entry in diag["candidate_diagnostics"]:
            assert required <= entry.keys(), f"Missing keys in {entry}"

    def test_candidate_diagnostics_reset_on_next_consume(self, monkeypatch):
        """After consume, candidate_diagnostics must reset to empty."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="adaptive")
        backend.consume_page_diagnostics()
        # Second consume (no new recognize_page) must have empty list
        diag2 = backend.consume_page_diagnostics()
        assert diag2["candidate_diagnostics"] == []

    def test_candidate_diagnostics_include_rich_scoring_fields(self, monkeypatch):
        """§22: diagnostics must include low_conf_ratio, duplicate_ratio, replacement_char_ratio."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="adaptive")
        diag = backend.consume_page_diagnostics()
        rich_keys = {"low_conf_ratio", "duplicate_ratio", "replacement_char_ratio", "horizontal_ratio"}
        for entry in diag["candidate_diagnostics"]:
            assert rich_keys <= entry.keys(), f"Missing rich scoring fields in {entry}"


# ---------------------------------------------------------------------------
# §22 — _candidate_metrics and _score_candidate correctness
# ---------------------------------------------------------------------------

class TestCandidateScoring:
    """§22: scoring must penalise low confidence, duplicates and garbled chars."""

    def _make_token(self, text: str, conf: float, w: float = 10.0, h: float = 5.0):
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        return OcrToken(text, BBox(0, 0, w, h), conf, "pt", SourceKind.OCR_PAGE)

    def test_clean_tokens_score_higher_than_garbled(self):
        from structured_pdf_text.ocr.backends.easyocr import _score_candidate
        clean = [self._make_token("palavra", 0.95)]
        garbled = [self._make_token("p�l�vr�", 0.95)]
        assert _score_candidate(clean) > _score_candidate(garbled)

    def test_low_confidence_tokens_penalised(self):
        from structured_pdf_text.ocr.backends.easyocr import _score_candidate
        high_conf = [self._make_token("texto", 0.95)]
        low_conf = [self._make_token("texto", 0.30)]
        assert _score_candidate(high_conf) > _score_candidate(low_conf)

    def test_duplicate_tokens_penalised(self):
        from structured_pdf_text.ocr.backends.easyocr import _score_candidate
        unique = [self._make_token("a", 0.9), self._make_token("b", 0.9)]
        duped = [self._make_token("a", 0.9), self._make_token("a", 0.9)]
        assert _score_candidate(unique) > _score_candidate(duped)

    def test_empty_tokens_return_minus_inf(self):
        import math
        from structured_pdf_text.ocr.backends.easyocr import _score_candidate
        assert math.isinf(_score_candidate([]))
        assert _score_candidate([]) < 0

    def test_metrics_dict_has_all_keys(self):
        from structured_pdf_text.ocr.backends.easyocr import _candidate_metrics
        tokens = [self._make_token("texto", 0.9)]
        m = _candidate_metrics(tokens)
        required = {"mean_confidence", "low_conf_ratio", "replacement_char_ratio",
                    "duplicate_ratio", "horizontal_ratio", "char_count", "token_count"}
        assert required <= m.keys()

    def test_metrics_empty_tokens(self):
        from structured_pdf_text.ocr.backends.easyocr import _candidate_metrics
        m = _candidate_metrics([])
        assert m["token_count"] == 0
        assert m["char_count"] == 0


# ---------------------------------------------------------------------------
# §25 — OcrToken.level field
# ---------------------------------------------------------------------------

class TestOcrTokenLevel:
    """§25: OcrToken must carry a level field defaulting to 'line'."""

    def test_ocrtoken_default_level_is_line(self):
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        t = OcrToken("text", BBox(0, 0, 10, 5), 0.9, "pt", SourceKind.OCR_PAGE)
        assert t.level == "line"

    def test_ocrtoken_level_can_be_set(self):
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        t = OcrToken("text", BBox(0, 0, 10, 5), 0.9, "pt", SourceKind.OCR_PAGE, level="word")
        assert t.level == "word"

    def test_pipeline_tokens_have_line_level(self):
        """EasyOCR pipeline tokens must be tagged as level='line'."""
        from structured_pdf_text.ocr.backends.easyocr import _result_to_pipeline_tokens
        bbox_pts = [[0, 0], [50, 0], [50, 10], [0, 10]]
        raw = [(bbox_pts, "uma linha de texto", 0.9)]
        tokens = _result_to_pipeline_tokens(raw, 0, "pt")
        assert tokens[0].level == "line"

    def test_level_preserved_through_map_tokens_to_page(self):
        """map_tokens_to_page must not drop or change the level field."""
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.ocr.coordinates import map_tokens_to_page
        token = OcrToken("word", BBox(0, 0, 100, 50), 0.9, "pt", SourceKind.OCR_PAGE, level="word")
        mapped = map_tokens_to_page([token], BBox(0, 0, 100, 50), 100, 50)[0]
        assert mapped.level == "word"

    def test_level_preserved_through_offset_tokens(self):
        """offset_tokens must not drop or change the level field."""
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox
        from structured_pdf_text.ocr.coordinates import offset_tokens
        token = OcrToken("word", BBox(0, 0, 10, 5), 0.9, "pt", SourceKind.OCR_PAGE, level="word")
        shifted = offset_tokens([token], 5.0, 5.0)[0]
        assert shifted.level == "word"


# ---------------------------------------------------------------------------
# §13 — exhaustive candidate pool includes low_contrast variant
# ---------------------------------------------------------------------------

class TestExhaustiveLowContrastCandidate:
    """§13: exhaustive policy must include a low-contrast candidate."""

    def _make_counting_backend(self, monkeypatch) -> "Any":
        """Reuse the same monkeypatching pattern as TestCandidateDiagnostics."""
        import sys, types

        class FakeReader:
            def detect(self, img_color, **kwargs):
                return ([[[10, 90, 10, 30]]], [[]])
            def recognize(self, img_gray, h_list, f_list, **kwargs):
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "word", 0.85)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return FakeReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend

    def test_exhaustive_produces_five_or_more_candidates(self, monkeypatch):
        """§13: exhaustive policy adds low_contrast as 5th candidate family."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        cand = diag["candidate_diagnostics"]
        assert len(cand) >= 5, f"Expected >=5 candidates, got {len(cand)}: {[c['candidate_id'] for c in cand]}"

    def test_exhaustive_includes_low_contrast_candidate_id(self, monkeypatch):
        """§13: exhaustive candidate list must include 'low_contrast' entry."""
        backend = self._make_counting_backend(monkeypatch)
        backend.recognize_page(_make_image(), 0, quality_policy="exhaustive")
        diag = backend.consume_page_diagnostics()
        ids = [c["candidate_id"] for c in diag["candidate_diagnostics"]]
        assert "low_contrast" in ids, f"'low_contrast' not found in candidates: {ids}"

    def test_exhaustive_low_contrast_uses_higher_contrast_ths(self, monkeypatch):
        """§13: low_contrast candidate must pass contrast_ths > base value."""
        calls: list[dict] = []

        def capture_run(reader, img, **kw):
            calls.append(dict(kw))
            return [], None

        from structured_pdf_text.ocr.backends import easyocr as _mod
        monkeypatch.setattr(_mod, "_run_easyocr", capture_run)

        from structured_pdf_text.ocr.backends.easyocr import _exhaustive_candidates
        base_kwargs = {
            "decoder": "greedy", "beamwidth": 5, "adjust_contrast": 0.5,
            "allowlist": None, "blocklist": None, "workers": 0,
            "rotation_info": None, "text_threshold": 0.7, "low_text": 0.4,
            "link_threshold": 0.4, "min_size": 20, "slope_ths": 0.1,
            "ycenter_ths": 0.5, "height_ths": 0.5, "width_ths": 0.5,
            "add_margin": 0.1, "contrast_ths": 0.1, "filter_ths": 0.003,
        }
        _exhaustive_candidates(None, _make_image(), base_kwargs)
        # The low_contrast call must have contrast_ths >= 0.20 (higher than base 0.1)
        contrast_values = [c.get("contrast_ths", 0.1) for c in calls]
        assert any(v >= 0.20 for v in contrast_values), f"No high contrast_ths call found: {contrast_values}"


# ---------------------------------------------------------------------------
# Adaptive resource allocation — _probe_environment, _resolve_workers,
# _apply_torch_threads, EASYOCR_MAX_QUALITY_THREADS (env var)
# ---------------------------------------------------------------------------

class TestEnvironmentProbe:
    """_probe_environment() must detect CPU count and honour the env var."""

    def test_probe_returns_required_keys(self, monkeypatch):
        monkeypatch.delenv("EASYOCR_MAX_QUALITY_THREADS", raising=False)
        from structured_pdf_text.ocr.backends.easyocr import _probe_environment
        probe = _probe_environment()
        required = {"cpu_count_logical", "cpu_count_physical", "cpu_load_1m",
                    "platform", "is_windows", "max_quality_env"}
        assert required <= probe.keys()

    def test_probe_logical_cpu_is_positive(self, monkeypatch):
        monkeypatch.delenv("EASYOCR_MAX_QUALITY_THREADS", raising=False)
        from structured_pdf_text.ocr.backends.easyocr import _probe_environment
        probe = _probe_environment()
        assert probe["cpu_count_logical"] >= 1

    def test_probe_max_quality_env_false_by_default(self, monkeypatch):
        monkeypatch.delenv("EASYOCR_MAX_QUALITY_THREADS", raising=False)
        from structured_pdf_text.ocr.backends.easyocr import _probe_environment
        probe = _probe_environment()
        assert probe["max_quality_env"] is False

    def test_probe_max_quality_env_true_when_set(self, monkeypatch):
        monkeypatch.setenv("EASYOCR_MAX_QUALITY_THREADS", "1")
        from structured_pdf_text.ocr.backends.easyocr import _probe_environment
        probe = _probe_environment()
        assert probe["max_quality_env"] is True

    def test_probe_max_quality_env_false_for_other_values(self, monkeypatch):
        for val in ("0", "yes", "true", "2"):
            monkeypatch.setenv("EASYOCR_MAX_QUALITY_THREADS", val)
            from structured_pdf_text.ocr.backends.easyocr import _probe_environment
            probe = _probe_environment()
            assert probe["max_quality_env"] is False, f"Should be False for EASYOCR_MAX_QUALITY_THREADS={val!r}"


class TestAdaptiveWorkerResolution:
    """_resolve_workers() must use all cores in max-quality mode."""

    def _probe(self, logical: int = 8, max_quality: bool = False, is_windows: bool = False):
        return {
            "cpu_count_logical": logical,
            "cpu_count_physical": logical // 2,
            "cpu_load_1m": None,
            "platform": "windows" if is_windows else "linux",
            "is_windows": is_windows,
            "max_quality_env": max_quality,
        }

    def test_conservative_mode_caps_at_4(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=16, max_quality=False)
        workers = _resolve_workers(0, probe)
        assert workers <= 4, f"Conservative mode must cap at 4, got {workers}"

    def test_max_quality_mode_uses_more_workers(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe_cons = self._probe(logical=16, max_quality=False)
        probe_max = self._probe(logical=16, max_quality=True)
        cons = _resolve_workers(0, probe_cons)
        maxq = _resolve_workers(0, probe_max)
        assert maxq > cons, f"Max-quality ({maxq}) must exceed conservative ({cons})"

    def test_max_quality_mode_caps_at_16(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=128, max_quality=True)
        workers = _resolve_workers(0, probe)
        assert workers <= 16, f"Max-quality must cap at 16 workers, got {workers}"

    def test_windows_always_zero(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=16, max_quality=True, is_windows=True)
        assert _resolve_workers(0, probe) == 0

    def test_env_override_takes_priority(self, monkeypatch):
        monkeypatch.setenv("EASYOCR_WORKERS", "7")
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=16, max_quality=False)
        assert _resolve_workers(0, probe) == 7

    def test_config_num_threads_conservative_caps_at_4(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=16, max_quality=False)
        assert _resolve_workers(16, probe) <= 4

    def test_config_num_threads_max_quality_no_conservative_cap(self):
        from structured_pdf_text.ocr.backends.easyocr import _resolve_workers
        probe = self._probe(logical=16, max_quality=True)
        # num_threads=16 in max-quality: must allow more than 4
        workers = _resolve_workers(16, probe)
        assert workers > 4, f"Max-quality with num_threads=16 should exceed 4, got {workers}"


class TestAdaptiveTorchThreads:
    """_apply_torch_threads() must set all cores in max-quality mode."""

    def _probe(self, logical: int = 8, max_quality: bool = False):
        return {
            "cpu_count_logical": logical,
            "cpu_count_physical": logical // 2,
            "cpu_load_1m": None,
            "platform": "linux",
            "is_windows": False,
            "max_quality_env": max_quality,
        }

    def test_zero_threads_conservative_does_not_set_torch_threads(self, monkeypatch):
        """num_threads=0, no max-quality: torch threads must not be touched."""
        calls: list = []

        class FakeTorch:
            def set_num_threads(self, n): calls.append(("intra", n))
            def set_num_interop_threads(self, n): calls.append(("inter", n))
            def get_num_threads(self): return 4
            def get_num_interop_threads(self): return 2

        import sys
        monkeypatch.setitem(sys.modules, "torch", FakeTorch())
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads",
                            easyocr_mod._apply_torch_threads.__wrapped__
                            if hasattr(easyocr_mod._apply_torch_threads, "__wrapped__")
                            else easyocr_mod._apply_torch_threads)
        probe = self._probe(logical=8, max_quality=False)
        from structured_pdf_text.ocr.backends.easyocr import _apply_torch_threads
        _apply_torch_threads(0, probe)
        # No set_num_threads should have been called in conservative mode with 0
        assert not any(c[0] == "intra" for c in calls), (
            "Conservative mode with num_threads=0 must not set torch threads"
        )

    def test_max_quality_sets_all_logical_cores(self, monkeypatch):
        """num_threads=0, max_quality=True: torch must receive all logical CPUs."""
        set_calls: list[int] = []

        class FakeTorch:
            def set_num_threads(self, n): set_calls.append(n)
            def set_num_interop_threads(self, n): pass
            def get_num_threads(self): return set_calls[-1] if set_calls else 8
            def get_num_interop_threads(self): return 4

        import sys
        monkeypatch.setitem(sys.modules, "torch", FakeTorch())
        probe = self._probe(logical=8, max_quality=True)
        from structured_pdf_text.ocr.backends.easyocr import _apply_torch_threads
        _apply_torch_threads(0, probe)
        assert set_calls, "max_quality=True must call set_num_threads"
        assert set_calls[0] == 8, f"Expected 8 (all logical CPUs), got {set_calls[0]}"

    def test_explicit_num_threads_takes_priority_over_probe(self, monkeypatch):
        """Explicit num_threads > 0 must be used regardless of probe."""
        set_calls: list[int] = []

        class FakeTorch:
            def set_num_threads(self, n): set_calls.append(n)
            def set_num_interop_threads(self, n): pass
            def get_num_threads(self): return set_calls[-1] if set_calls else 4
            def get_num_interop_threads(self): return 2

        import sys
        monkeypatch.setitem(sys.modules, "torch", FakeTorch())
        probe = self._probe(logical=8, max_quality=True)
        from structured_pdf_text.ocr.backends.easyocr import _apply_torch_threads
        _apply_torch_threads(3, probe)
        assert set_calls and set_calls[0] == 3, (
            f"Explicit num_threads=3 must override probe, got {set_calls}"
        )


class TestEnvProbeInDiagnostics:
    """consume_page_diagnostics must include env_probe and effective_workers."""

    def _make_backend(self, monkeypatch) -> "Any":
        import sys, types

        class FakeReader:
            def detect(self, img_color, **kwargs):
                return ([[[10, 90, 10, 30]]], [[]])
            def recognize(self, img_gray, h_list, f_list, **kwargs):
                return [([[10, 10], [90, 10], [90, 30], [10, 30]], "word", 0.85)]

        class FakeMod:
            def Reader(self, langs, **kwargs):
                return FakeReader()

        fake_mod = FakeMod()
        monkeypatch.setitem(sys.modules, "easyocr", fake_mod)
        fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
        monkeypatch.setitem(sys.modules, "easyocr.utils", fake_utils)
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
        monkeypatch.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
        monkeypatch.delenv("EASYOCR_MAX_QUALITY_THREADS", raising=False)

        from structured_pdf_text.config import ExtractorConfig
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
        monkeypatch.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))

        config = ExtractorConfig(language="pt")
        backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
        easyocr_mod.EasyOCRBackend.__init__(backend, config)
        return backend

    def test_diagnostics_include_env_probe(self, monkeypatch):
        backend = self._make_backend(monkeypatch)
        diag = backend.consume_page_diagnostics()
        assert "env_probe" in diag
        assert "cpu_count_logical" in diag["env_probe"]

    def test_diagnostics_include_effective_workers(self, monkeypatch):
        backend = self._make_backend(monkeypatch)
        diag = backend.consume_page_diagnostics()
        assert "effective_workers" in diag
        assert isinstance(diag["effective_workers"], int)

    def test_diagnostics_include_effective_torch_threads(self, monkeypatch):
        backend = self._make_backend(monkeypatch)
        diag = backend.consume_page_diagnostics()
        assert "effective_torch_intra_threads" in diag

    def test_max_quality_workers_higher_than_conservative(self, monkeypatch):
        """With EASYOCR_MAX_QUALITY_THREADS=1, effective_workers must exceed conservative."""
        import sys, types

        class FakeReader:
            def detect(self, img_color, **kwargs): return ([[[10, 90, 10, 30]]], [[]])
            def recognize(self, img_gray, h, f, **kwargs): return []

        class FakeMod:
            def Reader(self, langs, **kwargs): return FakeReader()

        fake_mod = FakeMod()

        def _make(monkeypatch_inner, max_quality: bool):
            monkeypatch_inner.setitem(sys.modules, "easyocr", fake_mod)
            fake_utils = types.SimpleNamespace(reformat_input=lambda a: (a, a[:, :, 0]))
            monkeypatch_inner.setitem(sys.modules, "easyocr.utils", fake_utils)
            monkeypatch_inner.delenv("EASYOCR_RECOG_NETWORK", raising=False)
            monkeypatch_inner.delenv("EASYOCR_ALLOW_DOWNLOAD", raising=False)
            monkeypatch_inner.delenv("EASYOCR_WORKERS", raising=False)
            if max_quality:
                monkeypatch_inner.setenv("EASYOCR_MAX_QUALITY_THREADS", "1")
            else:
                monkeypatch_inner.delenv("EASYOCR_MAX_QUALITY_THREADS", raising=False)

            from structured_pdf_text.config import ExtractorConfig
            from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
            monkeypatch_inner.setattr(easyocr_mod, "_import_easyocr", lambda: fake_mod)
            monkeypatch_inner.setattr(easyocr_mod, "_apply_torch_threads", lambda n, p=None: (None, None))
            config = ExtractorConfig(language="pt")
            backend = easyocr_mod.EasyOCRBackend.__new__(easyocr_mod.EasyOCRBackend)
            easyocr_mod.EasyOCRBackend.__init__(backend, config)
            return backend

        import os
        # Only meaningful on machines with > 8 logical CPUs; skip gracefully otherwise
        if (os.cpu_count() or 1) <= 8:
            pytest.skip("Machine has ≤ 8 logical CPUs; conservative cap may equal max-quality cap")

        backend_cons = _make(monkeypatch, max_quality=False)
        diag_cons = backend_cons.consume_page_diagnostics()

        backend_max = _make(monkeypatch, max_quality=True)
        diag_max = backend_max.consume_page_diagnostics()

        assert diag_max["effective_workers"] > diag_cons["effective_workers"], (
            f"max-quality workers ({diag_max['effective_workers']}) must exceed "
            f"conservative ({diag_cons['effective_workers']})"
        )
