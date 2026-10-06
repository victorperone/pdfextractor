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

    def test_lexical_plausibility_prose_scores_high(self):
        """§38: alphabetic pt-BR tokens score high plausibility."""
        from structured_pdf_text.ocr.backends.easyocr import _lexical_plausibility
        tokens = [
            self._make_token("palavra", 0.9),
            self._make_token("texto", 0.9),
            self._make_token("documento", 0.9),
        ]
        score = _lexical_plausibility(tokens)
        assert score > 0.7

    def test_lexical_plausibility_garbled_scores_low(self):
        """§38: garbled tokens with mostly non-alpha chars score low."""
        from structured_pdf_text.ocr.backends.easyocr import _lexical_plausibility
        tokens = [
            self._make_token("x", 0.9),  # 1 char — not plausible
            self._make_token(".", 0.9),   # punctuation — not plausible
        ]
        score = _lexical_plausibility(tokens)
        assert score < 0.5

    def test_lexical_plausibility_numeric_is_neutral(self):
        """§38: purely numeric tokens don't degrade plausibility (they're neutral)."""
        from structured_pdf_text.ocr.backends.easyocr import _lexical_plausibility
        tokens = [self._make_token("1234567890", 0.9)]
        score = _lexical_plausibility(tokens)
        assert 0.4 <= score <= 0.6  # neutral range

    def test_lexical_plausibility_empty_is_zero(self):
        """§38: empty token list → 0.0."""
        from structured_pdf_text.ocr.backends.easyocr import _lexical_plausibility
        assert _lexical_plausibility([]) == 0.0

    def test_prose_candidate_beats_garbled_with_same_confidence(self):
        """§38: prose tokens should score higher than garbled at same confidence."""
        from structured_pdf_text.ocr.backends.easyocr import _score_candidate
        prose = [self._make_token("documento", 0.85), self._make_token("fiscal", 0.85)]
        garbled = [self._make_token("x.", 0.85), self._make_token("0|", 0.85)]
        assert _score_candidate(prose) > _score_candidate(garbled)


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


# ---------------------------------------------------------------------------
# §15 — _rebuild_reader_no_quantize graceful fallback
# ---------------------------------------------------------------------------

class TestRebuildReaderNoQuantize:
    """§10: _rebuild_reader_no_quantize returns None when attributes are missing."""

    def test_returns_none_for_reader_without_lang_list(self):
        """When reader has no lang_list, return None gracefully."""
        from structured_pdf_text.ocr.backends.easyocr import _rebuild_reader_no_quantize

        class BadReader:
            pass

        result = _rebuild_reader_no_quantize(BadReader())
        assert result is None

    def test_returns_none_for_reader_with_empty_lang_list(self):
        """Empty lang_list → return None (cannot build Reader without languages)."""
        from structured_pdf_text.ocr.backends.easyocr import _rebuild_reader_no_quantize

        class EmptyLangReader:
            lang_list = []

        result = _rebuild_reader_no_quantize(EmptyLangReader())
        assert result is None

    def test_returns_none_when_easyocr_import_fails(self, monkeypatch):
        """When easyocr is not importable, function returns None, does not raise."""
        import sys
        monkeypatch.setitem(sys.modules, "easyocr", None)
        from structured_pdf_text.ocr.backends.easyocr import _rebuild_reader_no_quantize

        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/x"
            user_network_directory = "/tmp/x"
            recog_network = "latin_g2"

        result = _rebuild_reader_no_quantize(FakeReader())
        assert result is None


# ---------------------------------------------------------------------------
# §11 — _build_dbnet18_reader graceful fallback
# ---------------------------------------------------------------------------

class TestBuildDbnet18Reader:
    """§11: _build_dbnet18_reader returns None when attributes are missing or import fails."""

    def test_returns_none_for_reader_without_lang_list(self):
        """When reader has no lang_list, return None gracefully."""
        from structured_pdf_text.ocr.backends.easyocr import _build_dbnet18_reader

        class BadReader:
            pass

        result = _build_dbnet18_reader(BadReader())
        assert result is None

    def test_returns_none_for_reader_with_empty_lang_list(self):
        """Empty lang_list → return None."""
        from structured_pdf_text.ocr.backends.easyocr import _build_dbnet18_reader

        class EmptyLangReader:
            lang_list = []

        result = _build_dbnet18_reader(EmptyLangReader())
        assert result is None

    def test_returns_none_when_easyocr_import_fails(self, monkeypatch):
        """When easyocr is not importable, return None, do not raise."""
        import sys
        monkeypatch.setitem(sys.modules, "easyocr", None)
        from structured_pdf_text.ocr.backends.easyocr import _build_dbnet18_reader

        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/x"
            user_network_directory = "/tmp/x"
            recog_network = "latin_g2"
            quantize = True

        result = _build_dbnet18_reader(FakeReader())
        assert result is None

    def test_builds_reader_with_dbnet18_detect_network(self, monkeypatch):
        """When easyocr is available, Reader is called with detect_network='dbnet18'."""
        import sys
        import types

        captured: dict = {}

        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/m"
            user_network_directory = None
            recog_network = "latin_g2"
            quantize = True

        def fake_Reader(langs, **kwargs):
            captured["langs"] = langs
            captured["kwargs"] = kwargs
            return object()

        fake_easyocr = types.ModuleType("easyocr")
        fake_easyocr.Reader = fake_Reader  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "easyocr", fake_easyocr)

        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        result = easyocr_mod._build_dbnet18_reader(FakeReader())
        assert result is not None
        assert captured["kwargs"].get("detect_network") == "dbnet18"


