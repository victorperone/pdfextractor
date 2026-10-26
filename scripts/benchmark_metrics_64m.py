"""Benchmark runner for compute_metrics.py with 64M cell budget.

Runs evaluator for V7, V3, V5, V6 using existing baseline Markdown files.
Monitors resource usage every 1 second. Does NOT run OCR or modify baseline files.

Output: output/tesseract/benchmark_metrics_64m/<corpus>/
"""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
BASELINE = REPO_ROOT / "output" / "tesseract" / "baseline"
OUT_ROOT = REPO_ROOT / "output" / "tesseract" / "benchmark_metrics_64m"
SCRIPT = REPO_ROOT / "scripts" / "compute_metrics.py"

# Corpus configs — must match automation/config.py
CORPORA = {
    "V7": {
        "hyp": BASELINE / "V7" / "extracted_paginated.md",
        "manifesto": REPO_ROOT / "corpus" / "V7" / "Document_AI_V7_MANIFESTO.json",
        "reference": None,
        "metrics_source": "manifesto",
    },
    "V3": {
        "hyp": BASELINE / "V3" / "extracted_paginated.md",
        "manifesto": None,
        "reference": REPO_ROOT / "corpus" / "V3" / "Document_AI_V3_REFERENCIA.md",
        "metrics_source": "reference",
    },
    "V5": {
        "hyp": BASELINE / "V5" / "extracted_paginated.md",
        "manifesto": REPO_ROOT / "corpus" / "V5" / "Document_AI_V5_MANIFESTO.json",
        "reference": None,
        "metrics_source": "manifesto",
    },
    "V6": {
        "hyp": BASELINE / "V6" / "extracted_paginated.md",
        "manifesto": REPO_ROOT / "corpus" / "V6" / "Document_AI_V6_MANIFESTO.json",
        "reference": None,
        "metrics_source": "manifesto",
    },
}

# Evaluation order: V7 first (previously blocked), then others
EVAL_ORDER = ["V7", "V3", "V5", "V6"]


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _mb(val_bytes: int) -> float:
    return round(val_bytes / (1024 ** 2), 1)


def _gb(val_bytes: int) -> float:
    return round(val_bytes / (1024 ** 3), 2)


def _system_memory() -> dict:
    """Read Windows system-wide memory stats via wmic."""
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return {
            "ram_total_gb": _gb(stat.ullTotalPhys),
            "ram_avail_gb": _gb(stat.ullAvailPhys),
            "commit_total_gb": _gb(stat.ullTotalPageFile),
            "commit_avail_gb": _gb(stat.ullAvailPageFile),
        }
    except Exception:
        return {}


def _process_mem(pid: int) -> dict:
    """Get process memory via psutil, including all children."""
    try:
        import psutil
        proc = psutil.Process(pid)
        rss = proc.memory_info().rss
        priv = getattr(proc.memory_info(), "private", 0)
        children = proc.children(recursive=True)
        for ch in children:
            try:
                info = ch.memory_info()
                rss += info.rss
                priv += getattr(info, "private", 0)
            except Exception:
                pass
        n_children = len(children)
        return {"rss_mb": _mb(rss), "private_mb": _mb(priv), "n_children": n_children}
    except Exception:
        return {}


def _monitor_loop(pid: int, samples: list, stop_event: threading.Event) -> None:
    """Background thread: collect resource samples every 1 second."""
    while not stop_event.is_set():
        t = time.monotonic()
        proc_mem = _process_mem(pid)
        sys_mem = _system_memory()
        samples.append({"t": round(t, 2), **proc_mem, **sys_mem})
        # Safety check — abort signal if RAM or commit too low
        if sys_mem.get("ram_avail_gb", 99) < 4.0:
            print(f"\n[MONITOR] WARNING: RAM avail {sys_mem['ram_avail_gb']:.2f} GB < 4.0 GB threshold!", flush=True)
        if sys_mem.get("commit_avail_gb", 99) < 8.0:
            print(f"\n[MONITOR] WARNING: Commit avail {sys_mem['commit_avail_gb']:.2f} GB < 8.0 GB threshold!", flush=True)
        time.sleep(1.0)


