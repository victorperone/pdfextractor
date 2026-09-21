from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pypdfium2 as pdfium


ROOT = Path(__file__).parent / "corpus" / "ocr_stress_v1"
GENERATOR_PATH = ROOT / "generate.py"


def _generator_module():
    spec = importlib.util.spec_from_file_location("ocr_stress_corpus_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_objects(page) -> list[object]:
    return [obj for obj in page.get_objects() if getattr(obj, "type", None) == 3]


def test_full_corpus_has_fixed_manifest_and_pdf_integrity(tmp_path: Path) -> None:
    generator = _generator_module()
    output_dir = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    pdf_path, written_manifest = generator.generate(
        output_dir,
        manifest_path=manifest_path,
    )

    assert written_manifest == manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["page_count"] == 60
    assert manifest["source_page_count"] == 60
    assert [page["source_page"] for page in manifest["pages"]] == list(range(1, 61))
    assert len({page["source_page_marker"] for page in manifest["pages"]}) == 60
    assert manifest["pdf_sha256"] == _sha256(pdf_path)
    assert manifest["ocr_models_loaded"] is False
    assert manifest["production_parser_imported"] is False

    document = pdfium.PdfDocument(str(pdf_path))
    assert len(document) == 60
    for page_record in manifest["pages"]:
        page = document[page_record["page"] - 1]
        width, height = page.get_size()
        expected_width, expected_height = page_record["page_size_pt"]
        assert abs(width - expected_width) < 0.1
        assert abs(height - expected_height) < 0.1
        text = page.get_textpage().get_text_range().strip()
        assert bool(text) is page_record["has_native_text_layer"]
        if page_record["has_native_text_layer"]:
            assert page_record["source_page_marker"] in text
        if page_record["raster_regions"]:
            assert _image_objects(page)


def test_raster_pages_have_no_selectable_text_and_render(tmp_path: Path) -> None:
    generator = _generator_module()
    pdf_path, manifest_path = generator.generate(
        tmp_path / "outputs",
        manifest_path=tmp_path / "manifest.json",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    document = pdfium.PdfDocument(str(pdf_path))
    raster_only = [page for page in manifest["pages"] if not page["has_native_text_layer"]]
    assert len(raster_only) >= 20
    for page_record in raster_only:
        page = document[page_record["page"] - 1]
        assert page.get_textpage().get_text_range().strip() == ""
        rendered = page.render(scale=0.2).to_pil()
        assert rendered.width > 0 and rendered.height > 0
        assert _image_objects(page)


def test_required_subsets_preserve_original_page_mapping(tmp_path: Path) -> None:
    generator = _generator_module()
    for selection in ([15, 16], [36, 37], [38, 39], [56, 57]):
        label = f"{selection[0]}-{selection[-1]}"
        pdf_path, manifest_path = generator.generate(
            tmp_path / label,
            pages=selection,
            manifest_path=tmp_path / f"{label}.json",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["page_count"] == 2
        assert [page["source_page"] for page in manifest["pages"]] == selection
        assert [page["page"] for page in manifest["pages"]] == [1, 2]
        assert len(pdfium.PdfDocument(str(pdf_path))) == 2
        assert all(page["continuation"] for page in manifest["pages"])


def test_continuous_figure_uses_one_source_with_distinct_crops(tmp_path: Path) -> None:
    generator = _generator_module()
    _, manifest_path = generator.generate(
        tmp_path / "15-16",
        pages=[15, 16],
        manifest_path=tmp_path / "15-16.json",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    first, second = manifest["pages"]
    assert first["continuation"]["shared_image_id"] == "figure-source-15-16"
    assert second["continuation"]["shared_image_id"] == "figure-source-15-16"
    assert first["raster_regions"][0]["source_crop"] == "left"
    assert second["raster_regions"][0]["source_crop"] == "right"


def test_manifest_declares_valid_local_codes_and_no_external_assets() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    qr_pages = [
        figure
        for page in manifest["pages"]
        for figure in page["figures"]
        if figure.get("kind") in {"qr", "code128"}
    ]
    assert qr_pages
    assert all(figure["valid_generated"] for figure in qr_pages)
    assert not list((ROOT / "assets").glob("**/*")) if (ROOT / "assets").exists() else True
