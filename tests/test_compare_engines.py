from __future__ import annotations

from scripts.compare_engines import (
    _comparability_key,
    _render_comparison_table,
    _validate_comparability,
)


def _run(**updates):
    data = {
        "pdf_sha256": "pdf-sha",
        "reference_sha256": "reference-sha",
        "manifest_sha256": "manifest-sha",
        "mode": "balanced",
        "selected_pages": [1, 2],
        "pages_selected": 2,
        "pages_selected_but_missing": [],
        "missing_pages_penalised": False,
        "benchmark_status": "valid",
        "grupo1_texto": {"cer_text_only": 0.0},
    }
    data.update(updates)
    return data


def test_missing_page_and_partial_status_do_not_make_same_input_incomparable():
    complete = _run()
    partial = _run(
        benchmark_status="partial",
        pages_selected_but_missing=[2],
        missing_pages_penalised=True,
        pages_selected_and_present=1,
        grupo1_texto={"cer_text_only": 0.5},
    )

    assert _comparability_key(complete) == _comparability_key(partial)
    assert _validate_comparability(
        [("engine-a", complete), ("engine-b", partial)], allow_partial=False
    ) == []
    report = _render_comparison_table([("engine-a", complete), ("engine-b", partial)])
    assert "partial" in report
    assert "0.5000" in report


def test_different_pdf_is_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(pdf_sha256="other-pdf"))],
        allow_partial=False,
    )

    assert any("pdf_sha256" in error for error in errors)


def test_different_reference_or_manifest_is_not_comparable():
    errors = _validate_comparability(
        [
            ("engine-a", _run()),
            ("engine-b", _run(reference_sha256="other-reference")),
            ("engine-c", _run(manifest_sha256="other-manifest")),
        ],
        allow_partial=False,
    )

    assert any("reference_sha256" in error for error in errors)
    assert any("manifest_sha256" in error for error in errors)


def test_different_selected_page_sets_are_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=[3, 4]))],
        allow_partial=False,
    )

    assert any("selected_pages" in error for error in errors)


def test_same_page_count_with_different_page_numbers_is_not_comparable():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=[10, 11]))],
        allow_partial=False,
    )

    assert any("selected_pages" in error for error in errors)


def test_comparison_requires_exact_page_list_for_multiple_runs():
    errors = _validate_comparability(
        [("engine-a", _run()), ("engine-b", _run(selected_pages=None))],
        allow_partial=False,
    )

    assert any("exact selected page list" in error for error in errors)


def test_condition_lists_are_split_and_profile_recovery_metadata_is_reported():
    report = _render_comparison_table([
        ("rapidocr-onnx", _run(
            stability_status="recovered",
            recovery_count=2,
            elapsed_s=12.5,
            memory={"peak_rss_bytes": 1024 * 1024},
            engine_identity={
                "runtime": "onnxruntime", "profile": "latin/pt-compatible",
                "language": "pt", "extra": {"rec_model": "latin.onnx"},
            },
            per_page=[{
                "conditions": ["small_text", "low_contrast"],
                "family": "OCR de tabelas",
                "cer_text_only": 0.1,
                "wer": 0.2,
                "deletion_rate": 0.1,
                "currency_f1": 0.9,
                "identifier_f1": 0.8,
                "cell_cer": 0.1,
                "missing_page_rate": 1.0,
                "easyocr_fallback_rate": 0.0,
            }],
        ))
    ])
    assert "small_text" in report and "low_contrast" in report
    assert "OCR de tabelas" in report
    assert "latin/pt-compatible" in report
    assert "recovered" in report and "2" in report
    assert "Peak RSS MB" in report