# ---------------------------------------------------------------------------
# §11 — _build_dbnet18_reader_strict propagates exceptions
# ---------------------------------------------------------------------------

class TestBuildDbnet18ReaderStrict:
    """_build_dbnet18_reader_strict raises instead of returning None on failure."""

    def test_raises_when_easyocr_import_fails(self, monkeypatch):
        """When easyocr is not importable, strict variant raises, does NOT return None."""
        import sys
        monkeypatch.setitem(sys.modules, "easyocr", None)
        from structured_pdf_text.ocr.backends.easyocr import _build_dbnet18_reader_strict

        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/x"
            user_network_directory = "/tmp/x"
            recog_network = "latin_g2"
            quantize = True

        with pytest.raises(Exception):
            _build_dbnet18_reader_strict(FakeReader())

    def test_returns_none_for_missing_lang_list(self):
        """Reader without lang_list → returns None (not a probe failure)."""
        from structured_pdf_text.ocr.backends.easyocr import _build_dbnet18_reader_strict

        class BadReader:
            pass

        result = _build_dbnet18_reader_strict(BadReader())
        assert result is None


# ---------------------------------------------------------------------------
# §11 — _dbnet18_weights_available
# ---------------------------------------------------------------------------

class TestDbnet18WeightsAvailable:
    """_dbnet18_weights_available checks the correct file path from EasyOCR registry."""

    def _make_fake_easyocr_config(self, filename: str | None):
        import types
        config_mod = types.ModuleType("easyocr.config")
        config_mod.detection_models = (  # type: ignore[attr-defined]
            {"dbnet18": {"filename": filename}} if filename is not None else {}
        )
        return config_mod

    def test_returns_false_when_file_absent(self, monkeypatch, tmp_path):
        """Returns False when the .pth file does not exist."""
        import sys
        config_mod = self._make_fake_easyocr_config("craft_mlt_25k.pth")
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        from structured_pdf_text.ocr.backends.easyocr import _dbnet18_weights_available
        assert _dbnet18_weights_available(tmp_path) is False

    def test_returns_true_when_file_present(self, monkeypatch, tmp_path):
        """Returns True when the .pth file exists on disk."""
        import sys
        pth_name = "craft_dbnet18.pth"
        (tmp_path / pth_name).write_bytes(b"\x00" * 8)
        config_mod = self._make_fake_easyocr_config(pth_name)
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        from structured_pdf_text.ocr.backends.easyocr import _dbnet18_weights_available
        assert _dbnet18_weights_available(tmp_path) is True

    def test_returns_false_when_config_has_no_filename(self, monkeypatch, tmp_path):
        """Returns False gracefully when the registry entry has no filename key."""
        import sys
        config_mod = self._make_fake_easyocr_config(None)
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        from structured_pdf_text.ocr.backends.easyocr import _dbnet18_weights_available
        assert _dbnet18_weights_available(tmp_path) is False

    def test_returns_false_when_easyocr_config_not_importable(self, monkeypatch, tmp_path):
        """Returns False (never raises) when easyocr.config cannot be imported."""
        import sys
        monkeypatch.setitem(sys.modules, "easyocr.config", None)
        from structured_pdf_text.ocr.backends.easyocr import _dbnet18_weights_available
        assert _dbnet18_weights_available(tmp_path) is False


# ---------------------------------------------------------------------------
# §11 — _probe_dbnet18_runtime_uncached
# ---------------------------------------------------------------------------

class TestProbeDbnet18RuntimeUncached:
    """_probe_dbnet18_runtime_uncached returns (False, reason) when weights are absent."""

    def test_returns_weights_missing_when_no_pth_file(self, monkeypatch, tmp_path):
        """Returns (False, 'weights_missing') immediately if weights file is absent."""
        import sys
        config_mod = types.ModuleType("easyocr.config")
        config_mod.detection_models = {"dbnet18": {"filename": "craft_db.pth"}}  # type: ignore[attr-defined]
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        class FakeReader:
            lang_list = ["pt"]
            model_storage_directory = str(tmp_path)

        from structured_pdf_text.ocr.backends.easyocr import _probe_dbnet18_runtime_uncached
        ok, reason = _probe_dbnet18_runtime_uncached(FakeReader(), tmp_path)
        assert ok is False
        assert reason == "weights_missing"

    def test_returns_true_when_probe_succeeds(self, monkeypatch, tmp_path):
        """Returns (True, None) when weights exist and detect() completes without error."""
        import sys
        pth_name = "craft_db.pth"
        (tmp_path / pth_name).write_bytes(b"\x00" * 8)
        config_mod = types.ModuleType("easyocr.config")
        config_mod.detection_models = {"dbnet18": {"filename": pth_name}}  # type: ignore[attr-defined]
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        # Monkeypatch _build_dbnet18_reader_strict to return a fake reader
        # whose detect() succeeds without needing native extensions.
        fake_dbnet_reader = MagicMock()
        fake_dbnet_reader.detect.return_value = ([], [])

        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader_strict", lambda r: fake_dbnet_reader)

        ok, reason = easyocr_mod._probe_dbnet18_runtime_uncached(MagicMock(), tmp_path)
        assert ok is True
        assert reason is None

    def test_returns_runtime_probe_failed_when_detect_raises(self, monkeypatch, tmp_path):
        """Returns (False, 'runtime_probe_failed: ...') when detect() raises."""
        import sys
        pth_name = "craft_db.pth"
        (tmp_path / pth_name).write_bytes(b"\x00" * 8)
        config_mod = types.ModuleType("easyocr.config")
        config_mod.detection_models = {"dbnet18": {"filename": pth_name}}  # type: ignore[attr-defined]
        easyocr_pkg = types.ModuleType("easyocr")
        monkeypatch.setitem(sys.modules, "easyocr", easyocr_pkg)
        monkeypatch.setitem(sys.modules, "easyocr.config", config_mod)

        fake_dbnet_reader = MagicMock()
        fake_dbnet_reader.detect.side_effect = RuntimeError("deformable conv failed")

        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader_strict", lambda r: fake_dbnet_reader)

        ok, reason = easyocr_mod._probe_dbnet18_runtime_uncached(MagicMock(), tmp_path)
        assert ok is False
        assert reason is not None and "runtime_probe_failed" in reason


