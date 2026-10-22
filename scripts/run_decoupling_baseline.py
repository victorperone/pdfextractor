#!/usr/bin/env python3
"""Capture immutable pre-refactoring OCR artifacts with the existing E2E runner.

Provision models and run preflight separately. Each extraction runs in a fresh
process, sequentially, without changing recognition settings or accepting a
partial document as successful. A fresh output directory is required.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
CORPORA = {
    "V3": "Document_AI_V3",
    "V4": "Corpus_Stress_OCR_Markdown_V4",
    "V5": "Document_AI_V5",
}
DEPLOYMENTS = {
    "easyocr": ("easyocr", None, "easyocr"),
    "paddleocr": ("paddle", None, "paddleocr"),
    "tesseract": ("tesseract", None, "tesseract"),
    "rapidocr": ("rapidocr", "onnxruntime", "rapidocr"),
    "rapidocr-openvino": ("rapidocr", "openvino", "rapidocr-openvino"),
}
# Explicit recognition/runtime settings only; do not dump credentials from the
# surrounding process into a reproducibility artifact.
ENVIRONMENT_KEYS = (
    "EASYOCR_ADD_MARGIN", "EASYOCR_ADJUST_CONTRAST", "EASYOCR_ALLOWLIST",
    "EASYOCR_ALLOW_DOWNLOAD", "EASYOCR_BEAMWIDTH", "EASYOCR_BLOCKLIST",
    "EASYOCR_CONTRAST_THS", "EASYOCR_DECODER", "EASYOCR_FILTER_THS",
    "EASYOCR_HEIGHT_THS", "EASYOCR_LINK_THRESHOLD", "EASYOCR_LOW_TEXT",
    "EASYOCR_MAG_RATIO", "EASYOCR_MAX_QUALITY_THREADS", "EASYOCR_MIN_SIZE",
    "EASYOCR_MODULE_PATH", "EASYOCR_QUANTIZE", "EASYOCR_RECOG_NETWORK",
    "EASYOCR_ROTATION_INFO", "EASYOCR_SLOPE_THS", "EASYOCR_TEXT_THRESHOLD",
    "EASYOCR_WIDTH_THS", "EASYOCR_WORKERS", "EASYOCR_YCENTER_THS",
    "RAPIDOCR_ANGLE_CLS", "RAPIDOCR_BOX_THRESH", "RAPIDOCR_DET_MODEL",
    "RAPIDOCR_DET_THRESH", "RAPIDOCR_REC_KEYS", "RAPIDOCR_REC_MODEL",
    "RAPIDOCR_TEXT_SCORE", "RAPIDOCR_UNCLIP_RATIO",
    "TESSERACT_CMD", "TESSERACT_CONF_MIN", "TESSERACT_DPI", "TESSERACT_LANG",
    "TESSERACT_OEM", "TESSERACT_OSD", "TESSERACT_OSD_CONF_MIN",
    "TESSERACT_PROFILE", "TESSERACT_PSM", "TESSERACT_TESSDATA_DIR", "TESSDATA_PREFIX",
    "PADDLE_ENABLE_MKLDNN", "PADDLE_PDX_CACHE_HOME", "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK",
    "PADDLE_WORKER_INIT_TIMEOUT", "PADDLE_WORKER_REQUEST_TIMEOUT", "PADDLE_WORKER_TIMEOUT",
    "PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", "PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR",
    "PDFEXTRACTOR_SMOKE_FONT", "OCR_RENDER_SCALE", "FLAGS_enable_pir_api",
    "OMP_THREAD_LIMIT", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True)


def source_files(exclude: Path) -> dict[str, str]:
    paths = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    return {
        name: sha256(REPO / name) for name in sorted(set(paths))
        if name and (REPO / name).is_file() and not (REPO / name).is_relative_to(exclude)
    }


def extract_command(pdf: Path, output: Path, engine: str, provider: str | None, run_id: str) -> list[str]:
    command = [
        sys.executable, str(REPO / "scripts/evaluate_e2e.py"), str(pdf),
        "--engine", engine, "--mode", "balanced", "--language", "pt-BR",
        "--save-features", "--run-id", run_id, "--output-dir", str(output),
    ]
    if provider:
        command.extend(["--provider", provider])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--corpora", choices=CORPORA, nargs="+", default=list(CORPORA))
    parser.add_argument("--configurations", choices=DEPLOYMENTS, nargs="+", default=list(DEPLOYMENTS))
    parser.add_argument("--dry-run", action="store_true", help="Print commands without creating artifacts or models")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    corpora = list(dict.fromkeys(args.corpora))
    deployments = list(dict.fromkeys(args.configurations))
    if args.dry_run:
        for corpus in corpora:
            for name in deployments:
                engine, provider, directory = DEPLOYMENTS[name]
                print(json.dumps(extract_command(
                    REPO / "corpus" / corpus / f"{CORPORA[corpus]}.pdf",
                    output / corpus / directory, engine, provider, f"{corpus.lower()}-{name}-01",
                )))
        return 0
    if output.exists():
        parser.error("output directory already exists; choose a new namespace")
    pdfs = {corpus: REPO / "corpus" / corpus / f"{CORPORA[corpus]}.pdf" for corpus in corpora}
    if any(not pdf.is_file() for pdf in pdfs.values()):
        parser.error("one or more corpus PDFs are missing")
    # Same config builder as the child runner, imported only for a real capture.
    sys.path.insert(0, str(REPO / "scripts"))
    from evaluate_e2e import _build_config

    files = source_files(output)
    pdf_hashes = {corpus: sha256(pdf) for corpus, pdf in pdfs.items()}
    config = {
        name: asdict(_build_config(engine, "balanced", "pt-BR", None, provider))
        for name in deployments for engine, provider, _ in [DEPLOYMENTS[name]]
    }
    output.mkdir(parents=True)
    snapshot = {
        "schema_version": "ocr-decoupling-baseline-1.0",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git("rev-parse", "HEAD").strip(), "git_status": git("status", "--short"),
        "python": sys.version, "platform": platform.platform(),
        "packages": {d.metadata["Name"]: d.version for d in metadata.distributions()},
        "files": files, "corpus": pdf_hashes, "configs": config,
        "runtime_environment": {name: os.environ.get(name) for name in ENVIRONMENT_KEYS},
    }
    create_json(output / "environment.json", snapshot)
    (output / "working-tree.patch").write_text(git("diff", "HEAD"), encoding="utf-8")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for name in untracked:
        path = REPO / name
        if name and path.is_file() and not path.is_relative_to(output):
            target = output / "untracked-snapshot" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    failed = False
    partial = False
    for corpus in corpora:
        for name in deployments:
            if source_files(output) != files or sha256(pdfs[corpus]) != pdf_hashes[corpus]:
                parser.exit(1, "Baseline inputs changed during capture; refusing to continue\n")
            engine, provider, directory = DEPLOYMENTS[name]
            target = output / corpus / directory
            target.mkdir(parents=True)
            run_id = f"{corpus.lower()}-{name}-01"
            command = extract_command(pdfs[corpus], target, engine, provider, run_id)
            create_json(target / "command.json", {"argv": command})
            create_json(target / "resolved-config.json", config[name])
            print(f"START {corpus}/{name}", flush=True)
            with (target / "extraction.log").open("x", encoding="utf-8") as log:
                result = subprocess.run(command, cwd=REPO, env=environment, stdout=log, stderr=subprocess.STDOUT)
            manifest = target / f"manifesto_e2e_{engine}_{run_id}.json"
            record = {"exit_code": result.returncode, "artifacts": {}}
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                record.update({key: data.get(key) for key in ("document_status", "benchmark_status", "page_count")})
                markdown = Path(data["extracted_markdown"])
                with (target / f"{CORPORA[corpus]}.md").open("xb") as stream:
                    stream.write(markdown.read_bytes())
                partial |= data.get("benchmark_status") == "partial" or data.get("document_status") == "partial_success"
                failed |= (
                    data.get("benchmark_status") not in {"valid", "partial"}
                    or data.get("document_status") not in {"success", "partial_success"}
                )
            if source_files(output) != files or sha256(pdfs[corpus]) != pdf_hashes[corpus]:
                record["inputs_changed"] = True
                failed = True
            record["artifacts"] = {p.name: sha256(p) for p in target.iterdir() if p.is_file()}
            create_json(target / "result.json", record)
            partial |= result.returncode == 2
            failed |= result.returncode not in (0, 2) or not manifest.is_file()
            print(f"END {corpus}/{name}: exit={result.returncode}; status={record.get('document_status')}", flush=True)
            if record.get("inputs_changed"):
                return 1
    return 1 if failed else 2 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
