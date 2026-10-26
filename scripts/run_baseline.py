"""Tesseract OCR baseline orchestrator.

Runs extract_worker.py + compute_metrics.py for V3, V5, V6, V7 sequentially,
with resource monitoring per corpus. All output goes to
output/tesseract/baseline/<corpus>/.

Does NOT modify shared evaluation code, shared pipeline, or other engines.
Usage:
    python scripts/run_baseline.py [--corpora V3 V5 V6 V7]
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

from automation.config import (
    CORPUS,
    CORPUS_MIN_COMMIT_GB,
    CORPUS_MIN_RAM_GB,
    CORPUS_EXTRACT_TIMEOUT_S,
)
from automation.resource_guard import (
    ResourceMonitor,
    can_start_extraction,
    system_memory_status,
)

BASELINE_OUTPUT = REPO_ROOT / "output" / "tesseract" / "baseline"
STATUS_FILE = BASELINE_OUTPUT / "baseline_status.json"

BASELINE_CORPORA = ["V3", "V5", "V6", "V7"]

# Observation-only warning thresholds per corpus (not kill limits)
EXTRACT_TIMEOUTS = {
    "V3": 14_400,  # 4 h observation
    "V5":  7_200,  # 2 h observation
    "V6": 14_400,  # 4 h observation (95 pgs, est ~2.5×V5)
    "V7": 10_800,  # 3 h observation (80 pgs, est ~2×V5)
}
METRICS_TIMEOUT = 3_600  # 1 h observation


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _brt_now() -> str:
    brt = datetime.timezone(datetime.timedelta(hours=-3))
    return datetime.datetime.now(brt).strftime("%H:%M BRT")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("baseline")


# ---------------------------------------------------------------------------
# Status file (read by monitoring cron)
# ---------------------------------------------------------------------------

def _read_status() -> dict:
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_status(update: dict) -> None:
    BASELINE_OUTPUT.mkdir(parents=True, exist_ok=True)
    state = _read_status()
    state.update(update)
    state["last_updated"] = _utcnow()
    try:
        STATUS_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not write baseline_status.json: %s", exc)


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _preflight(corpus: str) -> tuple[bool, str | None]:
    min_commit = CORPUS_MIN_COMMIT_GB.get(corpus, 10.0)
    min_ram = CORPUS_MIN_RAM_GB.get(corpus, 6.0)
    ok, reason = can_start_extraction(
        min_commit_avail_gb=min_commit, min_ram_avail_gb=min_ram
    )
    if ok:
        mem = system_memory_status()
        log.info(
            "Pre-flight OK: %s  commit_avail=%.1f GB (min=%.1f)  ram_avail=%.1f GB (min=%.1f)",
            corpus, mem.commit_avail_gb, min_commit, mem.ram_avail_gb, min_ram,
        )
    else:
        log.warning("Pre-flight FAIL: %s — %s", corpus, reason)
    return ok, reason


# ---------------------------------------------------------------------------
# Git info (for run_manifest)
# ---------------------------------------------------------------------------

def _git_info() -> dict:
    try:
        sha = subprocess.run(
            ["git", "log", "-1", "--format=%H"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10
        ).stdout.strip()
        mods = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10
        ).stdout.strip()
        return {"git_sha": sha, "local_modifications": mods or None}
    except Exception:
        return {"git_sha": None, "local_modifications": None}


_GIT_INFO = _git_info()  # computed once at startup


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract(corpus: str, out_dir: Path) -> tuple[bool, dict]:
    corpus_info = CORPUS[corpus]
    pdf_path = corpus_info["pdf"]
    out_dir.mkdir(parents=True, exist_ok=True)

    mem0 = system_memory_status()
    start_utc = _utcnow()

    # Write run_manifest.json before starting
    manifest = {
        "corpus": corpus,
        "engine": "tesseract",
        "language": "pt-BR",
        "quality": "balanced_adaptive",
        "config_profile": "balanced_adaptive_tesseract_pt-BR",
        "pdf": str(pdf_path),
        "output_dir": str(out_dir),
        "metrics_source": corpus_info.get("metrics_source", "manifesto"),
        "start_time_utc": start_utc,
        "end_time_utc": None,
        "status": "running",
        **_GIT_INFO,
        "commit_avail_start_gb": round(mem0.commit_avail_gb, 2),
        "ram_avail_start_gb": round(mem0.ram_avail_gb, 2),
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    cmd = [
        str(REPO_ROOT / ".venv" / "Scripts" / "python.exe"),
        str(REPO_ROOT / "automation" / "extract_worker.py"),
        "--ocr-engine", "tesseract",
        str(pdf_path),
        str(out_dir),
    ]

    log.info("Starting extraction: %s -> %s", corpus, out_dir)
    _write_status({
        "current_corpus": corpus,
        "phase": "extracting",
        "start_time_utc": start_utc,
    })

    signal_path = out_dir / "resource_limit.json"
    t0 = time.monotonic()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(REPO_ROOT),
    )
    _write_status({"pid": proc.pid})

    warn_timeout = EXTRACT_TIMEOUTS.get(corpus, 7200)
    with ResourceMonitor(
        proc,
        signal_path=signal_path,
        log=log,
        poll_interval=30,
        commit_avail_start_gb=mem0.commit_avail_gb,
    ) as monitor:
        try:
            stdout, _ = proc.communicate(timeout=warn_timeout)
            out = stdout or ""
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            log.warning(
                "SLOW EXTRACTION: %s — %.0fs elapsed (warn=%ds). Still running, waiting...",
                corpus, elapsed, warn_timeout,
            )
            stdout, _ = proc.communicate()
            out = stdout or ""

    elapsed = time.monotonic() - t0
    end_utc = _utcnow()

    # Resource profile
    profile = monitor.memory_profile
    profile.update({
        "elapsed_s": round(elapsed, 1),
        "returncode": proc.returncode,
        "commit_avail_start_gb": round(mem0.commit_avail_gb, 2),
    })
    (out_dir / "resource_profile.json").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )

    # Update manifest
    manifest["end_time_utc"] = end_utc
    manifest["elapsed_s"] = round(elapsed, 1)
    manifest["returncode"] = proc.returncode
    manifest["resource_profile"] = profile

    # Read extract_info.json
    info_path = out_dir / "extract_info.json"
    success = False
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        manifest["extract_info"] = info
        actual_engine = info.get("ocr_engine", "unknown")
        page_count = info.get("page_count", "?")
        status_str = info.get("status", "unknown")
        log.info(
            "%s extraction done: status=%s pages=%s engine=%s elapsed=%.0fs rc=%d",
            corpus, status_str, page_count, actual_engine, elapsed, proc.returncode,
        )
        if actual_engine != "tesseract":
            log.error("ENGINE MISMATCH for %s: expected tesseract got %s", corpus, actual_engine)
        else:
            success = status_str == "success"
    else:
        log.error("extract_info.json not found for %s (rc=%d)", corpus, proc.returncode)

    if monitor.triggered:
        log.error("ResourceMonitor triggered for %s — resource abort", corpus)
        success = False

    manifest["status"] = "extracted" if success else "extraction_failed"
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    if out.strip():
        # Save extraction log excerpt for diagnostics
        log.debug("Extract output (%d chars):\n%s", len(out), out[:3000])

    return success, profile


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_metrics(corpus: str, out_dir: Path) -> bool:
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

    run_id = f"baseline_tesseract_{corpus}"
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
    _write_status({"current_corpus": corpus, "phase": "computing_metrics"})

    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(REPO_ROOT),
    )
    try:
        stdout, _ = proc.communicate(timeout=METRICS_TIMEOUT)
        out = stdout or ""
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        log.warning(
            "SLOW METRICS: %s — %.0fs elapsed, still waiting...", corpus, elapsed
        )
        stdout, _ = proc.communicate()
        out = stdout or ""

    elapsed = time.monotonic() - t0
    log.info("Metrics done: %s rc=%d elapsed=%.0fs", corpus, proc.returncode, elapsed)
    if proc.returncode != 0 and out.strip():
        log.warning("Metrics stderr: %s", out[:2000])

    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Quality summary (artifact #10)
# ---------------------------------------------------------------------------

def _write_quality_summary(corpus: str, out_dir: Path, resource_profile: dict) -> None:
    run_id = f"baseline_tesseract_{corpus}"
    canonical_path = out_dir / f"metrics_v3_tesseract_{run_id}.json"
    legacy_path = out_dir / f"metrics_tesseract_{run_id}.json"

    summary: dict = {
        "corpus": corpus,
        "run_id": run_id,
        "engine": "tesseract",
        "resource_profile": resource_profile,
    }

    # Extract key metrics from canonical file
    if canonical_path.exists():
        try:
            c = json.loads(canonical_path.read_text(encoding="utf-8"))
            ev = c.get("evaluation", {})
            summary["evaluation_status"] = ev.get("status")
            summary["applicable_metrics"] = ev.get("applicable_metric_count")
            summary["unavailable_metrics"] = ev.get("unavailable_metric_count")

            # Extract canonical key metrics by group
            groups = c.get("metric_groups", {})
            key_metrics: dict = {}
            for gname, gdata in groups.items():
                if not isinstance(gdata, dict):
                    continue
                for mname, mval in gdata.items():
                    if isinstance(mval, (int, float)):
                        key_metrics[f"{gname}.{mname}"] = mval
            summary["canonical_key_metrics"] = key_metrics
        except Exception as exc:
            summary["canonical_parse_error"] = str(exc)

    # Extract legacy key metrics
    if legacy_path.exists():
        try:
            leg = json.loads(legacy_path.read_text(encoding="utf-8"))
            legacy_key = {}
            for gname in ["grupo1_texto", "grupo2_estrutura_markdown",
                          "grupo3_tabelas", "grupo4_ordem_integridade",
                          "grupo5_dados_criticos"]:
                g = leg.get(gname, {})
                for k, v in g.items():
                    if isinstance(v, (int, float)):
                        legacy_key[f"{gname}.{k}"] = v
            summary["legacy_key_metrics"] = legacy_key
        except Exception as exc:
            summary["legacy_parse_error"] = str(exc)

    (out_dir / "quality_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    log.info("Quality summary written: %s/quality_summary.json", corpus)


# ---------------------------------------------------------------------------
# Per-corpus runner
# ---------------------------------------------------------------------------

def _run_corpus(corpus: str) -> str:
    out_dir = BASELINE_OUTPUT / corpus
    log.info("\n" + "=" * 60)
    log.info("CORPUS: %s  [%s BRT]", corpus, _brt_now())
    log.info("=" * 60)

    # Pre-flight
    _write_status({"current_corpus": corpus, "phase": "preflight"})
    ok, reason = _preflight(corpus)
    if not ok:
        _write_status({
            "current_corpus": corpus,
            "phase": "skipped",
            f"result_{corpus}": {"status": "skipped_preflight", "reason": reason},
        })
        return "skipped_preflight"

    # Extraction
    success, profile = _extract(corpus, out_dir)
    if not success:
        _write_status({
            "phase": "extraction_failed",
            f"result_{corpus}": {"status": "extraction_failed", "resource_profile": profile},
        })
        return "extraction_failed"

    # Metrics
    metrics_ok = _compute_metrics(corpus, out_dir)

    # Quality summary
    _write_quality_summary(corpus, out_dir, profile)

    result_status = "complete" if metrics_ok else "metrics_failed"
    _write_status({
        "phase": result_status,
        f"result_{corpus}": {"status": result_status, "resource_profile": profile},
    })
    log.info("CORPUS %s: %s  [%s BRT]", corpus, result_status, _brt_now())
    return result_status


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Tesseract baseline runner")
    parser.add_argument(
        "--corpora", nargs="+", default=BASELINE_CORPORA,
        choices=list(CORPUS.keys()) + ["all"],
        help="Corpora to run (default: V3 V5 V6 V7)",
    )
    args = parser.parse_args()
    corpora = args.corpora

    BASELINE_OUTPUT.mkdir(parents=True, exist_ok=True)
    log.info("Tesseract Baseline — start  [%s BRT]", _brt_now())
    log.info("Corpora: %s", corpora)
    log.info("Output: %s", BASELINE_OUTPUT)

    _write_status({
        "phase": "started",
        "corpora": corpora,
        "start_time_utc": _utcnow(),
        "baseline_output": str(BASELINE_OUTPUT),
    })

    results: dict[str, str] = {}
    for corpus in corpora:
        results[corpus] = _run_corpus(corpus)

    # Final summary
    all_complete = all(s == "complete" for s in results.values())
    final_phase = "finished_complete" if all_complete else "finished_partial"
    _write_status({
        "current_corpus": None,
        "phase": final_phase,
        "end_time_utc": _utcnow(),
        "results": results,
    })

    log.info("\n" + "=" * 60)
    log.info("BASELINE FINISHED  [%s BRT]", _brt_now())
    for corpus, status in results.items():
        log.info("  %-4s: %s", corpus, status)
    if not all_complete:
        sys.exit(1)


if __name__ == "__main__":
    main()
