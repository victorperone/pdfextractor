from scripts.evaluate_e2e import _classify_document_status, _classify_page_status, _page_body
from scripts.evaluate_e2e import _diagnostic_reasons
from types import SimpleNamespace


def test_successful_recovery_is_valid_content_with_recovered_stability() -> None:
    assert _classify_page_status("recovered", {}, True, 12) == ("ok", "recovered")
    assert _classify_page_status(
        "success", {"partial_reasons": ["figure_ocr_unavailable"]}, True, 12
    ) == ("degraded", "degraded")
    assert _classify_document_status("success", [], []) == "valid"


def test_partial_and_failed_extraction_statuses_are_not_valid() -> None:
    assert _classify_page_status("degraded", {}, True, 0) == ("degraded", "degraded")
    assert _classify_document_status("partial_success", [], []) == "partial"
    assert _classify_document_status("failure", [], []) == "invalid"
    assert _classify_document_status("success", [3], []) == "invalid"


def test_page_heading_alone_is_not_content():
    assert _page_body("## Página 9\n  \n") == ""
    assert _page_body("## Página 9\nTexto OCR") == "Texto OCR"


def test_partial_reasons_and_complexity_reasons_are_independent_fields():
    partial, complexity = _diagnostic_reasons(SimpleNamespace(
        facts={"partial_reasons": ["page_ocr_unavailable"]},
        reasons=["low_native_text"],
    ))
    assert partial == ["page_ocr_unavailable"]
    assert complexity == ["low_native_text"]
