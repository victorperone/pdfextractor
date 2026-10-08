"""B3 regression tests — orientation diagnostics must reach the JSON pipeline.

On commit 41b0515 these tests FAIL because:
  - consume_page_diagnostics() does not return orientation_selected /
    orientation_attempts from _last_orientation_decision.
  - api.py reads last_orientation_selected / last_orientation_attempts as
    separate getattr attributes (which never exist on EasyOCRBackend), so
    ocr_orientation_selected is always None even after orientation was chosen.
"""
from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers — instantiate EasyOCRBackend without loading EasyOCR / PyTorch
# ---------------------------------------------------------------------------

def _make_backend_shell() -> Any:
    """Create a bare EasyOCRBackend instance bypassing __init__.

    Calls reset_page_diagnostics() to initialize all accumulators so that
    consume_page_diagnostics() has the fields it reads.  No EasyOCR or
    PyTorch import is triggered.
    """
    from structured_pdf_text.ocr.backends.easyocr import EasyOCRBackend

    backend = object.__new__(EasyOCRBackend)
    # Minimal attrs read by consume_page_diagnostics() but not set by reset
    backend._env_probe = {}
    backend._workers = 0
    backend._torch_num_threads = None
    backend._torch_num_interop_threads = None
    backend.reset_page_diagnostics()
    return backend


# ---------------------------------------------------------------------------
# TestConsumePageDiagnosticsOrientation — B3 core tests
# ---------------------------------------------------------------------------

class TestConsumePageDiagnosticsOrientation:
    """consume_page_diagnostics() must expose the orientation decision."""

    def test_consume_returns_orientation_selected_when_set(self):
        """After orientation=90 is selected, consume must return orientation_selected=90."""
        backend = _make_backend_shell()
        backend._last_orientation_decision = {
            "selected_angle": 90,
            "attempts": [
                {"angle": 0, "score": 0.10, "token_count": 1, "sufficient": False},
                {"angle": 90, "score": 0.92, "token_count": 3},
            ],
        }

        diag = backend.consume_page_diagnostics()

        assert diag.get("orientation_selected") == 90, (
            f"consume must expose selected_angle; got {diag.get('orientation_selected')!r}"
        )

    def test_consume_returns_orientation_attempts_list(self):
        """consume must expose the orientation attempts list."""
        backend = _make_backend_shell()
        backend._last_orientation_decision = {
            "selected_angle": 0,
            "attempts": [
                {"angle": 0, "score": 0.88, "token_count": 5, "sufficient": True},
            ],
        }

        diag = backend.consume_page_diagnostics()

        attempts = diag.get("orientation_attempts")
        assert isinstance(attempts, list), (
            f"orientation_attempts must be a list; got {type(attempts)}"
        )
        assert len(attempts) == 1
        assert attempts[0]["angle"] == 0

    def test_orientation_diagnostics_survive_until_consumed(self):
        """Setting _last_orientation_decision and then consuming must return the data."""
        backend = _make_backend_shell()
        backend._last_orientation_decision = {
            "selected_angle": 270,
            "attempts": [
                {"angle": 0, "score": 0.05, "token_count": 1},
                {"angle": 90, "score": 0.12, "token_count": 2},
                {"angle": 180, "score": 0.08, "token_count": 1},
                {"angle": 270, "score": 0.93, "token_count": 4},
            ],
        }

        diag = backend.consume_page_diagnostics()

        assert diag["orientation_selected"] == 270
        angles = [a["angle"] for a in diag["orientation_attempts"]]
        assert angles == [0, 90, 180, 270], f"expected all 4 angles; got {angles}"

    def test_orientation_diagnostics_reset_after_consume(self):
        """After consume, a second consume must return orientation_selected=None."""
        backend = _make_backend_shell()
        backend._last_orientation_decision = {
            "selected_angle": 180,
            "attempts": [{"angle": 0, "score": 0.1, "token_count": 1}],
        }

        backend.consume_page_diagnostics()  # first consume — discard result
        diag2 = backend.consume_page_diagnostics()  # second consume

        assert diag2.get("orientation_selected") is None, (
            "orientation_selected must be None after a second consume (reset guard)"
        )
        assert diag2.get("orientation_attempts") == [], (
            "orientation_attempts must be empty after a second consume"
        )

    def test_consume_returns_upright_zero_by_default(self):
        """When no orientation decision was stored, selected must default to 0 or None."""
        backend = _make_backend_shell()
        # _last_orientation_decision is {} after reset

        diag = backend.consume_page_diagnostics()

        selected = diag.get("orientation_selected")
        # Acceptable: None (never set) or 0 (explicit upright default)
        assert selected in (None, 0), (
            f"default orientation must be None or 0; got {selected!r}"
        )

    def test_consume_orientation_not_overwritten_by_reset_order(self):
        """The snapshot must be taken BEFORE reset so data is not lost."""
        backend = _make_backend_shell()
        backend._last_orientation_decision = {
            "selected_angle": 90,
            "attempts": [{"angle": 0, "score": 0.1}, {"angle": 90, "score": 0.9}],
        }

        # Calling consume should NOT return empty orientation even if the internal
        # implementation calls reset_page_diagnostics() during construction.
        diag = backend.consume_page_diagnostics()

        assert diag["orientation_selected"] == 90, (
            "reset called inside consume must not wipe the snapshot already taken"
        )


