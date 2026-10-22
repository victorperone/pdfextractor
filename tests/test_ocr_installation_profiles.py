"""Deployment installation must preserve existing environments and cv2 ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import check_ocr_install as checker
from scripts import preflight_ocr_backends as preflight
from scripts import setup_ocr_environments as setup


def distribution(name, version="4.10.0.84"):
    return SimpleNamespace(metadata={"Name": name}, version=version)


@pytest.mark.parametrize("names,profile,pip_code,passed", [
    (["opencv_python_headless"], "easyocr", 0, True),
    (["opencv-python"], "rapidocr-onnxruntime", 0, True),
    (["opencv-contrib-python"], "paddle", 0, True),
    (["opencv-contrib-python"], "easyocr", 0, False),
    (["opencv-python", "opencv-python-headless"], None, 0, False),
    (["opencv-python", "opencv_python"], None, 0, False),
    ([], None, 0, False),
    (["opencv-contrib-python"], None, 1, False),
])
def test_distribution_ownership_and_dependency_metadata(monkeypatch, names, profile, pip_code, passed):
    monkeypatch.setattr(checker.metadata, "distributions", lambda: map(distribution, names))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=pip_code, stdout="missing dependencies" if pip_code else "ok", stderr="")
    monkeypatch.setattr(checker.subprocess, "run", run)
    report = checker.inspect_install(profile)
    assert report["passed"] is passed
    assert report["pip_check"]["returncode"] == pip_code
    assert len(calls) == 1 and calls[0][-2:] == ["pip", "check"]
    assert not report["models_checked"] and not report["quality_accepted"]


def test_runtime_imports_run_in_separate_processes_and_record_crash(monkeypatch):
    monkeypatch.setattr(checker.metadata, "distributions", lambda: [distribution("opencv-python")])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert "timeout" not in kwargs
        return SimpleNamespace(returncode=-11 if "import_module('paddle')" in command[-1] else 0,
                               stdout="", stderr="crash" if len(calls) == 4 else "")
    monkeypatch.setattr(checker.subprocess, "run", run)
    report = checker.inspect_install(None, imports=True)
    assert not report["passed"]
    assert len(calls) == 1 + len(checker.PROFILE_MODULES)
    probes = {p["configuration"]: p for p in report["import_probes"]}
    assert probes["paddle"]["returncode"] == -11
    assert probes["easyocr"]["returncode"] == 0
    for command in calls[1:]:
        assert command[:3] == [sys.executable, "-I", "-c"]
        assert not ("import_module('paddle')" in command[-1] and "import_module('torch')" in command[-1])


def test_invalid_dependencies_prevent_importing_native_runtimes(monkeypatch):
    monkeypatch.setattr(checker.metadata, "distributions", lambda: [distribution("opencv-python")])
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="missing", stderr="")
    monkeypatch.setattr(checker.subprocess, "run", run)
    report = checker.inspect_install("rapidocr-openvino", imports=True)
    assert not report["passed"] and report["import_probes"] == []
    assert len(calls) == 1


def test_report_namespace_cannot_be_overwritten(tmp_path, monkeypatch):
    report = tmp_path / "report.json"
    report.write_text("original")
    monkeypatch.setattr(sys, "argv", ["check", "--report", str(report)])
    monkeypatch.setattr(checker, "inspect_install", lambda *a, **k: pytest.fail("must not probe"))
    with pytest.raises(FileExistsError):
        checker.main()
    assert report.read_text() == "original"


@pytest.mark.parametrize("windows", [False, True])
def test_each_profile_installs_one_opencv_and_its_own_extra(tmp_path, windows):
    plans = setup.build_plan(list(setup.EXTRAS), tmp_path, windows=windows,
                             cache_home=tmp_path / "cache", provision_models=True)
    assert len({p["python"] for p in plans}) == 5
    for plan in plans:
        name = plan["configuration"]
        install = next(c for c in plan["commands"] if "package-install.json" in " ".join(c))
        cv2 = [arg for arg in install if arg.startswith("opencv-")]
        assert cv2 == [setup.OPENCV[name]]
        extras = [arg for arg in install if arg.startswith(str(setup.ROOT)) and "[" in arg]
        assert extras == ([f"{setup.ROOT}[{setup.EXTRAS[name]}]"] if setup.EXTRAS[name] else [])
        assert ("Scripts/python.exe" if windows else "bin/python") in plan["python"]
        cache = tmp_path / "cache" / "paddlex" if name == "paddle" else tmp_path / "cache"
        assert plan["commands"][-1][-2:] == ["--cache-home", str(cache)]
        if name == "easyocr":
            assert "https://download.pytorch.org/whl/cpu" in plan["commands"][1]


def test_offline_plan_has_no_indexes_or_model_downloads(tmp_path):
    plan = setup.build_plan(["easyocr"], tmp_path, windows=False, cache_home=tmp_path,
                            no_index=True, find_links=(str(tmp_path / "wheels with spaces"),))[0]
    for command in plan["commands"][1:3]:
        assert "--no-index" in command and "--index-url" not in command
        assert command[command.index("--find-links") + 1] == str(tmp_path / "wheels with spaces")
    assert len(plan["commands"]) == 4


def test_batch_checks_all_targets_before_installing_or_creating_any(tmp_path, monkeypatch):
    plans = setup.build_plan(["easyocr", "paddle"], tmp_path, windows=False, cache_home=tmp_path)
    occupied = tmp_path / "paddle"
    occupied.mkdir()
    marker = occupied / "keep"
    marker.write_text("existing")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: pytest.fail("must not install"))
    with pytest.raises(FileExistsError):
        setup.execute(plans, tmp_path)
    assert not (tmp_path / "easyocr").exists()
    assert marker.read_text() == "existing"


@pytest.mark.parametrize("returncode", [0, 1])
def test_installation_failure_is_persisted_and_never_marked_quality_acceptance(tmp_path, monkeypatch, returncode):
    plans = setup.build_plan(["tesseract"], tmp_path / "environments", windows=False, cache_home=tmp_path)
    monkeypatch.setenv("PYTHONPATH", "/foreign/checkout")
    monkeypatch.setenv("PYTHONHOME", "/foreign/python")
    calls = []
    def run(command, **kwargs):
        assert "PYTHONPATH" not in kwargs["env"] and "PYTHONHOME" not in kwargs["env"]
        assert "timeout" not in kwargs
        calls.append(command)
        if returncode and len(calls) == 2:
            raise subprocess.CalledProcessError(returncode, command)
    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.execute(plans, tmp_path) == returncode
    report = json.loads((tmp_path / "environments/tesseract/setup-report.json").read_text())
    assert report["status"] == ("completed" if returncode == 0 else "failed")
    assert report["completed_commands"] == (3 if returncode == 0 else 1)
    assert report["source_hashes"]
    assert report["quality_accepted"] is report["release_accepted"] is False


def test_preflight_uses_selected_cache_without_initializing_runtime(tmp_path, monkeypatch):
    captured = []
    def probe(config):
        captured.append(config)
        return preflight.ReadinessResult(preflight.ReadinessStatus.READY)
    monkeypatch.setattr(preflight, "probe_static", probe)
    result = preflight._check("paddle", None, "pt-BR", False, str(tmp_path))
    assert result.status is preflight.ReadinessStatus.READY
    assert captured[0].ocr_cache_home == str(tmp_path)


def test_plan_cli_does_not_create_environments(tmp_path):
    result = subprocess.run([sys.executable, str(Path(setup.__file__)), "--configuration", "tesseract",
                             "--env-root", str(tmp_path / "absent"), "--plan"],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[0]["configuration"] == "tesseract"
    assert not (tmp_path / "absent").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Linux/WSL Bash entry point")
@pytest.mark.parametrize("failure", [None, "missing_environment", "corpus_lock"])
@pytest.mark.parametrize("gpu", [False, True])
def test_benchmark_dispatches_isolated_interpreters_and_stops_on_preflight_failure(tmp_path, failure, gpu):
    import shutil

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(setup.ROOT / "scripts/run_benchmark.sh", scripts / "run_benchmark.sh")
    log = tmp_path / "calls.jsonl"
    stub = (
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as f: f.write(json.dumps([sys.argv[0], sys.argv[1:]]) + '\\n')\n"
        f"sys.exit(3 if {failure!r} == 'corpus_lock' and 'provision_acceptance_corpus.py' in sys.argv[1] else 0)\n"
    )
    core = tmp_path / "core python"
    core.write_text(stub)
    core.chmod(0o755)
    env_root = tmp_path / "environments with spaces"
    for profile in ("easyocr", "tesseract"):
        python = env_root / profile / "bin/python"
        python.parent.mkdir(parents=True)
        if failure == "missing_environment" and profile == "tesseract":
            continue
        python.write_text(stub)
        python.chmod(0o755)
    for name in ("input.pdf", "reference.md", "manifest.json", "validation.txt"):
        (tmp_path / name).touch()
    env = dict(os.environ)
    # A broken/unrelated RapidOCR environment must not block another engine.
    env["RAPIDOCR_REC_MODEL"] = "/unrelated/missing/legacy.onnx"
    result = subprocess.run(["bash", str(scripts / "run_benchmark.sh"),
                             "--configurations", "easyocr,tesseract", "--python", str(core),
                             "--environment-root", str(env_root),
                             "--pdf", "input.pdf", "--reference", "reference.md",
                             "--manifesto", "manifest.json", "--validation", "validation.txt",
                             *(["--gpu"] if gpu else [])],
                            env=env, capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert not any("verify_rapidocr_artifacts.py" in " ".join(args) for _, args in calls)
    extractions = [(python, args) for python, args in calls if "evaluate_e2e.py" in args[0]]
    if failure:
        assert result.returncode == 2
        assert extractions == []
        if failure == "missing_environment":
            assert calls == []
    else:
        assert result.returncode == 0, result.stderr
        assert [Path(python) for python, _ in extractions] == [
            env_root / name / "bin/python" for name in ("easyocr", "tesseract")]
        assert [args[args.index("--engine") + 1] for _, args in extractions] == ["easyocr", "tesseract"]
        assert all(("--gpu" in args) is gpu for _, args in extractions)
        metrics = [python for python, args in calls if "compute_metrics.py" in args[0]]
        assert metrics == [str(core)] * 2
