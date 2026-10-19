"""Immutable artifact publication: actual CLIs, concurrent writers and failures."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from structured_pdf_text.evaluation import EvaluationRequest, evaluate
from structured_pdf_text.evaluation import artifacts as module
from structured_pdf_text.evaluation.artifacts import (
    Artifact, ArtifactPublicationError, artifact_namespace, json_text,
    publication_identity, publish_artifacts, reserved_filenames,
)

ROOT = Path(__file__).resolve().parents[1]
CLIS = ["compute_metrics.py", "regrade_existing.py"]
REF = "## Página 1\n\nTexto.\n\n## Página 2\n\nOutro texto."
IDENTITY = "a" * 64


def snapshot(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()}


def prepared(tmp_path, *, engine="audit", run_id="r1"):
    for name in ("ref.md", "hyp.md"):
        (tmp_path / name).write_text(REF, encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"pages": [
        {"page": 1, "family": "texto", "conditions": ["small_text"]},
        {"page": 2, "family": "controle", "conditions": []},
    ]}))
    run = {"selected_pages": [1, 2], "pdf_sha256": "b" * 64,
           "mode": "ocr", "benchmark_protocol_id": "artifact-test-v1",
           "engine_identity": {"profile": "pt"}, "recovery_count": 0}
    (tmp_path / "run.json").write_text(json.dumps(run))
    return ["--reference", str(tmp_path / "ref.md"), "--hypothesis", str(tmp_path / "hyp.md"),
            "--manifesto", str(tmp_path / "manifest.json"), "--run-manifest", str(tmp_path / "run.json"),
            "--engine", engine, "--run-id", run_id, "--output-dir", str(tmp_path / "out"), "--quiet"]


def command(script, args):
    return [sys.executable, str(ROOT / "scripts" / script), *map(str, args)]


def cli(script, args):
    return subprocess.run(command(script, args), cwd=ROOT, text=True, capture_output=True, timeout=30)


@pytest.mark.parametrize("script", CLIS)
@pytest.mark.parametrize("change", ["hypothesis", "reference", "manifest", "scope", "pdf", "mode", "profile", "recovery", "protocol"])
def test_changed_inputs_cannot_replace_any_prior_file(tmp_path, script, change):
    args = prepared(tmp_path)
    first = cli(script, args)
    assert first.returncode == 0, first.stderr
    before = snapshot(tmp_path / "out")
    if change in {"hypothesis", "reference"}:
        name = "hyp.md" if change == "hypothesis" else "ref.md"
        (tmp_path / name).write_text(REF.replace("Texto.", "Conteúdo alterado."))
    elif change == "manifest":
        (tmp_path / "manifest.json").write_text(json.dumps({"pages": [{"page": 1, "family": "outra"}]}))
    else:
        run = json.loads((tmp_path / "run.json").read_text())
        key, value = {
            "scope": ("selected_pages", [1]), "pdf": ("pdf_sha256", "c" * 64),
            "mode": ("mode", "native"), "profile": ("engine_identity", {"profile": "outro"}),
            "recovery": ("recovery_count", 1), "protocol": ("benchmark_protocol_id", "v2"),
        }[change]
        run[key] = value
        (tmp_path / "run.json").write_text(json.dumps(run))
    second = cli(script, args)
    assert second.returncode != 0
    assert "artifact collision" in second.stderr.lower(), second.stderr
    assert snapshot(tmp_path / "out") == before


@pytest.mark.parametrize("script", CLIS)
def test_repeating_identical_inputs_preserves_all_bytes_and_mtimes(tmp_path, script):
    args = prepared(tmp_path)
    assert cli(script, args).returncode == 0
    before = snapshot(tmp_path / "out")
    assert cli(script, args).returncode == 0
    assert snapshot(tmp_path / "out") == before
    assert not list((tmp_path / "out").glob("*.lock"))


@pytest.mark.parametrize("first,second", [(CLIS[0], CLIS[1]), (CLIS[1], CLIS[0])])
@pytest.mark.parametrize("engine", ["audit", "rapidocr-onnxruntime"])
def test_both_clis_can_extend_same_publication_without_overwriting_roles(tmp_path, first, second, engine):
    args = prepared(tmp_path, engine=engine)
    assert cli(first, args).returncode == 0
    before = snapshot(tmp_path / "out")
    result = cli(second, args)
    assert result.returncode == 0, result.stderr
    after = snapshot(tmp_path / "out")
    for name, value in before.items():
        if not name.startswith(".evaluation-"):
            assert after[name] == value
    journal_path = next((tmp_path / "out").glob(".evaluation-*.json"))
    journal = json.loads(journal_path.read_text())
    roles = {entry["role"] for entry in journal["artifacts"].values()}
    assert roles == {"canonical_metrics", "legacy_metrics", "canonical_errors", "legacy_errors"}
    slug = engine.replace("-", "_")
    canonical = json.loads((tmp_path / "out" / f"metrics_v3_{slug}_r1.json").read_text())
    assert canonical["schema_version"] == "evaluation-3.0.0"
    legacy_files = [name for name, record in journal["artifacts"].items() if record["role"] == "legacy_metrics"]
    assert "grupo1_texto" in json.loads((tmp_path / "out" / legacy_files[0]).read_text())
    stable = snapshot(tmp_path / "out")
    assert cli(first, args).returncode == cli(second, args).returncode == 0
    assert snapshot(tmp_path / "out") == stable


@pytest.mark.parametrize("script", CLIS)
def test_different_input_cross_cli_cannot_claim_existing_alias(tmp_path, script):
    args = prepared(tmp_path)
    assert cli(script, args).returncode == 0
    before = snapshot(tmp_path / "out")
    (tmp_path / "hyp.md").write_text(REF.replace("Texto.", "Alterado."))
    other = CLIS[1] if script == CLIS[0] else CLIS[0]
    result = cli(other, args)
    assert result.returncode != 0 and "collision" in result.stderr
    assert snapshot(tmp_path / "out") == before


@pytest.mark.parametrize("script", CLIS)
@pytest.mark.parametrize("filename", ["metrics_audit_r1.json", "metrics_v3_audit_r1.json", "errors_audit_r1.md"])
def test_unmanaged_prior_artifacts_are_preserved_fail_closed(tmp_path, script, filename):
    args = prepared(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / filename).write_text("Prior evidence without publication journal")
    before = snapshot(out)
    result = cli(script, args)
    assert result.returncode != 0 and "unmanaged" in result.stderr.lower()
    assert snapshot(out) == before


@pytest.mark.parametrize("script", CLIS)
@pytest.mark.parametrize("identifier", ["../outside", "a/b", "a\\b", "..", "", "a:b", "a|b"])
@pytest.mark.parametrize("flag", ["--engine", "--run-id"])
def test_cli_rejects_unsafe_output_identifiers_before_creating_files(tmp_path, script, identifier, flag):
    args = prepared(tmp_path)
    args[args.index(flag)+1] = identifier
    result = cli(script, args)
    assert result.returncode != 0 and "identifier" in result.stderr.lower()
    assert not (tmp_path / "out").exists()


def test_hyphen_underscore_engine_aliases_cannot_overwrite_different_engine(tmp_path):
    args = prepared(tmp_path, engine="engine-a")
    assert cli(CLIS[0], args).returncode == 0
    before = snapshot(tmp_path / "out")
    args[args.index("--engine")+1] = "engine_a"
    assert cli(CLIS[1], args).returncode != 0
    assert snapshot(tmp_path / "out") == before


def test_joined_filename_collisions_share_publication_namespace(tmp_path):
    args = prepared(tmp_path, engine="engine_a", run_id="b")
    assert cli(CLIS[1], args).returncode == 0
    before = snapshot(tmp_path / "out")
    args[args.index("--engine")+1] = "engine"
    args[args.index("--run-id")+1] = "a_b"
    assert cli(CLIS[0], args).returncode != 0
    assert snapshot(tmp_path / "out") == before


def test_publication_identity_ignores_clock_commit_and_dictionary_order():
    result = evaluate(EvaluationRequest(ref_text="Texto.", hyp_text="Texto."))
    other = copy.deepcopy(result)
    other["evaluator"].update(created_at_utc="later", implementation_commit="another")
    a = publication_identity(result, ref_text="Texto.", hyp_text="Texto.", run_metadata={"b": 2, "a": 1})
    b = publication_identity(other, ref_text="Texto.", hyp_text="Texto.", run_metadata={"a": 1, "b": 2})
    assert a == b
    other["evaluator"]["metric_definitions_version"] = "next"
    assert a != publication_identity(other, ref_text="Texto.", hyp_text="Texto.", run_metadata={"a": 1, "b": 2})


@pytest.mark.parametrize("field", ["max_text_edit_cells", "max_alignment_pairs"])
def test_resource_policy_changes_cannot_reuse_publication_identity(field):
    result = evaluate(EvaluationRequest(ref_text="Texto.", hyp_text="Texto."))
    changed = copy.deepcopy(result)
    changed["evaluator"]["resource_policy"][field] += 1
    a = publication_identity(result, ref_text="Texto.", hyp_text="Texto.", run_metadata={})
    b = publication_identity(changed, ref_text="Texto.", hyp_text="Texto.", run_metadata={})
    assert a != b


def test_operational_page_metadata_is_part_of_artifact_identity():
    result = evaluate(EvaluationRequest(ref_text="Texto.", hyp_text="Texto."))
    a = publication_identity(result, ref_text="Texto.", hyp_text="Texto.", run_metadata={"pages": [{"page": 1, "status": "ok"}]})
    b = publication_identity(result, ref_text="Texto.", hyp_text="Texto.", run_metadata={"pages": [{"page": 1, "status": "partial"}]})
    assert a != b


def test_same_contents_at_other_source_paths_reuse_original_artifacts(tmp_path):
    args = prepared(tmp_path)
    assert cli(CLIS[1], args).returncode == 0
    before = snapshot(tmp_path / "out")
    for flag in ["--reference", "--hypothesis"]:
        source = Path(args[args.index(flag)+1])
        destination = tmp_path / f"copy_{source.name}"
        destination.write_bytes(source.read_bytes())
        args[args.index(flag)+1] = str(destination)
    assert cli(CLIS[1], args).returncode == 0
    assert snapshot(tmp_path / "out") == before


@pytest.mark.parametrize("problem", ["tamper", "missing", "bad_journal", "bad_protocol", "symlink"])
def test_damage_is_not_silently_repaired_or_overwritten(tmp_path, problem):
    args = prepared(tmp_path)
    assert cli(CLIS[1], args).returncode == 0
    out = tmp_path / "out"
    metric = out / "metrics_audit_r1.json"
    journal = next(out.glob(".evaluation-*.json"))
    if problem == "tamper":
        metric.write_text("changed outside publisher")
    elif problem == "missing":
        metric.unlink()
    elif problem == "bad_journal":
        journal.write_text("{invalid")
    elif problem == "bad_protocol":
        value = json.loads(journal.read_text())
        value["protocol"] = "unknown"
        journal.write_text(json.dumps(value))
    else:
        external = tmp_path / "external.json"
        external.write_bytes(metric.read_bytes())
        metric.unlink()
        metric.symlink_to(external)
    before = snapshot(out)
    assert cli(CLIS[1], args).returncode != 0
    assert snapshot(out) == before


@pytest.mark.parametrize("bad_delta", ["missing", "{invalid", "[]"])
def test_invalid_delta_input_is_validated_before_any_publication(tmp_path, bad_delta):
    args = prepared(tmp_path)
    delta = tmp_path / "v1.json"
    if bad_delta != "missing":
        delta.write_text(bad_delta)
    args += ["--v1-metrics", str(delta)]
    result = cli(CLIS[1], args)
    assert result.returncode != 0
    assert snapshot(tmp_path / "out") == {}


def test_delta_can_be_added_but_its_source_cannot_change(tmp_path):
    args = prepared(tmp_path)
    assert cli(CLIS[1], args).returncode == 0
    initial = snapshot(tmp_path / "out")
    v1 = tmp_path / "v1.json"
    v1.write_text(json.dumps({"heading_f1": 1}))
    args += ["--v1-metrics", str(v1)]
    assert cli(CLIS[1], args).returncode == 0
    for name, value in initial.items():
        if not name.startswith(".evaluation-"):
            assert snapshot(tmp_path / "out")[name] == value
    before = snapshot(tmp_path / "out")
    assert cli(CLIS[1], args).returncode == 0
    assert snapshot(tmp_path / "out") == before
    v1.write_text(json.dumps({"heading_f1": 0}))
    assert cli(CLIS[1], args).returncode != 0
    assert snapshot(tmp_path / "out") == before


def test_all_artifact_conflicts_are_preflighted_before_writing(tmp_path):
    (tmp_path / "occupied.md").write_text("keep")
    before = snapshot(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="Unmanaged"):
        publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "new.json", "{}"),
                                                   Artifact("b", "occupied.md", "new")])
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("fail_at", ["artifact", "journal"])
def test_io_failure_rolls_back_only_new_files_and_releases_lock(tmp_path, monkeypatch, fail_at):
    publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("prior", "prior.md", "prior")])
    before = snapshot(tmp_path)
    original = module.atomic_create_text
    def fail(path, content):
        if Path(path).name == "b.md":
            raise OSError("injected disk failure")
        return original(path, content)
    if fail_at == "artifact":
        monkeypatch.setattr(module, "atomic_create_text", fail)
    else:
        monkeypatch.setattr(module, "atomic_write_json", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "a.md", "A"), Artifact("b", "b.md", "B")])
    assert snapshot(tmp_path) == before
    assert not list(tmp_path.glob("*.lock"))


def test_busy_or_interrupted_lock_is_preserved_and_blocks_writer(tmp_path):
    lock, _ = module._paths(tmp_path, "ns")
    lock.write_text("another process or interrupted writer")
    before = snapshot(tmp_path)
    with pytest.raises(ArtifactPublicationError, match="busy or interrupted"):
        publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "a.md", "A")])
    assert snapshot(tmp_path) == before


def test_lock_is_exclusive_across_actual_processes(tmp_path):
    lock, _ = module._paths(tmp_path, "ns")
    child = """import sys