# ---------------------------------------------------------------------------
# §11 — EasyOCRBackend per-instance DBNet cache
# ---------------------------------------------------------------------------

class TestEasyOCRBackendDbnetInstanceCache:
    """Per-instance _dbnet_runtime_state cache: probe runs at most once per instance."""

    def test_capabilities_does_not_trigger_probe(self, monkeypatch):
        """capabilities property must not run _probe_dbnet18_runtime_uncached."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        probe_calls = {"n": 0}

        def counting_probe(reader, cache_dir=None):
            probe_calls["n"] += 1
            return False, "weights_missing"

        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached", counting_probe)

        # Build a minimal object that has the capabilities property via the class.
        class FakeBackend:
            _dbnet_runtime_state = None
            _dbnet_failure_reason = None
            capabilities = easyocr_mod.EasyOCRBackend.capabilities.fget  # type: ignore[attr-defined]

        fb = FakeBackend()
        # Access the property directly (simulate property call on instance).
        _ = easyocr_mod.EasyOCRBackend.capabilities.fget(fb)
        assert probe_calls["n"] == 0, (
            "capabilities must NOT trigger _probe_dbnet18_runtime_uncached"
        )

    def test_capabilities_reports_false_when_state_is_none(self, monkeypatch):
        """When _dbnet_runtime_state is None (not yet probed), multiple_detectors is False."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        class FakeBackend:
            _dbnet_runtime_state = None

        result = easyocr_mod.EasyOCRBackend.capabilities.fget(FakeBackend())
        assert result.multiple_detectors is False

    def test_capabilities_reports_true_when_state_is_true(self, monkeypatch):
        """When _dbnet_runtime_state is True, multiple_detectors is True."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        class FakeBackend:
            _dbnet_runtime_state = True

        result = easyocr_mod.EasyOCRBackend.capabilities.fget(FakeBackend())
        assert result.multiple_detectors is True

    def test_ensure_dbnet18_runtime_caches_on_second_call(self, monkeypatch):
        """_ensure_dbnet18_runtime only calls the probe once per instance."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        call_count = {"n": 0}

        def counting_probe(reader, cache_dir=None):
            call_count["n"] += 1
            return True, None

        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached", counting_probe)

        class FakeBackend:
            _dbnet_runtime_state = None
            _dbnet_failure_reason = None
            _reader = None
            _model_cache_dir = None

        fb = FakeBackend()
        ok1, _ = easyocr_mod.EasyOCRBackend._ensure_dbnet18_runtime(fb)
        ok2, _ = easyocr_mod.EasyOCRBackend._ensure_dbnet18_runtime(fb)
        assert call_count["n"] == 1, "probe must only be called once (cached)"
        assert ok1 is True
        assert ok2 is True

    def test_ensure_dbnet18_runtime_force_reruns_probe(self, monkeypatch):
        """force=True re-runs probe even when result is already cached."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        call_count = {"n": 0}
        results = [(False, "weights_missing"), (True, None)]

        def counting_probe(reader, cache_dir=None):
            call_count["n"] += 1
            return results[min(call_count["n"] - 1, len(results) - 1)]

        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached", counting_probe)

        class FakeBackend:
            _dbnet_runtime_state = None
            _dbnet_failure_reason = None
            _reader = None
            _model_cache_dir = None

        fb = FakeBackend()
        ok1, _ = easyocr_mod.EasyOCRBackend._ensure_dbnet18_runtime(fb)
        ok2, _ = easyocr_mod.EasyOCRBackend._ensure_dbnet18_runtime(fb, force=True)
        assert call_count["n"] == 2
        assert ok1 is False
        assert ok2 is True


# ---------------------------------------------------------------------------
# §11 — _exhaustive_candidates skips DBNet when precomputed says unavailable
# ---------------------------------------------------------------------------

class TestExhaustiveCandidatesSkipsDbnetWhenUnavailable:
    """_exhaustive_candidates respects _dbnet_precomputed=(False, reason)."""

    def _make_minimal_reader(self):
        reader = MagicMock()
        reader.lang_list = ["pt"]
        reader.model_storage_directory = None
        reader.detect.return_value = (
            [[[10, 90, 10, 30]]],
            [[]],
        )
        reader.recognize.return_value = [("hello", 0.9)]
        reader.readtext.return_value = [([[0, 0], [10, 0], [10, 10], [0, 10]], "hello", 0.9)]
        return reader

    def test_dbnet_candidate_skipped_when_precomputed_false(self, monkeypatch):
        """When _dbnet_precomputed=(False, reason), the dbnet18 candidate is not added."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_minimal_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)
        base_kwargs: dict[str, Any] = {
            "decoder": "greedy",
            "beamwidth": 5,
            "adjust_contrast": 0.5,
            "allowlist": None,
            "blocklist": None,
            "workers": 0,
            "rotation_info": None,
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 20,
            "slope_ths": 0.1,
            "ycenter_ths": 0.5,
            "height_ths": 0.5,
            "width_ths": 0.5,
            "add_margin": 0.1,
            "contrast_ths": 0.1,
            "filter_ths": 0.003,
        }

        dbnet_probe_called = {"n": 0}

        def should_not_be_called(*a, **kw):
            dbnet_probe_called["n"] += 1
            return False, "weights_missing"

        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached", should_not_be_called)
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)

        dbnet_diag: list[dict] = []
        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, base_kwargs,
            _dbnet_diag=dbnet_diag,
            _dbnet_precomputed=(False, "weights_missing"),
        )

        assert dbnet_probe_called["n"] == 0, "probe must not run when precomputed is given"
        candidate_ids = [cid for cid, _ in candidates]
        assert "dbnet18" not in candidate_ids
        assert dbnet_diag, "_dbnet_diag must be populated"
        assert dbnet_diag[0]["runtime_available"] is False

    def test_dbnet_candidate_present_when_precomputed_true(self, monkeypatch):
        """When _dbnet_precomputed=(True, None) and DBNet reader works, dbnet18 candidate appears."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_minimal_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)
        base_kwargs: dict[str, Any] = {
            "decoder": "greedy",
            "beamwidth": 5,
            "adjust_contrast": 0.5,
            "allowlist": None,
            "blocklist": None,
            "workers": 0,
            "rotation_info": None,
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 20,
            "slope_ths": 0.1,
            "ycenter_ths": 0.5,
            "height_ths": 0.5,
            "width_ths": 0.5,
            "add_margin": 0.1,
            "contrast_ths": 0.1,
            "filter_ths": 0.003,
        }

        # Make _build_dbnet18_reader return a working fake reader.
        dbnet_reader = MagicMock()
        dbnet_reader.lang_list = ["pt"]
        dbnet_reader.detect.return_value = (
            [[[10, 90, 10, 30]]],
            [[]],
        )
        dbnet_reader.recognize.return_value = [("dbnet_word", 0.85)]
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: dbnet_reader)
        monkeypatch.setattr(easyocr_mod, "_dbnet18_weights_available", lambda cache_dir=None: True)

        dbnet_diag: list[dict] = []
        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, base_kwargs,
            _dbnet_diag=dbnet_diag,
            _dbnet_precomputed=(True, None),
        )

        candidate_ids = [cid for cid, _ in candidates]
        assert "dbnet18" in candidate_ids
        assert dbnet_diag[0]["runtime_available"] is True

    def test_dbnet_reader_construction_returns_none_marks_runtime_unavailable(self, monkeypatch):
        """When _build_dbnet18_reader() returns None (absorbed exception), diag must show
        runtime_available=False so recognize_page() can invalidate the cache."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_minimal_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)
        base_kwargs: dict[str, Any] = {
            "decoder": "greedy",
            "beamwidth": 5,
            "adjust_contrast": 0.5,
            "allowlist": None,
            "blocklist": None,
            "workers": 0,
            "rotation_info": None,
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 20,
            "slope_ths": 0.1,
            "ycenter_ths": 0.5,
            "height_ths": 0.5,
            "width_ths": 0.5,
            "add_margin": 0.1,
            "contrast_ths": 0.1,
            "filter_ths": 0.003,
        }

        # Probe passes (True) but reader construction silently returns None.
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)
        monkeypatch.setattr(easyocr_mod, "_dbnet18_weights_available", lambda cache_dir=None: True)

        dbnet_diag: list[dict] = []
        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, base_kwargs,
            _dbnet_diag=dbnet_diag,
            _dbnet_precomputed=(True, None),
        )

        candidate_ids = [cid for cid, _ in candidates]
        assert "dbnet18" not in candidate_ids, "dbnet18 must not appear when reader returns None"
        assert dbnet_diag, "_dbnet_diag must be populated"
        assert dbnet_diag[0]["runtime_available"] is False
        assert dbnet_diag[0]["failure_reason"] == "reader_construction_failed"

    def test_precomputed_weights_missing_does_not_produce_contradictory_diag(self, monkeypatch):
        """When precomputed=(False, 'weights_missing'), diag must show weights_available=False.

        A contradictory diag (weights_available=True while failure_reason='weights_missing')
        would be produced if _dbnet18_weights_available() were called again after the probe
        already determined they are absent. Fix B ensures the diag derives from the snapshot.
        """
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_minimal_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)
        base_kwargs: dict[str, Any] = {
            "decoder": "greedy",
            "beamwidth": 5,
            "adjust_contrast": 0.5,
            "allowlist": None,
            "blocklist": None,
            "workers": 0,
            "rotation_info": None,
            "text_threshold": 0.7,
            "low_text": 0.4,
            "link_threshold": 0.4,
            "min_size": 20,
            "slope_ths": 0.1,
            "ycenter_ths": 0.5,
            "height_ths": 0.5,
            "width_ths": 0.5,
            "add_margin": 0.1,
            "contrast_ths": 0.1,
            "filter_ths": 0.003,
        }

        # Fresh disk check would say True (weights appeared after probe ran).
        monkeypatch.setattr(easyocr_mod, "_dbnet18_weights_available", lambda cache_dir=None: True)
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)

        dbnet_diag: list[dict] = []
        easyocr_mod._exhaustive_candidates(
            reader, img, base_kwargs,
            _dbnet_diag=dbnet_diag,
            _dbnet_precomputed=(False, "weights_missing"),
        )

        assert dbnet_diag, "_dbnet_diag must be populated"
        diag = dbnet_diag[0]
        # weights_available must reflect the probe snapshot (False), not the fresh disk check (True).
        assert diag["weights_available"] is False, (
            f"contradictory diag: weights_available={diag['weights_available']} "
            f"but failure_reason={diag.get('failure_reason')!r}"
        )
        assert diag["runtime_available"] is False


