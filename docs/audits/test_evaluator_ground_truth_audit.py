"""Independent requirements probes: Markdown only, no PDF or OCR calls."""
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from scripts.regrade_existing import _run_v2
from scripts.compare_engines import _render_comparison_table_v2, _validate_comparability_v2
from structured_pdf_text.evaluation import parse_document, CanonicalDocument
from structured_pdf_text.evaluation.aggregation import aggregate_metric, check_comparability
from structured_pdf_text.evaluation.models import MetricResult, MetricStatus
from structured_pdf_text.evaluation.critical_data import compute_critical_data_metrics
from structured_pdf_text.evaluation.integrity_metrics import markdown_validity_report


def evaluate(tmp_path, reference, hypothesis, selected=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rp, hp = tmp_path / 'ref.md', tmp_path / 'hyp.md'
    rp.write_text(reference, encoding='utf-8')
    hp.write_text(hypothesis, encoding='utf-8')
    return _run_v2(reference, hypothesis, ref_path=rp, hyp_path=hp,
                   manifesto_path=None, engine='audit', run_id='r1',
                   selected_page_scope=selected).to_dict()


def cli(script, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *map(str, args)],
                          cwd=ROOT, text=True, capture_output=True, timeout=30)


TABLE = '| Nome | Valor |\n|---|---|\n| Ana | 100 |'
TWO_PAGES = '## Página 1\n\nTexto correto.\n\n## Página 2\n\n# Conteúdo fora do recorte\n\nR$ 999,00'


def test_selected_page_scope_applies_to_quality(tmp_path):
    d = evaluate(tmp_path, TWO_PAGES, '## Página 1\n\nTexto correto.', [1])
    assert d['quality']['text']['cer_sequential_normalized']['value'] == 0


def test_selected_page_scope_applies_to_missing_page_rate(tmp_path):
    d = evaluate(tmp_path, TWO_PAGES, '## Página 1\n\nTexto correto.', [1])
    assert d['inputs']['selected_missing_pages'] == []
    assert d['integrity']['missing_page_rate']['value'] == 0


def test_same_effective_reference_hash_implies_same_evaluated_reference(tmp_path):
    h = '## Página 1\n\nTexto correto.'
    a = evaluate(tmp_path / 'a', TWO_PAGES, h, [1])
    b = evaluate(tmp_path / 'b', TWO_PAGES.replace('Conteúdo fora do recorte', 'ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ'), h, [1])
    assert a['inputs']['reference_effective_sha256'] == b['inputs']['reference_effective_sha256']
    assert a['quality'] == b['quality']


def test_missing_whole_table_has_full_cell_deletion(tmp_path):
    d = evaluate(tmp_path, '## Página 1\n\n' + TABLE, '## Página 1\n\n')
    assert d['quality']['tables']['cell_cer']['value'] == 1


def test_missing_table_row_reduces_cell_accuracy(tmp_path):
    d = evaluate(tmp_path, TABLE + '\n| Bruno | 999 |', TABLE)
    assert d['quality']['tables']['cell_content_exact_match']['value'] < 1


def test_missing_table_row_increases_cell_cer(tmp_path):
    d = evaluate(tmp_path, TABLE + '\n| Bruno | 999 |', TABLE)
    assert d['quality']['tables']['cell_cer']['value'] > 0


def test_raw_markdown_cer_sees_bold_delimiters(tmp_path):
    d = evaluate(tmp_path, '**importante**', 'importante')
    assert d['quality']['text']['cer_markdown_raw']['value'] > 0


def test_visible_text_keeps_adjacent_inline_text_together(tmp_path):
    d = evaluate(tmp_path, 'a**b**c', 'abc')
    assert d['quality']['text']['cer_sequential_visible']['value'] == 0


def test_visible_text_preserves_literal_code_characters(tmp_path):
    d = evaluate(tmp_path, '```\n**literal**\n```', '```\nliteral\n```')
    assert d['quality']['text']['cer_sequential_visible']['value'] > 0


def test_aligned_cer_does_not_count_table_ancestors(tmp_path):
    d = evaluate(tmp_path, TABLE, TABLE)
    canonical = CanonicalDocument.from_nodes(parse_document(TABLE))
    leaf_chars = sum(len(n.visible_text) for n in canonical.leaf_nodes)
    assert d['quality']['text']['aligned_block_text_cer']['counts']['ref_chars'] == leaf_chars