from pathlib import Path
from structured_pdf_text.evaluation.artifacts import Artifact, publish_artifacts, ArtifactPublicationError
try:
    publish_artifacts(Path(sys.argv[1]), 'ns', 'b'*64, [Artifact('a','a.md','child')])
except ArtifactPublicationError:
    sys.exit(17)
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    with module._publication_lock(lock):
        result = subprocess.run([sys.executable, "-c", child, str(tmp_path)], env=env, capture_output=True, timeout=30)
        assert result.returncode == 17, result.stderr
    publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "a.md", "parent")])
    assert (tmp_path / "a.md").read_text() == "parent"


def test_two_cli_writers_with_different_inputs_never_mix_publication(tmp_path):
    args = prepared(tmp_path)
    other_hyp = tmp_path / "other.md"
    other_hyp.write_text(REF.replace("Texto.", "Diferente."))
    other = args.copy()
    other[other.index("--hypothesis")+1] = str(other_hyp)
    a = subprocess.Popen(command(CLIS[1], args), cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    b = subprocess.Popen(command(CLIS[1], other), cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    a.communicate(timeout=30)
    b.communicate(timeout=30)
    assert sorted([a.returncode, b.returncode]) == [0, 1]
    out = tmp_path / "out"
    journal = json.loads(next(out.glob(".evaluation-*.json")).read_text())
    assert len(journal["artifacts"]) == 2
    for name, entry in journal["artifacts"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == entry["sha256"]
    assert not list(out.glob("*.lock"))


@pytest.mark.parametrize("bad_name", ["../outside", "/tmp/outside", "a\\b", ".evaluation-internal.json", "a|b", ""])
def test_publisher_rejects_unsafe_paths_before_acquiring_lock(tmp_path, bad_name):
    with pytest.raises(ArtifactPublicationError):
        publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", bad_name, "A")])
    assert snapshot(tmp_path) == {}


def test_journal_digests_describe_exact_bytes_and_alias_copy_preserves_crlf(tmp_path):
    publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "first.md", "A\r\nB\r\n")])
    publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "alias.md", "ignored new timestamp")])
    assert (tmp_path / "alias.md").read_bytes() == (tmp_path / "first.md").read_bytes() == b"A\r\nB\r\n"
    journal = json.loads(next(tmp_path.glob(".evaluation-*.json")).read_text())
    for name, entry in journal["artifacts"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == entry["sha256"]


def test_json_publication_rejects_nonfinite_values():
    for value in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValueError):
            json_text({"value": value})