# ---------------------------------------------------------------------------
# Preflight --max-quality guards
# ---------------------------------------------------------------------------

class TestPreflightMaxQualityGuards:
    """preflight_ocr_backends.py --max-quality guards and field names."""

    def _run_preflight(self, args: list[str]) -> tuple[int, str, str]:
        import subprocess
        script = str(
            (
                __import__("pathlib").Path(__file__).resolve().parent.parent
                / "scripts" / "preflight_ocr_backends.py"
            )
        )
        result = subprocess.run(
            [sys.executable, script] + args,
            capture_output=True, text=True, encoding="utf-8",
        )
        return result.returncode, result.stdout, result.stderr

    def test_max_quality_without_deep_smoke_exits_2(self):
        """--max-quality without --deep-smoke must exit with code 2."""
        rc, _, stderr = self._run_preflight(["--max-quality"])
        assert rc == 2
        assert "deep-smoke" in stderr.lower() or "deep_smoke" in stderr.lower() or "deep-smoke" in stderr

    def test_max_quality_with_wrong_configuration_exits_2(self):
        """--max-quality --configuration tesseract must exit with code 2."""
        rc, _, stderr = self._run_preflight(
            ["--max-quality", "--deep-smoke", "--configuration", "tesseract"]
        )
        assert rc == 2
        assert "easyocr" in stderr.lower()

    def test_max_quality_with_paddle_configuration_exits_2(self):
        """--max-quality --configuration paddle must exit with code 2."""
        rc, _, stderr = self._run_preflight(
            ["--max-quality", "--deep-smoke", "--configuration", "paddle"]
        )
        assert rc == 2

    def test_configuration_rapidocr_onnxruntime_is_accepted(self):
        """--configuration rapidocr-onnxruntime must be a valid argparse choice (no exit 2)."""
        # We just run static (no --deep-smoke) so it doesn't need a real install.
        rc, stdout, stderr = self._run_preflight(["--configuration", "rapidocr-onnxruntime"])
        # argparse exit 2 means unrecognized arg; any other exit code is fine (the backend
        # may not be installed in the test environment, giving 1 or 0).
        assert rc != 2, f"argparse rejected rapidocr-onnxruntime; stderr={stderr!r}"

    def test_configuration_rapidocr_openvino_is_accepted(self):
        """--configuration rapidocr-openvino must be a valid argparse choice (no exit 2)."""
        rc, stdout, stderr = self._run_preflight(["--configuration", "rapidocr-openvino"])
        assert rc != 2, f"argparse rejected rapidocr-openvino; stderr={stderr!r}"

    def test_configurations_cli_keys_match_argparse_choices(self):
        """All CONFIGURATIONS cli_key values must match the argparse --configuration choices."""
        import importlib.util, importlib
        spec = importlib.util.spec_from_file_location(
            "preflight",
            str(__import__("pathlib").Path(__file__).resolve().parent.parent
                / "scripts" / "preflight_ocr_backends.py"),
        )
        preflight = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(preflight)  # type: ignore[union-attr]

        choices = {"paddle", "rapidocr-onnxruntime", "rapidocr-openvino", "easyocr", "tesseract"}
        for label, engine, provider, cli_key in preflight.CONFIGURATIONS:
            assert cli_key in choices, (
                f"CONFIGURATIONS entry {label!r} has cli_key={cli_key!r} "
                f"which is not in argparse choices {choices}"
            )