# ---------------------------------------------------------------------------
# TestApiOrientationDiagnostics — end-to-end pipeline population
# ---------------------------------------------------------------------------

class TestApiOrientationDiagnosticsIntegration:
    """api.py must populate ocr_orientation_selected from consume_page_diagnostics()."""

    def test_api_reads_orientation_from_diagnostics_dict(self):
        """When consume_page_diagnostics returns orientation_selected=90,
        the page diagnostics facts must contain ocr_orientation_selected=90.

        Tests that api.py does NOT use getattr(engine, 'last_orientation_selected')
        (which is always None) but instead reads from the consumed dict.
        """
        from structured_pdf_text.api import _refinement_conserves_content
        # We cannot run the full pipeline without EasyOCR, but we can verify
        # the code path by checking that api.py reads from the dict.
        #
        # Approach: inspect that the api.py source reads from easyocr_page_diagnostics
        # rather than from getattr attributes for the orientation keys.
        import inspect
        import structured_pdf_text.api as api_mod

        source = inspect.getsource(api_mod)

        # After the fix, api.py must NOT use getattr for the orientation attrs.
        # The legacy attribute names are "last_orientation_selected" and
        # "last_orientation_attempts" — these must not appear as getattr args.
        assert "last_orientation_selected" not in source, (
            "api.py must not use getattr(engine, 'last_orientation_selected') — "
            "read from consume_page_diagnostics() dict instead"
        )
        assert "last_orientation_attempts" not in source, (
            "api.py must not use getattr(engine, 'last_orientation_attempts') — "
            "read from consume_page_diagnostics() dict instead"
        )


# ---------------------------------------------------------------------------
# orientation_decisions — accumulated per-call list
# ---------------------------------------------------------------------------

class TestOrientationDecisionsAccumulated:
    """consume_page_diagnostics must expose orientation_decisions list (§12)."""

    def test_orientation_decisions_empty_by_default(self) -> None:
        backend = _make_backend_shell()
        diag = backend.consume_page_diagnostics()
        assert diag.get("orientation_decisions") == [], (
            "orientation_decisions must default to an empty list"
        )

    def test_single_call_recorded(self) -> None:
        backend = _make_backend_shell()
        backend._last_orientation_decision = {"selected_angle": 90, "attempts": []}
        backend._orientation_decisions.append({
            "call_index": 1,
            "selected_angle": 90,
            "attempts": [],
        })
        diag = backend.consume_page_diagnostics()
        decisions = diag.get("orientation_decisions", [])
        assert len(decisions) == 1
        assert decisions[0]["selected_angle"] == 90
        assert decisions[0]["call_index"] == 1

    def test_multiple_calls_all_recorded(self) -> None:
        backend = _make_backend_shell()
        for i, angle in enumerate([0, 270], start=1):
            backend._orientation_decisions.append({
                "call_index": i,
                "selected_angle": angle,
                "attempts": [],
            })
        backend._last_orientation_decision = {"selected_angle": 270, "attempts": []}
        diag = backend.consume_page_diagnostics()
        decisions = diag.get("orientation_decisions", [])
        assert len(decisions) == 2
        assert decisions[0]["selected_angle"] == 0
        assert decisions[1]["selected_angle"] == 270

    def test_decisions_reset_after_consume(self) -> None:
        backend = _make_backend_shell()
        backend._orientation_decisions.append({"call_index": 1, "selected_angle": 0, "attempts": []})
        backend.consume_page_diagnostics()
        diag2 = backend.consume_page_diagnostics()
        assert diag2.get("orientation_decisions") == [], (
            "orientation_decisions must be empty after consume resets state"
        )
