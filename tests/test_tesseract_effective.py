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
