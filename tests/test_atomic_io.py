from __future__ import annotations

from pathlib import Path

import pytest

from structured_pdf_text.atomic_io import atomic_write_json, atomic_write_text


def test_atomic_text_write_replaces_complete_file(tmp_path: Path) -> None:
    target = tmp_path / "artifact.md"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new complete content")
    assert target.read_text(encoding="utf-8") == "new complete content"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_replace_failure_preserves_previous_file_and_cleans_temp(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "manifest.json"
    target.write_text('{"state":"old"}', encoding="utf-8")

    def fail_replace(_source, _target):
        raise OSError("simulated interruption")

    monkeypatch.setattr("structured_pdf_text.atomic_io.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        atomic_write_json(target, {"state": "new"})
    assert target.read_text(encoding="utf-8") == '{"state":"old"}'
    assert list(tmp_path.glob(".*.tmp")) == []
