"""GPU intent must not be confused with a runtime's effective execution device."""
from dataclasses import fields, replace
import sys
from types import SimpleNamespace

import pytest

from structured_pdf_text import ExtractorConfig
from structured_pdf_text.errors import ConfigurationError, ResourceExhaustedExtractionError
from structured_pdf_text.ocr.factory import resolve_config


@pytest.mark.parametrize("engine", ["easyocr", "paddle", "rapidocr", "tesseract"])
def test_gpu_preference_is_immutable_and_engine_local(engine):
    config = ExtractorConfig(ocr_engine=engine, use_gpu=True)
    settings = resolve_config(config).settings
    assert settings.use_gpu is True
    assert not resolve_config(replace(config, use_gpu=False)).settings.use_gpu
    assert settings.use_gpu is True


@pytest.mark.parametrize("invalid", [1, 0, "true", None])
def test_gpu_preference_requires_a_boolean(invalid):
    with pytest.raises(ConfigurationError, match="use_gpu"):
        ExtractorConfig(use_gpu=invalid)
    settings = resolve_config(ExtractorConfig()).settings
    with pytest.raises(ValueError, match="use_gpu"):
        replace(settings, use_gpu=invalid)


def test_gpu_opt_in_preserves_legacy_positional_configuration_arguments():
    legacy = ExtractorConfig(ocr_engine="paddle", num_threads=2)
    arguments = [getattr(legacy, item.name) for item in fields(legacy) if item.init and item.name != "use_gpu"]
    config = ExtractorConfig(*arguments, use_gpu=True)
    assert config.ocr_engine == "paddle" and config.num_threads == 2
    assert config.use_gpu


@pytest.mark.parametrize("requested,available,reader_device,expected", [
    (False, True, "cpu", "cpu"), (True, False, "cpu", "cpu"),
    (True, True, "cuda:0", "cuda"), (True, True, "cpu", "cpu"),
])
def test_easyocr_uses_reader_device_and_disables_cpu_quantization_on_cuda(
    monkeypatch, tmp_path, requested, available, reader_device, expected,
):
    from structured_pdf_text.ocr.backends import easyocr as backend
    captured = {}
    def available_cuda():
        assert requested, "CPU execution must not probe CUDA"
        return available
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=available_cuda)))
    class Reader:
        def __init__(self, languages, **kwargs):
            captured.update(kwargs)
            self.device = reader_device
    monkeypatch.setattr(backend, "_import_easyocr", lambda: SimpleNamespace(Reader=Reader))
    monkeypatch.setattr(backend, "_apply_torch_threads", lambda *args: (None, None))
    engine = backend.EasyOCRBackend(ExtractorConfig(use_gpu=requested, ocr_cache_home=tmp_path))
    assert captured["gpu"] is (requested and available)
    assert captured["download_enabled"] is False
    if requested and available:
        assert captured["quantize"] is False
    assert engine.identity.device == expected
    assert engine.identity.extra["gpu_requested"] is requested
    if requested and expected == "cpu":
        assert engine.identity.extra["gpu_fallback_reason"]
    engine.close()


@pytest.mark.parametrize("requested,compiled,count,expected", [
    (False, True, 1, "cpu"), (True, False, 0, "cpu"),
    (True, True, 0, "cpu"), (True, True, 1, "gpu:0"),
])
def test_paddle_explicit_cpu_and_cuda_model_options(monkeypatch, tmp_path, requested, compiled, count, expected):
    from structured_pdf_text.ocr import paddle as backend
    captured = {}
    def compiled_cuda():
        assert requested, "CPU execution must not auto-select a GPU from the GPU wheel"
        return compiled
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(is_compiled_with_cuda=compiled_cuda,
        device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: count))))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=lambda **kwargs: captured.update(kwargs) or object()))
    monkeypatch.setattr(backend, "_resolve_required_local_models", lambda *args, **kwargs: {})
    monkeypatch.setattr(backend, "_log_ocr_versions", lambda: None)
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    engine = backend.PaddleOcrEngine(use_gpu=requested, num_threads=-1)
    assert engine.effective_device == ("unknown" if requested else "cpu")
    engine._init_ocr()
    assert captured["device"] == engine.effective_device == expected
    if expected.startswith("gpu"):
        assert captured["enable_mkldnn"] is False
    if requested and expected == "cpu":
        assert engine.gpu_fallback_reason == "cuda_unavailable"


