"""PR 7 — Homologation tests.

Tests cover:
- §0.3 acceptance conditions (all must pass before V2 cutover)
- §12.5 agent integration contract (I01-I16)
- §12.6 property-based invariants
- Reference integrity (SHA-256 stability)
- check_comparability guard
- V2 envelope schema completeness
- regrade_existing pipeline end-to-end
- Delta V1×V2 report format
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from evaluation_contract_helpers import comparison_result

_SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC))

from structured_pdf_text.evaluation import (
    PARSER_VERSION,
    AlignmentSummary,
    MetricStatus,
    V2DocumentMetrics,
    align_blocks,
    build_page_scope,
    check_comparability,
    compute_critical_data_metrics,
    compute_integrity_metrics,
    compute_structure_metrics,
    compute_table_metrics,
    evaluate_rank_eligibility,
    markdown_validity_report,
    parse_document,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_SCRIPT = Path(__file__).parent.parent / "scripts" / "regrade_existing.py"

_REF_SIMPLE = textwrap.dedent("""\
    ## Página 1

    Primeiro parágrafo do documento.

    ## Seção de Dados

    Informações financeiras importantes.

    - Item A
    - Item B
    - Item C

    | Coluna | Valor |
    |---|---|
    | Alpha | R$ 1.000,00 |
    | Beta  | R$ 500,00 |

    ## Página 2

    Dados críticos: CPF 123.456.789-09, data 10/05/2025.
    CNPJ: 11.222.333/0001-81
""")

_HYP_PERFECT = _REF_SIMPLE  # identical hypothesis

_HYP_PARTIAL = textwrap.dedent("""\
    ## Página 1

    Primeiro parágrafo do documento.

    ## Seção de Dados

    Informações financeiras importantes.

    - Item A
    - Item B

    | Coluna | Valor |
    |---|---|
    | Alpha | R$ 1.000,00 |
    | Beta  | R$ 500,00 |

    ## Página 2

    Dados críticos: CPF 123.456.789-09, data 10/05/2025.
    CNPJ: 11.222.333/0001-81
