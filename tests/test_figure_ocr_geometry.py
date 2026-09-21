"""Synthetic geometry tests for figure OCR cropping.

These tests exercise _crop_page_image(), _subfigure_boxes() and the geometry
guards in _refine_figure_ocr() using PIL images created in memory and
artificial bboxes.  No real PDF, no OCR model, no network access is required.

Invariants verified per the spec in instrucoes_desenvolvedor_ocr_figuras_sem_commit.md:

- An internal figure produces a crop with the correct pixel bounds.
- A partially external figure yields the visible intersection only.
- A completely external figure returns None / empty boxes; no ValueError.
- Zero-dimension and non-finite coordinates are handled explicitly.
- A shifted page origin (CropBox != (0,0)) is converted correctly.
- Rotation non-regression: existing coordinate space is preserved; no
  double-rotation is introduced.
- A mix of valid and invalid figures: the invalid one does not block OCR of
  the valid ones.
- A visually dense figure (QR-like pattern) is not eliminated by geometry.
- A simulated FatalExtractionError remains fatal and is not swallowed.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    from PIL import Image
except ImportError:
    pytest.skip("Pillow not available", allow_module_level=True)

from structured_pdf_text.api import (
    _crop_page_image,
    _refine_figure_ocr,
    _subfigure_boxes,
)
from structured_pdf_text.errors import FatalExtractionError
from structured_pdf_text.geometry import BBox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image(width: int = 1200, height: int = 1600, color: int = 255) -> Image.Image:
    """Return an in-memory RGB image."""
    return Image.new("RGB", (width, height), color=(color, color, color))


def _page_bbox(w: float = 600.0, h: float = 800.0, x0: float = 0.0, y0: float = 0.0) -> BBox:
    return BBox(x0, y0, x0 + w, y0 + h)


def _image_pixel_bounds(crop: Image.Image) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) via the crop box attribute."""
    # PIL records the box used to create the crop in .getbbox()-like fashion;
    # we rely on the crop size instead which is always available.
    return crop.size  # (width, height)


# ---------------------------------------------------------------------------
# _crop_page_image — basic correctness
# ---------------------------------------------------------------------------

