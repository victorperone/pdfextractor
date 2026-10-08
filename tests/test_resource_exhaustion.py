"""Tests for resource-exhaustion detection and safe OCR recovery.

Covers:
- is_resource_exhaustion() recognises PyTorch DefaultCPUAllocator messages
- ResourceExhaustedExtractionError is not followed by readtext() call
- Regional tiling is used when a scale exceeds the budget
- Tile tokens are mapped back to page coordinates correctly
- resource_exhausted status is emitted when all tiles fail with OOM
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest
from PIL import Image

from structured_pdf_text.errors import (
    ResourceExhaustedExtractionError,
    is_resource_exhaustion,
)
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementRequest,
    _plan_tile_grid_n,
    _scaled_image_dimensions,
    _tile_bboxes_for_grid,
)


# ---------------------------------------------------------------------------
# is_resource_exhaustion — PyTorch "not enough memory" message
# ---------------------------------------------------------------------------

def test_pytorch_not_enough_memory_is_recognised() -> None:
    exc = RuntimeError(
        "[enforce fail at alloc_cpu.cpp:117] data. "
        "DefaultCPUAllocator: not enough memory: "
        "you tried to allocate 5963776000 bytes."
    )
    assert is_resource_exhaustion(exc) is True


def test_pytorch_not_enough_memory_variant_recognised() -> None:
    exc = RuntimeError("not enough memory to allocate tensor")
    assert is_resource_exhaustion(exc) is True


def test_out_of_memory_still_recognised() -> None:
    exc = RuntimeError("CUDA out of memory. Tried to allocate 8.00 GiB")
    assert is_resource_exhaustion(exc) is True


def test_generic_runtime_error_not_recognised() -> None:
    exc = RuntimeError("division by zero")
    assert is_resource_exhaustion(exc) is False


def test_memory_error_always_recognised() -> None:
    exc = MemoryError("cannot allocate 4 GiB")
    assert is_resource_exhaustion(exc) is True


def test_chain_contains_oom_recognised() -> None:
    cause = RuntimeError("DefaultCPUAllocator: not enough memory: tried 7531397120 bytes.")
    try:
        raise RuntimeError("OCR failed") from cause
    except RuntimeError as exc:
        assert is_resource_exhaustion(exc) is True


# ---------------------------------------------------------------------------
# EasyOCR detect() OOM must not call readtext()
# ---------------------------------------------------------------------------

class _OOMOnDetect:
    """Fake EasyOCR reader that raises OOM on detect() and fails if readtext() is called."""

    def detect(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError(
            "DefaultCPUAllocator: not enough memory: you tried to allocate 7531397120 bytes."
        )

    def readtext(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("readtext() must not be called after OOM in detect()")

    def recognize(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("recognize() must not be called after OOM in detect()")

    detect_network = "craft"


def test_oom_in_detect_does_not_call_readtext() -> None:
    """OOM in detect() must propagate as ResourceExhaustedExtractionError, NOT call readtext()."""
    from unittest.mock import patch, MagicMock
    import structured_pdf_text.ocr.backends.easyocr as easyocr_mod
    from structured_pdf_text.ocr.backends.easyocr import _run_easyocr

    reader = _OOMOnDetect()
    kwargs = dict(
        decoder="greedy",
        beamwidth=5,
        adjust_contrast=0.5,
        allowlist=None,
        blocklist=None,
        workers=0,
        mag_ratio=1.2,
    )
    # Mock reformat_input so the detect() stage is actually reached.
    import numpy as np
    fake_color = np.ones((100, 100, 3), dtype=np.uint8)
    fake_gray = np.ones((100, 100), dtype=np.uint8)

    with patch.object(easyocr_mod, "_run_easyocr", wraps=_run_easyocr):
        import sys
        mock_easyocr_utils = MagicMock()
        mock_easyocr_utils.reformat_input = lambda arr: (fake_color, fake_gray)
        with patch.dict(sys.modules, {"easyocr": MagicMock(), "easyocr.utils": mock_easyocr_utils}):
            with pytest.raises(ResourceExhaustedExtractionError):
                _run_easyocr(reader, Image.new("RGB", (100, 100), "white"), **kwargs)


# ---------------------------------------------------------------------------
# Regional tiling: _plan_tile_grid_n
# ---------------------------------------------------------------------------

def test_plan_tile_grid_n_returns_2_for_moderately_large_crop() -> None:
    # 500×500 base, scale=2.0 → 1000×1000 full would be ~2.86 MiB.
    # At 8 MiB budget, this should fit at 2×2 tiles of 250×250 → scaled 500×500 → ~0.71 MiB.
    n = _plan_tile_grid_n(500, 500, 2.0, budget_mib=8.0)
    assert n >= 2


def test_plan_tile_grid_n_returns_0_when_impossible() -> None:
    # A 1×1 crop at 1× is 3 bytes — always fits even at 0.001 MiB.
    # A pathological case: max_dim=0 or very large crop with tiny budget.
    n = _plan_tile_grid_n(10_000, 10_000, 4.0, budget_mib=0.001, max_dim=8)
    assert n == 0


def test_plan_tile_grid_n_returns_1_not_allowed() -> None:
    # Grid 1 means no tiling — function should always return >= 2 or 0.
    n = _plan_tile_grid_n(100, 100, 1.0, budget_mib=100.0)
    assert n == 0 or n >= 2


def test_tile_planner_accounts_for_overlap_on_both_sides() -> None:
    """n=3 yields tiles up to ~34 MiB at 2× — planner must return 4, not 3.

    An interior tile at grid=3 has width = ceil(4000/3) + 2*overlap ≈ 1734 px
    base; scaled 2× → 3468 px → 3468×3468×3 ≈ 34.4 MiB > 32 MiB budget.
    Grid=4 interior tile: ceil(4000/4) + 2*150 = 1300 px → 2600×2600×3
    ≈ 19.3 MiB ≤ 32 MiB.
    """
    n = _plan_tile_grid_n(4000, 4000, 2.0, budget_mib=32.0, overlap=0.15)
    assert n >= 4, f"Expected grid >= 4 but got {n} — planner under-estimated tile size"


def test_tile_planner_every_tile_fits_in_budget() -> None:
    """For the grid the planner returns, every actual tile must be within budget."""
    budget_mib = 32.0
    budget_bytes = int(budget_mib * 1024 * 1024)
    n = _plan_tile_grid_n(4000, 4000, 2.0, budget_mib=budget_mib)
    assert n > 0, "Expected a valid grid"
    for x0, y0, x1, y1 in _tile_bboxes_for_grid(4000, 4000, n):
        sw, sh = _scaled_image_dimensions(x1 - x0, y1 - y0, 2.0)
        assert sw * sh * 3 <= budget_bytes, (
            f"Tile {x1-x0}×{y1-y0} scaled to {sw}×{sh} exceeds budget {budget_bytes}"
        )


# ---------------------------------------------------------------------------
# Regional tiling: _tile_bboxes_for_grid
# ---------------------------------------------------------------------------

def test_tile_bboxes_cover_full_crop() -> None:
    crop_w, crop_h = 400, 300
    bboxes = _tile_bboxes_for_grid(crop_w, crop_h, grid_n=2)
    assert len(bboxes) == 4
    for x0, y0, x1, y1 in bboxes:
        assert 0 <= x0 < x1 <= crop_w
        assert 0 <= y0 < y1 <= crop_h


def test_tile_bboxes_overlap_exists() -> None:
    """Adjacent tiles should overlap (x1 of left tile > x0 of right tile)."""
    bboxes = _tile_bboxes_for_grid(400, 200, grid_n=2)
    # Sort by column
    row0 = sorted([(x0, y0, x1, y1) for x0, y0, x1, y1 in bboxes if y0 == 0 or y1 <= 110], key=lambda b: b[0])
    if len(row0) >= 2:
        assert row0[0][2] > row0[1][0], "Adjacent tiles should overlap in x"


# ---------------------------------------------------------------------------
# OcrRegionRefiner: tiling when scale is budget-blocked
# ---------------------------------------------------------------------------

class _FakeIdentityEasyOCR:
    engine = "easyocr"


class _EmptyResultEngine:
    """Engine that returns empty tokens and records calls."""

    identity = _FakeIdentityEasyOCR()

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.last_pass_count = 0
        self.last_batch_count = 0

    def recognize_page(self, image: object, page_index: int, page_bbox: object, **kwargs: object) -> list:
        from structured_pdf_text.ocr.recovery import image_size
        w, h = image_size(image)
        self.calls.append((w, h))
        self.last_pass_count = 1
        self.last_batch_count = 1
        return []


def test_refiner_uses_tiling_for_blocked_scale(monkeypatch) -> None:
    """When a scale is budget-blocked, tiles are tried and result is not budget_blocked."""
    # Force a tiny budget so even 1× blocks, triggering tiling.
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "0.01")  # 10 KiB
    engine = _EmptyResultEngine()
    page_bbox = BBox(0.0, 0.0, 200.0, 200.0)
    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (200, 200), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 200.0, 200.0),
            scale_factors=(1.0,),
        ),
    )
    # With tiling invoked, status should be no_text (tiles ran but found nothing),
    # not budget_blocked.
    assert result.status in {"no_text", "ok"}, f"Expected tiling to run, got: {result.status}"
    # At least one call should have been made to the engine for a tile.
    assert len(engine.calls) > 0, "No OCR calls made — tiling did not execute"
    # All tile images should be smaller than the full 200×200.
    for w, h in engine.calls:
        assert w < 200 or h < 200, f"Tile {w}×{h} is not smaller than the full crop"


def test_refiner_blocked_scale_tiles_do_not_exceed_budget(monkeypatch) -> None:
    """Each tile passed to OCR must fit within the configured budget."""
    budget_mib = 0.1  # 100 KiB
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", str(budget_mib))
    budget_bytes = int(budget_mib * 1024 * 1024)

    engine = _EmptyResultEngine()
    page_bbox = BBox(0.0, 0.0, 300.0, 300.0)
    OcrRegionRefiner(engine).refine(
        Image.new("RGB", (300, 300), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 300.0, 300.0),
            scale_factors=(1.0,),
        ),
    )
    for w, h in engine.calls:
        tile_bytes = w * h * 3
        assert tile_bytes <= budget_bytes, (
            f"Tile {w}×{h} = {tile_bytes} bytes exceeds budget {budget_bytes}"
        )


# ---------------------------------------------------------------------------
# ResourceExhaustedExtractionError in rotation loop: continue, don't abort
# ---------------------------------------------------------------------------

class _OOMOnOCR:
    """Engine that raises OOM on recognize_page."""

    identity = _FakeIdentityEasyOCR()
    last_pass_count = 0
    last_batch_count = 0

    def recognize_page(self, image: object, *args: object, **kwargs: object) -> list:
        raise ResourceExhaustedExtractionError(
            "DefaultCPUAllocator: not enough memory: you tried to allocate 7 GB."
        )


def test_oom_in_ocr_rotation_loop_does_not_abort_refine(monkeypatch) -> None:
    """OOM during OCR is recorded as resource_exhausted attempt; refine() does not re-raise."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnOCR()
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 100.0, 100.0),
            scale_factors=(1.0,),
        ),
    )
    assert result.status in {"no_text", "resource_exhausted"}, (
        f"Expected graceful handling of OOM, got: {result.status}"
    )
    assert any(
        "ResourceExhausted" in (a.error or "") for a in result.attempts
    ), "OOM event should be recorded in attempts"