@pytest.fixture
def rapid_factory(tmp_path, monkeypatch):
    from structured_pdf_text.ocr.backends import rapidocr as backend
    from structured_pdf_text.ocr.pipelines.rapidocr import models
    for name in ("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL"):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        monkeypatch.setenv(name, str(path))
    monkeypatch.setattr(models, "verify_artifacts", lambda *args: dict.fromkeys(("detector", "recognizer", "dictionary", "classifier"), "a" * 64))
    monkeypatch.setattr(backend, "_rapidocr_config_types", lambda: (
        lambda value: value, lambda value: value, SimpleNamespace(PPOCRV6="v6", PPOCRV4="v4")))
    def build(*, requested=True, available=True, providers=(True, True), runtime="onnxruntime", preload_error=None, constructor_error=None):
        def preload(**kwargs):
            assert requested and runtime == "onnxruntime"
            if preload_error:
                raise preload_error
        monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(preload_dlls=preload,
            get_available_providers=lambda: ["CUDAExecutionProvider"] if available else ["CPUExecutionProvider"]))
        class Engine:
            __module__ = "rapidocr.fixture"
            def __init__(self, *, params):
                if params.get("EngineConfig.onnxruntime.use_cuda") and constructor_error:
                    raise constructor_error
                self.params = params
                for attribute, active in zip(("text_det", "text_rec"), providers):
                    active = active and params.get("EngineConfig.onnxruntime.use_cuda", False)
                    eps = ["CUDAExecutionProvider", "CPUExecutionProvider"] if active else ["CPUExecutionProvider"]
                    session = SimpleNamespace(providers=eps, fallback_enabled=True)
                    session.get_providers = lambda session=session: session.providers
                    session.disable_fallback = lambda session=session: setattr(session, "fallback_enabled", False)
                    setattr(self, attribute, SimpleNamespace(session=SimpleNamespace(session=session)))
        monkeypatch.setattr(backend, "_import_rapidocr", lambda *args: Engine)
        return backend.RapidOCRBackend(ExtractorConfig(ocr_engine="rapidocr", ocr_provider=runtime, use_gpu=requested), runtime)
    return build


@pytest.mark.parametrize("available,providers,device", [
    (True, (True, True), "cuda"), (True, (True, False), "cpu"),
    (True, (False, False), "cpu"), (False, (True, True), "cpu"),
])
def test_rapidocr_checks_active_detector_and_recognizer_sessions(rapid_factory, available, providers, device):
    engine = rapid_factory(available=available, providers=providers)
    assert engine.identity.device == device
    assert engine._engine.text_det.session.session.fallback_enabled is (device != "cuda")
    assert engine._engine.text_rec.session.session.fallback_enabled is (device != "cuda")
    if device == "cuda":
        assert engine._engine.params["EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search"] == "DEFAULT"
    else:
        assert engine.identity.extra["gpu_fallback_reason"]


def test_rapidocr_cpu_does_not_probe_cuda_and_openvino_never_changes_provider(rapid_factory):
    cpu = rapid_factory(requested=False)
    assert cpu.identity.device == "cpu"
    assert cpu._engine.text_det.session.session.fallback_enabled
    ov = rapid_factory(runtime="openvino")
    assert ov.identity.device == "cpu" and ov.identity.runtime == "openvino"
    assert ov.identity.extra["gpu_fallback_reason"] == "openvino_profile_cpu_only"


def test_rapidocr_identity_tracks_live_provider_changes_and_retains_last_device_after_close(rapid_factory):
    engine = rapid_factory()
    assert engine.identity.device == "cuda"
    detector = engine._engine.text_det.session.session
    recognizer = engine._engine.text_rec.session.session
    detector.providers = ["CPUExecutionProvider"]
    assert engine.identity.device == "mixed"
    recognizer.providers = ["CPUExecutionProvider"]
    assert engine.identity.device == "cpu"
    assert engine.identity.extra["gpu_fallback_reason"] == "cuda_provider_not_active_in_all_stages"
    detector.providers = recognizer.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert engine.identity.device == "cuda"
    assert engine.identity.extra["gpu_fallback_reason"] is None
    engine.close()
    assert engine.identity.device == "cuda"