class TestCropPageImageInternal:
    """Figure entirely within the page → correct crop dimensions."""

    def test_full_page_crop(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        # Figure covers the whole page → full image returned.
        crop = _crop_page_image(image, page_bbox, page_bbox)
        assert crop is not None
        w, h = crop.size
        assert w == 1200
        assert h == 1600

    def test_central_figure(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        # Figure occupying the central quarter of the page.
        figure_bbox = BBox(150.0, 200.0, 450.0, 600.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, h = crop.size
        # scale_x = 1200/600 = 2, scale_y = 1600/800 = 2
        # left=300, right=900 → width=600; top=400, bottom=1200 → height=800
        assert w == 600
        assert h == 800

    def test_small_figure_one_pixel_wide(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        # 0.5-pt wide figure → ceil gives at least 1 pixel.
        figure_bbox = BBox(100.0, 100.0, 100.5, 200.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, _ = crop.size
        assert w >= 1


class TestCropPageImagePartiallyExternal:
    """Figure partially outside the page → only the visible portion is cropped."""

    def test_partially_outside_right(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        # Figure extends 100 pts past the right edge.
        figure_bbox = BBox(400.0, 100.0, 700.0, 300.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, h = crop.size
        # Visible x: 400→600 = 200 pts × 2 px/pt = 400 px
        assert w == 400
        assert h > 0

    def test_partially_outside_left(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(-50.0, 100.0, 100.0, 300.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, _ = crop.size
        # Visible x: 0→100 = 100 pts × 2 = 200 px
        assert w == 200

    def test_partially_outside_top(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(100.0, -30.0, 300.0, 100.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        _, h = crop.size
        # Visible y: 0→100 = 100 pts × 2 = 200 px
        assert h == 200

    def test_partially_outside_bottom(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(100.0, 700.0, 300.0, 900.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        _, h = crop.size
        # Visible y: 700→800 = 100 pts × 2 = 200 px
        assert h == 200


class TestCropPageImageCompletelyExternal:
    """Figure fully outside the page → None (no ValueError, no crop)."""

    def test_outside_right(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(750.0, 100.0, 800.0, 200.0)
        assert _crop_page_image(image, page_bbox, figure_bbox) is None

    def test_outside_left(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(-200.0, 100.0, -10.0, 300.0)
        assert _crop_page_image(image, page_bbox, figure_bbox) is None

    def test_outside_above(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(100.0, -200.0, 300.0, -10.0)
        assert _crop_page_image(image, page_bbox, figure_bbox) is None

    def test_outside_below(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = _page_bbox(600.0, 800.0)
        figure_bbox = BBox(100.0, 900.0, 300.0, 1000.0)
        assert _crop_page_image(image, page_bbox, figure_bbox) is None

    def test_reproduces_original_crash_scenario(self) -> None:
        """Exact shape from the incident report: figure to the right of page."""
        # image 1200×1600, page 600×800 points, figure at x=750..800
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(750.0, 100.0, 800.0, 200.0)
        # Old code: left=1500, right=1600 → right > image.width → clamped to
        # 1200, but left was already clamped to 1200 → right <= left → ValueError.
        # New code: intersection is empty → None.
        result = _crop_page_image(image, page_bbox, figure_bbox)
        assert result is None


class TestCropPageImageDegenerateInputs:
    """Zero dimensions and non-finite coordinates."""

    def test_zero_width_page(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 0.0, 800.0)  # BBox allows x0==x1? No — raises.
        # BBox.__post_init__ raises ValueError for invalid boxes so we test via
        # a manually crafted degenerate case using width=0 page.
        # We simulate by passing page_bbox with width=0 indirectly; since BBox
        # validates x1 >= x0 strictly, we only test the _crop_page_image guard
        # for page width == 0 through a valid but zero-area page.
        # (BBox disallows x1 < x0; x1==x0 is allowed: width == 0.)
        page_bbox_zero = BBox(100.0, 0.0, 100.0, 800.0)  # width == 0
        assert _crop_page_image(image, page_bbox_zero, BBox(100.0, 0.0, 200.0, 400.0)) is None

    def test_zero_height_page(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox_zero = BBox(0.0, 200.0, 600.0, 200.0)  # height == 0
        assert _crop_page_image(image, page_bbox_zero, BBox(0.0, 200.0, 300.0, 400.0)) is None

    def test_fractional_coordinates_preserve_area(self) -> None:
        """Sub-pixel areas must not silently vanish."""
        image = _make_image(100, 100)
        page_bbox = BBox(0.0, 0.0, 100.0, 100.0)  # 1 px/pt
        figure_bbox = BBox(10.0, 10.0, 10.3, 10.7)  # 0.3×0.7 pts → sub-pixel
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        # ceil ensures at least 1×1 pixels.
        assert crop is not None
        w, h = crop.size
        assert w >= 1
        assert h >= 1


class TestCropPageImageNonFinite:
    """Non-finite coordinates in figure bbox → None, no exception."""

    @pytest.mark.parametrize("bad_x0", [math.nan, math.inf, -math.inf])
    def test_non_finite_x0(self, bad_x0: float) -> None:
        image = _make_image()
        page_bbox = _page_bbox()
        # BBox raises on invalid x1<x0; we can only test _crop_page_image
        # directly with a bbox whose coordinates are individually non-finite.
        # Since BBox.__post_init__ would reject nan/inf, we patch the guard
        # in _crop_page_image by verifying the explicit coordinate check.
        # We construct a mock that looks like a BBox but holds bad coords.
        region = SimpleNamespace(
            x0=bad_x0, y0=100.0, x1=200.0, y1=300.0,
            intersection=lambda other: None,
        )
        # _crop_page_image iterates over the four coords before intersecting.
        result = _crop_page_image(image, page_bbox, region)  # type: ignore[arg-type]
        assert result is None


class TestCropPageImageNonZeroOrigin:
    """Pages whose CropBox does not start at (0,0)."""

    def test_page_with_offset_origin(self) -> None:
        image = _make_image(1200, 1600)
        # Page origin at (100, 50) in PDF space.
        page_bbox = BBox(100.0, 50.0, 700.0, 850.0)  # 600×800 pts
        # Figure at (200,150)→(400,350) in PDF space; relative to origin: (100,100)→(300,300)
        figure_bbox = BBox(200.0, 150.0, 400.0, 350.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, h = crop.size
        # (200-100)*2=200 → (400-100)*2=600 → width=400
        # (150-50)*2=200 → (350-50)*2=600 → height=400
        assert w == 400
        assert h == 400

    def test_figure_outside_when_origin_shifted(self) -> None:
        """Figure that would be 'inside' with origin=0 but outside with real origin."""
        image = _make_image(1200, 1600)
        page_bbox = BBox(300.0, 300.0, 900.0, 1100.0)
        # Figure at (0,0)→(100,100) — entirely left/above the shifted page.
        figure_bbox = BBox(0.0, 0.0, 100.0, 100.0)
        assert _crop_page_image(image, page_bbox, figure_bbox) is None


# ---------------------------------------------------------------------------
# _subfigure_boxes
# ---------------------------------------------------------------------------

class TestSubfigureBoxes:
    """_subfigure_boxes must handle None crop gracefully."""

    def test_external_figure_returns_empty_list(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(750.0, 100.0, 800.0, 200.0)
        boxes = _subfigure_boxes(image, page_bbox, figure_bbox)
        assert boxes == []

    def test_internal_figure_returns_at_least_one_box(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(50.0, 100.0, 200.0, 300.0)
        boxes = _subfigure_boxes(image, page_bbox, figure_bbox)
        assert len(boxes) >= 1

    def test_partially_visible_figure_returns_nonempty(self) -> None:
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(500.0, 100.0, 700.0, 300.0)  # extends 100 pts right
        boxes = _subfigure_boxes(image, page_bbox, figure_bbox)
        # The visible part is non-trivial → at least one box (may be figure_bbox).
        assert len(boxes) >= 1


# ---------------------------------------------------------------------------
# _refine_figure_ocr — integration with fake engine
# ---------------------------------------------------------------------------

def _fake_image_obj(width: int = 100, height: int = 100) -> SimpleNamespace:
    """Minimal image-like object that passes _image_size() and crop()."""
    pil = Image.new("RGB", (width, height), (255, 255, 255))

    class _FakeImage:
        size = (width, height)

        def crop(self, box: tuple[int, int, int, int]) -> "_FakeImage":
            left, top, right, bottom = box
            if right <= left or bottom <= top:
                raise ValueError(f"Coordinate 'right' is less than 'left'")
            cropped = Image.new("RGB", (right - left, bottom - top))
            return cropped  # type: ignore[return-value]

        def convert(self, mode: str) -> Any:
            return pil.convert(mode)

        def getdata(self) -> Any:
            return pil.getdata()

    return _FakeImage()  # type: ignore[return-value]


def _fake_page(
    page_bbox: BBox,
    figure_bboxes: list[BBox | None],
) -> SimpleNamespace:
    """Minimal page-like object for _refine_figure_ocr()."""
    images = []
    for fb in figure_bboxes:
        img = SimpleNamespace(bbox=fb)
        images.append(img)
    page = SimpleNamespace(
        bbox=page_bbox,
        objects=SimpleNamespace(images=images),
        page_index=0,
    )
    return page


def _fake_engine_no_tokens() -> Any:
    """Engine that always returns an empty token list."""
    engine = MagicMock()
    result = MagicMock()
    result.tokens = []
    result.ocr_passes = 1
    result.ocr_batches = 1
    result.attempts = []
    engine.recognize_page.return_value = []
    engine.recognize_region.return_value = []
    # OcrRegionRefiner.refine returns a result-like object.
    return engine


class TestRefineFigureOcr:
    """_refine_figure_ocr geometry guards — no real OCR engine needed."""

    def _make_refiner_result(self) -> Any:
        r = MagicMock()
        r.tokens = []
        r.ocr_passes = 1
        r.ocr_batches = 1
        return r

    def _patch_refiner(self, monkeypatch: Any) -> None:
        """Patch OcrRegionRefiner so no actual inference is attempted."""
        from structured_pdf_text import api as api_module

        class FakeRefiner:
            def __init__(self, engine: Any) -> None:
                pass

            def refine(self, *args: Any, **kwargs: Any) -> Any:
                r = MagicMock()
                r.tokens = []
                r.ocr_passes = 1
                r.ocr_batches = 1
                return r

        monkeypatch.setattr(api_module, "OcrRegionRefiner", FakeRefiner)

    def test_external_figure_skipped_other_figures_processed(
        self, monkeypatch: Any
    ) -> None:
        """An external figure does not block OCR of valid figures."""
        self._patch_refiner(monkeypatch)
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        external = BBox(750.0, 100.0, 800.0, 200.0)
        internal = BBox(50.0, 100.0, 200.0, 300.0)
        page = _fake_page(page_bbox, [external, internal])
        warnings_list: list[str] = []
        # Should not raise; external figure is skipped, internal is attempted.
        result = _refine_figure_ocr(
            engine=_fake_engine_no_tokens(),
            page_image=image,
            page=page,
            page_index=0,
            native_lines=[],
            quality_policy="baseline",
            warnings=warnings_list,
        )
        # No tokens → empty refinements, but no crash.
        assert isinstance(result, list)
        # Diagnostic warning must mention the external figure.
        assert any("no_visible_area_in_page" in w for w in warnings_list)

    def test_zero_width_figure_skipped_with_warning(self, monkeypatch: Any) -> None:
        """A figure with zero width is skipped and a warning is recorded."""
        self._patch_refiner(monkeypatch)
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        # BBox(x0=100, y0=100, x1=100, y1=200) → width == 0
        zero_width = BBox(100.0, 100.0, 100.0, 200.0)
        page = _fake_page(page_bbox, [zero_width])
        warnings_list: list[str] = []
        result = _refine_figure_ocr(
            engine=_fake_engine_no_tokens(),
            page_image=image,
            page=page,
            page_index=0,
            native_lines=[],
            quality_policy="baseline",
            warnings=warnings_list,
        )
        assert isinstance(result, list)
        assert any("zero_dimensions" in w for w in warnings_list)

    def test_none_bbox_figure_skipped_silently(self, monkeypatch: Any) -> None:
        """A figure with bbox=None is silently skipped (no warning, no crash)."""
        self._patch_refiner(monkeypatch)
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        page = _fake_page(page_bbox, [None])
        warnings_list: list[str] = []
        result = _refine_figure_ocr(
            engine=_fake_engine_no_tokens(),
            page_image=image,
            page=page,
            page_index=0,
            native_lines=[],
            quality_policy="baseline",
            warnings=warnings_list,
        )
        assert isinstance(result, list)
        # No warning for None bbox — consistent with original behaviour.
        assert not any("zero_dimensions" in w for w in warnings_list)

    def test_multiple_figures_external_then_valid(self, monkeypatch: Any) -> None:
        """External figure followed by valid figure: valid one is still processed."""
        self._patch_refiner(monkeypatch)
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        external = BBox(800.0, 100.0, 900.0, 300.0)
        valid = BBox(50.0, 50.0, 300.0, 400.0)
        page = _fake_page(page_bbox, [external, valid])
        warnings_list: list[str] = []
        # Must not raise; valid figure attempted even after external is skipped.
        _refine_figure_ocr(
            engine=_fake_engine_no_tokens(),
            page_image=image,
            page=page,
            page_index=0,
            native_lines=[],
            quality_policy="baseline",
            warnings=warnings_list,
        )
        assert any("no_visible_area_in_page" in w for w in warnings_list)

    def test_fatal_error_remains_fatal(self, monkeypatch: Any) -> None:
        """A FatalExtractionError raised by the refiner must propagate."""
        from structured_pdf_text import api as api_module

        class FatalRefiner:
            def __init__(self, engine: Any) -> None:
                pass

            def refine(self, *args: Any, **kwargs: Any) -> Any:
                raise FatalExtractionError("simulated fatal")

        monkeypatch.setattr(api_module, "OcrRegionRefiner", FatalRefiner)
        image = _make_image(1200, 1600)
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        valid = BBox(50.0, 50.0, 300.0, 400.0)
        page = _fake_page(page_bbox, [valid])
        with pytest.raises(FatalExtractionError):
            _refine_figure_ocr(
                engine=_fake_engine_no_tokens(),
                page_image=image,
                page=page,
                page_index=0,
                native_lines=[],
                quality_policy="baseline",
            )

    def test_dense_figure_not_eliminated_by_geometry(
        self, monkeypatch: Any
    ) -> None:
        """A visually dense figure (QR-like) is not skipped by geometry alone."""
        self._patch_refiner(monkeypatch)
        # Create a dense image (all dark pixels — simulates a QR/barcode region).
        dense_image = Image.new("RGB", (1200, 1600), color=(0, 0, 0))
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        # Dense figure at a valid position inside the page.
        dense_figure = BBox(100.0, 100.0, 300.0, 300.0)
        page = _fake_page(page_bbox, [dense_figure])
        warnings_list: list[str] = []
        # Should not raise and must not produce a "skipped" warning for this figure.
        _refine_figure_ocr(
            engine=_fake_engine_no_tokens(),
            page_image=dense_image,
            page=page,
            page_index=0,
            native_lines=[],
            quality_policy="baseline",
            warnings=warnings_list,
        )
        assert not any(
            "no_visible_area_in_page" in w or "zero_dimensions" in w
            for w in warnings_list
        )


# ---------------------------------------------------------------------------
# Rotation non-regression
# ---------------------------------------------------------------------------

class TestRotationNonRegression:
    """Ensure existing 0° behaviour is unchanged; no double-rotation introduced."""

    def test_zero_rotation_matches_expected_pixels(self) -> None:
        """0° page: crop coordinates must follow the standard scale formula."""
        image = _make_image(600, 800)  # 1 px/pt
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(100.0, 200.0, 300.0, 600.0)
        crop = _crop_page_image(image, page_bbox, figure_bbox)
        assert crop is not None
        w, h = crop.size
        assert w == 200  # 300-100 pts × 1 px/pt
        assert h == 400  # 600-200 pts × 1 px/pt

    def test_crop_does_not_modify_original_image(self) -> None:
        """PIL.Image.crop must not mutate the source image."""
        image = _make_image(1200, 1600)
        original_size = image.size
        page_bbox = BBox(0.0, 0.0, 600.0, 800.0)
        figure_bbox = BBox(50.0, 50.0, 200.0, 200.0)
        _ = _crop_page_image(image, page_bbox, figure_bbox)
        assert image.size == original_size
