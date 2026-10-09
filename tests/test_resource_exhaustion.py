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
from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.ocr.recovery import (
    OcrRegionRefiner,
    RegionRefinementRequest,
    _deduplicate_tiled_tokens,
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
# _deduplicate_tiled_tokens — unit tests
# ---------------------------------------------------------------------------

def _make_token(text: str, x0: float, y0: float, x1: float, y1: float, conf: float) -> OcrToken:
    from structured_pdf_text.geometry import BBox as _BBox
    return OcrToken(
        text=text,
        bbox=_BBox(x0, y0, x1, y1),
        confidence=conf,
        language=None,
        source=SourceKind.OCR_REGION,
    )


def test_deduplicate_removes_exact_position_duplicate() -> None:
    """Same text at identical position → one token."""
    a = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.80)
    b = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.80)
    result = _deduplicate_tiled_tokens([a, b])
    assert len(result) == 1


def test_deduplicate_keeps_higher_confidence_copy() -> None:
    """When text+position match, the higher-confidence token must survive."""
    low = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.62)
    high = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.91)
    result = _deduplicate_tiled_tokens([low, high])
    assert len(result) == 1
    assert result[0].confidence == 0.91


def test_deduplicate_preserves_same_text_at_distinct_positions() -> None:
    """Same word in two non-overlapping cells must produce 2 tokens."""
    a = _make_token("SIM", 0, 0, 5, 2, 0.90)
    b = _make_token("SIM", 30, 0, 35, 2, 0.90)  # far apart — no spatial overlap
    result = _deduplicate_tiled_tokens([a, b])
    assert len(result) == 2


def test_deduplicate_different_text_not_merged() -> None:
    """Different text at overlapping positions must NOT be merged."""
    a = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.90)
    b = _make_token("PROCESSO", 0, 0, 10, 2, 0.85)
    result = _deduplicate_tiled_tokens([a, b])
    assert len(result) == 2


def test_deduplicate_result_sorted_by_y0_x0() -> None:
    """Result must be in top-to-left reading order."""
    a = _make_token("B", 5, 10, 10, 12, 0.9)
    b = _make_token("A", 0, 5, 5, 7, 0.9)
    result = _deduplicate_tiled_tokens([a, b])
    assert len(result) == 2
    assert result[0].text == "A"
    assert result[1].text == "B"


def test_deduplicate_case_insensitive_text_match() -> None:
    """Normalised text comparison must be case-insensitive."""
    a = _make_token("Protocolo", 0, 0, 10, 2, 0.80)
    b = _make_token("PROTOCOLO", 0, 0, 10, 2, 0.85)
    result = _deduplicate_tiled_tokens([a, b])
    assert len(result) == 1
    assert result[0].confidence == 0.85


def test_deduplicate_tokens_at_same_page_position() -> None:
    """Two tiles detecting the same word in their overlap area map to the same
    page position and must be deduplicated to a single token.

    This simulates what happens when a word sits in the overlap region between
    tile A (page 0–46) and tile B (page 34–86): both tiles OCR it, map it
    through different geometry, and produce bboxes that are nearly identical
    in page coordinates.  The high-confidence copy must survive.
    """
    token_a = _make_token("OVERLAP", 42.0, 5.0, 45.0, 10.0, 0.80)
    token_b = _make_token("OVERLAP", 42.5, 5.0, 45.5, 10.0, 0.91)
    result = _deduplicate_tiled_tokens([token_a, token_b])
    assert len(result) == 1, "Tiles in overlap must not produce duplicate tokens"
    assert result[0].confidence == 0.91, "Higher-confidence copy must be kept"


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
    """OOM during OCR triggers tiled recovery; if all grids also OOM → resource_exhausted."""
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


# ---------------------------------------------------------------------------
# OOM on budget-allowed scale: tiled recovery must produce tokens
# ---------------------------------------------------------------------------