def test_rapidocr_missing_cuda_libraries_is_reported_as_cpu_fallback(rapid_factory):
    engine = rapid_factory(preload_error=ImportError("missing CUDA shared library"))
    assert engine.identity.device == "cpu"
    assert engine.identity.extra["gpu_fallback_reason"] == "cuda_probe_failed:ImportError"


def test_device_probe_oom_remains_fatal(rapid_factory):
    with pytest.raises(ResourceExhaustedExtractionError):
        rapid_factory(preload_error=RuntimeError("CUDA out of memory"))


@pytest.mark.parametrize("message,expected", [
    ("libcudnn.so not found", True), ("CUDA driver initialization failed", True),
    ("CUDA error: no kernel image is available", True),
    ("Invalid model protobuf", False), ("Model weights not found", False),
])
def test_gpu_configuration_classifier_preserves_model_errors(message, expected):
    from structured_pdf_text.ocr.device import gpu_configuration_failure
    assert gpu_configuration_failure(RuntimeError(message)) is expected


def test_gpu_configuration_classifier_walks_causes_and_preserves_fatal_errors():
    from structured_pdf_text.errors import ExtractionCancelled
    from structured_pdf_text.ocr.device import gpu_configuration_failure
    outer = RuntimeError("Initialization failed")
    outer.__cause__ = ImportError("libcublas.so not found")
    assert gpu_configuration_failure(outer)
    outer.__cause__ = RuntimeError("CUDA out of memory")
    with pytest.raises(ResourceExhaustedExtractionError):
        gpu_configuration_failure(outer)
    assert not gpu_configuration_failure(ExtractionCancelled("CUDA cancelled"))


def test_rapidocr_gpu_constructor_failure_retries_same_medium_model_on_cpu(rapid_factory, caplog):
    engine = rapid_factory(constructor_error=RuntimeError("libcudnn.so not found"))
    assert engine.identity.device == "cpu"
    assert engine.identity.profile == "pp-ocrv6-medium"
    assert "cuda_initialization_failed" in engine.identity.extra["gpu_fallback_reason"]
    assert "using CPU" in caplog.text


def test_rapidocr_gpu_library_failure_during_inference_retries_cpu_once(rapid_factory, monkeypatch, caplog):
    from structured_pdf_text.ocr.backends import rapidocr as backend
    engine = rapid_factory()
    calls = []
    def infer(runtime, image):
        cuda = runtime.params["EngineConfig.onnxruntime.use_cuda"]
        calls.append(cuda)
        if cuda:
            raise RuntimeError("CUDNN_STATUS_NOT_INITIALIZED error")
        return ["CPU result"]
    monkeypatch.setattr(backend, "_run_rapidocr", infer)
    assert engine._infer(object()) == ["CPU result"]
    assert engine.identity.device == "cpu"
    assert engine.identity.extra["gpu_requested"]
    assert "cuda_runtime_failed" in engine.identity.extra["gpu_fallback_reason"]
    assert calls == [True, False]
    assert "retrying on CPU" in caplog.text


def test_rapidocr_gpu_model_errors_and_oom_do_not_trigger_cpu_retry(rapid_factory, monkeypatch):
    from structured_pdf_text.ocr.backends import rapidocr as backend
    engine = rapid_factory()
    def fail(*args):
        raise RuntimeError("Invalid model protobuf")
    monkeypatch.setattr(backend, "_run_rapidocr", fail)
    with pytest.raises(RuntimeError, match="protobuf"):
        engine._infer(object())
    assert engine.identity.device == "cuda"
    def oom(*args):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(backend, "_run_rapidocr", oom)
    with pytest.raises(ResourceExhaustedExtractionError):
        engine._infer(object())


def test_cpu_retry_is_bounded_even_if_cpu_also_has_missing_dependencies():
    from structured_pdf_text.ocr.device import retry_gpu_configuration_on_cpu
    class Engine:
        calls = 0
        def _fallback_to_cpu(self, exc):
            return True
        @retry_gpu_configuration_on_cpu
        def run(self):
            self.calls += 1
            raise ImportError("libcudnn.so not found")
    engine = Engine()
    with pytest.raises(ImportError):
        engine.run()
    assert engine.calls == 2