def test_distinct_run_ids_publish_without_interference(tmp_path):
    args = prepared(tmp_path)
    assert cli(CLIS[1], args).returncode == 0
    before = snapshot(tmp_path / "out")
    args[args.index("--run-id")+1] = "r2"
    (tmp_path / "hyp.md").write_text(REF.replace("Texto.", "Outro resultado."))
    assert cli(CLIS[1], args).returncode == 0
    for name, value in before.items():
        assert snapshot(tmp_path / "out")[name] == value
    assert (tmp_path / "out/metrics_audit_r2.json").is_file()


def test_reserved_names_cover_both_cli_naming_conventions():
    assert artifact_namespace("engine-a", "r1") == artifact_namespace("engine_a", "r1")
    names = reserved_filenames("engine-a", "r1")
    assert {"metrics_engine-a_r1.json", "metrics_engine_a_r1.json", "metrics_v3_engine_a_r1.json"} <= names


def test_new_file_creation_never_replaces_a_racing_writer(tmp_path, monkeypatch):
    from structured_pdf_text import atomic_io
    original = atomic_io.os.link
    target = tmp_path / "target.txt"
    def race(source, destination):
        # Another process wins after publication's existence preflight.
        Path(destination).write_bytes(b"other writer")
        return original(source, destination)
    monkeypatch.setattr(atomic_io.os, "link", race)
    with pytest.raises(FileExistsError):
        atomic_io.atomic_create_text(target, "our content")
    assert target.read_bytes() == b"other writer"
    assert list(tmp_path.iterdir()) == [target]


