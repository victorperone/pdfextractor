"""Exercise capture orchestration with a process, without loading OCR models."""
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import run_decoupling_baseline as runner


def test_existing_namespace_is_rejected_before_importing_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["capture", "--output-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


def test_dry_run_has_no_artifacts_and_separates_providers(tmp_path, monkeypatch, capsys):
    target = tmp_path / "new"
    monkeypatch.setattr(sys, "argv", ["capture", "--output-dir", str(target), "--dry-run"])
    assert runner.main() == 0
    commands = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(commands) == 15
    assert not target.exists()
    assert all("--allow-partial" not in command for command in commands)
    rapid = [command for command in commands if command[command.index("--engine") + 1] == "rapidocr"]
    assert {command[command.index("--provider") + 1] for command in rapid} == {"onnxruntime", "openvino"}


@pytest.mark.parametrize("exit_code,reported_partial", [(0, False), (1, False), (2, True), (0, True)])
def test_process_outcome_is_preserved(tmp_path, monkeypatch, exit_code, reported_partial):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    corpus = repo / "corpus/V5"
    corpus.mkdir(parents=True)
    (corpus / "Document_AI_V5.pdf").write_bytes(b"fixture consumed only by fake runner")
    program = '''
import json,sys
from pathlib import Path
args=sys.argv
out=Path(args[args.index('--output-dir')+1])
engine=args[args.index('--engine')+1]
run=args[args.index('--run-id')+1]
code=EXIT_CODE
partial=REPORTED_PARTIAL
if code != 1:
    md=out/'fake.md';md.write_text('## Página 1\\nValor -0,01\\n')
    manifest={'extracted_markdown':str(md),'page_count':1,
              'document_status':'partial_success' if partial else 'success',
              'benchmark_status':'partial' if partial else 'valid'}
    (out/f'manifesto_e2e_{engine}_{run}.json').write_text(json.dumps(manifest))
sys.exit(code)
'''.replace("EXIT_CODE", str(exit_code)).replace("REPORTED_PARTIAL", str(reported_partial))
    (scripts / "evaluate_e2e.py").write_text(program)

    @dataclass
    class Config:
        mode: str = "balanced"

    monkeypatch.setattr(runner, "REPO", repo)
    monkeypatch.setattr(runner, "source_files", lambda exclude: {})
    monkeypatch.setattr(runner, "git", lambda *args: "base" if args == ("rev-parse", "HEAD") else "")
    monkeypatch.setitem(sys.modules, "evaluate_e2e", SimpleNamespace(_build_config=lambda *args: Config()))
    target = tmp_path / "artifacts"
    monkeypatch.setattr(sys, "argv", ["capture", "--output-dir", str(target),
                                     "--corpora", "V5", "--configurations", "tesseract"])
    assert runner.main() == (1 if exit_code == 1 else 2 if reported_partial else 0)
    result = json.loads((target / "V5/tesseract/result.json").read_text())
    assert result["exit_code"] == exit_code
    markdown = target / "V5/tesseract/Document_AI_V5.md"
    assert markdown.exists() is (exit_code != 1)
    if exit_code != 1:
        assert "Valor -0,01" in markdown.read_text()
        assert result["benchmark_status"] == ("partial" if reported_partial else "valid")
