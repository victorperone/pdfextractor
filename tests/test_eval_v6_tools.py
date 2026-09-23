from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).parents[1]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = _load("compare_v5_v6_eval", "scripts/eval_v6/compare_v5_v6.py")
tablemagic = _load("tablemagic_eval", "scripts/eval_v6/eval_tablemagic.py")
raster = _load("raster_eval", "scripts/eval_v6/rasterize_pdf.py")


def test_compare_pages_are_human_facing_and_deduplicated() -> None:
    assert compare.parse_pages("27,12,27-29") == [12, 27, 28, 29]
    with pytest.raises(ValueError, match="1-based"):
        compare.parse_pages("0")


def test_tablemagic_requires_every_local_submodel() -> None:
    inventory = [
        {
            "role": "ocr",
            "name": "model",
            "exists": True,
            "file_count": 1,
            "has_weight": True,
            "has_metadata": False,
        }
    ]
    with pytest.raises(FileNotFoundError, match="complete local submodels"):
        tablemagic.validate_model_inventory(inventory)


def test_tablemagic_profiles_keep_internal_ocr_identity_explicit() -> None:
    v5 = tablemagic.model_names("tm-v5")
    v6 = tablemagic.model_names("tm-v6")
    assert v5["text_detection"] == "PP-OCRv5_server_det"
    assert v5["text_recognition"] == "latin_PP-OCRv5_mobile_rec"
    assert v6["text_detection"] == "PP-OCRv6_medium_det"
    assert v6["text_recognition"] == "PP-OCRv6_medium_rec"
    assert v5["wired_structure"] == v6["wired_structure"]


def test_tablemagic_result_json_preserves_full_payload() -> None:
    item = SimpleNamespace(json='{"table_res_list":[{"cell_box_list":[[1,2,3,4]]}]}')
    assert tablemagic._result_json(item)["table_res_list"][0]["cell_box_list"]


def test_rasterize_pdf_removes_extractable_text(tmp_path: Path) -> None:
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    source = tmp_path / "source.pdf"
    canvas_pdf = canvas.Canvas(str(source), pagesize=(200, 200))
    canvas_pdf.drawString(20, 100, "OCRS-P10-CONTROL")
    canvas_pdf.save()

    output = tmp_path / "raster.pdf"
    manifest = tmp_path / "manifest.json"
    result = raster.rasterize_pdf(source, output, manifest, scale=1.0)

    assert result["text_layer_validation"]["passed"] is True
    assert result["pages"][0]["output_text_chars"] == 0
    assert output.exists()
    assert manifest.exists()