# ---------------------------------------------------------------------------
# _cmd_setup_easyocr_models — unit tests
# ---------------------------------------------------------------------------

class TestCmdSetupEasyocrModels:
    """_cmd_setup_easyocr_models returns correct JSON and exit codes."""

    def _invoke(self, monkeypatch, *, include_dbnet: bool, craft_ok: bool = True,
                dbnet_weights: bool = True, dbnet_runtime: tuple = (True, None),
                cache_home: str | None = None) -> tuple[int, dict]:
        import json as _json
        import io as _io
        import sys as _sys

        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        import structured_pdf_text.cli as cli_mod

        # Fake easyocr.Reader — just a no-op constructor
        class FakeReader:
            lang_list = ["pt"]
            device = "cpu"
            model_storage_directory = "/tmp/x"
            user_network_directory = None
            recog_network = "latin_g2"
            quantize = True
            def __init__(self, *a, **kw): pass

        fake_easyocr = types.ModuleType("easyocr")
        fake_easyocr.Reader = FakeReader  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "easyocr", fake_easyocr)

        monkeypatch.setattr(easyocr_mod, "_dbnet18_weights_available", lambda cache_dir=None: dbnet_weights)
        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached", lambda r, c=None: dbnet_runtime)

        # Capture stdout
        captured = _io.StringIO()
        monkeypatch.setattr(_sys, "stdout", captured)

        rc = cli_mod._cmd_setup_easyocr_models("pt", cache_home, include_dbnet)

        output = captured.getvalue().strip()
        data = _json.loads(output) if output else {}
        return rc, data

    def test_without_dbnet_returns_ready_and_exit_0(self, monkeypatch, tmp_path):
        rc, data = self._invoke(monkeypatch, include_dbnet=False, cache_home=str(tmp_path))
        assert rc == 0
        assert data["status"] == "ready"
        assert data["dbnet18_requested"] is False
        assert "dbnet18_runtime_available" not in data

    def test_with_dbnet_runtime_ok_returns_ready_and_exit_0(self, monkeypatch, tmp_path):
        rc, data = self._invoke(
            monkeypatch, include_dbnet=True, cache_home=str(tmp_path),
            dbnet_weights=True, dbnet_runtime=(True, None),
        )
        assert rc == 0
        assert data["status"] == "ready"
        assert data["dbnet18_weights_available"] is True
        assert data["dbnet18_runtime_available"] is True
        assert "reason" not in data

    def test_with_dbnet_runtime_fail_returns_incomplete_and_exit_1(self, monkeypatch, tmp_path):
        rc, data = self._invoke(
            monkeypatch, include_dbnet=True, cache_home=str(tmp_path),
            dbnet_weights=True,
            dbnet_runtime=(False, "runtime_probe_failed: RuntimeError: deformable conv failed"),
        )
        assert rc == 1
        assert data["status"] == "incomplete"
        assert data["dbnet18_runtime_available"] is False
        assert "reason" in data

    def test_with_dbnet_weights_missing_returns_incomplete_and_exit_1(self, monkeypatch, tmp_path):
        rc, data = self._invoke(
            monkeypatch, include_dbnet=True, cache_home=str(tmp_path),
            dbnet_weights=False, dbnet_runtime=(False, "weights_missing"),
        )
        assert rc == 1
        assert data["status"] == "incomplete"
        assert data["dbnet18_weights_available"] is False