def run_eval(corpus: str) -> dict:
    cfg = CORPORA[corpus]
    out_dir = OUT_ROOT / corpus
    out_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"benchmark64m_tesseract_{corpus}"
    hyp = cfg["hyp"]

    if cfg["metrics_source"] == "manifesto":
        src_flag, src_path = "--manifesto", cfg["manifesto"]
    else:
        src_flag, src_path = "--reference", cfg["reference"]

    cmd = [
        str(VENV_PY),
        str(SCRIPT),
        "--hypothesis", str(hyp),
        src_flag, str(src_path),
        "--engine", "tesseract",
        "--run-id", run_id,
        "--output-dir", str(out_dir),
    ]

    print(f"\n{'='*60}", flush=True)
    print(f"CORPUS: {corpus}  [{_now_utc()}]", flush=True)
    print(f"  hyp      : {hyp}", flush=True)
    print(f"  src_flag : {src_flag}", flush=True)
    print(f"  out_dir  : {out_dir}", flush=True)
    print(f"  run_id   : {run_id}", flush=True)

    sys_before = _system_memory()
    print(f"  RAM avail before: {sys_before.get('ram_avail_gb', '?')} GB", flush=True)
    print(f"  Commit avail before: {sys_before.get('commit_avail_gb', '?')} GB", flush=True)

    samples: list[dict] = []
    stop_event = threading.Event()

    t0_wall = time.time()
    t0_mono = time.monotonic()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(REPO_ROOT),
    )

    monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(proc.pid, samples, stop_event),
        daemon=True,
    )
    monitor_thread.start()

    try:
        stdout_data, _ = proc.communicate()
    finally:
        stop_event.set()
        monitor_thread.join(timeout=5.0)

    elapsed = time.monotonic() - t0_mono
    rc = proc.returncode

    # Compute peak resource from samples
    peak_rss_mb = max((s.get("rss_mb", 0) for s in samples), default=0)
    peak_private_mb = max((s.get("private_mb", 0) for s in samples), default=0)
    peak_children = max((s.get("n_children", 0) for s in samples), default=0)
    min_ram_avail_gb = min((s.get("ram_avail_gb", 99) for s in samples), default=99)
    min_commit_avail_gb = min((s.get("commit_avail_gb", 99) for s in samples), default=99)

    sys_after = _system_memory()

    print(f"\n  Elapsed  : {elapsed:.1f}s", flush=True)
    print(f"  RetCode  : {rc}", flush=True)
    print(f"  Peak RSS : {peak_rss_mb:.0f} MB ({peak_rss_mb/1024:.2f} GB)", flush=True)
    print(f"  Peak private/commit: {peak_private_mb:.0f} MB ({peak_private_mb/1024:.2f} GB)", flush=True)
    print(f"  Peak children: {peak_children}", flush=True)
    print(f"  Min RAM avail: {min_ram_avail_gb:.2f} GB", flush=True)
    print(f"  Min commit avail: {min_commit_avail_gb:.2f} GB", flush=True)

    if stdout_data and stdout_data.strip():
        # Print last 3000 chars of output
        out_excerpt = stdout_data[-3000:] if len(stdout_data) > 3000 else stdout_data
        print(f"\n  --- Output (last {len(out_excerpt)} chars) ---", flush=True)
        print(out_excerpt, flush=True)

    # Save raw samples
    samples_path = out_dir / "resource_samples.json"
    with open(samples_path, "w", encoding="utf-8") as f:
        json.dump({"corpus": corpus, "elapsed_s": round(elapsed, 1), "samples": samples}, f, indent=2)

    # Find output metrics files
    metrics_files = list(out_dir.glob("metrics_*.json"))
    print(f"  Metrics files: {[f.name for f in metrics_files]}", flush=True)

    result = {
        "corpus": corpus,
        "run_id": run_id,
        "elapsed_s": round(elapsed, 1),
        "returncode": rc,
        "peak_rss_mb": round(peak_rss_mb, 1),
        "peak_private_mb": round(peak_private_mb, 1),
        "peak_children": peak_children,
        "min_ram_avail_gb": round(min_ram_avail_gb, 2),
        "min_commit_avail_gb": round(min_commit_avail_gb, 2),
        "ram_avail_before_gb": sys_before.get("ram_avail_gb"),
        "ram_avail_after_gb": sys_after.get("ram_avail_gb"),
        "commit_avail_before_gb": sys_before.get("commit_avail_gb"),
        "commit_avail_after_gb": sys_after.get("commit_avail_gb"),
        "metrics_files": [f.name for f in metrics_files],
        "n_samples": len(samples),
        "stdout_length": len(stdout_data or ""),
    }

    # Parse key metrics from canonical output
    for mf in metrics_files:
        if mf.name.startswith("metrics_v3_"):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                ev = data.get("evaluation", {})
                result["eval_status"] = ev.get("status")
                result["applicable_metric_count"] = ev.get("applicable_metric_count")
                result["unavailable_metric_count"] = ev.get("unavailable_metric_count")
                # Extract a few key metrics
                groups = data.get("metric_groups", {})
                key = {}
                for gname, gdata in groups.items():
                    if isinstance(gdata, dict):
                        for mname, mval in gdata.items():
                            if isinstance(mval, (int, float)):
                                key[f"{gname}.{mname}"] = mval
                result["canonical_key_metrics"] = key
            except Exception as e:
                result["canonical_parse_error"] = str(e)
        elif mf.name.startswith("metrics_tesseract_"):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                leg = {}
                for gname in ["grupo1_texto", "grupo2_estrutura_markdown", "grupo3_tabelas",
                              "grupo4_ordem_integridade", "grupo5_dados_criticos"]:
                    g = data.get(gname, {})
                    for k, v in g.items():
                        if isinstance(v, (int, float)):
                            leg[f"{gname}.{k}"] = v
                result["legacy_key_metrics"] = leg
            except Exception as e:
                result["legacy_parse_error"] = str(e)

    return result


