"""Regression checks for exhaustive OCR cost without removing candidates."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from structured_pdf_text.errors import FatalExtractionError, ResourceExhaustedExtractionError
from structured_pdf_text.ocr.backends import easyocr as backend


class Reader:
    def __init__(self):
        self.detect_calls = 0
        self.recognize_calls = 0

    def detect(self, image, **kwargs):
        self.detect_calls += 1
        return ([[[1, 6, 1, 6]]], [[]])

    def recognize(self, image, horizontal, free, **kwargs):
        self.recognize_calls += 1
        return [([[1, 1], [6, 1], [6, 6], [1, 6]], "texto", 0.9)]


@pytest.fixture
def exhaustive(monkeypatch):
    monkeypatch.setitem(sys.modules, "easyocr.utils", SimpleNamespace(
        reformat_input=lambda image: (image, image[:, :, 0]),
    ))
    monkeypatch.setattr(backend, "_image_preprocessing_candidates", lambda *a, **k: [])
    monkeypatch.setattr(backend, "_apply_clahe", lambda image: image)
    monkeypatch.setattr(backend, "_apply_deskew_with_inverse", lambda image: (image, None))
    monkeypatch.setattr(backend, "_rebuild_reader_no_quantize", lambda reader: Reader())
    monkeypatch.setattr(backend, "_build_dbnet18_reader", lambda reader: Reader())
    monkeypatch.setenv("EASYOCR_MAG_RATIO", "1.2")
    image = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)
    kwargs = dict(
        decoder="greedy", beamwidth=5, adjust_contrast=0.5,
        allowlist=None, blocklist=None, workers=0, text_threshold=0.7,
        low_text=0.4, link_threshold=0.4, min_size=20,
        width_ths=0.5, add_margin=0.1,
    )
    return image, kwargs


def run_exhaustive(reader, image, kwargs, **options):
    return backend._exhaustive_candidates(
        reader, image, kwargs, _dbnet_precomputed=(True, None), **options,
    )


def test_detection_cache_preserves_every_candidate_and_geometry(exhaustive, monkeypatch):
    image, kwargs = exhaustive
    cached_reader = Reader()
    stats = {}
    cached = run_exhaustive(cached_reader, image, kwargs, _detection_stats=stats)
    monkeypatch.setattr(backend, "_DetectionCachingReader", lambda reader, stats: reader)
    plain_reader = Reader()
    plain = run_exhaustive(plain_reader, image, kwargs)
    assert cached == plain
    assert cached_reader.recognize_calls == plain_reader.recognize_calls
    assert cached_reader.detect_calls < plain_reader.detect_calls
    assert stats["easyocr_exhaustive_detection_cache_hits"] >= 3


def test_cache_requires_same_pixels_parameters_and_isolates_mutation():
    reader = Reader()
    stats = {"easyocr_exhaustive_detection_calls": 0, "easyocr_exhaustive_detection_cache_hits": 0}
    proxy = backend._DetectionCachingReader(reader, stats)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    first = proxy.detect(image, width_ths=0.5)
    first[0][0][0][0] = 999
    assert proxy.detect(image.copy(), width_ths=0.5)[0][0][0][0] == 1
    proxy.detect(image, width_ths=0.25)
    image[0, 0, 0] = 255
    proxy.detect(image, width_ths=0.5)
    assert reader.detect_calls == 3
    assert stats["easyocr_exhaustive_detection_cache_hits"] == 1


def test_failed_detection_is_never_cached():
    calls = []

    def fail(image, **kwargs):
        calls.append(1)
        raise ValueError("detector failed")

    proxy = backend._DetectionCachingReader(SimpleNamespace(detect=fail), {
        "easyocr_exhaustive_detection_calls": 0, "easyocr_exhaustive_detection_cache_hits": 0,
    })
    for _ in range(2):
        with pytest.raises(ValueError):
            proxy.detect(np.zeros((8, 8, 3), dtype=np.uint8))
    assert len(calls) == 2


@pytest.mark.parametrize("available", [True, False])
def test_auxiliary_models_and_failed_builds_are_reused(exhaustive, monkeypatch, available):
    image, kwargs = exhaustive
    builds = {"no_quantize": 0, "dbnet18": 0}

    def factory(label):
        def build(reader):
            builds[label] += 1
            return Reader() if available else None
        return build

    monkeypatch.setattr(backend, "_rebuild_reader_no_quantize", factory("no_quantize"))
    monkeypatch.setattr(backend, "_build_dbnet18_reader", factory("dbnet18"))
    cache = {}
    first = run_exhaustive(Reader(), image, kwargs, _reader_cache=cache)
    second = run_exhaustive(Reader(), image.copy(), kwargs, _reader_cache=cache)
    assert first == second
    assert builds == {"no_quantize": 1, "dbnet18": 1}
    engine = backend.EasyOCRBackend.__new__(backend.EasyOCRBackend)
    engine._reader, engine._auxiliary_readers, engine._closed = Reader(), cache, False
    engine.close()
    assert cache == {}


@pytest.mark.parametrize("call_number", range(1, 14))
def test_memory_errors_abort_even_optional_exhaustive_variants(exhaustive, monkeypatch, call_number):
    image, kwargs = exhaustive
    calls = []

    def recognize(reader, image, **kwargs):
        calls.append(1)
        if len(calls) == call_number:
            raise MemoryError("allocation failed")
        return [([[1, 1], [6, 1], [6, 6], [1, 6]], "texto", 0.9)], None

    monkeypatch.setattr(backend, "_run_easyocr", recognize)
    # Required variants propagate MemoryError; optional variants classify it.
    with pytest.raises((MemoryError, ResourceExhaustedExtractionError)):
        run_exhaustive(Reader(), image, kwargs)
    assert len(calls) == call_number


@pytest.mark.parametrize("stage", ["reformat", "detect", "recognize"])
@pytest.mark.parametrize("error", [MemoryError("no memory"), FatalExtractionError("fatal")])
def test_primary_fatal_errors_never_retry_readtext(exhaustive, monkeypatch, stage, error):
    image, kwargs = exhaustive
    reader = Reader()

    def fail(*args, **kwargs):
        raise error

    def forbidden_fallback(*args, **kwargs):
        pytest.fail("A fatal failure must not trigger another OCR call")

    if stage == "reformat":
        monkeypatch.setitem(sys.modules, "easyocr.utils", SimpleNamespace(reformat_input=fail))
    else:
        monkeypatch.setattr(reader, stage, fail)
    reader.readtext = forbidden_fallback
    with pytest.raises(FatalExtractionError):
        backend._run_easyocr(reader, image, **kwargs)


@pytest.mark.parametrize("threads", [0, 2, 16])
def test_default_loader_workers_do_not_follow_inference_threads(monkeypatch, threads):
    monkeypatch.delenv("EASYOCR_WORKERS", raising=False)
    probe = {"is_windows": False, "cpu_count_logical": 20, "max_quality_env": False}
    assert backend._resolve_workers(threads, probe) == 0
    monkeypatch.setenv("EASYOCR_WORKERS", "3")
    assert backend._resolve_workers(threads, probe) == 3


def test_auxiliary_builds_use_constructor_snapshot_without_upstream_language_attribute(monkeypatch):
    builds = []

    def build(languages, **kwargs):
        builds.append((languages, kwargs))
        return Reader()

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=build))
    original = Reader()  # Like upstream, exposes neither lang_list nor recog_network.
    original._pdfextractor_init_options = {
        "lang_list": ["pt"], "gpu": False, "quantize": True,
        "recog_network": "latin_g1", "model_storage_directory": "/existing/models",
        "download_enabled": True,
    }
    assert backend._rebuild_reader_no_quantize(original) is not None
    assert backend._build_dbnet18_reader_strict(original) is not None
    assert builds[0][0] == builds[1][0] == ["pt"]
    assert builds[0][1]["quantize"] is False
    assert builds[1][1]["detect_network"] == "dbnet18"
    assert builds[1][1]["quantize"] is True
    for _, options in builds:
        assert options["recog_network"] == "latin_g1"
        assert options["model_storage_directory"] == "/existing/models"
        assert options["download_enabled"] is False


def test_upstream_quantize_tuple_false_is_not_treated_as_true():
    reader = SimpleNamespace(lang_list=["pt"], quantize=(False,), device="cpu")
    assert backend._reader_init_options(reader)["quantize"] is False


@pytest.mark.parametrize("height,width", [(600, 200), (200, 600)])
def test_dbnet_canvas_uses_short_side_instead_of_enlarging_rectangular_crops(height, width):
    craft = SimpleNamespace(detect_network="craft")
    dbnet = SimpleNamespace(detect_network="dbnet18")
    assert backend._detector_canvas_size(craft, height, width, 1.2) == 720
    assert backend._detector_canvas_size(dbnet, height, width, 1.2) == 240


def test_runtime_probe_does_not_expand_tiny_image_to_default_canvas(monkeypatch):
    calls = []
    reader = SimpleNamespace(detect=lambda image, **kwargs: calls.append((image.shape, kwargs)))
    monkeypatch.setattr(backend, "_dbnet18_weights_available", lambda path: True)
    monkeypatch.setattr(backend, "_build_dbnet18_reader_strict", lambda original: reader)
    assert backend._probe_dbnet18_runtime_uncached(None) == (True, None)
    assert calls == [((96, 320, 3), {"canvas_size": 96})]


@pytest.mark.parametrize("language,network,filename", [
    ("en", None, "english_g2"), ("en-US", "standard", "english_g2"),
    ("pt-BR", "standard", "latin_g2"), ("pt-BR", "latin_g1", "latin_g1"),
])
def test_readiness_uses_actual_recognition_weights(monkeypatch, tmp_path, language, network, filename):
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr import readiness
    from structured_pdf_text.ocr.languages import easyocr_recognition_model
    monkeypatch.setenv("EASYOCR_MODULE_PATH", str(tmp_path))
    if network is None:
        monkeypatch.delenv("EASYOCR_RECOG_NETWORK", raising=False)
    else:
        monkeypatch.setenv("EASYOCR_RECOG_NETWORK", network)
    (tmp_path / "craft_mlt_25k.pth").write_bytes(b"test model")
    (tmp_path / f"{filename}.pth").write_bytes(b"test model")
    monkeypatch.setattr(readiness.importlib.util, "find_spec", lambda name: object())
    fake_config = SimpleNamespace(detection_models={})
    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(config=fake_config))
    monkeypatch.setitem(sys.modules, "easyocr.config", fake_config)
    assert easyocr_recognition_model(language, network) == filename
    assert readiness.probe_static(ExtractorConfig(language=language)).status == readiness.ReadinessStatus.READY


def test_setup_probes_real_style_reader_without_saved_language_list(monkeypatch, tmp_path, capsys):
    from structured_pdf_text.cli import _cmd_setup_easyocr_models
    builds = []

    def build(languages, **kwargs):
        builds.append((languages, kwargs))
        return Reader()

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=build))
    monkeypatch.setattr(backend, "_dbnet18_weights_available", lambda path: True)
    monkeypatch.setenv("EASYOCR_RECOG_NETWORK", "latin_g1")
    assert _cmd_setup_easyocr_models("pt-BR", str(tmp_path), True) == 0
    assert builds[0][0] == builds[-1][0] == ["pt"]
    assert builds[-1][1]["download_enabled"] is False
    assert all(options["recog_network"] == "latin_g1" for _, options in builds)


def test_region_refinement_counts_internal_variants_and_resets_per_request(exhaustive, monkeypatch):
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.recovery import OcrRegionRefiner, RegionRefinementRequest
    image, _ = exhaustive
    fake_module = SimpleNamespace(Reader=lambda *args, **kwargs: Reader())
    monkeypatch.setattr(backend, "_import_easyocr", lambda: fake_module)
    monkeypatch.setattr(backend, "_apply_torch_threads", lambda *args: (None, None))
    engine = backend.EasyOCRBackend(ExtractorConfig())
    monkeypatch.setattr(engine, "_ensure_dbnet18_runtime", lambda: (True, None))
    page = BBox(0, 0, 8, 8)
    result = OcrRegionRefiner(engine).refine(image, 0, page, RegionRefinementRequest(
        bbox=page, scale_factors=(1.0, 1.5), quality_policy="exhaustive",
    ))
    assert result.ocr_passes == result.ocr_batches == engine._easyocr_calls
    assert result.ocr_passes > len(result.attempts)
    previous_total = engine._easyocr_calls
    engine.recognize_page(image, 0, page)
    assert engine.last_pass_count == engine.last_batch_count == 1
    assert engine._easyocr_calls == previous_total + 1
    engine.close()
