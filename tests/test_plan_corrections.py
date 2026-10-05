from __future__ import annotations

import ctypes
import json
from pathlib import Path
from types import SimpleNamespace

from reportlab.pdfgen import canvas

import structured_pdf_text.cli as cli_module
import structured_pdf_text.diagnostics.compare as compare_module
import structured_pdf_text.memory as memory_module
from structured_pdf_text.api import PdfTextExtractor, _safe_complexity_scale
from structured_pdf_text.assemble.document import assemble_document
from structured_pdf_text.config import ExtractorConfig, SecurityLimits, effective_ocr_quality_thresholds
from structured_pdf_text.diagnostics.compare import ComparisonExtraction, compare_extractors
from structured_pdf_text.ocr.models import PROFILES, get_profile


def _pdf(path: Path) -> Path:
    pdf = canvas.Canvas(str(path), pagesize=(240, 240))
    pdf.drawString(20, 200, "Texto de teste")
    pdf.save()
    return path


def test_v6_medium_is_an_alias_of_default_v6_profile() -> None:
    assert PROFILES["pt-v6-medium"] is PROFILES["pt"]
    assert get_profile("pt").detection == "PP-OCRv6_medium_det"
    assert get_profile("pt-v6-medium").recognition == "PP-OCRv6_medium_rec"
    assert get_profile("pt-v5").detection == "PP-OCRv5_server_det"


def test_render_scale_preserves_requested_size_inside_and_at_limit() -> None:
    assert _safe_complexity_scale(9, 10, 99, 1.0) == 1.0
    assert _safe_complexity_scale(10, 10, 100, 1.0) == 1.0
    assert _safe_complexity_scale(10, 10, 100, 0.5) == 0.5


def test_render_scale_reduces_only_above_pixel_limit() -> None:
    scale = _safe_complexity_scale(10, 10, 99, 1.0)
    assert scale < 1.0
    assert (10 * scale).__ceil__() * (10 * scale).__ceil__() <= 99


def test_render_scale_respects_rgb_byte_limit() -> None:
    scale = _safe_complexity_scale(100, 100, 1_000_000, 2.0, max_bytes=30_000)
    assert scale <= 1.0


def test_security_limits_no_longer_expose_project_timeout() -> None:
    assert not hasattr(SecurityLimits(), "document_timeout_seconds")


def test_linux_memory_snapshot_has_explicit_unit_and_source() -> None:
    snapshot = memory_module.process_memory_snapshot()
    assert snapshot["unit"] == "bytes"
    assert snapshot["scope"] == "current_process"
    if snapshot["available"]:
        assert snapshot["source"]
        assert snapshot["current_rss_bytes"] >= 0
        assert snapshot["peak_rss_bytes"] is None or snapshot["peak_rss_bytes"] >= 0
    else:
        assert snapshot["current_rss_bytes"] is None
        assert snapshot["peak_rss_bytes"] is None
        assert snapshot["error_reason"]


def test_windows_memory_snapshot_uses_native_process_counters(monkeypatch) -> None:
    class Psapi:
        @staticmethod
        def GetProcessMemoryInfo(_handle, counters_pointer, _size):
            counters = counters_pointer._obj
            counters.WorkingSetSize = 1024
            counters.PeakWorkingSetSize = 2048
            return 1

    class Kernel32:
        @staticmethod
        def GetCurrentProcess():
            return 17

    monkeypatch.setattr(memory_module.sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(psapi=Psapi(), kernel32=Kernel32()),
        raising=False,
    )
    snapshot = memory_module.process_memory_snapshot()
    assert snapshot["source"] == "windows:GetProcessMemoryInfo"
    assert snapshot["current_rss_bytes"] == 1024
    assert snapshot["peak_rss_bytes"] == 2048
    assert snapshot["unit"] == "bytes"


