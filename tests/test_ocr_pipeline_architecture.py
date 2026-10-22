import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_ocr_dependencies import PUBLIC_ENGINES, check_tree, regression_engines, violations

ROOT = Path(__file__).resolve().parents[1]
_FAKE_EXTENSION_CALLS = []


def _build_fifth_backend(config):
    class FifthBackend:
        identity = None
        capabilities = {"recognition": True}
        def close(self): _FAKE_EXTENSION_CALLS.append("closed")
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs):
            from structured_pdf_text.document import OcrToken, SourceKind
            from structured_pdf_text.geometry import BBox
            _FAKE_EXTENSION_CALLS.append((config.ocr_engine, page_index))
            box = page_bbox or BBox(0, 0, image.width, image.height)
            return [OcrToken(
                "fifth engine authorized", BBox(
                    box.x0 + box.width*.1, box.y0 + box.height*.1,
                    box.x0 + box.width*.9, box.y0 + box.height*.9,
                ), .9, "pt-BR", SourceKind.OCR_PAGE,
            )]
    return FifthBackend()


def test_real_ocr_dependency_graph():
    assert check_tree(ROOT/"src") == []


@pytest.mark.parametrize("source", [
    "from structured_pdf_text.ocr.backends.paddle import PaddleOCRBackend",
    "from ...backends import paddle",
    "import importlib as loader\nloader.import_module('structured_pdf_text.ocr.pipelines.paddle.policy')",
    "from importlib import import_module as load\nload('structured_pdf_text.ocr.backends.paddle')",
    "__import__('structured_pdf_text.ocr.backends.paddle')",
    "from builtins import __import__ as load\nload('structured_pdf_text.ocr.backends.paddle')",
    "import importlib\nimportlib.import_module(name='structured_pdf_text.ocr.backends.paddle')",
    "import importlib\nimportlib.import_module('.paddle', package='structured_pdf_text.ocr.backends')",
    "import builtins as b\nb.__import__('paddleocr')",
    "import paddleocr",
])
def test_gate_rejects_artificial_cross_engine_dependencies(source):
    assert violations(source, "structured_pdf_text.ocr.pipelines.easyocr.pipeline")


def test_gate_supports_a_fifth_engine_and_neutral_rules():
    assert violations("from structured_pdf_text.ocr.backends.easyocr import X", "structured_pdf_text.ocr.pipelines.fifth.pipeline")
    assert not violations("from structured_pdf_text.ocr.backends.fifth import X", "structured_pdf_text.ocr.pipelines.fifth.pipeline")
    assert not violations("import onnxruntime", "structured_pdf_text.ocr.backends.fifth")
    assert violations("import torch", "structured_pdf_text.ocr.common.geometry")
    assert not violations("from structured_pdf_text.ocr.backends.easyocr import X", "structured_pdf_text.ocr.factory")


@pytest.mark.parametrize("module", [
    "structured_pdf_text.api", "structured_pdf_text.native.pdfium_source",
    "structured_pdf_text.tables.visual", "structured_pdf_text.fusion.integrator",
    "structured_pdf_text.renderers.markdown", "structured_pdf_text.diagnostics.report",
])
def test_gate_covers_core_consumers_outside_ocr_directory(module):
    assert violations("import torch", module)
    assert violations("from structured_pdf_text.ocr.backends.easyocr import X", module)
    assert not violations("from structured_pdf_text.ocr.pipeline.contracts import OCRPipelineResult", module)


def test_shared_quality_module_has_no_paddle_result_parser():
    from pathlib import Path
    source = Path("src/structured_pdf_text/ocr/quality.py").read_text(encoding="utf-8")
    assert "PaddleRawMetrics" not in source
    assert "raw_result_metrics" not in source


def test_each_builtin_pipeline_owns_a_local_recovery_profile():
    from structured_pdf_text.ocr.pipelines.easyocr.pipeline import EasyOCRPipeline
    from structured_pdf_text.ocr.pipelines.paddle.pipeline import PaddlePipeline
    from structured_pdf_text.ocr.pipelines.rapidocr.pipeline import RapidOCRPipeline
    from structured_pdf_text.ocr.pipelines.tesseract.pipeline import TesseractPipeline

    pipelines = (EasyOCRPipeline, PaddlePipeline, RapidOCRPipeline, TesseractPipeline)
    profiles = tuple(pipeline.recovery_policy for pipeline in pipelines)
    assert len({id(profile) for profile in profiles}) == 4
    assert len({id(profile.candidates) for profile in profiles}) == 4
    assert len({id(pipeline.cell_policy) for pipeline in pipelines}) == 4
    assert len({id(pipeline.integration_policy) for pipeline in pipelines}) == 4
    assert len({id(pipeline.token_refinement_policy) for pipeline in pipelines}) == 4
    assert len({id(pipeline.fusion_policy) for pipeline in pipelines}) == 4
    assert len({id(pipeline.figure_segmentation_policy) for pipeline in pipelines}) == 4
    assert len({id(pipeline.raster_boundary_policy) for pipeline in pipelines}) == 4
    assert tuple(pipeline.raster_boundary_policy.mode for pipeline in pipelines) == ("mask", "mask", "trim", "mask")
    assert all(profile.scale_factors("region", quality_policy="baseline") == (1.0,) for profile in profiles)
    assert all(profile.scale_factors("critical_data") == (1.5, 2.0, 3.0) for profile in profiles)


