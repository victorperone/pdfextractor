import math

from scripts.compute_metrics import _build_error_report, _macro_average


def test_macro_average_ignores_non_applicable_and_non_finite_values():
    pages = [
        {"currency_f1": 0.5},
        {"currency_f1": None},
        {"currency_f1": 1.0},
        {"currency_f1": math.nan},
    ]
    assert _macro_average(pages, "currency_f1") == 0.75


def test_macro_average_is_none_when_metric_is_not_applicable_anywhere():
    pages = [{"currency_f1": None}, {}, {"currency_f1": math.inf}]
    assert _macro_average(pages, "currency_f1") is None


def test_macro_average_does_not_treat_boolean_as_numeric_score():
    assert _macro_average([{"currency_f1": True}], "currency_f1") is None


def test_error_report_handles_not_applicable_page_metrics():
    report = _build_error_report("engine", "run", [{
        "page": 1,
        "cer_normalized": None,
        "wer": None,
        "substitution_rate": None,
        "deletion_rate": None,
        "insertion_rate": None,
        "heading_f1": None,
        "block_f1": None,
    }], {})

    assert "CER=n/a WER=n/a" in report
    assert "Substitutions: n/a" in report
