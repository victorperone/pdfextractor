"""Fixture integrity tests; deliberately do not test extraction quality yet."""
import json
from pathlib import Path

import importlib.util

# Keep the fixture test independent from the project's runtime package.
FIXTURES = Path(__file__).resolve().parent
if not (FIXTURES / 'Document_Text_Stress_V1.manifest.json').exists():
    FIXTURES = FIXTURES / 'corpus' / 'native_text_stress'
validator_path = FIXTURES / 'validate_native_text_stress.py'
assert validator_path.exists(), f'Corpus validator not found: {validator_path}'
spec = importlib.util.spec_from_file_location('native_text_stress_validator', validator_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
validate = module.validate



def test_native_stress_fixture_hashes_and_relations():
    result = validate(FIXTURES, check_pdf=False)
    assert result['corpus_integrity'] == 'PASS'
    assert result['pages'] == 200
    assert result['cells'] > 0


def test_native_stress_fixture_native_page_markers():
    result = validate(FIXTURES, check_pdf=True)
    assert result['pdf_case_markers_missing'] == []


def test_every_family_has_ten_deterministic_variants():
    manifest = json.loads((FIXTURES / 'Document_Text_Stress_V1.manifest.json').read_text(encoding='utf-8'))
    assert len(manifest['page_families']) == 20
    assert set(manifest['page_families'].values()) == {10}