def test_changed_paragraph_boundaries_are_penalised(tmp_path):
    d = evaluate(tmp_path, 'Long paragraph one\n\nShort', 'Long\n\nparagraph one Short')
    assert d['quality']['structure']['paragraph_detection_f1']['value'] < 1


def test_inverted_pages_are_not_perfect_reading_order(tmp_path):
    ref = '## Página 1\n\nAlfa.\n\n## Página 2\n\nBeta.'
    hyp = '## Página 2\n\nBeta.\n\n## Página 1\n\nAlfa.'
    d = evaluate(tmp_path, ref, hyp)
    assert d['quality']['reading_order']['pairwise_reading_order_accuracy']['value'] < 1


def test_ordered_list_changed_start_is_measured(tmp_path):
    d = evaluate(tmp_path, '1. Primeiro\n2. Segundo', '5. Primeiro\n6. Segundo')
    assert d['quality']['structure']['ordered_list_ordinal_accuracy']['value'] == 0


def test_page_marker_in_fenced_code_is_literal():
    nodes = parse_document('## Página 1\n\n```markdown\n## Página 2\n```')
    assert {n.page for n in nodes} == {1}


@pytest.mark.parametrize('status', [MetricStatus.FALSE_NEGATIVE_ONLY, MetricStatus.FALSE_POSITIVE_ONLY])
def test_macro_keeps_zero_f1_from_missing_or_invented_entities(status):
    metrics = [MetricResult(value=1.0, status=MetricStatus.OK, direction='higher_is_better'),
               MetricResult(value=0.0, status=status, direction='higher_is_better')]
    assert aggregate_metric(metrics).macro_value == 0.5


def test_public_comparator_rejects_different_pdfs():
    a = {'schema_version': 'evaluation-3.0.0', 'inputs': {
        'reference_effective_sha256': 'a' * 64,
        'reference_serialization_version': 'reference-effective-v1',
        'selected_pages': [1], 'pdf_sha256': 'b' * 64}}
    b = copy.deepcopy(a)
    b['inputs']['pdf_sha256'] = 'c' * 64
    assert check_comparability(a, b)[0] is False


def test_public_comparator_rejects_missing_hash_protocol():
    a = {'schema_version': 'evaluation-3.0.0', 'inputs': {
        'reference_effective_sha256': 'a' * 64, 'selected_pages': [1]}}
    assert check_comparability(a, a)[0] is False


def test_manifest_without_ground_truth_is_rejected(tmp_path):
    h, m = tmp_path / 'hyp.md', tmp_path / 'manifest.json'
    h.write_text('## Página 1\n\n', encoding='utf-8')
    m.write_text(json.dumps({'pages': [{'page': 1, 'family': 'metadata_only'}]}))
    result = cli('regrade_existing.py', '--hypothesis', h, '--manifesto', m,
                 '--engine', 'audit', '--run-id', 'r1', '--output-dir', tmp_path / 'out', '--quiet')
    assert result.returncode != 0


def test_canonical_failure_does_not_return_cli_success(tmp_path):
    h, r, out = tmp_path / 'hyp.md', tmp_path / 'ref.md', tmp_path / 'out'
    h.write_text('## Página 1\n\nTexto.\n\n## Página 1\n\nTexto.', encoding='utf-8')
    r.write_text('## Página 1\n\nTexto.', encoding='utf-8')
    result = cli('compute_metrics.py', '--hypothesis', h, '--reference', r,
                 '--engine', 'audit', '--run-id', 'r1', '--output-dir', out, '--quiet')
    assert result.returncode != 0 or (out / 'metrics_v3_audit_r1.json').exists()


def test_regrade_keeps_run_stability_metadata(tmp_path):
    h, r, m, out = tmp_path / 'hyp.md', tmp_path / 'ref.md', tmp_path / 'run.json', tmp_path / 'out'
    h.write_text('## Página 1\n\nTexto.', encoding='utf-8')
    r.write_text(h.read_text(), encoding='utf-8')
    m.write_text(json.dumps({'selected_pages': [1], 'benchmark_status': 'partial',
                            'stability_status': 'recovered', 'recovery_count': 3,
                            'benchmark_protocol_id': 'p1'}))
    result = cli('regrade_existing.py', '--hypothesis', h, '--reference', r, '--run-manifest', m,
                 '--engine', 'audit', '--run-id', 'r1', '--output-dir', out, '--quiet')
    assert result.returncode == 0, result.stderr
    d = json.loads((out / 'metrics_audit_r1.json').read_text())
    assert d['operational']['benchmark_status'] == 'partial'
    assert d['operational']['recovery_count'] == 3


