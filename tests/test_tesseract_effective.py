from __future__ import annotations

from PIL import Image

from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.ocr.backends import tesseract as tesseract_module
from structured_pdf_text.ocr.backends.tesseract import TesseractBackend
from structured_pdf_text.ocr.contracts import OCRRequest


def test_raw_and_pipeline_paths_share_effective_tesseract_options(monkeypatch) -> None:
    calls = []

    def fake_tsv(image, language, psm, oem, **kwargs):
        calls.append((language, psm, oem, kwargs))
        return ""

    monkeypatch.setattr(tesseract_module, "_run_tesseract_tsv", fake_tsv)
    backend = TesseractBackend.__new__(TesseractBackend)
    backend._config = ExtractorConfig(ocr_engine="tesseract", num_threads=3)
    backend._language = "pt-BR"
    backend._tesseract_cmd = "configured-tesseract"
    backend._profile = "tesseract-degraded-scan-v1"
    backend._extra_flags = ["--tessdata-dir", "/models/tessdata"]
    backend._tess_lang = "por"
    backend._psm = 6
    backend._oem = 1
    backend._dpi = 300
    backend._conf_min = 0.0
    backend._osd_enabled = False
    backend._osd_conf_min = 2.0
    backend._tessdata = "/models/tessdata"
    backend._version = "5.test"
    backend._artifact_hashes = {}

    image = Image.new("RGB", (100, 50), "white")
    request = OCRRequest(
        image=image,
        image_sha256="0" * 64,
        document_id="parity",
        page_index=0,
        input_kind="page",
        language="pt-BR",
    )
    backend.recognize(request)
    backend.recognize_page(image, 0)

    assert len(calls) == 2
    assert calls[0] == calls[1]
    language, psm, oem, options = calls[0]
    assert (language, psm, oem) == ("por", 6, 1)
    assert options["dpi"] == 300
    assert options["executable"] == "configured-tesseract"
    assert options["profile"] == "tesseract-degraded-scan-v1"
    assert options["num_threads"] == 3


def test_osd_rotation_maps_pipeline_and_canonical_tokens(monkeypatch) -> None:
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t20\t10\t90\tPALAVRA\n"
    )
    config = ExtractorConfig(ocr_engine="tesseract")
    backend = TesseractBackend.__new__(TesseractBackend)
    backend._config = config
    backend._language = "pt-BR"
    backend._tess_lang = "por"
    backend._profile = "default"
    backend._version = "5.test"
    backend._tessdata = "test-data"
    backend._artifact_hashes = {}
    backend._dpi = 144
    backend._conf_min = 0.0
    backend._psm = 3
    backend._oem = 1
    backend._tesseract_cmd = "tesseract"
    backend._osd_enabled = True
    backend._osd_conf_min = 2.0
    backend._run_effective_tesseract = lambda image, dpi: (tsv, rotation, 200, 100)
    image = Image.new("RGB", (200, 100))

    for rotation in (0, 90, 180, 270):
        backend._run_effective_tesseract = lambda image, dpi, rotation=rotation: (
            tsv, rotation, 200, 100
        )
        pipeline_tokens = backend.recognize_page(image, 0)
        assert len(pipeline_tokens) == 1
        assert pipeline_tokens[0].text == "PALAVRA"
        assert pipeline_tokens[0].bbox.width > 0
        assert pipeline_tokens[0].bbox.height > 0

        request = OCRRequest(
            image=image,
            image_sha256="0" * 64,
            document_id="rotated-page",
            page_index=0,
            input_kind="region",
            language="pt-BR",
            region_bbox=(5, 7, 205, 107),
        )
        canonical = backend.recognize(request)
        assert canonical.status == "ok"
        assert len(canonical.tokens) == 1
        assert canonical.tokens[0].bbox_px[0] >= 5
        assert canonical.tokens[0].bbox_px[1] >= 7
        assert canonical.tokens[0].polygon_px