def test_page_candidate_fusion_calibration_and_component_provenance_stay_local():
    from dataclasses import replace
    from structured_pdf_text.document import OcrToken, SourceKind
    from structured_pdf_text.geometry import BBox
    from structured_pdf_text.ocr.candidate_fusion import OcrCandidateFusionEngine, OcrCandidateResult
    from structured_pdf_text.ocr.pipelines.easyocr.policy import FUSION_POLICY as easy
    from structured_pdf_text.ocr.pipelines.paddle.policy import FUSION_POLICY as paddle
    from structured_pdf_text.ocr.pipelines.rapidocr.policy import FUSION_POLICY as rapid
    from structured_pdf_text.ocr.pipelines.tesseract.policy import FUSION_POLICY as tesseract

    primary = OcrToken("primary evidence", BBox(0, 0, 100, 20), .9, "pt-BR", SourceKind.OCR_PAGE)
    secondary = OcrToken("secondary evidence", BBox(0, 50, 100, 70), .9, "pt-BR", SourceKind.OCR_PAGE)
    candidates = [OcrCandidateResult("primary", "full-page", (primary,), .9, "fixture"),
                  OcrCandidateResult("secondary", "dbnet", (secondary,), .85, "fixture")]
    other = [OcrCandidateFusionEngine(policy).fuse(candidates) for policy in (paddle, rapid, tesseract)]
    baseline = OcrCandidateFusionEngine(easy).fuse(candidates)
    changed = OcrCandidateFusionEngine(replace(easy, novel_minimum_score=.9)).fuse(candidates)
    assert len(baseline.tokens) == 2 and len(changed.tokens) == 1
    assert baseline.tokens[1].ocr_provenance.detector == "dbnet"
    for policy, expected in zip((paddle, rapid, tesseract), other):
        assert OcrCandidateFusionEngine(policy).fuse(candidates) == expected
        assert len(expected.tokens) == 2 and expected.tokens[1].ocr_provenance.detector is None
    mechanism = (ROOT / "src/structured_pdf_text/ocr/candidate_fusion.py").read_text()
    assert "craft_greedy" not in mechanism and "beamsearch" not in mechanism


def test_builtin_settings_resolvers_live_with_the_selected_engine():
    from structured_pdf_text.ocr.registry import ENGINE_REGISTRY
    from structured_pdf_text.ocr.factory import resolve_config
    from structured_pdf_text import ExtractorConfig

    for engine in PUBLIC_ENGINES:
        entry = ENGINE_REGISTRY.resolve(engine)[0]
        assert entry.settings_resolver == (
            f"structured_pdf_text.ocr.pipelines.{engine}.settings:resolve_settings"
        )
        resolved = resolve_config(ExtractorConfig(ocr_engine=engine))
        assert resolved.settings.selection.engine == engine
        assert type(resolved.settings).__module__ == "structured_pdf_text.ocr.pipeline.settings"

    # Tesseract parses its executable settings in its own module, while RapidOCR
    # resolves model files in its model-owning package.
    from structured_pdf_text.ocr.pipelines.tesseract.settings import resolve_settings as tess_settings
    from structured_pdf_text.ocr.pipelines.rapidocr.settings import resolve_settings as rapid_settings
    assert tess_settings.__module__.endswith("pipelines.tesseract.settings")
    assert rapid_settings.__module__.endswith("pipelines.rapidocr.settings")


def test_environment_snapshots_are_owned_by_the_selected_engine(monkeypatch):
    from structured_pdf_text import ExtractorConfig
    from structured_pdf_text.ocr.factory import resolve_config

    for name, value in {
        "EASYOCR_WORKERS": "0",
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
        "RAPIDOCR_BOX_THRESH": "0.41",
        "TESSERACT_PSM": "6",
    }.items():
        monkeypatch.setenv(name, value)

    snapshots = {
        engine: dict(resolve_config(ExtractorConfig(ocr_engine=engine)).settings.environment)
        for engine in PUBLIC_ENGINES
    }
    assert set(snapshots["easyocr"]) == {"EASYOCR_WORKERS"}
    assert set(snapshots["paddle"]) == {"PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"}
    assert set(snapshots["rapidocr"]) == {"RAPIDOCR_BOX_THRESH"}
    assert set(snapshots["tesseract"]) == {"TESSERACT_PSM"}