def test_canonical_renderer_shows_run_outcomes(tmp_path):
    d = evaluate(tmp_path, 'Texto.', 'Texto.')
    d['operational'].update(stability_status='recovered', recovery_count=3,
                            benchmark_status='partial', per_page=[{'page': 1, 'conditions': ['blur']}])
    report = _render_comparison_table_v2([('audit', d)])
    assert 'recovered' in report and 'partial' in report


def test_raw_reference_identity_hash_is_platform_stable():
    from structured_pdf_text.evaluation.reference_hash import compute_reference_effective_hash
    assert compute_reference_effective_hash({1: 'Ação\r\n'})[0] == compute_reference_effective_hash({1: 'Ac\u0327a\u0303o\n'})[0]


def test_critical_data_penalises_cross_page_relocation():
    ref = parse_document('## Página 1\n\nR$ 100,00\n\n## Página 2\n\nTexto.', origin='reference')
    hyp = parse_document('## Página 1\n\nTexto.\n\n## Página 2\n\nR$ 100,00', origin='hypothesis')
    m = compute_critical_data_metrics(ref, hyp)
    assert m.currency.value == 1 and m.currency_page.value == 0


def test_critical_percent_is_distinct_from_plain_number():
    m = compute_critical_data_metrics(parse_document('30%'), parse_document('30'))
    assert m.percent.value == 0 and m.numeric.value == 0


def test_cli_comparator_rejects_different_pdfs():
    a = {'schema_version': 'evaluation-3.0.0', 'inputs': {
        'reference_effective_sha256': 'a' * 64, 'reference_serialization_version': 'reference-effective-v1',
        'selected_pages': [1], 'pdf_sha256': 'b' * 64, 'manifest_sha256': 'c' * 64, 'mode': 'ocr'},
        'operational': {'benchmark_protocol_id': 'p1'}}
    b = copy.deepcopy(a)
    b['inputs']['pdf_sha256'] = 'd' * 64
    assert _validate_comparability_v2([('a', a), ('b', b)])


def test_legacy_missing_table_penalty_is_fixed():
    from scripts.compute_metrics import compute_table_metrics
    m = compute_table_metrics('', TABLE)
    assert m['cell_cer'] == 1 and m['cell_exact_match'] == 0


def test_legacy_perfect_table_with_negative_page_keeps_f1():
    from scripts.compute_metrics import compute_table_metrics, aggregate_table_metrics_from_pages
    m = aggregate_table_metrics_from_pages([{'page': 1, **compute_table_metrics('', '')},
        {'page': 2, **compute_table_metrics(TABLE, TABLE)}], set(), {1: '', 2: TABLE})
    assert m['table_f1'] == 1 and m['cell_exact_match'] == 1


def test_html_table_ground_truth_is_not_discarded(tmp_path):
    ref = '## Página 1\n\n<table><tr><td>R$ 100,00</td></tr></table>'
    d = evaluate(tmp_path, ref, '## Página 1\n\n')
    assert d['quality']['text']['cer_sequential_normalized']['value'] == 1
    assert d['quality']['critical_data']['currency']['value'] == 0


def test_valid_four_backtick_fence_with_literal_triples_is_valid():
    text = '````markdown\n```\n````'
    assert markdown_validity_report(text).validity_status == 'valid'