def main():
    print(f"Benchmark 64M cell budget — {_now_utc()}", flush=True)
    print(f"VENV: {VENV_PY}", flush=True)
    sys_init = _system_memory()
    print(f"System RAM: {sys_init.get('ram_total_gb')} GB total, {sys_init.get('ram_avail_gb')} GB avail", flush=True)
    print(f"System commit: {sys_init.get('commit_total_gb')} GB total, {sys_init.get('commit_avail_gb')} GB avail", flush=True)

    # Verify DEFAULT_MAX_EDIT_CELLS
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.structured_pdf_text.evaluation.edit_distance import DEFAULT_MAX_EDIT_CELLS, EDIT_DISTANCE_POLICY
        from src.structured_pdf_text.evaluation.block_alignment import DEFAULT_MAX_ALIGNMENT_PAIRS
        print(f"\nVerified DEFAULT_MAX_EDIT_CELLS = {DEFAULT_MAX_EDIT_CELLS:,}", flush=True)
        print(f"Verified DEFAULT_MAX_ALIGNMENT_PAIRS = {DEFAULT_MAX_ALIGNMENT_PAIRS:,}", flush=True)
        print(f"EDIT_DISTANCE_POLICY = {EDIT_DISTANCE_POLICY}", flush=True)
    except Exception as e:
        print(f"WARNING: Could not verify constants: {e}", flush=True)

    all_results = {}
    total_t0 = time.monotonic()

    for corpus in EVAL_ORDER:
        result = run_eval(corpus)
        all_results[corpus] = result

    total_elapsed = time.monotonic() - total_t0

    # Save summary
    summary = {
        "run_time_utc": _now_utc(),
        "total_elapsed_s": round(total_elapsed, 1),
        "eval_order": EVAL_ORDER,
        "results": all_results,
    }
    summary_path = OUT_ROOT / "benchmark_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*60}", flush=True)
    print(f"BENCHMARK COMPLETE — {_now_utc()}", flush=True)
    print(f"Total elapsed: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)", flush=True)
    print(f"Summary: {summary_path}", flush=True)

    # Print table
    print(f"\n{'Corpus':<8} {'Elapsed(s)':<12} {'Peak RSS(GB)':<14} {'RC':<5} {'Status':<20} {'Unavail'}", flush=True)
    print("-" * 70, flush=True)
    for corpus, r in all_results.items():
        status = r.get("eval_status", "?")
        unavail = r.get("unavailable_metric_count", "?")
        peak_gb = r.get("peak_rss_mb", 0) / 1024
        print(
            f"{corpus:<8} {r.get('elapsed_s', 0):<12} {peak_gb:<14.2f} {r.get('returncode', '?'):<5} {str(status):<20} {unavail}",
            flush=True,
        )


if __name__ == "__main__":
    main()