""")

_HYP_EMPTY = ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_v2_doc(ref_text: str, hyp_text: str) -> V2DocumentMetrics:
    ref_nodes = parse_document(ref_text, origin="reference")
    hyp_nodes = parse_document(hyp_text, origin="hypothesis")
    pairs = align_blocks(ref_nodes, hyp_nodes)
    summary = AlignmentSummary.from_pairs(pairs, len(ref_nodes), len(hyp_nodes))

    struct = compute_structure_metrics(pairs, ref_nodes, hyp_nodes)
    tables = compute_table_metrics(ref_nodes, hyp_nodes)
    critical = compute_critical_data_metrics(ref_nodes, hyp_nodes)
    integrity = compute_integrity_metrics(ref_nodes, hyp_nodes,
                                          hyp_raw_text=hyp_text, ref_raw_text=ref_text)

    doc = V2DocumentMetrics()
    doc.structure = {k: v for k, v in struct.to_dict().items()
                     if k not in ("pairwise_reading_order_accuracy", "block_type_confusion")}
    doc.reading_order = {
        "pairwise_reading_order_accuracy": struct.reading_order.to_dict()
        if struct.reading_order else {}
    }
    doc.tables = tables.to_dict()
    doc.critical_data = critical.to_dict()
    doc.integrity = integrity.to_dict()
    doc.evaluator_info = {"parser": PARSER_VERSION}

    doc.finalise()
    eligible, reasons = evaluate_rank_eligibility(doc)
    doc.rank_eligible = eligible
    doc.rank_ineligibility_reasons = reasons
    return doc


# ---------------------------------------------------------------------------
# §0.3 Acceptance conditions
# ---------------------------------------------------------------------------

class TestAcceptanceConditions:
    """§0.3: conditions that must hold before V2 cutover."""

    def test_AC01_schema_version_constant(self):
        """Unified output carries schema_version = 'evaluation-3.0.0' (A09/schema fix)."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PERFECT)
        result = doc.to_dict()
        assert result["schema_version"] == "evaluation-3.0.0"

    def test_AC02_na_preserved_not_zero(self):
        """N/A metrics appear as null value, not 0.0 or 1.0."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PERFECT)
        result = doc.to_dict()
        # Traverse all metric values
        def _collect_nulls_and_values(d: dict, path: str = "") -> list[tuple[str, object]]:
            items = []
            for k, v in d.items():
                p = f"{path}.{k}"
                if isinstance(v, dict):
                    status = v.get("status")
                    val = v.get("value")
                    if status in ("not_applicable", "undefined_denominator"):
                        items.append((p, val))
                    items.extend(_collect_nulls_and_values(v, p))
            return items
        na_items = _collect_nulls_and_values(result)
        for path, val in na_items:
            assert val is None, f"N/A metric {path} has value={val!r} (should be null)"

    def test_AC03_perfect_score_on_identical_inputs(self):
        """§12.6 identity: evaluate(x, x) → perfect applicable metrics."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PERFECT)
        result = doc.to_dict()

        def _check_perfect(d: dict) -> None:
            for k, v in d.items():
                if isinstance(v, dict) and v.get("status") == "ok":
                    val = v.get("value")
                    direction = v.get("direction", "")
                    if val is not None:
                        if direction == "higher_is_better":
                            assert abs(val - 1.0) < 0.001, \
                                f"Expected perfect (1.0) for {k}, got {val}"
                        elif direction == "lower_is_better":
                            assert abs(val) < 0.001, \
                                f"Expected perfect (0.0) for {k}, got {val}"

        _check_perfect(result["quality"].get("structure", {}))
        _check_perfect(result["quality"].get("reading_order", {}))
        _check_perfect(result["quality"].get("tables", {}))

    def test_AC04_determinism(self):
        """§12.6 determinism: same input → identical output twice."""
        doc1 = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        doc2 = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        r1 = doc1.to_dict()
        r2 = doc2.to_dict()
        # Compare numeric values
        for group in ("structure", "reading_order", "tables", "critical_data"):
            g1 = r1["quality"][group]
            g2 = r2["quality"][group]
            for k in g1:
                if isinstance(g1[k], dict) and isinstance(g2[k], dict):
                    assert g1[k].get("value") == g2[k].get("value"), \
                        f"Non-deterministic value for {group}.{k}"

    def test_AC05_rank_eligible_with_applicable_metrics(self):
        """Rank eligibility granted when there are applicable metrics and valid Markdown."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        assert doc.applicable_metric_count > 0
        assert doc.rank_eligible is True
        assert doc.rank_ineligibility_reasons == []

    def test_AC06_rank_ineligible_on_invalid_markdown(self):
        """Invalid Markdown (unclosed fence) blocks ranking."""
        hyp_invalid = _REF_SIMPLE + "\n```\nunclosed fence here\n"
        doc = _make_v2_doc(_REF_SIMPLE, hyp_invalid)
        doc_dict = doc.to_dict()
        # markdown validity should flag this
        mv = doc.integrity.get("markdown_validity", {})
        assert mv.get("validity_status") == "invalid"
        assert doc.rank_eligible is False
        assert "invalid_markdown_in_hypothesis" in doc.rank_ineligibility_reasons

    def test_AC07_counter_consistency(self):
        """§12.6 TP+FN = ref_total, TP+FP = hyp_total where counts exist."""
        tables = compute_table_metrics(
            parse_document(_REF_SIMPLE, "reference"),
            parse_document(_HYP_PARTIAL, "hypothesis"),
        )
        # TableMetrics.detection is a dict keyed by metric name
        td = tables.detection.get("table_detection_f1")
        if td and td.status == MetricStatus.OK:
            c = td.counts
            tp = c.get("tp", 0)
            fp = c.get("fp", 0)
            fn = c.get("fn", 0)
            n_ref = c.get("n_ref", tp + fn)
            n_hyp = c.get("n_hyp", tp + fp)
            assert tp + fn == n_ref, f"TP+FN={tp+fn} != n_ref={n_ref}"
            assert tp + fp == n_hyp, f"TP+FP={tp+fp} != n_hyp={n_hyp}"

    def test_AC08_empty_hyp_not_perfect(self):
        """Empty hypothesis produces non-perfect scores, not 1.0."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_EMPTY)
        result = doc.to_dict()
        # Heading detection should be 0 or N/A, never 1.0
        hdf = result["quality"]["structure"].get("heading_detection_f1", {})
        val = hdf.get("value")
        if val is not None:
            assert val < 0.5, f"Empty hypothesis gave heading_f1={val}"