def test_memory_failure_is_unavailable_not_zero(monkeypatch) -> None:
    original_read_text = Path.read_text

    def fail_procfs(path: Path, *args, **kwargs):
        if str(path) == "/proc/self/status":
            raise OSError("blocked")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(memory_module.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", fail_procfs)
    monkeypatch.setitem(__import__("sys").modules, "psutil", None)
    snapshot = memory_module.process_memory_snapshot()
    assert not snapshot["available"]
    assert snapshot["current_rss_bytes"] is None
    assert snapshot["peak_rss_bytes"] is None


def test_confidence_thresholds_are_neutral_for_uncalibrated_families() -> None:
    paddle = effective_ocr_quality_thresholds(ExtractorConfig(ocr_engine="paddle"))
    rapid = effective_ocr_quality_thresholds(ExtractorConfig(ocr_engine="rapidocr"))
    assert paddle.strong_mean_confidence == 0.90
    assert rapid.strong_mean_confidence == 0.0
    assert rapid.max_low_confidence_char_ratio == 1.0
    assert rapid.minimum_printable_ratio == paddle.minimum_printable_ratio


def test_easyocr_confidence_thresholds_are_calibrated() -> None:
    easy = effective_ocr_quality_thresholds(ExtractorConfig(ocr_engine="easyocr"))
    paddle = effective_ocr_quality_thresholds(ExtractorConfig(ocr_engine="paddle"))
    rapid = effective_ocr_quality_thresholds(ExtractorConfig(ocr_engine="rapidocr"))

    # EasyOCR must have active (non-zero) confidence gating
    assert easy.strong_mean_confidence > 0.0, "EasyOCR must have active confidence gating"
    assert easy.max_low_confidence_char_ratio < 1.0, "EasyOCR must have active low-conf ratio gate"
    assert easy.low_confidence_threshold > 0.0, "EasyOCR must have active per-token threshold"

    # EasyOCR thresholds must be below Paddle's (EasyOCR confidence is lower on clean text)
    assert easy.strong_mean_confidence < paddle.strong_mean_confidence
    assert easy.max_low_confidence_char_ratio > paddle.max_low_confidence_char_ratio

    # EasyOCR must have stronger gates than uncalibrated engines (rapidocr)
    assert easy.strong_mean_confidence > rapid.strong_mean_confidence
    assert easy.max_low_confidence_char_ratio < rapid.max_low_confidence_char_ratio

    # Geometry and integrity gates must be preserved (not neutralized)
    assert easy.minimum_printable_ratio == paddle.minimum_printable_ratio
    assert easy.minimum_orientation_ratio == paddle.minimum_orientation_ratio


def test_extractor_close_is_idempotent_and_prevents_reuse(tmp_path: Path) -> None:
    extractor = PdfTextExtractor(ExtractorConfig(mode="native"))
    extractor.close()
    extractor.close()
    try:
        extractor.extract(tmp_path / "does-not-exist.pdf")
    except RuntimeError as exc:
        assert "closed" in str(exc)
    else:
        raise AssertionError("closed extractor accepted a new document")


def test_compare_never_substitutes_a_failed_requested_reference(monkeypatch, tmp_path: Path) -> None:
    class Adapter:
        def __init__(self, name: str, result: ComparisonExtraction):
            self.name = name
            self.result = result

        def available(self):
            return True

        def extract(self, _path):
            return self.result

    registry = {
        "requested": Adapter("requested", ComparisonExtraction("requested", "failure", 0, "", "", 1, error="failed")),
        "other": Adapter("other", ComparisonExtraction("other", "success", 1, "text", "text", 1)),
    }
    monkeypatch.setattr(compare_module, "comparison_adapters", lambda _language: registry)
    result = compare_extractors(tmp_path / "unused.pdf", ("requested", "other"), reference="requested")
    assert result["status"] == "failure"
    assert result["requested_reference"] == "requested"
    assert result["effective_reference"] is None
    assert result["reference"] is None
    assert "relative_to_reference" not in result["results"][1]


def test_all_failed_comparison_is_a_failure_even_with_diagnostic_results(monkeypatch, tmp_path: Path) -> None:
    class Adapter:
        def __init__(self, name: str):
            self.name = name

        def available(self):
            return True

        def extract(self, _path):
            return ComparisonExtraction(self.name, "failure", 0, "", "", 1, error="failed")

    monkeypatch.setattr(
        compare_module,
        "comparison_adapters",
        lambda _language: {name: Adapter(name) for name in ("reference", "other")},
    )
    result = compare_extractors(tmp_path / "unused.pdf", ("reference", "other"), reference="reference")
    assert result["status"] == "failure"
    assert not result["valid"]
    assert result["effective_reference"] is None
    assert all(item["status"] == "failure" for item in result["results"])


def test_cli_overlay_native_does_not_validate_ocr(monkeypatch, tmp_path: Path) -> None:
    def fail_if_called(**_kwargs):
        raise AssertionError("native overlay must not validate OCR models")

    monkeypatch.setattr(cli_module, "validate_local_ocr_models", fail_if_called)
    code = cli_module.main(["overlay", str(_pdf(tmp_path / "native-overlay.pdf")), "--page", "1", "--out", str(tmp_path / "overlay.png")])
    assert code == 0


def test_cli_overlay_ocr_mode_does_not_require_model_profile(capsys, tmp_path: Path) -> None:
    # Verify that --ocr-model-profile is never required, even in balanced OCR mode.
    # Uses paddle explicitly so the test fails on the missing PDF (exit 2) rather
    # than on EasyOCR package availability in the test environment.
    code = cli_module.main([
        "overlay", str(tmp_path / "not-opened.pdf"), "--page", "1", "--out", str(tmp_path / "out.png"),
        "--mode", "balanced", "--ocr-engine", "paddle",
    ])
    assert code == 2
    error = capsys.readouterr().err
    assert "--ocr-model-profile is required" not in error
    assert "not-opened.pdf" in error


def test_cli_all_failed_comparison_saves_diagnostic_and_returns_nonzero(monkeypatch, capsys, tmp_path: Path) -> None:
    class Adapter:
        def __init__(self, name: str):
            self.name = name

        def available(self):
            return True

        def extract(self, _path):
            return ComparisonExtraction(self.name, "failure", 0, "", "", 1, error="injected failure")

    names = ("structured-native", "pdfium-raw", "pymupdf")
    monkeypatch.setattr(
        compare_module,
        "comparison_adapters",
        lambda _language: {name: Adapter(name) for name in names},
    )
    code = cli_module.main(["compare", str(tmp_path / "unused.pdf")])
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["status"] == "failure"
    assert output["valid"] is False
    assert output["results"]


def test_cli_partial_extraction_keeps_output_and_returns_nonzero(monkeypatch, tmp_path: Path) -> None:
    from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource

    monkeypatch.setattr(
        PdfiumNativeEvidenceSource,
        "extract_page",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected page failure")),
    )
    pdf = _pdf(tmp_path / "partial.pdf")
    output = tmp_path / "partial.txt"
    code = cli_module.main(["extract", str(pdf), "--output-file", str(output)])
    assert code == 1
    assert output.exists()


def test_non_degrading_document_warning_does_not_create_partial_status(tmp_path: Path) -> None:
    document = PdfTextExtractor(ExtractorConfig()).extract(_pdf(tmp_path / "warning.pdf"))
    assembled = assemble_document(
        pages=document.pages,
        metadata=document.metadata,
        document_warnings=["informational warning"],
    )
    assert assembled.diagnostics.warnings == ["informational warning"]
    assert assembled.diagnostics.status.value == "success"


def test_cli_one_failed_page_is_partial_and_nonzero(monkeypatch, capsys, tmp_path: Path) -> None:
    from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource

    original = PdfiumNativeEvidenceSource.extract_page

    def fail_second_page(source, page_index):
        if page_index == 1:
            raise OSError("injected second-page failure")
        return original(source, page_index)

    monkeypatch.setattr(PdfiumNativeEvidenceSource, "extract_page", fail_second_page)
    path = tmp_path / "two-pages.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(240, 240))
    pdf.drawString(20, 200, "Primeira página")
    pdf.showPage()
    pdf.drawString(20, 200, "Segunda página")
    pdf.save()
    output = tmp_path / "partial.json"

    code = cli_module.main(["extract", str(path), "--output", "json", "--output-file", str(output)])

    assert code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["diagnostics"]["status"] == "partial_success"


def test_model_commands_remain_in_help_and_invalid_profile_fails_early(capsys, tmp_path: Path) -> None:
    try:
        cli_module.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_text = capsys.readouterr().out
    assert "setup-models" in help_text
    assert "models-status" in help_text
    assert cli_module.main(["models-status", "--ocr-model-profile", "unknown", "--cache-home", str(tmp_path)]) == 2


def test_paddle_cpu_runtime_policy_defaults_to_safe_one_dnn_off(monkeypatch) -> None:
    from structured_pdf_text.ocr.runtime_policy import resolve_paddle_runtime_policy

    monkeypatch.delenv("PADDLE_ENABLE_MKLDNN", raising=False)
    assert resolve_paddle_runtime_policy(system="Linux").enable_mkldnn is False
    monkeypatch.setenv("PADDLE_ENABLE_MKLDNN", "0")
    assert resolve_paddle_runtime_policy(system="Linux").enable_mkldnn is False
    monkeypatch.setenv("PADDLE_ENABLE_MKLDNN", "1")
    assert resolve_paddle_runtime_policy(system="Linux").enable_mkldnn is True


def test_models_status_requires_uvdoc_when_unwarping_is_enabled(tmp_path, capsys) -> None:
    from structured_pdf_text.cli import _cmd_models_status
    from structured_pdf_text.ocr.models import required_model_directories

    profile = get_profile("pt")
    required = required_model_directories(profile)
    assert required["doc_unwarping_model_dir"] == "UVDoc"
    for model_name in required.values():
        if model_name == "UVDoc":
            continue
        model_dir = tmp_path / "official_models" / model_name
        model_dir.mkdir(parents=True)
        (model_dir / "model.pdparams").write_text("fixture", encoding="utf-8")

    assert _cmd_models_status("pt", str(tmp_path)) == 1
    output = capsys.readouterr().out
    assert "[missing] UVDoc" in output
    assert "Offline OCR readiness: NOT READY" in output

    uvdoc_dir = tmp_path / "official_models" / "UVDoc"
    uvdoc_dir.mkdir()
    (uvdoc_dir / "model.pdparams").write_text("fixture", encoding="utf-8")
    assert _cmd_models_status("pt", str(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "[ok] UVDoc" in output
    assert "Offline OCR readiness: READY" in output


def test_paddle_direct_and_subprocess_modes_share_the_resolved_policy(monkeypatch) -> None:
    import structured_pdf_text.ocr.backends.paddle as paddle_backend
    import structured_pdf_text.ocr.paddle as paddle_engine

    monkeypatch.setenv("PADDLE_ENABLE_MKLDNN", "0")
    config = ExtractorConfig(language="pt")

    monkeypatch.setattr(paddle_backend, "_has_torch_conflict", lambda: True)
    subprocess_backend = paddle_backend.PaddleOCRBackend(config)
    assert subprocess_backend._subprocess_config["mkldnn"] is False
    assert subprocess_backend._subprocess_config["disable_pir_api"] is False

    direct_options = {}

    class EngineStub:
        def __init__(self, **options):
            direct_options.update(options)

    monkeypatch.setattr(paddle_backend, "_has_torch_conflict", lambda: False)
    monkeypatch.setattr(paddle_engine, "PaddleOcrEngine", EngineStub)
    paddle_backend.PaddleOCRBackend(config)
    assert direct_options["enable_mkldnn"] is subprocess_backend._subprocess_config["mkldnn"]


def test_page_ocr_exception_and_successful_empty_result_have_distinct_diagnostics(tmp_path) -> None:
    class Backend:
        def __init__(self, *, should_fail: bool) -> None:
            self.should_fail = should_fail

        def recognize_page(
            self,
            image,
            page_index,
            page_bbox=None,
            *,
            quality_variants=None,
            quality_policy=None,
        ):
            if self.should_fail:
                raise RuntimeError("backend failure")
            return []

        def recognize_region(self, image, page_index, region_bbox):
            return []

    pdf_path = _pdf(tmp_path / "ocr-diagnostic.pdf")
    config = ExtractorConfig(mode="ocr", language="pt", ocr_quality_variants=False)

    failed = PdfTextExtractor(config, ocr_engine=Backend(should_fail=True)).extract(pdf_path)
    failed_page = failed.pages[0]
    assert "page_ocr_unavailable" in failed_page.diagnostics.facts["partial_reasons"]
    assert any("backend failed before producing" in warning for warning in failed_page.diagnostics.warnings)
    assert not any("OCR completed successfully" in warning for warning in failed_page.diagnostics.warnings)

    empty = PdfTextExtractor(config, ocr_engine=Backend(should_fail=False)).extract(pdf_path)
    empty_page = empty.pages[0]
    assert "page_ocr_unavailable" not in empty_page.diagnostics.facts["partial_reasons"]
    assert any("OCR completed successfully but produced no usable tokens" in warning for warning in empty_page.diagnostics.warnings)