def test_easyocr_constructor_and_lazy_inference_library_failures_use_cpu(monkeypatch, tmp_path, caplog):
    from structured_pdf_text.ocr.backends import easyocr as backend
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)))
    monkeypatch.setattr(backend, "_apply_torch_threads", lambda *args: (None, None))
    options = []
    fail_constructor = [True]
    class Reader:
        def __init__(self, languages, **kwargs):
            options.append(kwargs)
            if kwargs["gpu"] and fail_constructor[0]:
                raise RuntimeError("CUDA driver initialization failed")
            self.device = "cuda:0" if kwargs["gpu"] else "cpu"
    monkeypatch.setattr(backend, "_import_easyocr", lambda: SimpleNamespace(Reader=Reader))
    config = ExtractorConfig(use_gpu=True, ocr_cache_home=tmp_path)
    engine = backend.EasyOCRBackend(config)
    assert engine.identity.device == "cpu" and options[-1]["gpu"] is False
    assert "cuda_initialization_failed" in engine.identity.extra["gpu_fallback_reason"]
    fail_constructor[0] = False
    engine = backend.EasyOCRBackend(config)
    assert engine.identity.device == "cuda"
    def run(reader, image, **kwargs):
        if reader.device.startswith("cuda"):
            raise RuntimeError("libcudnn.so not found")
        return [], None
    monkeypatch.setattr(backend, "_run_easyocr", run)
    import numpy as np
    assert engine.recognize_page(np.zeros((64, 128, 3), dtype=np.uint8), 0) == []
    assert engine.identity.device == "cpu"
    assert engine.identity.extra["gpu_requested"]
    assert engine._reader._pdfextractor_init_options["gpu"] is False
    assert "retrying on CPU" in caplog.text


def test_paddle_constructor_and_lazy_inference_library_failures_use_cpu(monkeypatch, tmp_path, caplog):
    from structured_pdf_text.ocr import paddle as backend
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(is_compiled_with_cuda=lambda: True,
        device=SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1))))
    options = []
    def construct(**kwargs):
        options.append(kwargs)
        if kwargs["device"].startswith("gpu"):
            raise RuntimeError("libcudnn.so not found")
        return object()
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=construct))
    monkeypatch.setattr(backend, "_resolve_required_local_models", lambda *args, **kwargs: {})
    monkeypatch.setattr(backend, "_log_ocr_versions", lambda: None)
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    engine = backend.PaddleOcrEngine(use_gpu=True, num_threads=-1)
    engine._init_ocr()
    assert [option["device"] for option in options] == ["gpu:0", "cpu"]
    assert options[-1]["text_detection_model_name"] == "PP-OCRv6_medium_det"
    assert options[-1]["text_recognition_model_name"] == "PP-OCRv6_medium_rec"
    assert engine.effective_device == "cpu"
    engine.effective_device = "gpu:0"
    engine.gpu_fallback_reason = None
    def recognize(*args, **kwargs):
        if engine.effective_device.startswith("gpu"):
            raise RuntimeError("libcublas.so not found")
        engine._init_ocr()
        return ["CPU result"]
    monkeypatch.setattr(engine, "_recognize_page_impl", recognize)
    assert engine.recognize_page(object(), 0) == ["CPU result"]
    assert engine.use_gpu and engine.effective_device == "cpu"
    assert "cuda_runtime_failed" in engine.gpu_fallback_reason
    assert "retrying on CPU" in caplog.text


@pytest.mark.parametrize("engine,provider", [("paddle", None), ("rapidocr", "onnxruntime"), ("rapidocr", "openvino")])
def test_all_paddle_v6_providers_default_to_medium(engine, provider):
    from structured_pdf_text.ocr.models import get_profile
    settings = resolve_config(ExtractorConfig(ocr_engine=engine, ocr_provider=provider)).settings
    if engine == "paddle":
        profile = get_profile(settings.model_profile)
        assert profile.detection == "PP-OCRv6_medium_det"
        assert profile.recognition == "PP-OCRv6_medium_rec"
    else:
        assert settings.model_size == "medium"


def test_cpu_compatibility_normalization_keeps_gpu_significant():
    from scripts.compare_ocr_baseline import _normalize_config_snapshot
    legacy = {"mode": "balanced"}
    assert _normalize_config_snapshot({**legacy, "use_gpu": False}) == legacy
    assert _normalize_config_snapshot({**legacy, "use_gpu": True}) != legacy