def test_injected_builtin_backend_receives_its_local_settings_without_loading_runtime(monkeypatch):
    from types import SimpleNamespace
    from structured_pdf_text import ExtractorConfig
    from structured_pdf_text.ocr.contracts import OCRBackendIdentity
    from structured_pdf_text.ocr.pipeline.adapters import adapt_injection
    from structured_pdf_text.ocr.pipeline.settings import EasyOCRSettings

    monkeypatch.setenv("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR", "41.0")
    engine = SimpleNamespace(identity=OCRBackendIdentity(
        engine="easyocr", runtime="torch-cpu", profile="test",
        language="pt-BR", device="cpu",
    ))
    pipeline = adapt_injection(ocr_engine=engine, config=ExtractorConfig())

    assert isinstance(pipeline.settings, EasyOCRSettings)
    assert pipeline.settings.selection.engine == "easyocr"
    assert pipeline.settings.rgb_budget_mib == 41.0
    assert pipeline.render_scale == pipeline.settings.render_scale


def test_registered_fifth_engine_flows_through_cli_authorization_and_integration(tmp_path):
    from structured_pdf_text import ExtractorConfig
    from structured_pdf_text.ocr.registry import BackendEntry, public_engines, registered_for_test
    from structured_pdf_text.ocr.factory import build_ocr_pipeline
    from structured_pdf_text.ocr.pipeline.contracts import EngineSelection

    entry = BackendEntry(
        name="fifthtest", display_name="Test extension", default_runtime="fake",
        default_profile="test", extras=None, setup_hint="test only",
        backend_factory=f"{__name__}:_build_fifth_backend",
        settings_resolver="structured_pdf_text.ocr.pipeline.settings:resolve_generic_settings",
        pipeline_factory="structured_pdf_text.ocr.factory:build_legacy_ocr_pipeline",
        capabilities=("page",),
    )
    _FAKE_EXTENSION_CALLS.clear()
    with registered_for_test(entry):
        assert "fifthtest" in public_engines()
        pipeline = build_ocr_pipeline(ExtractorConfig(mode="ocr", ocr_engine="fifthtest"))
        assert pipeline.descriptor.selection == EngineSelection("fifthtest")
        assert pipeline.raw_engine is not None
        assert pipeline.settings.selection == EngineSelection("fifthtest")
        assert not hasattr(pipeline.settings, "cache_home")
        pipeline.close()

        from reportlab.pdfgen import canvas
        from structured_pdf_text.cli import _main_impl
        pdf = tmp_path/"fifth-engine.pdf"
        output = tmp_path/"fifth-engine.txt"
        writer = canvas.Canvas(str(pdf), pagesize=(200, 100))
        writer.drawString(20, 50, "native evidence")
        writer.save()
        with pytest.raises(SystemExit) as help_exit:
            _main_impl(["extract", "unused.pdf", "--ocr-engine", "fifthtest", "--help"])
        assert help_exit.value.code == 0
        assert _main_impl([
            "extract", str(pdf), "--mode", "ocr", "--ocr-engine", "fifthtest",
            "--ocr-quality-policy", "baseline", "--output", "reading", "--output-file", str(output),
        ]) == 0
        assert "fifth engine authorized" in output.read_text(encoding="utf-8")
    assert "fifthtest" not in public_engines()
    assert "closed" in _FAKE_EXTENSION_CALLS
    assert ("fifthtest", 0) in _FAKE_EXTENSION_CALLS


@pytest.mark.parametrize("path", ["src/structured_pdf_text/ocr/pipeline/services.py", "src/structured_pdf_text/api.py", "src/structured_pdf_text/ocr/registry.py", "src/structured_pdf_text/tables/visual.py", "pyproject.toml", ".github/workflows/ci.yml"])
def test_global_change_requires_all_engines(path):
    assert regression_engines([path]) == PUBLIC_ENGINES


def test_local_change_still_requires_core_controls_and_selected_engine():
    assert regression_engines(["src/structured_pdf_text/ocr/backends/easyocr.py"]) == ("easyocr",)


