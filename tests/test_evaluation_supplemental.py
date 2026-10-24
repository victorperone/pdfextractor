"""Independent audit exposes critical mistakes hidden by global entity F1."""
import json

import pytest

from structured_pdf_text.evaluation.supplemental import AUDIT_VERSION, audit_extraction, critical_entities


@pytest.mark.parametrize("text,currency,value", [
    ("-R$ 250,00", "BRL", "-250"), ("R$ -250,00", "BRL", "-250"),
    ("−US$ 1.234,50", "USD", "-1234.5"), ("€ 42,90", "EUR", "42.9"),
    ("BRL +000,00", "BRL", "0"), ("EUR 12,30.", "EUR", "12.3"),
])
def test_complete_signed_currency_lexemes(text, currency, value):
    entities, invalid = critical_entities(text)
    assert entities == {("currency", f"{currency}:{value}"): 1}
    assert invalid == []


@pytest.mark.parametrize("text", ["R$ 48.000,0000,00", "R$ 1.23,00", "R$ 250,00abc", "-R$ -250,00"])
def test_invalid_suffixes_and_double_signs_cannot_match_a_valid_prefix(text):
    entities, invalid = critical_entities(text)
    assert not entities
    assert invalid == [text]


def test_ambiguous_identifiers_are_compared_exactly_without_confusable_folding():
    report = audit_extraction("## Página 1\n\nO0I1 N-01O ABC-001", "## Página 1\n\n0011 N-010 ABC-001")
    scores = report["per_page"][0]["critical_entities"]
    assert (scores["tp"], scores["fp"], scores["fn"]) == (1, 1, 2)


def test_values_swapped_between_cells_fail_context_despite_per_page_entity_perfection():
    ref = "## Página 1\n\n| Item | Valor |\n| --- | --- |\n| AB-1 | R$ 10,00 |\n| AB-2 | R$ 20,00 |"
    hyp = ref.replace("R$ 10,00", "TEMP").replace("R$ 20,00", "R$ 10,00").replace("TEMP", "R$ 20,00")
    report = audit_extraction(ref, hyp)
    assert report["summary"]["critical_entities"]["f1"] == 1
    assert report["summary"]["table_cell_entities"]["f1"] == .5


def test_html_span_anchor_coordinates_are_used_once_and_page_scope_is_preserved():
    table = '<table><tr><th rowspan="2">AA-1</th><td>US$ 10,00</td></tr><tr><td>€ 20,00</td></tr></table>'
    reference = f"## Página 1\n\n{table}\n\n## Página 2\n\nBB-2"
    report = audit_extraction(reference, f"## Página 1\n\n{table}", selected_pages=[1])
    assert report["summary"]["table_cell_entities"] == {"tp": 3, "fp": 0, "fn": 0, "f1": 1}
    assert [p["page"] for p in report["per_page"]] == [1]


def test_good_matched_order_does_not_hide_omitted_blocks_or_pages():
    reference = "## Página 1\n\nPrimeiro parágrafo.\n\nSegundo parágrafo.\n\n## Página 2\n\nAusência."
    report = audit_extraction(reference, "## Página 1\n\nPrimeiro parágrafo.", selected_pages=[1, 2])
    assert report["per_page"][0]["alignment"]["reference_coverage"] == .5
    assert report["per_page"][1]["alignment"]["reference_coverage"] == 0
    assert report["per_page"][1]["alignment"]["unmatched_reference_character_rate"] == 1
    assert report["summary"]["reference_coverage"]["worst_page"] == 2


def test_implicit_first_page_and_explicit_blank_pages_remain_in_the_audit_scope():
    implicit = audit_extraction("AA-1", "AA-1", selected_pages=[1])
    assert implicit["summary"]["critical_entities"]["tp"] == 1
    explicit = audit_extraction("## Página 1\n\n", "## Página 1\n\nAA-1")
    assert explicit["summary"]["critical_entities"]["fp"] == 1


def test_functional_link_and_checkbox_diagnostics_ignore_fenced_code():
    reference = "[portal](https://example.invalid)\n\n- [x] Done\n\n```text\n[other](https://ignored.invalid)\n```"
    hypothesis = "portal\n\n- ☐ Done"
    report = audit_extraction(reference, hypothesis)
    assert report["summary"]["link_destinations"]["fn"] == 1
    assert report["summary"]["checkbox_states"] == {"tp": 0, "fp": 1, "fn": 1, "f1": 0}


@pytest.mark.parametrize("script", ["compute_metrics.py", "regrade_existing.py"])
def test_optional_audit_publishes_alongside_unchanged_canonical_metrics(tmp_path, script):
    from test_evaluation_artifact_corrections import cli, prepared

    args = prepared(tmp_path)
    assert cli(script, args).returncode == 0
    metrics = next((tmp_path/"out").glob("metrics*.json"))
    before = metrics.read_bytes()
    result = cli(script, [*args, "--supplemental-audit"])
    assert result.returncode == 0, result.stderr
    assert metrics.read_bytes() == before
    audit = json.loads(next((tmp_path/"out").glob("audit*.json")).read_text())
    assert audit["audit_version"] == AUDIT_VERSION
    assert audit["canonical_definitions_unchanged"] == "3.1.0"