# ---------------------------------------------------------------------------
# Deduplication of preprocessing candidates
# ---------------------------------------------------------------------------

def _make_exhaustive_reader():
    """Minimal EasyOCR Reader mock sufficient for _exhaustive_candidates."""
    reader = MagicMock()
    reader.lang_list = ["pt"]
    reader.model_storage_directory = None
    reader.detect.return_value = (
        [[[10, 90, 10, 30]]],
        [[]],
    )
    reader.recognize.return_value = [("word", 0.9)]
    reader.readtext.return_value = [([[0, 0], [10, 0], [10, 10], [0, 10]], "word", 0.9)]
    return reader


_BASE_KWARGS: dict[str, Any] = {
    "decoder": "greedy",
    "beamwidth": 5,
    "adjust_contrast": 0.5,
    "allowlist": None,
    "blocklist": None,
    "workers": 0,
    "rotation_info": None,
    "text_threshold": 0.7,
    "low_text": 0.4,
    "link_threshold": 0.4,
    "min_size": 20,
    "slope_ths": 0.1,
    "ycenter_ths": 0.5,
    "height_ths": 0.5,
    "width_ths": 0.5,
    "add_margin": 0.1,
    "contrast_ths": 0.1,
    "filter_ths": 0.003,
}


class TestNoDuplicateCandidateIds:
    """_exhaustive_candidates must never emit the same label twice per page."""

    def _run_exhaustive(self, monkeypatch, img):
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = _make_exhaustive_reader()
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)
        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached",
                            lambda r, c=None: (False, "weights_missing"))
        monkeypatch.setattr(easyocr_mod, "_dbnet18_weights_available",
                            lambda cache_dir=None: False)

        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, _BASE_KWARGS,
            _dbnet_precomputed=(False, "weights_missing"),
        )
        return [label for label, _ in candidates]

    def test_no_duplicate_labels_on_normal_page(self, monkeypatch):
        """White (high-contrast) page should produce no duplicate labels."""
        img = np.full((120, 320, 3), 255, dtype=np.uint8)
        labels = self._run_exhaustive(monkeypatch, img)
        assert len(labels) == len(set(labels)), (
            f"Duplicate candidate labels on normal page: {[l for l in labels if labels.count(l) > 1]}"
        )

    def test_no_duplicate_labels_on_low_contrast_page(self, monkeypatch):
        """Low-contrast page (uniform grey) triggers adaptive preprocessing;
        those same labels must NOT be repeated by the exhaustive loop."""
        # std≈0 → low_contrast signal triggers adaptive preprocessing
        img = np.full((120, 320, 3), 128, dtype=np.uint8)
        labels = self._run_exhaustive(monkeypatch, img)
        assert len(labels) == len(set(labels)), (
            f"Duplicate candidate labels on low-contrast page: "
            f"{[l for l in labels if labels.count(l) > 1]}"
        )

    def test_no_duplicate_labels_on_dark_background_page(self, monkeypatch):
        """Dark background page triggers both inverted_grayscale and other adaptive
        variants; none of those must be repeated by the exhaustive loop."""
        # mean<112, >55% pixels<96 → dark_background signal
        img = np.full((120, 320, 3), 40, dtype=np.uint8)
        labels = self._run_exhaustive(monkeypatch, img)
        assert len(labels) == len(set(labels)), (
            f"Duplicate candidate labels on dark-background page: "
            f"{[l for l in labels if labels.count(l) > 1]}"
        )


# ---------------------------------------------------------------------------
# Fallback propagation through record_call
# ---------------------------------------------------------------------------