@pytest.mark.parametrize('canonical', [False, True])
def test_produced_metric_artifact_can_be_used_by_runner_comparator(tmp_path, canonical):
    h, r, m, rm, out = (tmp_path / name for name in ('hyp.md', 'ref.md', 'manifest.json', 'run.json', 'out'))
    h.write_text('## Página 1\n\nTexto.', encoding='utf-8')
    r.write_text(h.read_text(), encoding='utf-8')
    m.write_text(json.dumps({'pages': [{'page': 1, 'expected_markdown': 'Texto.'}]}))
    rm.write_text(json.dumps({'selected_pages': [1], 'pdf_sha256': 'a' * 64,
                             'mode': 'ocr', 'benchmark_protocol_id': 'p1'}))
    paths = []
    for engine in ('engine_a', 'engine_b'):
        result = cli('compute_metrics.py', '--hypothesis', h, '--reference', r,
                     '--manifesto', m, '--run-manifest', rm,
                     '--engine', engine, '--run-id', 'r1', '--output-dir', out, '--quiet')
        assert result.returncode == 0, result.stderr
        prefix = 'metrics_v3' if canonical else 'metrics'
        paths.append(out / f'{prefix}_{engine}_r1.json')
    result = cli('compare_engines.py', *paths, '--output', out / 'comparison.md', '--quiet')
    if canonical:
        assert result.returncode == 0, result.stderr
    else:
        # Legacy exports must stay rejected. Runner selection is now covered
        # by tests/test_evaluation_input_contract.py, including a Bash loop.
        assert result.returncode != 0
        assert 'schema' in result.stderr.lower()


def test_compute_and_regrade_accept_same_implicit_single_page_input(tmp_path):
    h, r, out = tmp_path / 'hyp.md', tmp_path / 'ref.md', tmp_path / 'out'
    h.write_text('Texto sem marcador.', encoding='utf-8')
    r.write_text(h.read_text(), encoding='utf-8')
    args = ['--hypothesis', h, '--reference', r, '--engine', 'audit',
            '--run-id', 'r1', '--output-dir', out, '--quiet']
    regrade = cli('regrade_existing.py', *args)
    compute = cli('compute_metrics.py', *args)
    assert regrade.returncode == 0, regrade.stderr
    assert compute.returncode == 0, compute.stderr


def test_same_run_id_with_different_input_preserves_prior_artifact(tmp_path):
    h, r, out = tmp_path / 'hyp.md', tmp_path / 'ref.md', tmp_path / 'out'
    h.write_text('## Página 1\n\nTexto.', encoding='utf-8')
    r.write_text(h.read_text(), encoding='utf-8')
    args = ['--hypothesis', h, '--reference', r, '--engine', 'audit',
            '--run-id', 'r1', '--output-dir', out, '--quiet']
    assert cli('regrade_existing.py', *args).returncode == 0
    first = (out / 'metrics_audit_r1.json').read_bytes()
    h.write_text('## Página 1\n\nConteúdo alterado.', encoding='utf-8')
    result = cli('regrade_existing.py', *args)
    assert result.returncode != 0 or (out / 'metrics_audit_r1.json').read_bytes() == first


def test_compute_manifest_with_embedded_marker_produces_canonical_artifact(tmp_path):
    h, m, out = tmp_path / 'hyp.md', tmp_path / 'manifest.json', tmp_path / 'out'
    text = '## Página 1\n\nTexto.'
    h.write_text(text, encoding='utf-8')
    m.write_text(json.dumps({'pages': [{'page': 1, 'expected_markdown': text}]}))
    result = cli('compute_metrics.py', '--hypothesis', h, '--manifesto', m,
                 '--engine', 'audit', '--run-id', 'r1', '--output-dir', out, '--quiet')
    assert result.returncode == 0, result.stderr
    assert (out / 'metrics_v3_audit_r1.json').exists()


def test_parser_identity_reports_installed_version():
    from importlib.metadata import version
    from structured_pdf_text.evaluation.markdown_parser import PARSER_VERSION
    assert version('markdown-it-py') in PARSER_VERSION


def test_legacy_parser_keeps_empty_structural_table_row():
    from scripts.compute_metrics import compute_table_metrics
    table = '| Campo | Valor |\n| --- | --- |\n| | |'
    assert compute_table_metrics(table, table)['cell_ref_count'] == 4


def test_legacy_parser_keeps_escaped_pipe_inside_one_cell():
    from scripts.compute_metrics import _parse_md_tables
    assert len(_parse_md_tables('| A \\| B | C |\n|---|---|\n')[0][0]) == 2


def test_legacy_heading_cer_sees_text_error():
    from scripts.compute_metrics import compute_structure_metrics
    d = compute_structure_metrics('# Titulo correto', '# Título correto')
    assert d['heading_text_cer'] is not None and d['heading_text_cer'] > 0
