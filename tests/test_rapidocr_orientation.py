from __future__ import annotations

from types import SimpleNamespace
import sys

import numpy as np
import pytest

from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.ocr.backends import rapidocr as backend_module
from structured_pdf_text.ocr.pipelines.rapidocr import models
from structured_pdf_text.ocr.pipelines.rapidocr.policy import RapidOCROrientationPolicy


def _source_image(height=120, width=200):
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    image[20:30, 40:150] = 0
    return image


def _ink_box(image):
    ys, xs = np.nonzero(image[..., 0] < 128)
    return float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)


def _rows_for(image, confidence, text="PALAVRA"):
    x0, y0, x1, y1 = _ink_box(image)
    box = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return [(box, text, confidence)]


@pytest.fixture
def backend(tmp_path, monkeypatch):
    for name in ("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(models, "verify_artifacts", lambda *args: dict.fromkeys(
        ("detector", "recognizer", "dictionary", "classifier"), "a" * 64))
    monkeypatch.setattr(backend_module, "_rapidocr_config_types", lambda: (
        lambda value: value, lambda value: value, SimpleNamespace(PPOCRV6="v6", PPOCRV4="v4")))

    class Engine:
        __module__ = "rapidocr.fixture"

        def __init__(self, *, params):
            self.params = params

    monkeypatch.setattr(backend_module, "_import_rapidocr", lambda *args: Engine)
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        preload_dlls=lambda **kwargs: None, get_available_providers=lambda: ["CPUExecutionProvider"]))
    return backend_module.RapidOCRBackend(
        ExtractorConfig(ocr_engine="rapidocr", ocr_provider="onnxruntime", use_gpu=False), "onnxruntime")


def _fake_infer(backend, respond):
    calls = []

    def infer(image):
        calls.append(np.asarray(image).copy())
        return respond(np.asarray(image))

    backend._infer = infer
    return calls


@pytest.mark.parametrize("quarter_turns", [1, 2, 3])
def test_quarter_turn_mapping_inverts_numpy_rotation_for_pixel_centres(quarter_turns):
    height, width = 37, 61
    for y, x in [(0, 0), (5, 12), (height - 1, width - 1), (30, 3)]:
        marker = np.zeros((height, width), dtype=np.uint8)
        marker[y, x] = 255
        turned = np.rot90(marker, quarter_turns)
        ty, tx = np.argwhere(turned == 255)[0]
        mapped = backend_module._quarter_turn_to_source([(tx + .5, ty + .5)], quarter_turns, height, width)
        assert mapped[0] == pytest.approx([x + .5, y + .5])


def test_healthy_upright_reading_never_triggers_a_search(backend):
    source = _source_image()
    calls = _fake_infer(backend, lambda image: _rows_for(image, .98))

    rows = backend._recognize_rows(source)

    assert len(calls) == 1
    assert rows[0][2] == .98
    assert backend._orientation_selected == {}


def test_empty_upright_reading_does_not_search(backend):
    calls = _fake_infer(backend, lambda image: [])

    assert backend._recognize_rows(_source_image()) == []
    assert len(calls) == 1


def test_request_diagnostics_retain_attempt_angles_and_page_history_without_live_aliases(backend):
    from structured_pdf_text.ocr.pipeline.diagnostics import LegacyDiagnosticsProjector
    source = _source_image()
    turned = np.ascontiguousarray(np.rot90(source, 1))
    calls = _fake_infer(backend, lambda image: _rows_for(image, .99, "READABLE "*8)
                        if np.array_equal(image, turned) else _rows_for(image, .4, "x"))
    backend._recognize_rows(source)
    first = LegacyDiagnosticsProjector.snapshot(backend, engine_name="rapidocr")
    assert first["orientation_selected"] == 90
    assert first["orientation_attempts"][0]["angle"] == 0
    assert {a["angle"] for a in first["orientation_attempts"]} == {0, 90, 180, 270}
    assert all(a["elapsed_ms"] >= 0 and a["device"] == "cpu" for a in first["orientation_attempts"])
    assert LegacyDiagnosticsProjector.operation_metrics(backend)["passes"] == 4
    _fake_infer(backend, lambda image: _rows_for(image, .99))
    backend._recognize_rows(source)
    result = LegacyDiagnosticsProjector.consume(backend, engine_name="rapidocr")
    assert [d["selected_angle"] for d in result["orientation_decisions"]] == [90, 0]
    assert len(result["orientation_decisions"][1]["attempts"]) == 1
    assert first["orientation_selected"] == 90
    assert len(calls) == 4
    assert backend.consume_page_diagnostics()["rapidocr_calls"] == 0