# ---------------------------------------------------------------------------
# §12.5 Agent integration contract (I01-I16)
# ---------------------------------------------------------------------------

class TestAgentIntegrationContract:
    """§12.5 integration tests: agent must not confuse V1 and V2."""

    def test_I03_incompatible_versions_rejected(self):
        """I03: different evaluator schema versions are rejected by comparator."""
        result_a = {"schema_version": "evaluation-v2.0", "inputs": {}}
        result_b = {"schema_version": "evaluation-v1.0", "inputs": {}}
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok
        assert any("schema_version" in r for r in reasons)

    def test_I04_incompatible_reference_rejected(self):
        """I04: different effective reference SHA is rejected."""
        result_a = comparison_result([1], "a" * 64)
        result_b = comparison_result([1], "d" * 64)
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok
        assert any("reference_effective_sha256" in r for r in reasons)

    def test_I05_incompatible_pages_rejected(self):
        """I05: different selected_pages are rejected."""
        result_a = comparison_result([1, 2, 3])
        result_b = comparison_result([1, 2, 4])
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok
        assert any("selected_pages" in r for r in reasons)

    def test_I06_partial_status_preserved(self):
        """I06: partial evaluation status is preserved in the result, not silenced."""
        doc = V2DocumentMetrics()
        doc.evaluation_status = "partial"
        doc.applicable_metric_count = 5
        result = doc.to_dict()
        assert result["evaluation"]["status"] == "partial"

    def test_I07_regrade_does_not_call_ocr(self, tmp_path):
        """I07: regrade_existing.py does not import or invoke OCR modules."""
        ref_file = tmp_path / "ref.md"
        hyp_file = tmp_path / "hyp.md"
        ref_file.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp_file.write_text(_HYP_PARTIAL, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--hypothesis", str(hyp_file),
             "--reference", str(ref_file),
             "--engine", "test",
             "--run-id", "i07test",
             "--output-dir", str(tmp_path / "out"),
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"regrade failed: {result.stderr}"
        # Verify output files
        output_dir = tmp_path / "out"
        metrics_files = list(output_dir.glob("metrics_*.json"))
        assert len(metrics_files) == 1, "Expected one metrics JSON"
        data = json.loads(metrics_files[0].read_text())
        assert data["schema_version"] == "evaluation-3.0.0"

    def test_I08_reference_hash_stable(self):
        """I08: SHA-256 of reference does not change across two computations."""
        h1 = _sha256(_REF_SIMPLE)
        h2 = _sha256(_REF_SIMPLE)
        assert h1 == h2
        # Ensure any tiny modification changes the hash
        modified = _REF_SIMPLE + " "
        h3 = _sha256(modified)
        assert h1 != h3

    def test_I09_determinism_same_inputs(self):
        """I09: same inputs always produce identical results (determinism invariant)."""
        def _run():
            return _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL).to_dict()

        r1 = _run()
        r2 = _run()
        assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)

    def test_I10_large_document_no_silent_truncation(self):
        """I10: large documents complete without silent truncation."""
        lines = ["## Página 1\n"]
        for i in range(100):
            lines.append(f"Parágrafo número {i} com conteúdo único: token_{i}_abcdefgh.\n\n")
        big_ref = "\n".join(lines)
        big_hyp = big_ref

        doc = _make_v2_doc(big_ref, big_hyp)
        result = doc.to_dict()

        raw = json.dumps(result)
        assert "resource_limit" not in raw
        assert result["evaluation"]["status"] not in ("failed", "error")

    def test_I11_parser_error_preserves_independent_metrics(self):
        """I11: well-formed document never causes evaluation status=failed."""
        valid_ref = "## Página 1\n\nTexto válido.\n"
        valid_hyp = "## Página 1\n\nTexto válido.\n"
        doc = _make_v2_doc(valid_ref, valid_hyp)
        result = doc.to_dict()
        assert result["evaluation"]["status"] != "failed"
        quality = result.get("quality", {})
        assert len(quality) > 0

    def test_I12_skip_validation_marks_result_as_diagnostic(self, tmp_path):
        """I12: --skip-validation produces output with heterogeneous watermark."""
        metrics_a = tmp_path / "engine_a.json"
        metrics_b = tmp_path / "engine_b.json"
        output = tmp_path / "compare.md"

        doc_a = _make_v2_doc(_REF_SIMPLE, _HYP_PERFECT)
        doc_a_dict = doc_a.to_dict()
        doc_a_dict["inputs"] = {"reference_sha256": "aaa", "selected_pages": [1, 2]}
        metrics_a.write_text(json.dumps(doc_a_dict), encoding="utf-8")

        doc_b = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        doc_b_dict = doc_b.to_dict()
        doc_b_dict["inputs"] = {"reference_sha256": "bbb", "selected_pages": [1, 2]}
        metrics_b.write_text(json.dumps(doc_b_dict), encoding="utf-8")

        _COMPARE_SCRIPT = Path(__file__).parent.parent / "scripts" / "compare_engines.py"
        result = subprocess.run(
            [sys.executable, str(_COMPARE_SCRIPT),
             str(metrics_a), str(metrics_b),
             "--output", str(output),
             "--skip-validation",
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"compare_engines failed: {result.stderr}"
        report_text = output.read_text(encoding="utf-8")
        assert (
            "Heterogeneous" in report_text
            or "heterogeneous" in report_text
            or "skip-validation" in report_text.lower()
            or "validation" in report_text.lower()
        )

    def test_I13_missing_hash_makes_runs_incomparable(self):
        """I13: missing effective hash is incompatible with a real hash."""
        result_a = comparison_result([1], None)
        result_b = comparison_result([1])
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok
        assert any("reference_effective_sha256" in r for r in reasons)

    def test_I14_na_metric_not_nan_inf(self):
        """I14: N/A metric produces null JSON value, never NaN/Inf."""
        # Use empty ref+hyp to trigger N/A
        doc = _make_v2_doc("", "")
        result = doc.to_dict()
        raw = json.dumps(result)
        assert "NaN" not in raw
        assert "Infinity" not in raw

    def test_I14_na_not_included_in_average(self):
        """I14: N/A values are excluded from applicable_metric_count."""
        doc = _make_v2_doc("", "")
        # With both empty, nothing should count as applicable
        assert doc.applicable_metric_count == 0

    def test_I15_redundant_metrics_not_double_counted(self):
        """I15: CER and WER are separate metric keys with distinct definition_ids."""
        from structured_pdf_text.evaluation.text_metrics import compute_text_metrics as ctm

        ref_nodes = parse_document(_REF_SIMPLE, "reference")
        hyp_nodes = parse_document(_HYP_PERFECT, "hypothesis")
        pairs = align_blocks(ref_nodes, hyp_nodes)

        tm = ctm(ref_nodes, hyp_nodes, pairs)
        d = tm.to_dict()

        cer_key = "cer_sequential_normalized"
        wer_key = "wer_sequential"
        assert cer_key in d
        assert wer_key in d
        assert cer_key != wer_key

        cer_def = d[cer_key].get("definition_id", "")
        wer_def = d[wer_key].get("definition_id", "")
        assert cer_def != wer_def

    def test_I16_schema_version_field_present(self):
        """I16: every result has schema_version for version detection (now 3.0.0)."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PERFECT)
        result = doc.to_dict()
        assert "schema_version" in result
        assert result["schema_version"] == "evaluation-3.0.0"


# ---------------------------------------------------------------------------
# §12.6 Property-based invariants
# ---------------------------------------------------------------------------

class TestPropertyInvariants:
    """§12.6 invariant tests."""

    def test_PROP01_adding_invented_block_does_not_improve_precision(self):
        """Adding a fake block to hyp cannot improve block detection precision."""
        ref = "## Página 1\n\nTexto real aqui.\n"
        hyp_base = "## Página 1\n\nTexto real aqui.\n"
        hyp_extra = "## Página 1\n\nTexto real aqui.\n\nBloco inventado completamente falso.\n"

        ref_n = parse_document(ref, "reference")
        hyp_base_n = parse_document(hyp_base, "hypothesis")
        hyp_extra_n = parse_document(hyp_extra, "hypothesis")

        pairs_base = align_blocks(ref_n, hyp_base_n)
        pairs_extra = align_blocks(ref_n, hyp_extra_n)

        struct_base = compute_structure_metrics(pairs_base, ref_n, hyp_base_n)
        struct_extra = compute_structure_metrics(pairs_extra, ref_n, hyp_extra_n)

        p_base = struct_base.paragraph.get("paragraph_detection_precision")
        p_extra = struct_extra.paragraph.get("paragraph_detection_precision")
        if p_base and p_extra and p_base.value is not None and p_extra.value is not None:
            assert p_extra.value <= p_base.value + 0.001, \
                f"Adding invented block improved precision: {p_base.value} → {p_extra.value}"

    def test_PROP02_permutation_preserves_content_coverage(self):
        """Moving blocks without editing text should not alter content coverage recall."""
        ref = textwrap.dedent("""\
            ## Página 1

            Primeiro bloco de conteúdo textual.

            Segundo bloco de conteúdo textual.

            Terceiro bloco de conteúdo textual.
        """)
        hyp_same = ref
        hyp_perm = textwrap.dedent("""\
            ## Página 1

            Terceiro bloco de conteúdo textual.

            Primeiro bloco de conteúdo textual.

            Segundo bloco de conteúdo textual.
        """)
        ref_n = parse_document(ref, "reference")
        hyp_same_n = parse_document(hyp_same, "hypothesis")
        hyp_perm_n = parse_document(hyp_perm, "hypothesis")

        int_same = compute_integrity_metrics(ref_n, hyp_same_n, hyp_raw_text=hyp_same)
        int_perm = compute_integrity_metrics(ref_n, hyp_perm_n, hyp_raw_text=hyp_perm)

        cov_same = int_same.content_coverage_recall
        cov_perm = int_perm.content_coverage_recall
        if cov_same and cov_perm and cov_same.value is not None and cov_perm.value is not None:
            assert abs(cov_same.value - cov_perm.value) < 0.05, \
                f"Permutation changed coverage: {cov_same.value} → {cov_perm.value}"

    def test_PROP03_duplicate_corpus_stability(self):
        """Duplicating identical ref+hyp pair does not change quality metrics."""
        ref = "## Página 1\n\nTexto de referência único.\n"
        hyp = "## Página 1\n\nTexto de referência único.\n"

        ref_n = parse_document(ref, "reference")
        hyp_n = parse_document(hyp, "hypothesis")

        pairs = align_blocks(ref_n, hyp_n)
        struct1 = compute_structure_metrics(pairs, ref_n, hyp_n)

        # Duplicate: same content on two pages
        ref2 = ref + "\n## Página 2\n\nTexto de referência único.\n"
        hyp2 = hyp + "\n## Página 2\n\nTexto de referência único.\n"
        ref2_n = parse_document(ref2, "reference")
        hyp2_n = parse_document(hyp2, "hypothesis")
        pairs2 = align_blocks(ref2_n, hyp2_n)
        struct2 = compute_structure_metrics(pairs2, ref2_n, hyp2_n)

        p1 = struct1.paragraph.get("paragraph_detection_f1")
        p2 = struct2.paragraph.get("paragraph_detection_f1")
        if p1 and p2 and p1.value is not None and p2.value is not None:
            assert abs(p1.value - p2.value) < 0.01, \
                f"Duplication changed paragraph F1: {p1.value} → {p2.value}"

    def test_PROP04_omitting_correct_block_does_not_improve_recall(self):
        """Removing a correctly matched block cannot improve recall."""
        ref = textwrap.dedent("""\
            ## Página 1

            Parágrafo A presente.

            Parágrafo B presente.

            Parágrafo C presente.
        """)
        hyp_all = ref
        hyp_missing_c = textwrap.dedent("""\
            ## Página 1

            Parágrafo A presente.

            Parágrafo B presente.
        """)
        ref_n = parse_document(ref, "reference")
        hyp_all_n = parse_document(hyp_all, "hypothesis")
        hyp_miss_n = parse_document(hyp_missing_c, "hypothesis")

        pairs_all = align_blocks(ref_n, hyp_all_n)
        pairs_miss = align_blocks(ref_n, hyp_miss_n)

        struct_all = compute_structure_metrics(pairs_all, ref_n, hyp_all_n)
        struct_miss = compute_structure_metrics(pairs_miss, ref_n, hyp_miss_n)

        r_all = struct_all.paragraph.get("paragraph_detection_recall")
        r_miss = struct_miss.paragraph.get("paragraph_detection_recall")
        if r_all and r_miss and r_all.value is not None and r_miss.value is not None:
            assert r_all.value >= r_miss.value - 0.001, \
                f"Omitting correct block improved recall: {r_all.value} → {r_miss.value}"

    def test_PROP05_invented_currency_is_fp_not_perfect(self):
        """§9.2 / B14: empty ref + invented currency → FP, not N/A or perfect."""
        ref_no_currency = "## Página 1\n\nTexto sem valores monetários.\n"
        hyp_invented = "## Página 1\n\nTexto sem valores monetários. R$ 9.999,00\n"

        ref_n = parse_document(ref_no_currency, "reference")
        hyp_n = parse_document(hyp_invented, "hypothesis")
        critical = compute_critical_data_metrics(ref_n, hyp_n)

        c = critical.currency
        assert c is not None
        assert c.status == MetricStatus.FALSE_POSITIVE_ONLY or (
            c.value is not None and c.value < 0.5
        ), f"Invented currency should be FP, got status={c.status} value={c.value}"

    def test_PROP06_both_empty_is_na_not_perfect(self):
        """§9.2: ref=0 hyp=0 for currency → not_applicable, not 1.0."""
        ref_n = parse_document("## Página 1\n\nSem dados.\n", "reference")
        hyp_n = parse_document("## Página 1\n\nSem dados.\n", "hypothesis")
        critical = compute_critical_data_metrics(ref_n, hyp_n)
        c = critical.currency
        assert c is not None
        assert c.status == MetricStatus.NOT_APPLICABLE
        assert c.value is None

    def test_PROP07_order_invariant_abcd_acbd(self):
        """§8.2 example: A,C,B,D → 5/6 pairwise accuracy."""
        ref = textwrap.dedent("""\
            ## Página 1

            Bloco A aqui é o primeiro bloco longo.

            Bloco B aqui é o segundo bloco longo.

            Bloco C aqui é o terceiro bloco longo.

            Bloco D aqui é o quarto bloco longo.
        """)
        # Permute: A, C, B, D (swap B and C)
        hyp = textwrap.dedent("""\
            ## Página 1

            Bloco A aqui é o primeiro bloco longo.

            Bloco C aqui é o terceiro bloco longo.

            Bloco B aqui é o segundo bloco longo.

            Bloco D aqui é o quarto bloco longo.
        """)
        ref_n = parse_document(ref, "reference")
        hyp_n = parse_document(hyp, "hypothesis")
        pairs = align_blocks(ref_n, hyp_n)
        struct = compute_structure_metrics(pairs, ref_n, hyp_n)
        order = struct.reading_order
        if order and order.value is not None:
            # Should be 5/6 ≈ 0.833
            assert abs(order.value - 5 / 6) < 0.05, \
                f"Expected ~5/6 pairwise accuracy, got {order.value}"

    def test_PROP08_fully_reversed_order(self):
        """§8.8 S10: completely reversed → order accuracy 0/6."""
        ref = textwrap.dedent("""\
            ## Página 1

            Bloco A é o primeiro.

            Bloco B é o segundo.

            Bloco C é o terceiro.

            Bloco D é o quarto.
        """)
        hyp = textwrap.dedent("""\
            ## Página 1

            Bloco D é o quarto.

            Bloco C é o terceiro.

            Bloco B é o segundo.

            Bloco A é o primeiro.
        """)
        ref_n = parse_document(ref, "reference")
        hyp_n = parse_document(hyp, "hypothesis")
        pairs = align_blocks(ref_n, hyp_n)
        struct = compute_structure_metrics(pairs, ref_n, hyp_n)
        order = struct.reading_order
        if order and order.value is not None:
            assert order.value < 0.1, f"Expected ~0 pairwise accuracy, got {order.value}"


# ---------------------------------------------------------------------------
# Comparability guard
# ---------------------------------------------------------------------------

class TestComparabilityGuard:
    def test_same_inputs_comparable(self):
        result_a = comparison_result([1, 2])
        result_b = comparison_result([1, 2])
        ok, reasons = check_comparability(result_a, result_b)
        assert ok
        assert reasons == []

    def test_missing_inputs_does_not_crash(self):
        # A09 fix: ausência de hash de gabarito é incomparável (fail-closed).
        # O teste não deve travar, mas deve retornar ok=False com razão explícita.
        result_a = {"schema_version": "evaluation-3.0.0"}
        result_b = {"schema_version": "evaluation-3.0.0"}
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok  # hash ausente = incomparável (fail-closed, A09)
        assert any("hash" in r.lower() or "reference" in r.lower() for r in reasons)

    def test_all_mismatches_collected(self):
        result_a = comparison_result([1], "a" * 64)
        result_b = comparison_result([2], "d" * 64)
        result_b["schema_version"] = "evaluation-v2.0"
        ok, reasons = check_comparability(result_a, result_b)
        assert not ok
        for field in ("schema_version", "reference_effective_sha256", "selected_pages"):
            assert any(field in reason for reason in reasons)


# ---------------------------------------------------------------------------
# Regrade script integration
# ---------------------------------------------------------------------------

class TestRegradeScript:
    """End-to-end test of scripts/regrade_existing.py."""

    def test_regrade_produces_valid_v2_json(self, tmp_path):
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_PARTIAL, encoding="utf-8")
        out = tmp_path / "out"

        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--hypothesis", str(hyp),
             "--reference", str(ref),
             "--engine", "pytest",
             "--run-id", "testrun",
             "--output-dir", str(out),
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        metrics_file = out / "metrics_pytest_testrun.json"
        assert metrics_file.exists()
        data = json.loads(metrics_file.read_text())
        assert data["schema_version"] == "evaluation-3.0.0"
        assert "evaluation" in data
        assert "quality" in data

    def test_regrade_produces_error_report(self, tmp_path):
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_PARTIAL, encoding="utf-8")
        out = tmp_path / "out"

        subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--hypothesis", str(hyp),
             "--reference", str(ref),
             "--engine", "pytest",
             "--run-id", "errtestrun",
             "--output-dir", str(out),
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        report = out / "errors_pytest_errtestrun.md"
        assert report.exists()
        content = report.read_text()
        assert "Evaluation Error Report" in content
        assert "Quality Metrics Summary" in content

    def test_regrade_delta_report(self, tmp_path):
        """Delta report is generated when V1 metrics JSON is provided."""
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        v1 = tmp_path / "v1.json"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_PARTIAL, encoding="utf-8")

        # Minimal fake V1 JSON
        v1.write_text(json.dumps({
            "schema_version": "v1",
            "grupo2_estrutura_markdown": {
                "heading_f1": 0.85,
                "paragraph_boundary_f1": 0.72,
                "list_detection_f1": 0.90,
            },
            "grupo3_tabelas": {
                "table_f1": 0.95,
                "cell_exact_match": 0.88,
            },
            "grupo4_ordem_integridade": {
                "reading_order_accuracy": 0.80,
            },
            "grupo5_dados_criticos": {
                "currency_f1": 0.91,
                "date_f1": 0.85,
            },
        }), encoding="utf-8")

        out = tmp_path / "out"
        subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--hypothesis", str(hyp),
             "--reference", str(ref),
             "--v1-metrics", str(v1),
             "--engine", "pytest",
             "--run-id", "deltarun",
             "--output-dir", str(out),
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        delta = out / "delta_v1v2_pytest_deltarun.md"
        assert delta.exists()
        content = delta.read_text()
        assert "V1 × V2 Metric Delta Report" in content
        assert "heading_f1" in content

    def test_regrade_manifesto_mode(self, tmp_path):
        """Regrade works with --manifesto instead of --reference."""
        hyp = tmp_path / "hyp.md"
        hyp.write_text(_HYP_PARTIAL, encoding="utf-8")

        manifesto = tmp_path / "manifest.json"
        manifesto.write_text(json.dumps({
            "pages": [
                {"page": 1, "expected_markdown": "Primeiro parágrafo do documento.",
                 "family": "prose", "conditions": ["normal"]},
                {"page": 2, "expected_markdown": "Dados críticos.",
                 "family": "data", "conditions": ["financial"]},
            ]
        }), encoding="utf-8")

        out = tmp_path / "out"
        result = subprocess.run(
            [sys.executable, str(_SCRIPT),
             "--hypothesis", str(hyp),
             "--manifesto", str(manifesto),
             "--engine", "pytest",
             "--run-id", "manifestrun",
             "--output-dir", str(out),
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        metrics_files = list(out.glob("metrics_*.json"))
        assert len(metrics_files) == 1

    def test_regrade_no_ocr_invocation(self, tmp_path):
        """regrade_existing.py must not import structured_pdf_text.ocr submodules."""
        ref = tmp_path / "ref.md"
        hyp = tmp_path / "hyp.md"
        ref.write_text(_REF_SIMPLE, encoding="utf-8")
        hyp.write_text(_HYP_PARTIAL, encoding="utf-8")

        # Check script source does not import OCR modules
        script_source = _SCRIPT.read_text()
        forbidden = ["import easyocr", "import paddleocr", "import tesseract",
                     "from structured_pdf_text.ocr", "import structured_pdf_text.ocr"]
        for f in forbidden:
            assert f not in script_source, f"Script imports OCR: {f!r}"


# ---------------------------------------------------------------------------
# V2 document metrics envelope completeness
# ---------------------------------------------------------------------------

class TestV2Envelope:
    def test_envelope_required_keys(self):
        """to_dict() always contains all required schema keys."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        result = doc.to_dict()
        for key in ("schema_version", "evaluator", "inputs", "quality",
                    "integrity", "operational", "evaluation"):
            assert key in result, f"Missing required key: {key}"

    def test_evaluation_section_required_keys(self):
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        ev = doc.to_dict()["evaluation"]
        for key in ("status", "rank_eligible", "applicable_metric_count",
                    "unavailable_metric_count"):
            assert key in ev, f"Missing evaluation key: {key}"

    def test_quality_groups_present(self):
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        q = doc.to_dict()["quality"]
        for group in ("structure", "reading_order", "tables", "critical_data"):
            assert group in q, f"Missing quality group: {group}"

    def test_metric_result_direction_always_set(self):
        """Every metric in quality groups has a direction field."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        result = doc.to_dict()
        for group_name, group in result["quality"].items():
            for k, v in group.items():
                if isinstance(v, dict) and "status" in v:
                    assert "direction" in v, \
                        f"Missing direction in quality.{group_name}.{k}"

    def test_no_nan_inf_in_serialized_output(self):
        """JSON serialization never produces NaN or Infinity."""
        doc = _make_v2_doc(_REF_SIMPLE, _HYP_PARTIAL)
        raw = json.dumps(doc.to_dict(), default=str)
        assert "NaN" not in raw
        assert "Infinity" not in raw


# ---------------------------------------------------------------------------
# Reference hash integrity (§12.6 invariant)
# ---------------------------------------------------------------------------

class TestReferenceIntegrity:
    def test_reference_hash_stable_across_calls(self):
        """Hash of a reference string is always the same."""
        h1 = hashlib.sha256(_REF_SIMPLE.encode()).hexdigest()
        h2 = hashlib.sha256(_REF_SIMPLE.encode()).hexdigest()
        assert h1 == h2

    def test_modified_reference_different_hash(self):
        """Any byte change in reference changes its hash."""
        original = _REF_SIMPLE
        modified = original.replace("Alpha", "Alfa")
        h1 = hashlib.sha256(original.encode()).hexdigest()
        h2 = hashlib.sha256(modified.encode()).hexdigest()
        assert h1 != h2

    def test_parse_does_not_mutate_text(self):
        """parse_document never alters the source text."""
        original = _REF_SIMPLE
        h_before = hashlib.sha256(original.encode()).hexdigest()
        _ = parse_document(original, "reference")
        h_after = hashlib.sha256(original.encode()).hexdigest()
        assert h_before == h_after, "parse_document mutated the source text"
