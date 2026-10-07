"""B1 regression tests — exhaustive orientation isolation (R73).

These tests verify that the exhaustive OCR path performs orientation selection
BEFORE quality variant fusion, preventing tokens from losing orientations from
entering the fused output.

On commit 41b0515 all tests in TestExhaustiveOrientationIsolation FAIL because:
  - _exhaustive_with_orientation_selection() does not exist
  - _exhaustive_candidates() does not have _include_rotations parameter

On commit 41b0515 TestAdaptiveOrientationEdgeCases may already pass (adaptive was
fixed in a prior round) — these are kept here as guards against regression.
"""
from __future__ import annotations

from unittest.mock import patch
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_kwargs() -> dict[str, Any]:
    """Minimal keyword args accepted by _run_easyocr."""
    return {
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


def _make_raw(pairs: list[tuple[str, float]], y: float = 0.0) -> list[Any]:
    """Build EasyOCR raw result items: (bbox_pts, text, confidence).

    Boxes are laid out left-to-right starting at x=0, each 8px per char wide.
    """
    result = []
    x = 0.0
    for text, conf in pairs:
        w = max(len(text) * 8, 8)
        box = [[x, y], [x + w, y], [x + w, y + 10], [x, y + 10]]
        result.append((box, text, conf))
        x += w + 4.0
    return result


def _make_image(h: int = 100, w: int = 300):
    """Return a plain numpy image array."""
    import numpy as np
    return np.ones((h, w, 3), dtype=np.uint8) * 200


# ---------------------------------------------------------------------------
# TestExhaustiveOrientationIsolation — B1 core tests
# ---------------------------------------------------------------------------

class TestExhaustiveOrientationIsolation:
    """Exhaustive path must select orientation BEFORE quality candidate fusion."""

    # ------------------------------------------------------------------
    # Test 1 — losing-rotation tokens must never appear in fused output
    # ------------------------------------------------------------------

    def test_exhaustive_does_not_fuse_tokens_from_losing_rotation(self):
        """Tokens from a losing orientation must not appear in the fused output.

        Scenario:
            upright (0°): "TEXTO", "CORRETO", "AQUI" — 3 tokens, conf≈0.90
                          (sufficient quality → no rotation probe needed)
            rot90:        "LIXO" at a non-overlapping position, conf=0.75

        Expected after fix:
            selected_angle == 0
            "LIXO" is NOT in the output token list.

        Before fix:
            _exhaustive_with_orientation_selection does not exist → ImportError.
        """
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import (
            _exhaustive_with_orientation_selection,
        )

        img = _make_image(100, 300)
        upright_raw = _make_raw([("TEXTO", 0.91), ("CORRETO", 0.89), ("AQUI", 0.90)])
        rot90_raw   = _make_raw([("LIXO", 0.75)], y=20.0)

        def mock_run(reader, image, **kw):
            import numpy as np
            arr = np.asarray(image)
            h, w = arr.shape[:2]
            return (rot90_raw if h > w else upright_raw), None

        def mock_exhaustive(reader_arg, img_arg, base_kw, *, _include_rotations=True, **kw):
            assert _include_rotations is False, (
                "_exhaustive_with_orientation_selection must call "
                "_exhaustive_candidates(_include_rotations=False)"
            )
            import numpy as np
            arr = np.asarray(img_arg)
            h, w = arr.shape[:2]
            return [("default", rot90_raw if h > w else upright_raw)]

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            with patch.object(easyocr_mod, "_exhaustive_candidates", mock_exhaustive):
                candidates, diag = _exhaustive_with_orientation_selection(
                    object(), img, _base_kwargs(),
                    page_index=0, language="pt",
                    _dbnet_precomputed=(False, "weights_missing"),
                )

        assert diag["selected_angle"] == 0, (
            f"3 high-conf upright tokens must win; got angle={diag['selected_angle']}"
        )
        all_texts = [t.text for _, toks in candidates for t in toks]
        assert "LIXO" not in all_texts, (
            "LIXO from losing rot90 must not appear in output"
        )
        assert any(t in all_texts for t in ("TEXTO", "CORRETO", "AQUI")), (
            "at least one upright token must be present"
        )

    # ------------------------------------------------------------------
    # Test 2 — rot90 selected when upright is insufficient
    # ------------------------------------------------------------------

    def test_exhaustive_selects_rot90_before_quality_fusion(self):
        """When upright quality is insufficient, rot90 must win orientation probe.

        Scenario:
            upright: 1 low-conf token → insufficient
            rot90:   3 high-conf tokens → selected
        """
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import (
            _exhaustive_with_orientation_selection,
        )

        img = _make_image(100, 300)
        upright_raw = _make_raw([("x", 0.15)])   # single token, very low conf
        rot90_raw   = _make_raw([("BOM", 0.95), ("TEXTO", 0.93), ("AQUI", 0.91)])

        def mock_run(reader, image, **kw):
            import numpy as np
            arr = np.asarray(image)
            h, w = arr.shape[:2]
            return (rot90_raw if h > w else upright_raw), None

        def mock_exhaustive(reader_arg, img_arg, base_kw, *, _include_rotations=True, **kw):
            assert _include_rotations is False
            import numpy as np
            arr = np.asarray(img_arg)
            h, w = arr.shape[:2]
            return [("default", rot90_raw if h > w else upright_raw)]

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            with patch.object(easyocr_mod, "_exhaustive_candidates", mock_exhaustive):
                candidates, diag = _exhaustive_with_orientation_selection(
                    object(), img, _base_kwargs(),
                    page_index=0, language="pt",
                    _dbnet_precomputed=(False, "weights_missing"),
                )

        assert diag["selected_angle"] == 90, (
            f"rot90 has higher quality; expected angle=90, got {diag['selected_angle']}"
        )
        all_texts = [t.text for _, toks in candidates for t in toks]
        assert "x" not in all_texts, "upright garbage must not appear"
        assert any(t in all_texts for t in ("BOM", "TEXTO", "AQUI")), (
            "rot90 good text must be present"
        )
        # A 90° clockwise raster correction maps back to token orientation 270°
        for _, toks in candidates:
            for tok in toks:
                assert tok.rotation == 270, f"token rotation must be 90, got {tok.rotation}"

    # ------------------------------------------------------------------
    # Test 3 — rot180 selected
    # ------------------------------------------------------------------

    def test_exhaustive_selects_rot180_before_quality_fusion(self):
        """rot180 must be selected when upright and rot90 are insufficient."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import (
            _exhaustive_with_orientation_selection,
        )

        img = _make_image(100, 300)
        # 180° rotation: shape remains (100,300), so h<w in all cases.
        # Use image size difference to distinguish calls by order.
        call_log: list[str] = []

        # upright probe: low quality
        upright_raw = _make_raw([("u", 0.10)])
        # rot90 probe: moderate but below rot180
        rot90_raw   = _make_raw([("a", 0.35), ("b", 0.30)])
        # rot180 probe: high quality
        rot180_raw  = _make_raw([("CERTO", 0.95), ("TEXTO", 0.94), ("DOC", 0.92)])
        # rot270 probe: not reached (rot180 already sufficient)
        rot270_raw  = _make_raw([("z", 0.10)])

        probe_order = iter([upright_raw, rot90_raw, rot180_raw, rot270_raw])

        def mock_run(reader, image, **kw):
            raw = next(probe_order, [])
            call_log.append(f"probe({len(raw)})")
            return raw, None

        def mock_exhaustive(reader_arg, img_arg, base_kw, *, _include_rotations=True, **kw):
            assert _include_rotations is False
            return [("default", rot180_raw)]

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            with patch.object(easyocr_mod, "_exhaustive_candidates", mock_exhaustive):
                candidates, diag = _exhaustive_with_orientation_selection(
                    object(), img, _base_kwargs(),
                    page_index=0, language="pt",
                    _dbnet_precomputed=(False, "weights_missing"),
                )

        assert diag["selected_angle"] == 180, (
            f"rot180 should win; got {diag['selected_angle']}"
        )
        all_texts = [t.text for _, toks in candidates for t in toks]
        assert any(t in all_texts for t in ("CERTO", "TEXTO", "DOC"))
        for _, toks in candidates:
            for tok in toks:
                assert tok.rotation == 180, f"expected rotation=180, got {tok.rotation}"

    # ------------------------------------------------------------------
    # Test 4 — rot270 selected
    # ------------------------------------------------------------------

    def test_exhaustive_selects_rot270_before_quality_fusion(self):
        """rot270 must be selected when upright, rot90 and rot180 are all insufficient."""
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import (
            _exhaustive_with_orientation_selection,
        )

        img = _make_image(100, 300)
        upright_raw = _make_raw([("u", 0.05)])
        rot90_raw   = _make_raw([("a", 0.12)])
        rot180_raw  = _make_raw([("b", 0.18)])
        rot270_raw  = _make_raw([("FINAL", 0.96), ("TEXTO", 0.95), ("OK", 0.93)])

        probe_order = iter([upright_raw, rot90_raw, rot180_raw, rot270_raw])

        def mock_run(reader, image, **kw):
            return next(probe_order, []), None

        def mock_exhaustive(reader_arg, img_arg, base_kw, *, _include_rotations=True, **kw):
            assert _include_rotations is False
            return [("default", rot270_raw)]

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            with patch.object(easyocr_mod, "_exhaustive_candidates", mock_exhaustive):
                candidates, diag = _exhaustive_with_orientation_selection(
                    object(), img, _base_kwargs(),
                    page_index=0, language="pt",
                    _dbnet_precomputed=(False, "weights_missing"),
                )

        assert diag["selected_angle"] == 270, (
            f"rot270 should win; got {diag['selected_angle']}"
        )
        for _, toks in candidates:
            for tok in toks:
                assert tok.rotation == 90, f"expected rotation=270, got {tok.rotation}"

    def test_rot90_selected_tokens_reconstruct_in_original_reading_order(self):
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.backends.easyocr import (
        _remap_raw_for_rotation,
        _result_to_pipeline_tokens,
        _token_rotation_from_applied_correction,
    )
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines

    raw_rotated = [
        (
            [[10, 10], [90, 10], [90, 20], [10, 20]],
            "LINHA UM",
            0.99,
        ),
        (
            [[10, 40], [90, 40], [90, 50], [10, 50]],
            "LINHA DOIS",
            0.99,
        ),
    ]

    remapped = _remap_raw_for_rotation(
        raw_rotated,
        90,
        200,
        100,
    )

    tokens = _result_to_pipeline_tokens(
        remapped,
        0,
        "pt",
        token_rotation=_token_rotation_from_applied_correction(90),
    )

    lines = reconstruct_ocr_lines(
        tokens,
        0,
        BBox(0, 0, 200, 100),
    )

    assert [line.text for line in lines] == [
        "LINHA UM",
        "LINHA DOIS",
    ]


    # ------------------------------------------------------------------
    # Test 5 — _include_rotations=False removes rotation candidates
    # ------------------------------------------------------------------

    def test_exhaustive_candidates_include_rotations_false_removes_rotation_labels(self):
        """_exhaustive_candidates(_include_rotations=False) must not produce rot* labels.

        Before fix: _include_rotations parameter does not exist → TypeError.
        After fix: rot90/rot180/rot270 are absent from results.
        """
        import numpy as np
        from structured_pdf_text.ocr.backends.easyocr import _exhaustive_candidates

        img = np.ones((100, 300, 3), dtype=np.uint8) * 200
        reader = object()

        default_raw = _make_raw([("texto", 0.90), ("doc", 0.85), ("ok", 0.88)])

        def mock_run(reader_arg, image, **kw):
            return default_raw, None

        import structured_pdf_text.ocr.backends.easyocr as easyocr_mod
        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            with patch.object(easyocr_mod, "_apply_deskew_with_inverse",
                              return_value=(img, __import__("numpy").eye(3))):
                with patch.object(easyocr_mod, "_apply_clahe", return_value=img):
                    results = _exhaustive_candidates(
                        reader, img, _base_kwargs(),
                        _dbnet_precomputed=(False, "weights_missing"),
                        _include_rotations=False,
                    )

        rotation_labels = {label for label, _ in results if label.startswith("rot")}
        assert not rotation_labels, (
            f"_include_rotations=False must suppress rotation candidates; "
            f"found: {rotation_labels}"
        )


# ---------------------------------------------------------------------------
# TestAdaptiveOrientationEdgeCases — guards for adaptive path (already fixed)
# ---------------------------------------------------------------------------

class TestAdaptiveOrientationEdgeCases:
    """The adaptive path already has orientation selection; these are regression guards."""

    def test_adaptive_many_bad_tokens_still_trigger_orientation_search(self):
        """Many low-confidence upright tokens must trigger rotation probes.

        Scenario:
            upright: 6 tokens, conf=0.05 → mean_conf<0.40 → insufficient → probe rotations
            rot90: "BOM TEXTO CERTO" (3 high-conf tokens) → selected

        The fix (_adaptive_with_orientation_selection) must handle this case.
        """
        import numpy as np
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import _adaptive_with_orientation_selection

        img = np.ones((100, 300, 3), dtype=np.uint8) * 200

        # 6 upright tokens with conf=0.05 → mean_conf=0.05 < 0.40 → not sufficient
        upright_raw = _make_raw([(f"t{i}", 0.05) for i in range(6)])
        # rot90: 3 high-conf tokens
        rot90_good  = _make_raw([("BOM", 0.95), ("TEXTO", 0.93), ("CERTO", 0.91)])

        def mock_run(reader, image, **kw):
            arr = np.asarray(image)
            h, w = arr.shape[:2]
            return (rot90_good if h > w else upright_raw), None

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            pipe_candidates, diag = _adaptive_with_orientation_selection(
                object(), img, _base_kwargs(),
                page_index=0, language="pt",
            )

        assert diag["selected_angle"] == 90, (
            f"6 low-conf upright tokens must trigger orientation search "
            f"and select rot90; got angle={diag['selected_angle']}"
        )
        all_texts = [t.text for _, toks in pipe_candidates for t in toks]
        assert any(t in all_texts for t in ("BOM", "TEXTO", "CERTO")), (
            "rot90 text must be selected"
        )
        # Upright garbage must not appear
        assert not any(t.startswith("t") and len(t) == 2 for t in all_texts), (
            "upright low-conf tokens must not appear when rot90 is selected"
        )

    def test_adaptive_sufficient_upright_skips_rotation_probe(self):
        """Strong upright quality must skip rotation probes entirely."""
        import numpy as np
        from structured_pdf_text.ocr.backends import easyocr as easyocr_mod
        from structured_pdf_text.ocr.backends.easyocr import _adaptive_with_orientation_selection

        img = np.ones((100, 300, 3), dtype=np.uint8) * 200
        upright_raw = _make_raw([
            ("BOAS", 0.95), ("PALAVRAS", 0.93), ("AQUI", 0.91),
        ])

        probe_calls: list[str] = []

        def mock_run(reader, image, **kw):
            import numpy as np
            arr = np.asarray(image)
            h, w = arr.shape[:2]
            probe_calls.append("rot" if h > w else "upright")
            return upright_raw, None

        with patch.object(easyocr_mod, "_run_easyocr", mock_run):
            _, diag = _adaptive_with_orientation_selection(
                object(), img, _base_kwargs(),
                page_index=0, language="pt",
            )

        assert diag["selected_angle"] == 0, (
            "sufficient upright quality must keep angle=0"
        )
        # No rotation probe should have been issued (all calls should be "upright")
        rotation_probes = [c for c in probe_calls if c == "rot"]
        assert not rotation_probes, (
            f"strong upright must not trigger rotation probes; got {probe_calls}"
        )
