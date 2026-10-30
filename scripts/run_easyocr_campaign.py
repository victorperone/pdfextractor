#!/usr/bin/env python3
"""Sequential EasyOCR campaign with metrics over V3 through V8."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
from structured_pdf_text.evaluation.reference_inputs import document_pages, manifest_pages, pages_markdown
from structured_pdf_text.evaluation.validation import validate_result

CORPORA = ('V3', 'V4', 'V5', 'V6', 'V7', 'V8')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sources():
    paths = [path for directory in ('src', 'scripts') for path in (ROOT/directory).rglob('*.py')]
    paths += [ROOT/name for name in ('pyproject.toml', 'uv.lock', 'constraints-ocr-gpu-linux-py312.txt')]
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)}


def metric_count(data):
    return sum(isinstance(item, dict) and 'value' in item and 'status' in item
               for values in (*data['quality'].values(), data['integrity']) for item in values.values())


def inputs(corpora=CORPORA):
    result = {}
    for version in corpora:
        folder = ROOT/'corpus'/version
        pdfs, manifests = sorted(folder.glob('*.pdf')), sorted(folder.glob('*MANIFESTO.json'))
        if len(pdfs) != 1 or len(manifests) != 1:
            raise ValueError(f'{version}: expected exactly one PDF and manifesto')
        refs = sorted(folder.glob('*REFERENCIA.md'))
        reference = refs[0] if len(refs) == 1 else folder/'Document_AI_V3.md'
        if not reference.is_file():
            raise ValueError(f'{version}: reference missing or ambiguous')
        result[version] = (pdfs[0], manifests[0], reference)
    return result


class Campaign:
    def __init__(self, output, selected, *, corpora=CORPORA):
        self.output, self.selected = output, selected
        self.corpora = tuple(corpora)
        self.python = ROOT/'.ocr-envs/gpu-ready-20261022/easyocr/bin/python'
        self.env = dict(os.environ, PYTHONHASHSEED='0', PYTHONUNBUFFERED='1',
                        EASYOCR_MODULE_PATH=str(ROOT/'.ocr-model-cache/easyocr'), EASYOCR_ALLOW_DOWNLOAD='0')
        self.report = dict(status='running', started_utc=datetime.now(timezone.utc).isoformat(),
            engine='easyocr', use_gpu=True, mode='balanced', language='pt-BR', quality_policy='adaptive',
            progress='monitored', output_format='markdown', corpora=list(self.corpora),
            excluded_corpora=[version for version in CORPORA if version not in self.corpora],
            expected_documents=len(self.corpora), max_parallel_extractions=1, metrics_workers=1,
            timing_note='Extraction and metrics run sequentially per document; timings remain observational.',
            source_hashes=sources(), runs={}, quality_accepted=False, release_accepted=False)

    def save(self):
        target = self.output/'campaign.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2)+'\n')
        temporary.replace(target)

    def execute(self, command, log):
        with log.open('xb') as stream:
            with subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT) as process:
                while chunk := os.read(process.stdout.fileno(), 65536):
                    stream.write(chunk)
                    stream.flush()
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                return process.wait()

    def preflight(self):
        proof = self.output/'gpu-preflight'
        code = self.execute([str(self.python), 'scripts/smoke_ocr_gpu.py', '--engine', 'easyocr',
            '--cache-home', str(ROOT/'.ocr-model-cache'), '--output-dir', str(proof)],
            self.output/'gpu-preflight.log')
        probe = json.loads((proof/'report.json').read_text())
        self.report['gpu_preflight'] = dict(exit_code=code, passed=probe.get('gpu_execution_accepted'),
                                             report=str(proof/'report.json'))
        self.save()
        if code or not probe.get('gpu_execution_accepted'):
            raise RuntimeError('Actual CUDA preflight failed; this campaign requires verified GPU execution')

    def extract(self, version):
        pdf, corpus_manifest, original_ref = self.selected[version]
        target = self.output/version
        target.mkdir()
        corpus = json.loads(corpus_manifest.read_text())
        expected = list(range(1, corpus['page_count']+1))
        reference = original_ref
        if set(document_pages(original_ref.read_text())) != set(expected):
            reference = target/'reference_effective.md'
            reference.write_text(pages_markdown(manifest_pages(corpus)), encoding='utf-8')
        run_id = f'{self.output.name}-{version.lower()}-gpu-balanced-adaptive'
        run = dict(run_id=run_id, pdf=str(pdf), pdf_sha256=sha(pdf), corpus_manifest=str(corpus_manifest),
            manifest_sha256=sha(corpus_manifest), reference_original=str(original_ref),
            reference_original_sha256=sha(original_ref), reference_effective=str(reference),
            reference_effective_sha256=sha(reference), expected_pages=len(expected),
            extraction_status='running', metrics_status='waiting')
        self.report['runs'][version] = run
        self.save()
        print(f'\n{version}: extração iniciada', flush=True)
        command = [str(self.python), '-u', 'scripts/evaluate_e2e.py', str(pdf), '--engine', 'easyocr',
            '--gpu', '--mode', 'balanced', '--language', 'pt-BR', '--save-features',
            '--output-dir', str(target), '--run-id', run_id]
        run['command'] = command
        started = time.monotonic()
        code = self.execute(command, target/'extraction.log')
        run.update(extraction_exit=code, extraction_wall_s=time.monotonic()-started)
        manifest = target/f'manifesto_e2e_easyocr_{run_id}.json'
        if not manifest.exists():
            run['extraction_status'] = 'failed'
            self.save()
            raise RuntimeError(f'{version}: extraction produced no manifest (exit {code})')
        data = json.loads(manifest.read_text())
        run.update(document_status=data.get('document_status'), benchmark_status=data.get('benchmark_status'),
            page_count=data.get('page_count'), device=data.get('engine_identity', {}).get('device'),
            engine_identity=data.get('engine_identity'), extraction_status='saved' if code in (0, 2) else 'failed')
        run['configuration_verified'] = (data.get('quality_policy') == 'adaptive' and data.get('mode') == 'balanced'
            and data.get('language') == 'pt-BR' and run['device'] == 'cuda' and data.get('selected_pages') == expected)
        run['input_unchanged'] = (sha(pdf) == run['pdf_sha256'] and sha(corpus_manifest) == run['manifest_sha256']
                                 and sha(original_ref) == run['reference_original_sha256']
                                 and sha(reference) == run['reference_effective_sha256'])
        self.save()
        return run

    def grade(self, version, run):
        target, run_id = self.output/version, run['run_id']
        run['metrics_status'] = 'running'
        self.save()
        command = [str(ROOT/'.venv/bin/python'), 'scripts/compute_metrics.py',
            '--hypothesis', str(target/f'extracted_easyocr_{run_id}.md'), '--reference', run['reference_effective'],
            '--manifesto', run['corpus_manifest'], '--run-manifest', str(target/f'manifesto_e2e_easyocr_{run_id}.json'),
            '--engine', 'easyocr', '--run-id', run_id, '--output-dir', str(target), '--supplemental-audit']
        run['compute_metrics_exit'] = self.execute(command, target/'compute_metrics.log')
        canonical = target/f'metrics_v3_easyocr_{run_id}.json'
        run['metrics_status'] = 'failed'
        if canonical.exists():
            data = json.loads(canonical.read_text())
            validate_result(data)
            run.update(canonical_metrics=str(canonical), canonical_metric_count=metric_count(data),
                evaluation_status=data['evaluation']['status'], rank_eligible=data['evaluation']['rank_eligible'])
            if (run['compute_metrics_exit'] == 0 and metric_count(data) == 69
                    and len(data['per_page']) == run['expected_pages']
                    and all(metric_count(page) == 69 for page in data['per_page'])
                    and (target/f'audit_easyocr_{run_id}.json').exists()):
                run['metrics_status'] = 'complete'
        self.save()
        print(f"\n{version}: extração {run['extraction_status']}; métricas {run['metrics_status']}", flush=True)

    def run(self):
        self.save()
        try:
            self.preflight()
            for version in self.corpora:
                self.grade(version, self.extract(version))
            self.report['source_hashes_after'] = sources()
            self.report['source_unchanged'] = self.report['source_hashes_after'] == self.report['source_hashes']
            valid = self.report['source_unchanged'] and len(self.report['runs']) == len(self.corpora) and all(
                run.get('extraction_exit') == 0 and run.get('metrics_status') == 'complete'
                and run.get('configuration_verified') and run.get('input_unchanged')
                and run.get('evaluation_status') == 'ok' and run.get('rank_eligible')
                for run in self.report['runs'].values())
            self.report['status'] = 'complete' if valid else 'completed_with_issues'
        except Exception as exc:
            self.report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        finally:
            self.report['finished_utc'] = datetime.now(timezone.utc).isoformat()
            self.save()
        print(self.report['status'], flush=True)
        return 0 if self.report['status'] == 'complete' else 2 if self.report['status'] == 'completed_with_issues' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true', help='Show planned documents without extracting')
    parser.add_argument('--corpora', nargs='+', choices=CORPORA, default=list(CORPORA),
                        help='Optional subset; documents always run in V3 through V8 order')
    args = parser.parse_args(argv)
    corpora = tuple(version for version in CORPORA if version in args.corpora)
    selected = inputs(corpora)
    if args.dry_run:
        print(json.dumps(dict(output=str(args.output_dir.resolve()), corpora=list(corpora),
                              excluded=[version for version in CORPORA if version not in corpora],
                              mode='balanced', language='pt-BR', engine='easyocr', quality_policy='adaptive',
                              use_gpu=True, progress='monitored', metrics=69, supplemental_audit=True), indent=2))
        return 0
    output = args.output_dir.resolve()
    if (output/'campaign.json').exists() or any((output/version).exists() for version in CORPORA):
        parser.error('Campaign artifacts already exist; refusing to overwrite')
    output.mkdir(parents=True, exist_ok=True)
    return Campaign(output, selected, corpora=corpora).run()


if __name__ == '__main__':
    raise SystemExit(main())