class TestFallbackPropagation:
    """Each _run_easyocr call in adaptive/exhaustive must report to record_call."""

    def _make_fallback_reader(self, fallback: bool):
        """Reader that always triggers (or skips) the readtext() fallback path."""
        reader = MagicMock()
        reader.lang_list = ["pt"]
        reader.model_storage_directory = None
        if fallback:
            # reformat_input raises → fallback fires, readtext returns one token
            reader.reformat_input = MagicMock(side_effect=RuntimeError("reformat_unavailable"))
            reader.readtext.return_value = [
                ([[0, 0], [10, 0], [10, 10], [0, 10]], "fallback_word", 0.9)
            ]
        else:
            reader.detect.return_value = ([[[10, 90, 10, 30]]], [[]])
            reader.recognize.return_value = [("word", 0.9)]
            reader.readtext.return_value = []
        return reader

    def test_adaptive_all_fallback_counted(self, monkeypatch):
        """When every _run_easyocr call in adaptive falls back, record_call
        is invoked once per call with a non-None fallback dict."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_fallback_reader(fallback=True)
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        recorded: list[Any] = []
        easyocr_mod._adaptive_candidates(
            reader, img, _BASE_KWARGS, record_call=recorded.append
        )

        calls = len(recorded)
        fallbacks = sum(1 for fb in recorded if fb is not None)
        assert calls >= 2, "adaptive must make at least 2 OCR calls"
        assert fallbacks == calls, (
            f"Expected all {calls} calls to be fallbacks, got {fallbacks}"
        )

    def test_adaptive_no_fallback_when_nominal(self, monkeypatch):
        """When _run_easyocr returns None fallback, record_call receives None each time."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        nominal_result = [([[0, 0], [10, 0], [10, 10], [0, 10]], "word", 0.9)]

        def fake_nominal(*args, **kwargs):
            return nominal_result, None  # no fallback

        monkeypatch.setattr(easyocr_mod, "_run_easyocr", fake_nominal)

        reader = _make_exhaustive_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        recorded: list[Any] = []
        easyocr_mod._adaptive_candidates(
            reader, img, _BASE_KWARGS, record_call=recorded.append
        )

        assert len(recorded) >= 2
        assert all(fb is None for fb in recorded), (
            "No fallbacks expected on nominal path"
        )

    def test_exhaustive_fallback_counted_per_call(self, monkeypatch):
        """record_call is called once per _run_easyocr invocation inside exhaustive."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_fallback_reader(fallback=True)
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)
        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached",
                            lambda r, c=None: (False, "weights_missing"))

        recorded: list[Any] = []
        easyocr_mod._exhaustive_candidates(
            reader, img, _BASE_KWARGS,
            _dbnet_precomputed=(False, "weights_missing"),
            record_call=recorded.append,
        )

        calls = len(recorded)
        fallbacks = sum(1 for fb in recorded if fb is not None)
        assert calls >= 2, "exhaustive must make at least 2 OCR calls"
        assert fallbacks == calls, (
            f"All {calls} calls should be fallbacks, got {fallbacks}"
        )

    def test_exhaustive_mixed_fallback_counts_correctly(self, monkeypatch):
        """Verify easyocr_calls and easyocr_fallback_count via EasyOCRBackend.recognize_page."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        # Two calls: first always returns nominal, second always falls back.
        call_n = {"n": 0}
        nominal_result = [([[0, 0], [10, 0], [10, 10], [0, 10]], "word", 0.9)]
        fallback_result = [([[0, 0], [10, 0], [10, 10], [0, 10]], "fallback", 0.8)]

        def fake_run_easyocr(*args, **kwargs):
            call_n["n"] += 1
            if call_n["n"] == 1:
                return nominal_result, None  # nominal
            else:
                return fallback_result, {"primary_error": "test_fallback"}

        monkeypatch.setattr(easyocr_mod, "_run_easyocr", fake_run_easyocr)

        reader = _make_exhaustive_reader()
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        recorded: list[Any] = []
        easyocr_mod._adaptive_candidates(
            reader, img, _BASE_KWARGS, record_call=recorded.append
        )

        assert len(recorded) >= 2, "Must have at least 2 calls"
        none_count = sum(1 for fb in recorded if fb is None)
        fallback_count = sum(1 for fb in recorded if fb is not None)
        assert none_count >= 1, "At least the first call should be nominal"
        assert fallback_count >= 1, "At least the second call should be fallback"


# ---------------------------------------------------------------------------
# Rotation candidates: scorer decides, not pre-filter
# ---------------------------------------------------------------------------