@pytest.mark.parametrize("module", ["structured_pdf_text", "structured_pdf_text.config", "structured_pdf_text.ocr.contracts", "structured_pdf_text.ocr.pipeline.contracts", "structured_pdf_text.ocr.backends", "structured_pdf_text.ocr.registry", "structured_pdf_text.ocr.factory", "structured_pdf_text.cli"])
def test_imports_in_fresh_process_load_no_ocr_framework(module):
    script = f"""
import importlib, sys
importlib.import_module({module!r})
forbidden = {{'torch','easyocr','paddle','paddleocr','paddlex','rapidocr','onnxruntime','openvino','pytesseract'}}
assert not forbidden.intersection(name.split('.')[0] for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/"src")), check=True, capture_output=True, text=True)


def test_new_contract_import_does_not_load_image_or_native_libraries():
    script = "import sys; import structured_pdf_text.ocr.pipeline.contracts; assert not {'PIL','numpy','pypdfium2'}.intersection(n.split('.')[0] for n in sys.modules)"
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/"src")), check=True, capture_output=True, text=True)


def test_static_easyocr_probe_does_not_attempt_runtime_import():
    script = """
import importlib.abc, importlib.util, sys
from unittest.mock import patch
from structured_pdf_text.config import ExtractorConfig
from structured_pdf_text.ocr.readiness import probe_static
attempts=[]
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'easyocr','torch'}:
   attempts.append(fullname)
   raise ImportError('runtime import forbidden')
sys.meta_path.insert(0,Guard())
with patch('importlib.util.find_spec', return_value=object()):
 probe_static(ExtractorConfig(ocr_engine='easyocr'))
assert not attempts, attempts
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/"src")), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("engine", PUBLIC_ENGINES)
@pytest.mark.parametrize("mode", ["native", "fast", "balanced"])
def test_native_page_never_constructs_ocr(engine, mode, monkeypatch, tmp_path):
    from reportlab.pdfgen import canvas
    from structured_pdf_text import ExtractorConfig, PdfTextExtractor
    import structured_pdf_text.ocr.factory as factory
    path = tmp_path/"native.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(500, 600))
    for i in range(25):
        pdf.drawString(30, 550-i*16, "Documento nativo com texto completo e valores 1234.")
    pdf.save()
    def forbidden(*args, **kwargs):
        pytest.fail("An OCR runtime was constructed for a native page")
    monkeypatch.setattr(factory, "build_ocr_backend", forbidden)
    if mode in ("native", "fast"):
        from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource
        monkeypatch.setattr(PdfiumNativeEvidenceSource, "render_page", forbidden)
        monkeypatch.setattr(PdfiumNativeEvidenceSource, "render_region", forbidden)
    with PdfTextExtractor(ExtractorConfig(mode=mode, ocr_engine=engine)) as extractor:
        doc = extractor.extract(path)
    assert doc.pages


@pytest.mark.parametrize("engine", PUBLIC_ENGINES)
def test_lazy_factory_constructs_only_selected_runtime_once(engine, monkeypatch):
    from structured_pdf_text import ExtractorConfig, PdfTextExtractor
    import structured_pdf_text.ocr.factory as factory
    calls = []
    class Fake:
        closed = 0
        def close(self): self.closed += 1
        def recognize_page(self, image, page_index, page_bbox=None, **kwargs): return []
    fake = Fake()
    def build(config):
        calls.append(config.ocr_engine)
        return fake
    monkeypatch.setattr(factory, "build_ocr_backend", build)
    extractor = PdfTextExtractor(ExtractorConfig(mode="ocr", ocr_engine=engine))
    assert calls == []
    extractor._ensure_ocr_engine()
    extractor._ensure_ocr_engine()
    extractor.close()
    extractor.close()
    assert calls == [engine]
    assert fake.closed == 1


@pytest.mark.parametrize("engine", PUBLIC_ENGINES)
def test_real_factory_imports_and_constructs_only_selected_engine(engine, monkeypatch):
    from types import ModuleType
    from structured_pdf_text import ExtractorConfig
    from structured_pdf_text.ocr.factory import build_ocr_backend
    calls = []
    for name in PUBLIC_ENGINES:
        module = ModuleType(f"structured_pdf_text.ocr.backends.{name}")
        def construct(config, _name=name, **kwargs):
            calls.append((_name,config,kwargs))
            return object()
        setattr(module,"build_backend",construct)
        monkeypatch.setitem(sys.modules,module.__name__,module)
    config = ExtractorConfig(mode="ocr",ocr_engine=engine)
    build_ocr_backend(config)
    assert len(calls) == 1
    assert calls[0][0] == engine
    assert calls[0][1].ocr_engine == engine


def test_public_lazy_exports_keep_identity_and_discoverability():
    import structured_pdf_text as package
    import structured_pdf_text.ocr as ocr
    from structured_pdf_text.api import PdfTextExtractor
    from structured_pdf_text.ocr.paddle import PaddleOcrEngine
    assert package.PdfTextExtractor is PdfTextExtractor
    assert ocr.PaddleOcrEngine is PaddleOcrEngine
    assert set(package.__all__) <= set(dir(package))
    assert set(ocr.__all__) <= set(dir(ocr))
    with pytest.raises(AttributeError):
        package.nonexistent_public_symbol
