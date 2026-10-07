"""Observe the real CLI without changing OCR settings or candidate selection.

Runs policies sequentially in fresh processes. Evidence is flushed per page and
per OCR call so an interrupted run still has useful, explicitly partial evidence.
Only AUDIT_* and PYTHONPATH are added; the supplied command uses the project venv.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    temporary.replace(path)


def install_observer():
    """Called by sitecustomize in the actual python -m CLI subprocess."""
    if not os.environ.get("AUDIT_RUN_DIR"):
        return
    from types import SimpleNamespace
    from structured_pdf_text import api
    from structured_pdf_text.document import to_plain_data
    from structured_pdf_text.ocr.backends import easyocr
    from structured_pdf_text.renderers.markdown import render_markdown

    directory = Path(os.environ["AUDIT_RUN_DIR"])
    events = (directory / "events.jsonl").open("a", buffering=1)
    state = {"page": None, "phase": "startup", "call": 0, "request": 0}
    started = time.perf_counter()

    def event(kind, **values):
        events.write(json.dumps({"event": kind, "elapsed_s": time.perf_counter() - started,
                                 "page": state["page"], "phase": state["phase"],
                                 **values}, ensure_ascii=False, default=str) + "\n")

    def phase_wrapper(name):
        original = getattr(api, name)
        @functools.wraps(original)
        def observed(*args, **kwargs):
            previous = state["phase"]
            state["phase"] = name
            tick = time.perf_counter()
            event("phase_start")
            try:
                return original(*args, **kwargs)
            finally:
                event("phase_end", seconds=time.perf_counter() - tick)
                state["phase"] = previous
        setattr(api, name, observed)

    for name in ("_recover_weak_ocr_regions", "_recover_selected_regions",
                 "_refine_small_footnote_tokens"):
        phase_wrapper(name)

    original_run = easyocr._run_easyocr
    @functools.wraps(original_run)
    def observed_run(reader, image, **kwargs):
        state["call"] += 1
        number = state["call"]
        tick = time.perf_counter()
        event("ocr_call_start", call=number, shape=list(image.shape),
              detector=getattr(reader, "detect_network", None), options=kwargs)
        try:
            result = original_run(reader, image, **kwargs)
        except BaseException as exc:
            event("ocr_call_error", call=number, seconds=time.perf_counter() - tick,
                  error=f"{type(exc).__name__}: {exc}")
            raise
        event("ocr_call_end", call=number, seconds=time.perf_counter() - tick,
              tokens=len(result[0]), fallback=result[1])
        return result
    easyocr._run_easyocr = observed_run

    original_recognize = easyocr.EasyOCRBackend.recognize_page
    @functools.wraps(original_recognize)
    def observed_recognize(self, image, page_index, page_bbox=None, **kwargs):
        state["request"] += 1
        number = state["request"]
        tick = time.perf_counter()
        event("ocr_request_start", request=number, bbox=to_plain_data(page_bbox),
              size=getattr(image, "size", None), options=kwargs)
        try:
            result = original_recognize(self, image, page_index, page_bbox, **kwargs)
        except BaseException as exc:
            event("ocr_request_error", request=number, seconds=time.perf_counter() - tick,
                  error=f"{type(exc).__name__}: {exc}")
            raise
        event("ocr_request_end", request=number, seconds=time.perf_counter() - tick,
              passes=self.last_pass_count, candidates=self._last_candidate_diagnostics,
              tokens=[{"text": token.text, "confidence": token.confidence,
                       "bbox": to_plain_data(token.bbox)} for token in result])
        return result
    easyocr.EasyOCRBackend.recognize_page = observed_recognize

    original_assemble = api.assemble_page
    @functools.wraps(original_assemble)
    def observed_assemble(*args, **kwargs):
        page = original_assemble(*args, **kwargs)
        single = SimpleNamespace(pages=[page], diagnostics=SimpleNamespace(facts={}))
        save(directory / f"page_{page.page_index + 1:03}.json", {
            "page": page.page_index + 1, "reading_text": page.reading_text,
            "raw_text": page.raw_text, "markdown": render_markdown(single),
            "diagnostics": to_plain_data(page.diagnostics),
            "table_count": len(page.tables), "region_count": len(page.regions),
        })
        event("page_end", processing_time_ms=page.diagnostics.processing_time_ms,
              characters=len(page.reading_text), warnings=page.diagnostics.warnings)
        return page
    api.assemble_page = observed_assemble

    original_extract = api.PdfTextExtractor.extract
    @functools.wraps(original_extract)
    def observed_extract(self, path, password=None, *, progress_callback=None):
        save(directory / "effective_config.json", to_plain_data(self.config))
        def progress(current, total):
            state["page"] = current
            state["phase"] = "page_initial"
            event("page_start", total=total)
            if progress_callback is not None:
                progress_callback(current, total)
        tick = time.perf_counter()
        result = original_extract(self, path, password=password, progress_callback=progress)
        duration = time.perf_counter() - tick
        # Separate final assembled diagnostics/text from the pre-document snapshots.
        save(directory / "document.json", result.to_dict())
        (directory / "diagnostic.md").write_text(render_markdown(result, diagnostic=True))
        save(directory / "extraction_summary.json", {
            "extract_with_observer_s": duration,
            "pages": len(result.pages), "diagnostics": to_plain_data(result.diagnostics),
        })
        event("document_end", pages=len(result.pages), seconds=duration,
              status=result.diagnostics.status.value)
        return result
    api.PdfTextExtractor.extract = observed_extract
    event("observer_ready", pid=os.getpid())


def resources(pid):
    sample = {"monotonic": time.monotonic(), "time_unix": time.time()}
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        wanted = {"VmRSS", "VmHWM", "VmSwap", "Threads"}
        sample.update({key: value.strip() for line in status.splitlines()
                       if ":" in line for key, value in [line.split(":", 1)] if key in wanted})
        stat = Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()
        sample.update(minor_faults=int(stat[7]), major_faults=int(stat[9]),
                      user_ticks=int(stat[11]), system_ticks=int(stat[12]))
        memory = Path("/proc/meminfo").read_text()
        keys = {"MemAvailable", "MemTotal", "SwapFree", "SwapTotal"}
        sample["host_memory"] = {key: value.strip() for line in memory.splitlines()
                                 for key, value in [line.split(":", 1)] if key in keys}
        vmstat = Path("/proc/vmstat").read_text()
        sample["host_swap"] = {key: int(value) for line in vmstat.splitlines()
                               for key, value in [line.split()] if key in {"pswpin", "pswpout"}}
    except (OSError, ValueError, IndexError):
        pass
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=Path("corpus/V3/Document_AI_V3.pdf"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--policies", nargs="+", default=["exhaustive", "baseline", "adaptive"])
    parser.add_argument("--exhaustive-output", type=Path,
                        default=Path("output/06-10-2026/Document_AI_V3.md"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    packages = {}
    for name in ("torch", "easyocr", "numpy", "pypdfium2", "Pillow", "opencv-python-headless"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    save(args.output_dir / "manifest.json", {
        "started_unix": time.time(), "python": sys.executable, "platform": platform.platform(),
        "pdf": str(args.pdf.resolve()), "pdf_sha256": digest(args.pdf), "packages": packages,
        "source_sha256": {str(p.relative_to(root)): digest(p) for p in sorted((root / "src").rglob("*.py"))},
        "cpu_count": os.cpu_count(), "clock_ticks": os.sysconf("SC_CLK_TCK"),
        "environment": {key: value for key, value in os.environ.items()
                        if key.startswith(("EASYOCR_", "OCR_", "OMP_", "MKL_", "TORCH_"))},
        "policies": args.policies,
    })
    observer = args.output_dir.resolve() / "observer"
    observer.mkdir(exist_ok=True)
    (observer / "sitecustomize.py").write_text(
        "from audit_cli_execution import install_observer\ninstall_observer()\n")
    for policy in args.policies:
        directory = args.output_dir.resolve() / policy
        directory.mkdir(exist_ok=True)
        output = args.exhaustive_output if policy == "exhaustive" else directory / "output.md"
        command = [sys.executable, "-m", "structured_pdf_text.cli", "extract", str(args.pdf),
                   "--mode", "balanced", "--ocr-quality-policy", policy,
                   "--output", "markdown", "-o", str(output)]
        environment = os.environ.copy()
        environment["AUDIT_RUN_DIR"] = str(directory)
        environment["PYTHONPATH"] = os.pathsep.join([str(observer), str(root / "scripts"),
                                                     str(root / "src"), environment.get("PYTHONPATH", "")])
        if output.exists():
            backup = directory / "previous_output.md"
            backup.write_bytes(output.read_bytes())
        started = time.monotonic()
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(command, cwd=root, env=environment, stdout=stdout, stderr=stderr)
            status = {"policy": policy, "pid": process.pid, "command": command,
                      "state": "running", "started_unix": time.time()}
            save(args.output_dir / "status.json", status)
            save(directory / "run.json", status)
            print(json.dumps(status), flush=True)
            with (directory / "resources.jsonl").open("w", buffering=1) as samples:
                while process.poll() is None:
                    samples.write(json.dumps(resources(process.pid)) + "\n")
                    time.sleep(2)
            status.update(state="finished", exit_code=process.returncode,
                          wall_s=time.monotonic() - started, finished_unix=time.time())
            if output.exists():
                status["output_sha256"] = digest(output)
                status["output_bytes"] = output.stat().st_size
                if output.resolve() != (directory / "output.md").resolve():
                    (directory / "output.md").write_bytes(output.read_bytes())
            save(directory / "run.json", status)
            save(args.output_dir / "status.json", status)
            print(json.dumps(status), flush=True)
    save(args.output_dir / "status.json", {"state": "complete", "finished_unix": time.time()})


if __name__ == "__main__":
    main()
