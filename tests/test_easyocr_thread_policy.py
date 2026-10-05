from __future__ import annotations

import sys
from types import SimpleNamespace

from structured_pdf_text.ocr.backends.easyocr import _apply_torch_threads


def test_torch_thread_identity_values_reflect_partial_global_update(monkeypatch) -> None:
    state = {"intra": 1, "inter": 2}

    def set_intra(value: int) -> None:
        state["intra"] = value

    def set_inter(_value: int) -> None:
        raise RuntimeError("inter-op pool was already initialized")

    torch = SimpleNamespace(
        set_num_threads=set_intra,
        set_num_interop_threads=set_inter,
        get_num_threads=lambda: state["intra"],
        get_num_interop_threads=lambda: state["inter"],
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert _apply_torch_threads(6) == (6, 2)