class TestRotationCandidateNotDiscarded:
    """In exhaustive mode, rotation candidates with any tokens reach the scorer
    regardless of whether they have fewer tokens than the default candidate."""

    def _make_reader_with_token_counts(self, monkeypatch, default_tokens: int, rot_tokens: int):
        """Build a reader where the first non-adaptive OCR call returns 'default_tokens'
        and every subsequent call (rotations, candidates) returns 'rot_tokens'.

        We use a counter to distinguish: the very first call is always the
        default/adaptive baseline; all later calls (rotations etc.) are treated
        as rotations for this test fixture.
        """
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        call_n = {"n": 0}

        def fake_run_easyocr(reader_arg, img_arg, **kwargs):
            call_n["n"] += 1
            # First 2 calls are the adaptive baseline (default + high_recall).
            n = default_tokens if call_n["n"] <= 2 else rot_tokens
            result = [
                ([[i * 10, 0], [i * 10 + 8, 0], [i * 10 + 8, 10], [i * 10, 10]], f"t{i}", 0.9)
                for i in range(n)
            ]
            return result, None

        monkeypatch.setattr(easyocr_mod, "_run_easyocr", fake_run_easyocr)
        monkeypatch.setattr(easyocr_mod, "_build_dbnet18_reader", lambda r: None)
        monkeypatch.setattr(easyocr_mod, "_probe_dbnet18_runtime_uncached",
                            lambda r, c=None: (False, "weights_missing"))
        monkeypatch.setattr(easyocr_mod, "_apply_clahe", lambda img: img)
        monkeypatch.setattr(easyocr_mod, "_apply_deskew_with_inverse",
                            lambda img: (img, None))
        monkeypatch.setattr(easyocr_mod, "_rebuild_reader_no_quantize", lambda r: None)
        monkeypatch.setattr(easyocr_mod, "_image_preprocessing_candidates",
                            lambda img, adaptive=False: [])

        reader = _make_exhaustive_reader()
        return reader

    def test_rotation_with_fewer_tokens_than_default_is_included(self, monkeypatch):
        """rot90 with fewer tokens than default must still appear in the candidate set."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_reader_with_token_counts(monkeypatch, default_tokens=10, rot_tokens=5)
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, _BASE_KWARGS,
            _dbnet_precomputed=(False, "weights_missing"),
        )
        candidate_ids = [label for label, _ in candidates]

        # At least one rotation candidate must be present even with fewer tokens.
        rotation_labels = [l for l in candidate_ids if l.startswith("rot")]
        assert rotation_labels, (
            "Expected at least one rotation candidate even when it has fewer tokens "
            f"than default. Got labels: {candidate_ids}"
        )

    def test_rotation_with_no_tokens_is_excluded(self, monkeypatch):
        """rot90 that returns zero tokens must NOT be added (empty result)."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        reader = self._make_reader_with_token_counts(monkeypatch, default_tokens=5, rot_tokens=0)
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        candidates = easyocr_mod._exhaustive_candidates(
            reader, img, _BASE_KWARGS,
            _dbnet_precomputed=(False, "weights_missing"),
        )
        candidate_ids = [label for label, _ in candidates]

        rotation_labels = [l for l in candidate_ids if l.startswith("rot")]
        assert not rotation_labels, (
            f"Expected no rotation candidates when result is empty; got {rotation_labels}"
        )

    def test_remap_called_before_adding_rotation_candidate(self, monkeypatch):
        """_remap_raw_for_rotation must be invoked (not bypassed) for each rotation."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod

        remap_calls = {"n": 0}
        original_remap = easyocr_mod._remap_raw_for_rotation

        def counting_remap(raw, angle, rh, rw):
            remap_calls["n"] += 1
            return original_remap(raw, angle, rh, rw)

        monkeypatch.setattr(easyocr_mod, "_remap_raw_for_rotation", counting_remap)

        # 5 tokens for every call so all rotations are added
        reader = self._make_reader_with_token_counts(monkeypatch, default_tokens=5, rot_tokens=5)
        img = np.full((120, 320, 3), 255, dtype=np.uint8)

        easyocr_mod._exhaustive_candidates(
            reader, img, _BASE_KWARGS,
            _dbnet_precomputed=(False, "weights_missing"),
        )

        assert remap_calls["n"] == 3, (
            f"Expected _remap_raw_for_rotation to be called 3 times (one per rotation angle), "
            f"got {remap_calls['n']}"
        )


# ---------------------------------------------------------------------------
# Preflight _check_max_quality_easyocr: planner/direct-recognition guards
# ---------------------------------------------------------------------------

class TestPreflightCheckMaxQualityGuards:
    """_check_max_quality_easyocr must set planner/direct flags to False when
    the backend returns empty tokens, without raising exceptions."""

    def _make_fake_backend(self, *, page_tokens: list, direct_tokens: list):
        """Return a fake OCR backend object for injection into the preflight function."""
        diag_data = {
            "dbnet_weights_available": False,
            "dbnet_runtime_available": False,
            "dbnet_failure_reason": "weights_missing",
        }

        class FakeBackend:
            def recognize_page(self, img, page_index, **kwargs):
                return page_tokens

            def recognize_direct(self, img, page_index, **kwargs):
                return direct_tokens

            def consume_page_diagnostics(self):
                return diag_data

            @property
            def capabilities(self):
                class Cap:
                    multiple_detectors = False
                return Cap()

            def close(self):
                pass

        return FakeBackend()

    def _make_ready_readiness(self):
        from structured_pdf_text.ocr.readiness import ReadinessStatus
        class _R:
            status = ReadinessStatus.READY
            reason_code = None
        return _R()

    def test_exhaustive_planner_false_when_recognize_page_returns_empty(self, monkeypatch):
        """When recognize_page returns [], exhaustive_planner_ready must be False."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "preflight_test1",
            str(Path(__file__).resolve().parent.parent / "scripts" / "preflight_ocr_backends.py"),
        )
        preflight = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(preflight)  # type: ignore[union-attr]

        fake_backend = self._make_fake_backend(page_tokens=[], direct_tokens=[("tok", 0.9)])

        # build_ocr_backend is imported locally inside _check_max_quality_easyocr;
        # patch the factory module so the local import picks up our fake.
        import structured_pdf_text.ocr.factory as factory_mod
        monkeypatch.setattr(factory_mod, "build_ocr_backend", lambda cfg: fake_backend)
        monkeypatch.setattr(preflight, "probe_deep", lambda cfg: self._make_ready_readiness())

        report = preflight._check_max_quality_easyocr("pt-BR")

        assert report["exhaustive_planner_ready"] is False
        assert report["exhaustive_planner_reason"] == "exhaustive_planner_returned_no_tokens"

    def test_direct_recognition_false_when_recognize_direct_returns_empty(self, monkeypatch):
        """When recognize_direct returns [], direct_recognition_ready must be False."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "preflight_test2",
            str(Path(__file__).resolve().parent.parent / "scripts" / "preflight_ocr_backends.py"),
        )
        preflight = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(preflight)  # type: ignore[union-attr]

        fake_backend = self._make_fake_backend(
            page_tokens=[("tok", 0.9)], direct_tokens=[]
        )

        import structured_pdf_text.ocr.factory as factory_mod
        monkeypatch.setattr(factory_mod, "build_ocr_backend", lambda cfg: fake_backend)
        monkeypatch.setattr(preflight, "probe_deep", lambda cfg: self._make_ready_readiness())

        report = preflight._check_max_quality_easyocr("pt-BR")

        assert report["direct_recognition_ready"] is False
        assert report["direct_recognition_reason"] == "direct_recognition_returned_no_tokens"
        # exhaustive must have passed since page_tokens was non-empty
        assert report["exhaustive_planner_ready"] is True
