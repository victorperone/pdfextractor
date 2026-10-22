#!/usr/bin/env python3
"""Create separate Python 3.12 environments for pinned OCR deployment profiles.

Existing environments are never modified. Package installation and optional
model provisioning may use the network; extraction stays offline. --plan emits
the exact commands without creating directories or importing OCR runtimes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EXTRAS = {
    "tesseract": None,
    "easyocr": "ocr-easyocr",
    "paddle": "ocr",
    "rapidocr-onnxruntime": "ocr-rapidocr-onnx",
    "rapidocr-openvino": "ocr-rapidocr-openvino",
}
OPENCV = {
    "tesseract": "opencv-python-headless==4.10.0.84",
    "easyocr": "opencv-python-headless==4.10.0.84",
    "paddle": "opencv-contrib-python==4.10.0.84",
    "rapidocr-onnxruntime": "opencv-python==4.10.0.84",
    "rapidocr-openvino": "opencv-python==4.10.0.84",
}


def build_plan(configurations: list[str], env_root: Path, *, windows: bool,
               cache_home: Path, provision_models: bool = False,
               no_index: bool = False, find_links: tuple[str, ...] = (), gpu: bool = False) -> list[dict]:
    if gpu and windows:
        raise ValueError("GPU environment setup is pinned for Linux/WSL2")
    plans = []
    for profile in configurations:
        cuda = gpu and profile in {"easyocr", "paddle", "rapidocr-onnxruntime"}
        constraints = ROOT / ("constraints-ocr-gpu-linux-py312.txt" if cuda else
                              f"constraints-ocr-{'windows' if windows else 'linux'}-py312.txt")
        environment = env_root / profile
        python = environment / ("Scripts/python.exe" if windows else "bin/python")
        pip = [str(python), "-I", "-m", "pip", "install", "-c", str(constraints)]
        if no_index:
            pip.append("--no-index")
        for link in find_links:
            pip.extend(["--find-links", link])
        commands = [[sys.executable, "-I", "-m", "venv", str(environment)]]
        if profile == "easyocr":
            suffix = "cu130" if cuda else "cpu"
            torch = pip + ["--report", str(environment / "torch-install.json"),
                           f"torch==2.14.0+{suffix}", f"torchvision==0.29.0+{suffix}"]
            if not no_index:
                torch += ["--index-url", f"https://download.pytorch.org/whl/{suffix}"]
            commands.append(torch)
        if cuda and profile == "paddle":
            paddle = pip + ["--report", str(environment / "paddle-gpu-install.json"), "paddlepaddle-gpu==3.3.1"]
            if not no_index:
                paddle += ["--extra-index-url", "https://www.paddlepaddle.org.cn/packages/stable/cu126/"]
            commands.append(paddle)
        extra = EXTRAS[profile]
        dependencies = []
        if cuda and profile == "paddle":
            extra = None  # The CPU extra would install a second owner of `paddle`.
            dependencies = ["paddleocr==3.7.0", "paddlex[ocr]==3.7.2"]
        elif cuda and profile == "rapidocr-onnxruntime":
            extra = None  # CPU and GPU ORT wheels both own `onnxruntime`.
            dependencies = ["rapidocr==3.9.2", "onnxruntime-gpu[cuda,cudnn]==1.30.0"]
        project = str(ROOT) + (f"[{extra}]" if extra else "")
        commands.append(pip + ["--report", str(environment / "package-install.json"),
                               "-e", project, "numpy==2.0.2", "psutil==7.2.2", OPENCV[profile], *dependencies])
        commands.append([str(python), "-I", str(ROOT / "scripts/check_ocr_install.py"),
                         "--configuration", profile, "--imports",
                         "--report", str(environment / "install-check.json")])
        if cuda:
            commands[-1].append("--gpu")
        if provision_models:
            model_cache = cache_home / "paddlex" if profile == "paddle" else cache_home
            if profile.startswith("rapidocr-"):
                commands.append([str(python), "-I", str(ROOT / "scripts/setup_rapidocr_models.py"),
                                 "--size", "all", "--cache-home", str(cache_home)])
            elif profile in {"easyocr", "paddle"}:
                commands.append([str(python), "-I", "-m", "structured_pdf_text.cli",
                                 f"setup-{profile}-models", "--cache-home", str(model_cache)])
            commands.append([str(python), "-I", str(ROOT / "scripts/preflight_ocr_backends.py"),
                             "--configuration", profile, "--language", "pt-BR",
                             "--cache-home", str(model_cache)])
            if cuda:
                commands[-1].append("--gpu")
        plans.append({"configuration": profile, "environment": str(environment),
                      "python": str(python), "constraints": str(constraints),
                      "provision_models": provision_models, "commands": commands})
        if gpu:
            plans[-1]["cuda_requested"] = cuda
    return plans


def execute(plans: list[dict], cache_home: Path) -> int:
    # Check the whole batch before reserving directories; never clear/reuse a
    # prior virtualenv or hide its installation report.
    for plan in plans:
        target = Path(plan["environment"])
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Environment already exists; choose another --env-root: {target}")
    failed = False
    for plan in plans:
        target = Path(plan["environment"])
        target.mkdir(parents=True, exist_ok=False)
        sources = (ROOT / "pyproject.toml", Path(plan["constraints"]),
                   ROOT / "scripts/check_ocr_install.py", Path(__file__).resolve(),
                   ROOT / "scripts/preflight_ocr_backends.py")
        def hashes():
            return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        report = {**plan, "completed_commands": 0, "status": "running",
                  "quality_accepted": False, "release_accepted": False,
                  "source_hashes": hashes()}
        env = dict(os.environ)
        for name in ("PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"):
            env.pop(name, None)
        env["PATH"] = str(Path(plan["python"]).parent) + os.pathsep + env.get("PATH", "")
        # Share model artifacts, never site-packages.
        env["EASYOCR_MODULE_PATH"] = str(cache_home / "easyocr")
        env["RAPIDOCR_CACHE_HOME"] = str(cache_home)
        try:
            with (target / "setup.log").open("x", encoding="utf-8") as log:
                for command in plan["commands"]:
                    print(f"[{plan['configuration']}] {command[2:5]}", flush=True)
                    log.write(json.dumps(command) + "\n")
                    log.flush()
                    subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, check=True)
                    report["completed_commands"] += 1
            report["status"] = "completed"
        except (OSError, subprocess.CalledProcessError) as exc:
            report["status"] = "failed"
            report["error"] = f"{type(exc).__name__}: {exc}"
            failed = True
        except KeyboardInterrupt:
            report["status"] = "interrupted"
            raise
        finally:
            report["source_hashes_after"] = hashes()
            report["source_unchanged"] = report["source_hashes"] == report["source_hashes_after"]
            if not report["source_unchanged"]:
                report["status"] = "failed"
                report["source_error"] = "Installation sources changed during setup"
                failed = True
            (target / "setup-report.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[{plan['configuration']}] {report['status']}: {target / 'setup-report.json'}", flush=True)
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", choices=tuple(EXTRAS), nargs="+", required=True)
    parser.add_argument("--env-root", type=Path)
    parser.add_argument("--gpu", action="store_true", help="Install separate CUDA profiles (Linux/WSL2); Tesseract/OpenVINO stay CPU")
    parser.add_argument("--cache-home", type=Path, default=Path.home() / ".cache/pdfextractor")
    parser.add_argument("--plan", action="store_true", help="Print commands only; no writes or downloads")
    parser.add_argument("--provision-models", action="store_true",
                        help="Download models and run readiness; Tesseract/por must exist in PATH")
    parser.add_argument("--no-index", action="store_true", help="Install only from supplied wheel locations")
    parser.add_argument("--find-links", action="append", default=[])
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        parser.error("Use Python 3.12 to create the pinned environments")
    if sys.platform not in {"linux", "win32"}:
        parser.error("Pinned environments are defined for Linux/WSL and Windows")
    if len(set(args.configuration)) != len(args.configuration):
        parser.error("Deployment configurations must not be repeated")
    if args.no_index and args.provision_models:
        parser.error("--no-index cannot be combined with model downloads")
    if args.gpu and sys.platform != "linux":
        parser.error("GPU environment setup is pinned for Linux/WSL2")
    env_root = args.env_root or (ROOT / ".ocr-envs/gpu" if args.gpu else ROOT / ".ocr-envs")
    plans = build_plan(args.configuration, env_root.resolve(), windows=sys.platform == "win32",
                       cache_home=args.cache_home.resolve(), provision_models=args.provision_models,
                       no_index=args.no_index, find_links=tuple(args.find_links), gpu=args.gpu)
    if args.plan:
        print(json.dumps(plans, indent=2))
        return 0
    try:
        return execute(plans, args.cache_home.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