def test_cross_namespace_filename_race_preserves_winner_and_rolls_back_loser(tmp_path, monkeypatch):
    from structured_pdf_text import atomic_io
    original = atomic_io.os.link
    target = tmp_path / "shared.md"
    def race(source, destination):
        if Path(destination) == target:
            target.write_bytes(b"winner")
        return original(source, destination)
    monkeypatch.setattr(atomic_io.os, "link", race)
    with pytest.raises(FileExistsError):
        publish_artifacts(tmp_path, "loser", IDENTITY,
                          [Artifact("a", "a.md", "ours"), Artifact("b", "shared.md", "ours")])
    assert snapshot(tmp_path).keys() == {"shared.md"}
    assert target.read_bytes() == b"winner"


def test_unsupported_atomic_link_fails_without_leaving_partial_files(tmp_path, monkeypatch):
    from structured_pdf_text import atomic_io
    def unsupported(*args):
        raise OSError("filesystem does not support hard links")
    monkeypatch.setattr(atomic_io.os, "link", unsupported)
    with pytest.raises(OSError):
        publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("a", "a.md", "ours")])
    assert snapshot(tmp_path) == {}


@pytest.mark.parametrize("change", ["quality", "integrity"])
def test_changed_canonical_result_with_same_identity_cannot_mix_old_metrics_with_new_reports(tmp_path, change):
    result = evaluate(EvaluationRequest(ref_text="Texto.", hyp_text="Texto."))
    publish_artifacts(tmp_path, "ns", IDENTITY, [Artifact("canonical_metrics", "metrics.json", json_text(result))])
    before = snapshot(tmp_path)
    changed = copy.deepcopy(result)
    changed[change]["unexpected_new_result"] = 0
    with pytest.raises(ArtifactPublicationError, match="Canonical result changed"):
        publish_artifacts(tmp_path, "ns", IDENTITY, [
            Artifact("canonical_metrics", "metrics.json", json_text(changed)),
            Artifact("delta_report", "delta.md", "new report based on changed result"),
        ])
    assert snapshot(tmp_path) == before
