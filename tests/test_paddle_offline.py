"""Offline OCR model validation tests (P0.10).

All tests run without loading paddle weights. A fake PaddleOCR class is used to
capture constructor keyword arguments and verify that the engine configures
local model directories correctly before any network access could occur.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from structured_pdf_text.ocr.paddle import (
    PaddleOcrEngine,
    PaddleOcrUnavailable,
    _LOCAL_MODEL_DIRECTORIES,
    validate_local_ocr_models,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model_dirs(root: Path, names: list[str] | None = None) -> None:
    """Create non-empty model directories under root/official_models/."""
    if names is None:
        names = list(_LOCAL_MODEL_DIRECTORIES.values())
    for name in names:
        model_dir = root / "official_models" / name
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.pdparams").write_text("fake", encoding="utf-8")


class FakePaddleOCR:
    """Captures constructor kwargs for assertion without loading any weights."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _make_engine(
    tmp_path: Path,
    language: str = "pt",
    **extra_options: Any,
) -> PaddleOcrEngine:
    return PaddleOcrEngine(language=language, cache_home=str(tmp_path), **extra_options)


# ---------------------------------------------------------------------------
# Case A — explicit model dirs are passed to PaddleOCR constructor
# ---------------------------------------------------------------------------

def test_explicit_model_dirs_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_model_dirs(tmp_path)

    captured: dict[str, Any] = {}

    def fake_paddle_ocr(**kwargs: Any) -> FakePaddleOCR:
        captured.update(kwargs)
        return FakePaddleOCR(**kwargs)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(fake_paddle_ocr)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path)
    engine._get_ocr()

    required_dirs = (
        "doc_orientation_classify_model_dir",
        "textline_orientation_model_dir",
        "text_detection_model_dir",
        "text_recognition_model_dir",
    )
    for key in required_dirs:
        assert key in captured, f"Expected {key!r} to be passed to PaddleOCR"
        assert captured[key], f"Expected {key!r} to be a non-empty path"


# ---------------------------------------------------------------------------
# Case B — PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK is set to "True"
# ---------------------------------------------------------------------------

def test_source_check_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_model_dirs(tmp_path)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(FakePaddleOCR)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path)
    engine._get_ocr()

    assert os.environ.get("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK") == "True"


# ---------------------------------------------------------------------------
# Case C — missing model dir raises PaddleOcrUnavailable before PaddleOCR
# ---------------------------------------------------------------------------

def test_missing_model_raises_before_paddleocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Do NOT create model dirs — they are all missing.
    paddleocr_called = {"called": False}

    def fake_paddle_ocr(**kwargs: Any) -> FakePaddleOCR:
        paddleocr_called["called"] = True
        return FakePaddleOCR(**kwargs)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(fake_paddle_ocr)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path)

    with pytest.raises(PaddleOcrUnavailable):
        engine._get_ocr()

    assert not paddleocr_called["called"], "PaddleOCR must not be called when models are missing"


# ---------------------------------------------------------------------------
# Case D — model name/dir pairs are consistent
# ---------------------------------------------------------------------------

_EXPECTED_NAME_TO_DIR_KEY = {
    "PP-LCNet_x1_0_doc_ori": "doc_orientation_classify_model_dir",
    "PP-LCNet_x1_0_textline_ori": "textline_orientation_model_dir",
    "PP-OCRv5_server_det": "text_detection_model_dir",
    "latin_PP-OCRv5_mobile_rec": "text_recognition_model_dir",
}

_NAME_KEYS = {
    "PP-LCNet_x1_0_doc_ori": "doc_orientation_classify_model_name",
    "PP-LCNet_x1_0_textline_ori": "textline_orientation_model_name",
    "PP-OCRv5_server_det": "text_detection_model_name",
    "latin_PP-OCRv5_mobile_rec": "text_recognition_model_name",
}


def test_model_name_dir_pairs_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_model_dirs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_paddle_ocr(**kwargs: Any) -> FakePaddleOCR:
        captured.update(kwargs)
        return FakePaddleOCR(**kwargs)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(fake_paddle_ocr)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path)
    engine._get_ocr()

    for model_name, dir_key in _EXPECTED_NAME_TO_DIR_KEY.items():
        name_key = _NAME_KEYS[model_name]
        assert name_key in captured, f"Expected {name_key!r} to be passed"
        assert captured[name_key] == model_name, (
            f"{name_key!r} should be {model_name!r}, got {captured[name_key]!r}"
        )
        assert dir_key in captured, f"Expected {dir_key!r} to be passed"
        # The dir path must contain the model name as its last component.
        assert Path(captured[dir_key]).name == model_name, (
            f"{dir_key!r} path tail should be {model_name!r}, "
            f"got {Path(captured[dir_key]).name!r}"
        )


# ---------------------------------------------------------------------------
# Case E — custom cache_home produces paths only under that root
# ---------------------------------------------------------------------------

def test_custom_cache_home_uses_only_that_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_model_dirs(tmp_path)
    captured: dict[str, Any] = {}

    def fake_paddle_ocr(**kwargs: Any) -> FakePaddleOCR:
        captured.update(kwargs)
        return FakePaddleOCR(**kwargs)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(fake_paddle_ocr)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path)
    engine._get_ocr()

    dir_keys = (
        "doc_orientation_classify_model_dir",
        "textline_orientation_model_dir",
        "text_detection_model_dir",
        "text_recognition_model_dir",
    )
    resolved_root = tmp_path.resolve()
    for key in dir_keys:
        assert key in captured
        path = Path(captured[key])
        assert path.is_relative_to(resolved_root), (
            f"{key!r} path {path} is not under {resolved_root}"
        )


# ---------------------------------------------------------------------------
# Case F — unknown language without explicit recognition dir fails locally
# ---------------------------------------------------------------------------

def test_unknown_language_without_recognition_dir_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "en" has no registered profile — must fail with a clear ValueError before
    # any network access or PaddleOCR initialization is attempted.
    _make_model_dirs(tmp_path)

    paddleocr_called = {"called": False}

    def fake_paddle_ocr(**kwargs: Any) -> FakePaddleOCR:
        paddleocr_called["called"] = True
        return FakePaddleOCR(**kwargs)

    fake_module = type("paddleocr", (), {"PaddleOCR": staticmethod(fake_paddle_ocr)})()
    monkeypatch.setitem(__import__("sys").modules, "paddleocr", fake_module)

    engine = _make_engine(tmp_path, language="en")

    with pytest.raises(ValueError, match="No local OCR profile configured for"):
        engine._get_ocr()

    assert not paddleocr_called["called"], "Network download must not be attempted"


# ---------------------------------------------------------------------------
# validate_local_ocr_models() public API
# ---------------------------------------------------------------------------

def test_validate_local_ocr_models_passes_when_ready(tmp_path: Path) -> None:
    _make_model_dirs(tmp_path)
    # Should not raise.
    validate_local_ocr_models(language="pt", cache_home=str(tmp_path))


def test_validate_local_ocr_models_raises_when_missing(tmp_path: Path) -> None:
    # Empty cache — no model directories at all.
    with pytest.raises(PaddleOcrUnavailable):
        validate_local_ocr_models(language="pt", cache_home=str(tmp_path))