def test_inference_reports_preprocessing_and_runtime_stage_times(backend):
    backend._engine = lambda image: SimpleNamespace(boxes=[], txts=[], scores=[], elapse_list=[.02, 0, .03])
    backend._recognize_rows(_source_image())
    attempt = backend.consume_page_diagnostics()["orientation_decisions"][0]["attempts"][0]
    assert attempt["detection_ms"] == 20 and attempt["recognition_ms"] == 30
    assert attempt["preprocess_ms"] >= 0 and attempt["runtime_ms"] >= 0


@pytest.mark.parametrize("quarter_turns", [1, 2, 3])
def test_weak_upright_reading_selects_the_confident_turn_and_maps_back_to_source(backend, quarter_turns):
    source = _source_image()
    turned_source = np.ascontiguousarray(np.rot90(source, quarter_turns))

    def respond(image):
        if np.array_equal(image, source):
            return _rows_for(image, .62, "xx")
        if np.array_equal(image, turned_source):
            return _rows_for(image, .99, "PALAVRA " * 8)
        return _rows_for(image, .61, "ruido")

    _fake_infer(backend, respond)

    rows = backend._recognize_rows(source)

    box = np.asarray(rows[0][0], dtype=float)
    assert box.min(axis=0).tolist() == pytest.approx(list(_ink_box(source)[:2]))
    assert box.max(axis=0).tolist() == pytest.approx(list(_ink_box(source)[2:]))
    assert rows[0][1].startswith("PALAVRA")
    assert backend._orientation_selected == {quarter_turns: 1}


def test_alternative_must_be_confident_not_only_longer(backend):
    source = _source_image()
    turned_source = np.ascontiguousarray(np.rot90(source, 3))

    def respond(image):
        if np.array_equal(image, turned_source):
            return _rows_for(image, .70, "PALAVRA " * 8)
        return _rows_for(image, .62, "xx")

    _fake_infer(backend, respond)

    rows = backend._recognize_rows(source)

    assert rows[0][1] == "xx"
    assert backend._orientation_selected == {}


def test_alternative_must_clear_the_minimum_gain_over_upright(backend):
    source = _source_image()
    turned_source = np.ascontiguousarray(np.rot90(source, 1))

    def respond(image):
        if np.array_equal(image, turned_source):
            return _rows_for(image, .95, "PALAVRA")
        return _rows_for(image, .88, "PALAVRA PALAVRA")

    _fake_infer(backend, respond)

    rows = backend._recognize_rows(source)

    assert rows[0][1] == "PALAVRA PALAVRA"
    assert backend._orientation_selected == {}


def test_authorized_page_path_returns_crop_coordinates_after_a_turn(backend):
    from structured_pdf_text.geometry import BBox

    source = _source_image()
    turned_source = np.ascontiguousarray(np.rot90(source, 3))

    def respond(image):
        if np.array_equal(image, turned_source):
            return _rows_for(image, .99, "PALAVRA " * 8)
        return _rows_for(image, .40, "x")

    _fake_infer(backend, respond)

    tokens = backend.recognize_page(source, 0, page_bbox=BBox(0, 0, source.shape[1], source.shape[0]))

    assert tokens
    expected = _ink_box(source)
    assert tokens[0].bbox.x0 == pytest.approx(expected[0])
    assert tokens[0].bbox.y0 == pytest.approx(expected[1])
    assert tokens[0].bbox.x1 == pytest.approx(expected[2])
    assert tokens[0].bbox.y1 == pytest.approx(expected[3])


