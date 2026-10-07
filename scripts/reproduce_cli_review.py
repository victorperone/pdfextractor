"""Small model-free reproductions for the CLI execution review.

Prints observed behavior, rather than claiming that a successful script exit
means the pipeline is correct. No model is constructed or downloaded.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_cli_execution import save
from structured_pdf_text.api import (
    _recover_selected_regions, _recover_weak_ocr_regions, _refine_small_footnote_tokens,
    _figures_requiring_ocr, _refine_figure_ocr,
)
from structured_pdf_text.config import OcrQualityThresholds
from structured_pdf_text.document import OcrToken, RegionKind, SourceKind
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult
from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
from structured_pdf_text.ocr.quality import assess_ocr_quality


def token(text, box, confidence=0.99):
    return OcrToken(text, BBox(*box), confidence, "pt-BR", SourceKind.OCR_PAGE)


def main():
    import numpy as np
    from structured_pdf_text.ocr.backends import easyocr

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("output/validacao_completa_2026-10-07"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = {
        "noise_inside_good_line": [
            OcrCandidateResult("default", "craft_greedy", (token(
                "Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.", (0, 0, 600, 20)),), 1),
            OcrCandidateResult("rot90", "craft_greedy", (token("8", (100, 0, 110, 20), 0.05),), -99),
        ],
        "split_candidate_duplicates_span": [
            OcrCandidateResult("default", "craft_greedy", (token("ABC DEF", (0, 0, 200, 20)),), 1),
            OcrCandidateResult("high_recall", "craft_greedy", (
                token("ABC", (0, 0, 100, 20)), token("DEF", (100, 0, 200, 20))), 0.9),
        ],
    }
    fusion = {}
    for name, candidates in cases.items():
        result = OcrCandidateFusionEngine().fuse(candidates)
        fusion[name] = {"output": [{"text": t.text, "confidence": t.confidence,
                                    "bbox": str(t.bbox), "provenance": t.provenance}
                                   for t in result.tokens],
                        "conflicts": result.conflict_count, "consensus": result.consensus_count}
    save(args.output_dir / "fusion_reproductions.json", fusion)

    image = np.full((100, 100, 3), 255, dtype=np.uint8)
    image[10:30, 10:30] = 0
    contrast = {"black_fraction": 0.04, "mean": float(image.mean()), "std": float(image.std()),
                "foreground": 0, "background": 255,
                "variants": [{"label": name, "identical_to_input": bool(np.array_equal(arr, image))}
                             for name, arr in easyocr._image_preprocessing_candidates(image, adaptive=True)]}
    save(args.output_dir / "adaptive_contrast_reproduction.json", contrast)

    body = [token("Corpo A", (10, 100, 110, 120), 1), token("Corpo B", (10, 200, 110, 220), 1)]
    note = token("NOTA ABC", (10, 850, 110, 860), 1)
    class NoteEngine:
        def __init__(self, result):
            self.result = result
        def recognize_page(self, image, page_index, page_bbox, *, quality_policy=None, page_rotation=0):
            return self.result
    page_box = BBox(0, 0, 600, 1000)
    result, accepted = _refine_small_footnote_tokens(
        engine=NoteEngine([note, note]), page_index=0, page_bbox=page_box, tokens=body + [note],
        quality_policy="exhaustive", region_renderer=lambda box, scale: object(), base_scale=3)
    refinement = {"duplicate_reread": {"accepted": accepted, "tokens": [t.text for t in result],
                    "reconstructed_lines": [line.text for line in reconstruct_ocr_lines(result, 0, page_box)]}}
    complete_note = token("NOTA ABC importante que deve ser preservada", (10, 850, 500, 860), 0.90)
    short_note = token("NOTA", (10, 850, 50, 860), 1)
    result, accepted = _refine_small_footnote_tokens(
        engine=NoteEngine([short_note]), page_index=0, page_bbox=page_box, tokens=body + [complete_note],
        quality_policy="exhaustive", region_renderer=lambda box, scale: object(), base_scale=3)
    refinement["truncated_reread"] = {"accepted": accepted, "old_text": complete_note.text,
                                      "old_confidence": 0.90, "reread_text": short_note.text,
                                      "reread_confidence": 1, "tokens": [t.text for t in result]}
    ordinary = token("2026ABC", (10, 850, 110, 870), 1)
    renders = []
    _, accepted = _refine_small_footnote_tokens(
        engine=NoteEngine([ordinary]), page_index=0, page_bbox=page_box, tokens=body + [ordinary],
        quality_policy="exhaustive", region_renderer=lambda box, scale: renders.append(str(box)), base_scale=3)
    refinement["ordinary_numeric_line"] = {"line_height": 20, "body_line_height": 20, "confidence": 1,
                                            "rerender_count": len(renders), "accepted": accepted, "boxes": renders}
    save(args.output_dir / "refinement_reproductions.json", refinement)

    backend = easyocr.EasyOCRBackend.__new__(easyocr.EasyOCRBackend)
    backend._closed, backend._reader, backend._language = False, object(), "pt-BR"
    backend._run_kwargs = lambda: {"text_threshold": 0.7, "low_text": 0.4, "link_threshold": 0.4, "min_size": 20}
    backend.reset_page_diagnostics()
    half_white = np.zeros((200, 200, 3), dtype=np.uint8)
    half_white[:, 100:] = 255
    calls = []
    def inference(reader, image, **kwargs):
        calls.append(kwargs)
        return [([[1, 1], [90, 1], [90, 10], [1, 10]], "CORRETO", 0.99)], None
    box = BBox(0, 0, 200, 200)
    class RegionEngine:
        identity = SimpleNamespace(engine="easyocr")
        @property
        def last_pass_count(self):
            return backend.last_pass_count
        @property
        def last_batch_count(self):
            return backend.last_batch_count
        def recognize_page(self, image, page_index, page_bbox=None, *, quality_variants=None,
                           quality_policy=None, page_rotation=0):
            return backend.recognize_page(image, page_index, page_bbox, quality_variants=quality_variants,
                                          quality_policy=quality_policy, page_rotation=page_rotation)
    with patch.object(easyocr, "_run_easyocr", inference):
        backend.recognize_page(half_white, 0, box, quality_policy="baseline")
        normal = len(calls)
        calls.clear()
        backend.recognize_page(half_white, 0, box, quality_policy="baseline", quality_variants=True)
        forwarded = len(calls)
        calls.clear()
        region = SimpleNamespace(region_id="missing-region", bbox=box, kind=RegionKind.FIGURE,
                                 quality=SimpleNamespace(reasons=("native_text_missing",)))
        result = _recover_selected_regions(RegionEngine(), half_white, 0, box, [region], True,
                                           quality_policy="baseline")
        baseline = {"page_baseline_inferences": normal,
                    "baseline_with_forwarded_quality_variants_inferences": forwarded,
                    "one_region_baseline_inferences": len(calls), "passes": result[1],
                    "batches": result[2], "stats": result[3]}
    save(args.output_dir / "baseline_region_reproduction.json", baseline)
    complete_line = token("Valor: R$ 9.876,54; desconto: -2,75%; quantidade: 00042.",
                          (10, 100, 510, 110), 0.85)
    partial_line = token("Valor", (10, 100, 50, 110), 1)
    class ShortRegionRefiner:
        def __init__(self, engine):
            pass
        def refine(self, *args):
            return SimpleNamespace(tokens=(partial_line,), ocr_passes=1, ocr_batches=1,
                                   attempts=(object(),), selected_scale_factor=1.5)
    with patch("structured_pdf_text.api.OcrRegionRefiner", ShortRegionRefiner):
        result, _, _, stats = _recover_weak_ocr_regions(
            object(), object(), 0, page_box, [complete_line],
            reconstruct_ocr_lines([complete_line], 0, page_box), "exhaustive", OcrQualityThresholds())
    weak = {"old_text": complete_line.text, "old_confidence": 0.85,
            "new_text": partial_line.text, "new_confidence": 1,
            "output": [t.text for t in result], "stats": stats}
    save(args.output_dir / "weak_region_truncation_reproduction.json", weak)
    # Keep the rotated backend path real; substitute only the heavy candidates.
    raw_rotated = [
        ([[10, 10], [90, 10], [90, 20], [10, 20]], "LINHA UM", 1),
        ([[10, 40], [90, 40], [90, 50], [10, 50]], "LINHA DOIS", 1),
    ]
    remapped = easyocr._remap_raw_for_rotation(raw_rotated, 90, 200, 100)
    # B1: exhaustive now goes through _exhaustive_with_orientation_selection —
    # patch that function directly and provide already-converted OcrToken lists.
    pipeline_tokens = list(easyocr._result_to_pipeline_tokens(remapped, 0, "pt", token_rotation=90))
    _mock_orient_diag = {"selected_angle": 90, "attempts": [
        {"angle": 0, "score": 0.10, "token_count": 0},
        {"angle": 90, "score": 0.92, "token_count": 2},
    ]}
    backend._quantize, backend._auxiliary_readers = True, {}
    backend._ensure_dbnet18_runtime = lambda: False
    orientation_box = BBox(0, 0, 200, 100)
    with patch.object(easyocr, "_exhaustive_with_orientation_selection",
                      return_value=([("rot90", pipeline_tokens)], _mock_orient_diag)):
        current_tokens = backend.recognize_page(np.zeros((100, 200, 3), dtype=np.uint8),
                                                0, orientation_box, quality_policy="exhaustive")
    # This illustrates the reconstruction contract, not an implemented fix.
    oriented_tokens = [replace(t, rotation=270) for t in current_tokens]
    orientation = {
        "candidate": "rot90", "applied_clockwise_correction": 90,
        "tokens": [{"text": t.text, "rotation": t.rotation, "bbox": str(t.bbox)}
                   for t in current_tokens],
        "current_lines": [line.text for line in reconstruct_ocr_lines(current_tokens, 0, orientation_box)],
        "orientation_preserved_lines": [line.text for line in reconstruct_ocr_lines(oriented_tokens, 0, orientation_box)],
        "assessment_with_correct_orientation": asdict(assess_ocr_quality(oriented_tokens)),
        "assessment_single_horizontal_digit": asdict(assess_ocr_quality([
            token("1", (10, 10, 15, 30), 1),
        ])),
    }
    save(args.output_dir / "rotation_orientation_reproduction.json", orientation)
    image_one, image_two = BBox(10, 100, 200, 200), BBox(300, 100, 500, 200)
    figure_page = SimpleNamespace(bbox=BBox(0, 0, 600, 300), objects=SimpleNamespace(
        images=[SimpleNamespace(bbox=image_one), SimpleNamespace(bbox=image_two)]))
    selected = [SimpleNamespace(bbox=image_one)]
    figure_requests = []
    class FigureRefiner:
        def __init__(self, engine):
            pass
        def refine(self, image, index, box, request):
            figure_requests.append(request.bbox)
            return SimpleNamespace(tokens=[token("FIGURA", (request.bbox.x0, request.bbox.y0,
                                                           request.bbox.x1, request.bbox.y1), 1)],
                                   status="ok", ocr_passes=1, ocr_batches=1)
    images_pending = _figures_requiring_ocr(figure_page, selected)
    with patch("structured_pdf_text.api.OcrRegionRefiner", FigureRefiner):
        figure_results = _refine_figure_ocr(object(), np.full((300, 600, 3), 255, dtype=np.uint8),
                                          figure_page, 0, [], images=images_pending)
    partial_region = SimpleNamespace(bbox=BBox(10, 100, 20, 110))
    one_figure = SimpleNamespace(bbox=figure_page.bbox, objects=SimpleNamespace(
        images=[SimpleNamespace(bbox=image_one)]))
    figures = {
        "already_covered_image_reread": {
            "figure_ocr_requested": bool(images_pending), "selected_bbox": str(image_one),
            "ocr_requests": [str(box) for box in figure_requests], "refinements": len(figure_results),
        },
        "partial_region_suppresses_whole_image": {
            "image_bbox": str(image_one), "selected_bbox": str(partial_region.bbox),
            "image_fraction_covered": image_one.overlap_ratio(partial_region.bbox),
            "selected_fraction_inside_image": partial_region.bbox.overlap_ratio(image_one),
            "figure_ocr_requested": bool(_figures_requiring_ocr(one_figure, [partial_region])),
        },
    }
    save(args.output_dir / "figure_recovery_reproductions.json", figures)
    print("Observed fusion:", {name: [t["text"] for t in data["output"]] for name, data in fusion.items()})
    print("Observed adaptive variants:", contrast["variants"])
    print("Observed note refinement:", refinement)
    print("Observed baseline:", baseline)
    print("Observed weak-region replacement:", weak)
    print("Observed orientation loss and false recovery:", orientation)
    print("Observed figure recovery decisions:", figures)


if __name__ == "__main__":
    main()
