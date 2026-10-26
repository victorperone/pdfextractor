"""Tesseract optimization loop runner.

Runs extract_worker.py + compute_metrics.py for V3, V5, V6, V7 sequentially,
saving output to output/loop/tesseract/<run_id>/<corpus>/.

Usage:
    python scripts/run_loop.py <run_id> [--corpora V3 V5 V6 V7]
    python scripts/run_loop.py 0001
    python scripts/run_loop.py 0001 --corpora V5 V6
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from automation.config import CORPUS, CORPUS_MIN_COMMIT_GB, CORPUS_MIN_RAM_GB, CORPUS_EXTRACT_TIMEOUT_S
from automation.resource_guard import can_start_extraction, system_memory_status

LOOP_ROOT = REPO_ROOT / "output" / "loop" / "tesseract"
LOOP_CORPORA = ["V3", "V5", "V6", "V7"]
METRICS_TIMEOUT = 7200  # 2 h (V7 eval can take ~26 min)


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("run_loop")


def _preflight(corpus: str) -> tuple[bool, str | None]:
    min_commit = CORPUS_MIN_COMMIT_GB.get(corpus, 11.0)
    min_ram = CORPUS_MIN_RAM_GB.get(corpus, 5.0)
    ok, reason = can_start_extraction(min_commit_avail_gb=min_commit, min_ram_avail_gb=min_ram)
    if ok:
        mem = system_memory_status()
        log.info(
            "Pre-flight OK: %s  commit_avail=%.1f GB (min=%.1f)  ram_avail=%.1f GB (min=%.1f)",
            corpus, mem.commit_avail_gb, min_commit, mem.ram_avail_gb, min_ram,
        )
    else:
        log.warning("Pre-flight FAIL: %s — %s", corpus, reason)
    return ok, reason


def _extract(corpus: str, out_dir: Path) -> bool:
    corpus_info = CORPUS[corpus]
    pdf_path = corpus_info["pdf"]
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(REPO_ROOT / ".venv" / "Scripts" / "python.exe"),
        str(REPO_ROOT / "automation" / "extract_worker.py"),
        "--ocr-engine", "tesseract",
        str(pdf_path),
        str(out_dir),
    ]

    log.info("Extracting %s -> %s", corpus, out_dir)
    t0 = time.monotonic()
    timeout = CORPUS_EXTRACT_TIMEOUT_S.get(corpus, 14400)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(REPO_ROOT)
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("SLOW EXTRACTION: %s — %.0fs, still waiting...", corpus, timeout)
        stdout, _ = proc.communicate()

    elapsed = time.monotonic() - t0
    log.info("Extraction done: %s rc=%d elapsed=%.0fs", corpus, proc.returncode, elapsed)

    info_path = out_dir / "extract_info.json"
    if not info_path.exists():
        log.error("extract_info.json not found for %s", corpus)
        return False

    info = json.loads(info_path.read_text(encoding="utf-8"))
    status = info.get("status", "unknown")
    engine = info.get("ocr_engine", "unknown")
    if engine != "tesseract":
        log.error("ENGINE MISMATCH for %s: expected tesseract got %s", corpus, engine)
        return False
    return status == "success"


def _compute_metrics(corpus: str, out_dir: Path, run_id: str) -> bool:
    corpus_info = CORPUS[corpus]
    hyp = out_dir / "extracted_paginated.md"
    if not hyp.exists():
        log.error("Hypothesis not found: %s", hyp)
        return False

    metrics_source = corpus_info.get("metrics_source", "manifesto")
    if metrics_source == "manifesto":
        src_flag, src_path = "--manifesto", corpus_info["manifesto"]
    else:
        src_flag, src_path = "--reference", corpus_info["reference"]

    cmd = [
        str(REPO_ROOT / ".venv" / "Scripts" / "python.exe"),
        str(REPO_ROOT / "scripts" / "compute_metrics.py"),
        "--hypothesis", str(hyp),
        src_flag, str(src_path),
        "--engine", "tesseract",
        "--run-id", run_id,
        "--output-dir", str(out_dir),
    ]

    log.info("Computing metrics: %s (run_id=%s)", corpus, run_id)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(REPO_ROOT)
    )
    try:
        stdout, _ = proc.communicate(timeout=METRICS_TIMEOUT)
    except subprocess.TimeoutExpired:
        log.warning("SLOW METRICS: %s", corpus)
        stdout, _ = proc.communicate()

    elapsed = time.monotonic() - t0
    log.info("Metrics done: %s rc=%d elapsed=%.0fs", corpus, proc.returncode, elapsed)
    return proc.returncode == 0


def _run_corpus(corpus: str, out_dir: Path, run_id_str: str) -> str:
    log.info("\n" + "=" * 60)
    log.info("CORPUS: %s  [run=%s]", corpus, run_id_str)

    ok, reason = _preflight(corpus)
    if not ok:
        log.warning("Skipping %s: preflight failed — %s", corpus, reason)
        return "skipped_preflight"

    success = _extract(corpus, out_dir)
    if not success:
        return "extraction_failed"

    run_id = f"loop{run_id_str}_tesseract_{corpus}"
    metrics_ok = _compute_metrics(corpus, out_dir, run_id)
    return "complete" if metrics_ok else "metrics_failed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Tesseract loop run")
    parser.add_argument("run_id", help="Run ID (e.g. 0001)")
    parser.add_argument("--corpora", nargs="+", default=LOOP_CORPORA, choices=list(CORPUS.keys()))
    args = parser.parse_args()

    run_dir = LOOP_ROOT / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log.info("Tesseract Loop Run %s — start [%s]", args.run_id, _utcnow())
    log.info("Corpora: %s", args.corpora)

    run_manifest = {
        "run_id": args.run_id,
        "start_time_utc": _utcnow(),
        "corpora": args.corpora,
        "engine": "tesseract",
        "profile": "balanced_adaptive",
        "language": "pt-BR",
        "loop_dir": str(run_dir),
        "status": "running",
    }
    (run_dir / "run.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")

    results: dict[str, str] = {}
    for corpus in args.corpora:
        out_dir = run_dir / corpus
        results[corpus] = _run_corpus(corpus, out_dir, args.run_id)

    all_complete = all(s == "complete" for s in results.values())
    final_status = "finished_complete" if all_complete else "finished_partial"

    run_manifest.update({
        "end_time_utc": _utcnow(),
        "status": final_status,
        "results": results,
    })
    (run_dir / "run.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")

    log.info("\n" + "=" * 60)
    log.info("RUN %s FINISHED: %s", args.run_id, final_status)
    for corpus, status in results.items():
        log.info("  %-4s: %s", corpus, status)

    if not all_complete:
        sys.exit(1)


if __name__ == "__main__":
    main()