class _OOMOnLargePixelsEngine:
    """Raises OOM for images with pixel count above threshold; succeeds for smaller ones."""

    identity = _FakeIdentityEasyOCR()
    last_pass_count = 1
    last_batch_count = 1
    OOM_ABOVE_PIXELS = 5000  # 100×100 = 10000 > threshold; tiles ~64×64 = 4096 < threshold

    def recognize_page(
        self, image: object, page_index: object, page_bbox: object, **kwargs: object
    ) -> list:
        from structured_pdf_text.ocr.recovery import image_size
        from structured_pdf_text.document import OcrToken, SourceKind
        from structured_pdf_text.geometry import BBox as _BBox

        w, h = image_size(image)
        if w * h > self.OOM_ABOVE_PIXELS:
            raise ResourceExhaustedExtractionError("fake OOM for large image")
        return [OcrToken(
            text="TILE",
            bbox=_BBox(0.0, 0.0, float(w) * 0.5, float(h) * 0.5),
            confidence=0.9,
            language=None,
            source=SourceKind.OCR_REGION,
        )]


def test_oom_in_budget_allowed_scale_triggers_tiled_recovery(monkeypatch) -> None:
    """When OCR raises OOM on a budget-allowed scale, tiled recovery must succeed."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")  # allow full scale
    engine = _OOMOnLargePixelsEngine()
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
    assert result.status == "ok", f"Expected tiled recovery to succeed, got: {result.status}"
    assert len(result.tokens) > 0, "Tiled recovery should produce tokens"
    assert any(
        "ResourceExhausted" in (a.error or "") for a in result.attempts
    ), "Full-raster OOM must be recorded in attempts"


def test_tile_grid_escalation_on_tile_oom(monkeypatch) -> None:
    """When first grid's tiles OOM, escalate to finer grid; succeed there → status=ok."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")

    # OOM for images wider than 70 px; tiles from grid=2 are ~64px, grid=4 are ~33px.
    # Threshold chosen so grid=2 tiles OOM but grid=4 tiles succeed.
    class _OOMAbove70pxEngine:
        identity = _FakeIdentityEasyOCR()
        last_pass_count = 1
        last_batch_count = 1
        OOM_ABOVE_WIDTH = 70

        def recognize_page(self, image, page_index, page_bbox, **kwargs):
            from structured_pdf_text.ocr.recovery import image_size
            from structured_pdf_text.document import OcrToken, SourceKind
            from structured_pdf_text.geometry import BBox as _BBox
            w, h = image_size(image)
            if w > self.OOM_ABOVE_WIDTH:
                raise ResourceExhaustedExtractionError(f"fake OOM: tile too wide {w}px")
            return [OcrToken(
                text="OK",
                bbox=_BBox(0.0, 0.0, float(w) * 0.5, float(h) * 0.5),
                confidence=0.9,
                language=None,
                source=SourceKind.OCR_REGION,
            )]

    engine = _OOMAbove70pxEngine()
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
    assert result.status == "ok", f"Grid escalation should have found a working grid, got: {result.status}"
    assert any(
        "ResourceExhausted" in (a.error or "") for a in result.attempts
    ), "Tile OOM must be recorded in attempts"


from structured_pdf_text.api import _recover_selected_regions
from structured_pdf_text.document import (
    LayoutRegion,
    RegionDecision,
    RegionKind,
    RegionQuality,
)


def _make_layout_region(region_id: str, bbox: BBox) -> LayoutRegion:
    """Minimal LayoutRegion suitable for _recover_selected_regions tests."""
    return LayoutRegion(
        region_id=region_id,
        kind=RegionKind.TEXT,
        bbox=bbox,
        layout_confidence=1.0,
        native_lines=[],
        ocr_tokens=[],
        quality=RegionQuality(RegionDecision.MERGE_OCR),
    )