def test_orientation_identity_records_the_policy_that_produced_the_result(backend):
    tuning = backend.identity.extra["effective_tuning"]

    assert tuning["orientation_search"]["policy_id"] == "rapidocr-orientation-search-v2"
    assert tuning["orientation_search"]["alternatives"] == [3, 1, 2]
    assert tuning["orientation_classifier_enabled"] is False


@pytest.mark.parametrize("kwargs", [
    {"confidence_floor": 1.5}, {"minimum_gain": .5}, {"alternatives": (0,)}, {"alternatives": ()},
])
def test_orientation_policy_rejects_invalid_calibration(kwargs):
    with pytest.raises(ValueError):
        RapidOCROrientationPolicy(**kwargs)


@pytest.mark.parametrize('quarter_turns',[1,2,3])
def test_pipeline_parser_preserves_polygon_and_logical_line_order(backend,quarter_turns):
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.reconstruct import reconstruct_ocr_lines
    upright=_source_image();source=np.ascontiguousarray(np.rot90(upright,-quarter_turns))
    rows=[([[40,20],[100,20],[100,30],[40,30]],'PRIMEIRA',.99),([[40,60],[100,60],[100,70],[40,70]],'SEGUNDA',.99)]
    _fake_infer(backend,lambda image:rows if np.array_equal(image,upright)else _rows_for(image,.4,'x'))
    h,w=source.shape[:2];page_bbox=BBox(17,23,17+w*2,23+h*2)
    tokens=backend.recognize_page(source,0,page_bbox=page_bbox)
    assert {t.rotation for t in tokens}=={quarter_turns*90}
    assert all(t.polygon and len(t.polygon)==4 for t in tokens)
    assert all(page_bbox.x0<=p.x<=page_bbox.x1 and page_bbox.y0<=p.y<=page_bbox.y1 for t in tokens for p in t.polygon)
    assert [line.text for line in reconstruct_ocr_lines(tokens,0,page_bbox)]==['PRIMEIRA','SEGUNDA']


def test_orientation_is_per_result_not_mutable_last_call_state(backend):
    from structured_pdf_text.ocr.backends.rapidocr import _result_to_pipeline_tokens
    source=_source_image();turned=np.ascontiguousarray(np.rot90(source,2))
    _fake_infer(backend,lambda image:_rows_for(image,.99,'PALAVRA '*8)if np.array_equal(image,turned)else _rows_for(image,.4,'x'))
    rotated=backend._recognize_rows(source)
    _fake_infer(backend,lambda image:_rows_for(image,.99,'OUTRA'))
    upright=backend._recognize_rows(source)
    assert {t.rotation for t in _result_to_pipeline_tokens(rotated,0,'pt-BR')}=={180}
    assert {t.rotation for t in _result_to_pipeline_tokens(upright,0,'pt-BR')}=={0}


@pytest.mark.parametrize('quarter_turns', [0, 1, 2, 3])
def test_parser_includes_rapidocr_implicit_tall_recognition_crop_turn(backend, quarter_turns):
    source = _source_image()
    turned = np.ascontiguousarray(np.rot90(source, quarter_turns))
    rows = [([[20, 20], [30, 20], [30, 100], [20, 100]], 'TEXTO VERTICAL', .99)]
    _fake_infer(backend, lambda image: rows if np.array_equal(image, turned) else _rows_for(image, .4, 'x'))
    tokens = backend.recognize_page(source, 0)
    # RapidOCR 3.9.2 rotates tall recognition crops CCW by 90 degrees in
    # addition to our optional whole-image orientation search.
    assert tokens[0].rotation == ((quarter_turns+1)*90) % 360