@pytest.mark.parametrize("engine", ["easyocr", "paddle", "rapidocr", "tesseract"])
def test_supplied_settings_control_device_at_factory_boundary(engine, monkeypatch):
    from structured_pdf_text.ocr import factory
    captured = []
    settings = resolve_config(ExtractorConfig(ocr_engine=engine, use_gpu=True)).settings
    monkeypatch.setattr(factory, "import_object", lambda path: lambda config: captured.append(config))
    factory.build_ocr_backend(ExtractorConfig(ocr_engine=engine, use_gpu=False, ocr_settings=settings))
    assert captured[0].use_gpu and captured[0].ocr_settings is settings


@pytest.mark.parametrize("arguments", [
    ["extract", "unused.pdf"], ["extract", "unused.pdf", "--best"],
    ["extract", "unused.pdf", "--max-quality"], ["inspect", "unused.pdf"],
    ["overlay", "unused.pdf", "--page", "1", "--out", "unused.svg"],
    ["report", "unused.pdf"], ["compare", "unused.pdf"],
])
@pytest.mark.parametrize("gpu", [False, True])
def test_cli_propagates_gpu_through_all_config_and_preset_branches(monkeypatch, arguments, gpu):
    from structured_pdf_text import cli
    class Captured(Exception):
        pass
    def capture(config, *args, **kwargs):
        assert config.use_gpu is gpu
        raise Captured
    monkeypatch.setattr(cli, "PdfTextExtractor", capture)
    monkeypatch.setattr(cli, "corpus_report", lambda paths, config, **kwargs: capture(config))
    monkeypatch.setattr(cli, "compare_extractors", lambda path, adapters, **kwargs: capture(kwargs["config"]))
    with pytest.raises(Captured):
        cli._main_impl([*arguments, *(["--gpu"] if gpu else [])])


def test_cuda_installer_keeps_runtime_namespaces_and_cpu_profiles_separate(tmp_path):
    from scripts import setup_ocr_environments as setup
    plans = setup.build_plan(list(setup.EXTRAS), tmp_path, windows=False, cache_home=tmp_path, gpu=True)
    for plan in plans:
        profile = plan["configuration"]
        cuda = profile in {"easyocr", "paddle", "rapidocr-onnxruntime"}
        assert plan["cuda_requested"] is cuda
        assert ("gpu-linux" in plan["constraints"]) is cuda
        install = next(command for command in plan["commands"] if "package-install.json" in " ".join(command))
        if profile in {"paddle", "rapidocr-onnxruntime"}:
            assert str(setup.ROOT) in install
            assert not any(arg.startswith(str(setup.ROOT) + "[") for arg in install)
        if profile == "paddle":
            assert "paddlepaddle-gpu==3.3.1" in plan["commands"][1]
            assert "https://www.paddlepaddle.org.cn/packages/stable/cu126/" in plan["commands"][1]
        if profile == "rapidocr-onnxruntime":
            assert "onnxruntime-gpu[cuda,cudnn]==1.30.0" in install
        if profile == "easyocr":
            assert "torch==2.14.0+cu130" in plan["commands"][1]
            assert "https://download.pytorch.org/whl/cu130" in plan["commands"][1]
        assert ("--gpu" in plan["commands"][-1]) is cuda


@pytest.mark.parametrize("profile,names,conflict", [
    ("paddle", ["opencv-contrib-python", "paddlepaddle", "paddlepaddle-gpu"], True),
    ("rapidocr-onnxruntime", ["opencv-python", "onnxruntime", "onnxruntime-gpu"], True),
    ("paddle", ["opencv-contrib-python", "paddlepaddle"], False),
    ("rapidocr-onnxruntime", ["opencv-python", "onnxruntime"], False),
])
def test_gpu_install_checker_rejects_cpu_or_colliding_wheels_before_import(monkeypatch, profile, names, conflict):
    from scripts import check_ocr_install as checker
    monkeypatch.setattr(checker.metadata, "distributions", lambda: [
        SimpleNamespace(metadata={"Name": name}, version="test") for name in names])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(checker.subprocess, "run", run)
    report = checker.inspect_install(profile, imports=True, gpu=True)
    assert not report["passed"] and not report["runtime_distributions_valid"]
    assert bool(report["runtime_namespace_conflicts"]) is conflict
    assert len(calls) == 1 and not report["import_probes"]