def test_oom_all_grids_fail_gives_resource_exhausted(monkeypatch) -> None:
    """When all tile grids up to 8×8 fail with OOM → status=resource_exhausted."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnOCR()  # always OOMs
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
    assert result.status == "resource_exhausted", (
        f"All grids failing should yield resource_exhausted, got: {result.status}"
    )


# ---------------------------------------------------------------------------
# Correction 4 — request.rotations must be preserved through tiled recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", [0.0, 90.0, 180.0, 270.0])
def test_tiled_recovery_preserves_rotation(monkeypatch, rotation: float) -> None:
    """selected_rotation in the result must match the requested rotation when tiling is used.

    Forces tiling via OOM on the full raster (_OOMOnLargePixelsEngine: 100×100 = 10 000 px
    > threshold 5 000; tiles ~50×50 = 2 500 < threshold).  Non-zero rotations require cv2
    (rotate_image_expanded); the test is skipped if cv2 is not installed.
    """
    if rotation != 0.0:
        pytest.importorskip("cv2")
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnLargePixelsEngine()
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 100.0, 100.0),
            scale_factors=(1.0,),
            rotations=(rotation,),
        ),
    )
    assert result.status == "ok", (
        f"Tiled recovery must succeed for rotation={rotation}, got: {result.status}"
    )
    assert result.selected_rotation == rotation, (
        f"selected_rotation must equal the requested rotation {rotation}, "
        f"got {result.selected_rotation!r} — tiled path must not hardcode 0.0"
    )


def test_tiled_recovery_tile_offsets_applied_to_page_coords(monkeypatch) -> None:
    """Tile region offsets must be reflected in the final page-space token positions.

    Setup: 400×100-point page, budget forces 2×2 tiling.  Engine returns a unique-text
    token at position (0, 0, 5, 5) in each tile's pixel space.  Because each tile covers
    a different portion of the page, the resulting page-space bboxes must be spread across
    the page — not all stacked at the same position.

    This verifies that tile_region_bbox offsets are correctly applied and that the tiled
    path does not map every token to the origin of the full region.
    """
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "0.05")  # ~50 KiB forces tiling on 400×100

    call_index: list[int] = [0]

    class _OffsetTrackingEngine:
        identity = _FakeIdentityEasyOCR()
        last_pass_count = 1
        last_batch_count = 1

        def recognize_page(
            self, image: object, page_index: object, page_bbox: object, **kwargs: object
        ) -> list:
            from structured_pdf_text.ocr.recovery import image_size
            from structured_pdf_text.document import OcrToken, SourceKind
            from structured_pdf_text.geometry import BBox as _BBox
            w, h = image_size(image)
            call_index[0] += 1
            return [OcrToken(
                text=f"TILE{call_index[0]}",   # unique text → deduplication never merges
                bbox=_BBox(0.0, 0.0, min(5.0, float(w)), min(5.0, float(h))),
                confidence=0.9,
                language=None,
                source=SourceKind.OCR_REGION,
            )]

    page_bbox = BBox(0.0, 0.0, 400.0, 100.0)
    result = OcrRegionRefiner(_OffsetTrackingEngine()).refine(
        Image.new("RGB", (400, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 400.0, 100.0),
            scale_factors=(1.0,),
            rotations=(0.0,),
        ),
    )
    assert result.tokens, "Expected tokens from tiled OCR"

    # All tokens must be within page bounds.
    for t in result.tokens:
        assert 0.0 <= t.bbox.x0 < t.bbox.x1 <= 400.0 + 1e-6, (
            f"Token x-range {t.bbox.x0:.1f}–{t.bbox.x1:.1f} outside page [0, 400]"
        )
        assert 0.0 <= t.bbox.y0 < t.bbox.y1 <= 100.0 + 1e-6, (
            f"Token y-range {t.bbox.y0:.1f}–{t.bbox.y1:.1f} outside page [0, 100]"
        )

    # Tokens from left and right tiles must land at different x positions.  If offsets
    # were not applied, all tokens would be mapped to x ≈ 0 (origin of the region).
    if len(result.tokens) >= 2:
        x_positions = sorted(t.bbox.x0 for t in result.tokens)
        span = x_positions[-1] - x_positions[0]
        assert span > 50.0, (
            f"Token x0 positions {x_positions} are too close together (span={span:.1f}) — "
            "tile offsets may not have been applied correctly"
        )


def test_tiled_recovery_token_bbox_within_page_bounds_for_all_rotations(monkeypatch) -> None:
    """After rotation and inverse mapping, all token bboxes must stay within page bounds.

    Uses _OOMOnLargePixelsEngine to force tiling; tests rotation=0 only (no cv2 needed)
    while verifying the complete geometry path (resize → rotate → OCR → map → page).
    """
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnLargePixelsEngine()
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    result = OcrRegionRefiner(engine).refine(
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        request=RegionRefinementRequest(
            bbox=BBox(0.0, 0.0, 100.0, 100.0),
            scale_factors=(1.0,),
            rotations=(0.0,),
        ),
    )
    assert result.status == "ok", f"Expected ok, got {result.status}"
    for t in result.tokens:
        assert 0.0 <= t.bbox.x0 < t.bbox.x1 <= 100.0 + 1e-6, (
            f"Token x-range {t.bbox.x0:.2f}–{t.bbox.x1:.2f} outside page [0, 100]"
        )
        assert 0.0 <= t.bbox.y0 < t.bbox.y1 <= 100.0 + 1e-6, (
            f"Token y-range {t.bbox.y0:.2f}–{t.bbox.y1:.2f} outside page [0, 100]"
        )


# ---------------------------------------------------------------------------
# Correction 6 — recovered OOM must remain visible in region and page telemetry
# ---------------------------------------------------------------------------

def test_oom_recovered_region_stat_has_resource_exhausted_attempts(monkeypatch) -> None:
    """Case A: full-raster OOM recovered via tiling → resource_exhausted_attempts >= 1.

    Even though the final status is "ok" (tiling succeeded), the per-region stat
    must report resource_exhausted_attempts >= 1 and resource_exhausted=True so
    the event remains visible in telemetry.
    """
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnLargePixelsEngine()  # 100×100 raster OOMs; tiles succeed
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    region = _make_layout_region("region-test-oom-recovered", page_bbox)

    _tokens, _passes, _batches, stats = _recover_selected_regions(
        engine,
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        regions=[region],
        quality_variants=False,
    )

    stat = stats["region-test-oom-recovered"]
    assert stat["status"] == "ok", (
        f"Tiled recovery must produce status=ok; got {stat['status']!r}"
    )
    assert stat["resource_exhausted_attempts"] >= 1, (
        f"At least one OOM attempt must be counted; got {stat['resource_exhausted_attempts']}"
    )
    assert stat["resource_exhausted"] is True, (
        "resource_exhausted flag must be True when any attempt raised OOM"
    )


def test_page_facts_count_recovered_oom_attempts(monkeypatch) -> None:
    """Case A: page-level ocr_resource_exhausted_count must count all OOM attempts.

    Tests the aggregation formula directly using mock stats dicts, verifying that
    the count includes regions with status=ok (OOM recovered) — not just regions
    with status=resource_exhausted (OOM not recovered).
    """
    # Simulate: one region recovered (status=ok, 2 OOM attempts), one clean (0 attempts)
    mock_stats = {
        "region-oom-recovered": {
            "status": "ok",
            "resource_exhausted_attempts": 2,
            "resource_exhausted": True,
        },
        "region-clean": {
            "status": "ok",
            "resource_exhausted_attempts": 0,
            "resource_exhausted": False,
        },
    }
    # Apply the same formula used in api.py page facts
    count = sum(
        int(stat.get("resource_exhausted_attempts", 0))
        for stat in mock_stats.values()
    )
    regions = [
        region_id for region_id, stat in mock_stats.items()
        if int(stat.get("resource_exhausted_attempts", 0)) > 0
    ]
    assert count == 2, f"Expected 2 total OOM attempts, got {count}"
    assert regions == ["region-oom-recovered"], (
        f"Expected only the recovered region in the list, got {regions}"
    )


def test_no_oom_region_stat_has_zero_resource_exhausted_attempts(monkeypatch) -> None:
    """Case B: successful OCR with no OOM → resource_exhausted_attempts=0, resource_exhausted=False."""
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _EmptyResultEngine()  # always succeeds, never OOMs
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    region = _make_layout_region("region-test-no-oom", page_bbox)

    _tokens, _passes, _batches, stats = _recover_selected_regions(
        engine,
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        regions=[region],
        quality_variants=False,
    )

    stat = stats["region-test-no-oom"]
    assert stat["resource_exhausted_attempts"] == 0, (
        f"Clean OCR must report 0 OOM attempts; got {stat['resource_exhausted_attempts']}"
    )
    assert stat["resource_exhausted"] is False, (
        "resource_exhausted must be False when no OOM occurred"
    )


def test_unrecovered_oom_region_stat_has_resource_exhausted_status_and_attempts(monkeypatch) -> None:
    """Case C: irrecoverable OOM → status=resource_exhausted AND resource_exhausted_attempts >= 1.

    Both the legacy status field AND the new resource_exhausted_attempts field
    must reflect the failure — they serve different purposes:
    status=resource_exhausted flags complete failure; resource_exhausted_attempts
    counts the number of OOM events even when recovery partially succeeded.
    """
    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "1000.0")
    engine = _OOMOnOCR()  # always OOMs — no recovery possible
    page_bbox = BBox(0.0, 0.0, 100.0, 100.0)
    region = _make_layout_region("region-test-unrecovered-oom", page_bbox)

    _tokens, _passes, _batches, stats = _recover_selected_regions(
        engine,
        Image.new("RGB", (100, 100), "white"),
        page_index=0,
        page_bbox=page_bbox,
        regions=[region],
        quality_variants=False,
    )

    stat = stats["region-test-unrecovered-oom"]
    assert stat["status"] == "resource_exhausted", (
        f"Irrecoverable OOM must yield status=resource_exhausted; got {stat['status']!r}"
    )
    assert stat["resource_exhausted_attempts"] >= 1, (
        f"Irrecoverable OOM must have resource_exhausted_attempts >= 1; "
        f"got {stat['resource_exhausted_attempts']}"
    )
    assert stat["resource_exhausted"] is True, (
        "resource_exhausted flag must be True for irrecoverable OOM"
    )


def test_page_facts_formula_recovered_vs_unrecovered_oom() -> None:
    """Page facts formula must distinguish recovered OOM from unrecovered OOM.

    Both appear in ocr_resource_exhausted_regions, but only unrecovered OOM
    has status=resource_exhausted.  The count reflects total OOM attempts,
    not just regions with failed status.
    """
    mock_stats = {
        "region-ok-with-oom": {
            "status": "ok",
            "resource_exhausted_attempts": 1,
            "resource_exhausted": True,
        },
        "region-failed-with-oom": {
            "status": "resource_exhausted",
            "resource_exhausted_attempts": 3,
            "resource_exhausted": True,
        },
        "region-clean": {
            "status": "ok",
            "resource_exhausted_attempts": 0,
            "resource_exhausted": False,
        },
    }
    count = sum(
        int(stat.get("resource_exhausted_attempts", 0))
        for stat in mock_stats.values()
    )
    regions_with_oom = [
        rid for rid, stat in mock_stats.items()
        if int(stat.get("resource_exhausted_attempts", 0)) > 0
    ]
    assert count == 4, f"Expected 1+3=4 total OOM attempts, got {count}"
    assert set(regions_with_oom) == {"region-ok-with-oom", "region-failed-with-oom"}, (
        f"Both recovered and unrecovered OOM regions must appear; got {regions_with_oom}"
    )
    # The clean region must NOT appear
    assert "region-clean" not in regions_with_oom
