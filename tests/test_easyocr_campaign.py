"""The prepared campaign includes V3 through V8 and grades in order."""
import importlib.util
import json
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[1]/'scripts/run_easyocr_campaign.py'
    spec = importlib.util.spec_from_file_location('easyocr_campaign', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_dry_run_includes_v4_without_creating_campaign(tmp_path, monkeypatch, capsys):
    campaign = module()
    monkeypatch.setattr(campaign, 'inputs', lambda corpora: {})
    output = tmp_path/'005'
    assert campaign.main(['--output-dir', str(output), '--dry-run']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['corpora'] == ['V3', 'V4', 'V5', 'V6', 'V7', 'V8']
    assert plan['excluded'] == []
    assert plan['metrics'] == 69 and plan['supplemental_audit']
    assert not output.exists()


def test_every_selected_document_is_extracted_and_graded_sequentially(tmp_path, monkeypatch):
    campaign = module()
    monkeypatch.setattr(campaign, 'sources', lambda: {})
    run = campaign.Campaign(tmp_path, {})
    events = []
    monkeypatch.setattr(run, 'preflight', lambda: None)

    def extract(version):
        events.append(('extract', version))
        value = dict(extraction_exit=0, configuration_verified=True, input_unchanged=True)
        run.report['runs'][version] = value
        return value

    def grade(version, value):
        events.append(('grade', version))
        value.update(metrics_status='complete', evaluation_status='ok', rank_eligible=True)

    monkeypatch.setattr(run, 'extract', extract)
    monkeypatch.setattr(run, 'grade', grade)
    assert run.run() == 0
    assert events == [(stage, version) for version in ['V3', 'V4', 'V5', 'V6', 'V7', 'V8']
                      for stage in ('extract', 'grade')]
    assert run.report['status'] == 'complete'


def test_explicit_subset_keeps_historical_scope_and_canonical_order(tmp_path, monkeypatch, capsys):
    campaign = module()
    monkeypatch.setattr(campaign, 'inputs', lambda corpora: {})
    assert campaign.main(['--output-dir', str(tmp_path/'004'), '--dry-run',
                          '--corpora', 'V8', 'V3', 'V5', 'V6', 'V7']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['corpora'] == ['V3', 'V5', 'V6', 'V7', 'V8']
    assert plan['excluded'] == ['V4']


def test_grading_requires_all_metrics_pages_and_actual_supplemental_artifact(tmp_path, monkeypatch):
    campaign = module()
    monkeypatch.setattr(campaign, 'sources', lambda: {})
    monkeypatch.setattr(campaign, 'validate_result', lambda data: None)  # schema has its own test suite
    runner = campaign.Campaign(tmp_path, {})
    target = tmp_path/'V3'
    target.mkdir()
    metrics = {'quality': {'text': {f'metric{i}': dict(value=1., status='ok') for i in range(69)}},
               'integrity': {}, 'evaluation': dict(status='ok', rank_eligible=True)}
    metrics['per_page'] = [{key: metrics[key] for key in ['quality', 'integrity']}]

    def execute(command, log):
        (target/'metrics_v3_easyocr_test.json').write_text(json.dumps(metrics))
        # Match the canonical evaluator's supplemental filename, not a runner-specific alias.
        (target/'audit_easyocr_test.json').write_text('{}')
        return 0

    monkeypatch.setattr(runner, 'execute', execute)
    info = dict(run_id='test', reference_effective='ref.md', corpus_manifest='manifest.json',
                expected_pages=1, extraction_status='saved')
    runner.grade('V3', info)
    assert info['metrics_status'] == 'complete'
    info['expected_pages'] = 2
    runner.grade('V3', info)
    assert info['metrics_status'] == 'failed'