def test_gpu_smoke_rejects_available_provider_without_cuda_node_execution():
    from scripts.smoke_ocr_gpu import _accepted
    identity = {"device": "cuda", "extra": {}}
    stages = {stage: {"providers": ["CUDAExecutionProvider"],
                      "node_events": {"CPUExecutionProvider": 100}}
              for stage in ("detector", "recognizer")}
    assert not _accepted("rapidocr", identity, {"stages": stages})
    stages["detector"]["node_events"]["CUDAExecutionProvider"] = 10
    assert not _accepted("rapidocr", identity, {"stages": stages})
    stages["recognizer"]["node_events"]["CUDAExecutionProvider"] = 10
    assert _accepted("rapidocr", identity, {"stages": stages})
    assert not _accepted("rapidocr", {"device": "cpu"}, {"stages": stages})


@pytest.mark.parametrize("engine,evidence", [
    ("paddle", {"predictors": []}),
    ("paddle", {"predictors": [{"created": True, "use_gpu": False}]}),
    ("easyocr", {"model_parameter_devices": {"detector": ["cuda:0"], "recognizer": ["cpu"]}}),
])
def test_gpu_smoke_cannot_accept_request_or_partial_gpu_placement(engine, evidence):
    from scripts.smoke_ocr_gpu import _accepted
    assert not _accepted(engine, {"device": "cuda", "extra": {}}, evidence)


def test_gpu_smoke_preserves_report_namespace_before_loading_runtime(tmp_path):
    from scripts.smoke_ocr_gpu import run
    path = tmp_path / "existing"
    path.mkdir()
    report = path / "report.json"
    report.write_text("original")
    with pytest.raises(FileExistsError):
        run("easyocr", path, tmp_path, tmp_path / "font.ttf")
    assert report.read_text() == "original"


@pytest.mark.parametrize("engine", ["easyocr", "paddle", "rapidocr"])
@pytest.mark.parametrize("ocr_tokens", [0, 3])
def test_gpu_smoke_requires_ocr_tokens_and_profiles_before_session_construction(monkeypatch, tmp_path, engine, ocr_tokens):
    from scripts import smoke_ocr_gpu as smoke
    events = []
    identity = SimpleNamespace(engine=engine, runtime="fixture", device="cuda", profile="fixture",
                               package_versions={}, artifact_hashes={}, extra={})
    pipeline = SimpleNamespace(raw_engine=SimpleNamespace(identity=identity), close=lambda: None)
    def instrument(selected, directory, stack):
        events.append("instrument")
        return []
    def compose(config):
        assert config.use_gpu
        if engine == "rapidocr":
            assert events == ["instrument"], "ORT profiling must precede session construction"
        events.append("compose")
        return pipeline
    document = SimpleNamespace(diagnostics=SimpleNamespace(status=SimpleNamespace(value="success")),
                               pages=[SimpleNamespace(diagnostics=SimpleNamespace(ocr_tokens_added=ocr_tokens))])
    class Extractor:
        def __init__(self, config, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract(self, path):
            assert "instrument" in events
            return document
    def fixture(directory, font):
        path = directory / "synthetic.pdf"
        path.write_bytes(b"synthetic")
        return path
    monkeypatch.setattr(smoke, "_instrument", instrument)
    monkeypatch.setattr(smoke, "build_ocr_pipeline", compose)
    monkeypatch.setattr(smoke, "PdfTextExtractor", Extractor)
    monkeypatch.setattr(smoke, "_fixture", fixture)
    monkeypatch.setattr(smoke, "render_markdown", lambda doc: (
        "AÇÃO: São Paulo 12345 R$ 42,90 AB-009" if ocr_tokens else "# synthetic\n\n## Página 1\n"))
    monkeypatch.setattr(smoke, "_evidence", lambda *args: {})
    monkeypatch.setattr(smoke, "_accepted", lambda *args: True)
    report = smoke.run(engine, tmp_path / "new", tmp_path, tmp_path / "font.ttf")
    assert report["gpu_execution_accepted"] is bool(ocr_tokens)
    assert report["ocr_tokens_added"] == ocr_tokens
    assert report["synthetic_content_accepted"] is bool(ocr_tokens)
    assert not report["corpus_executed"] and not report["quality_accepted"]
