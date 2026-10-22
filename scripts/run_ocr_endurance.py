#!/usr/bin/env python3
"""Measure repeated extraction with one selected runtime and explicit cleanup.

Provision local models first. This runner records operational evidence; a
completed workload does not approve recognition quality or a product release.
Each invocation requires a fresh output directory and never imposes an OCR
initialization/inference deadline.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from structured_pdf_text import ExtractorConfig, PdfTextExtractor
from structured_pdf_text.memory import ProcessTreeMemorySampler
from structured_pdf_text.ocr.readiness import probe_static, ReadinessStatus
from structured_pdf_text.ocr.registry import public_engines, public_providers
from structured_pdf_text.renderers import render_markdown
from scripts.evaluate_e2e import _parse_pages_arg


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity():
    import structured_pdf_text

    loaded = Path(structured_pdf_text.__file__).resolve().parent
    expected = (ROOT / "src/structured_pdf_text").resolve()
    if loaded != expected:
        raise ValueError("Loaded package is outside this checkout; use a fresh interpreter or set PYTHONPATH explicitly")
    paths = sorted((ROOT / "src").rglob("*.py"))
    paths += [Path(__file__), ROOT / "scripts/evaluate_e2e.py"]
    return {str(path.relative_to(ROOT)): sha256(path) for path in paths}


def resources(process):
    """A complete process-tree snapshot, with unavailable measurements explicit."""
    import psutil

    tree_errors = []
    try:
        descendants = process.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        descendants = []
        tree_errors.append(f"children: {type(exc).__name__}: {exc}")
    members = [process, *descendants]
    snapshots = []
    errors = tree_errors
    for member in members:
        try:
            descriptor_count = member.num_handles() if sys.platform == "win32" else member.num_fds()
            snapshots.append({"pid": member.pid, "created": member.create_time(),
                              "rss_bytes": member.memory_info().rss,
                              "descriptors": descriptor_count})
        except psutil.NoSuchProcess:
            continue  # An already-reaped short-lived child does not own resources.
        except (psutil.AccessDenied, OSError, AttributeError) as exc:
            errors.append(f"pid={member.pid}: {type(exc).__name__}: {exc}")
    parent = next((item for item in snapshots if item["pid"] == process.pid), None)
    return {"parent_rss_bytes": parent["rss_bytes"] if parent else None,
            "tree_rss_bytes": sum(item["rss_bytes"] for item in snapshots) if not errors and parent else None,
            "parent_descriptors": parent["descriptors"] if parent else None,
            "tree_descriptors": sum(item["descriptors"] for item in snapshots) if not errors and parent else None,
            "descriptor_kind": "handles" if sys.platform == "win32" else "file_descriptors",
            "children": [item for item in snapshots if item["pid"] != process.pid],
            "measurement_errors": errors}


def run(config, pdf, output, *, repeat, warmup):
    import psutil

    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError("warmup must be a nonnegative integer")
    # Verify the loaded code before claiming its hashes or reserving a run.
    source_before = source_identity()
    # Reserve the complete namespace before probing or constructing a runtime.
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": "ocr-endurance-1.0", "workload_status": "error",
              "release_accepted": False, "quality_accepted": False,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "python": sys.version,
              "config": asdict(config), "repeat": repeat, "warmup": warmup,
              "pdf": str(pdf.resolve()), "pdf_sha256": sha256(pdf),
              "source_before": source_before, "iterations": []}
    process = psutil.Process()
    initial = resources(process)
    original_children = {(item["pid"], item["created"]) for item in initial["children"]}
    extractor = None
    sampler = ProcessTreeMemorySampler()
    started = time.perf_counter()
    expected_digest = None
    expected_identity = None
    measurement_complete = not initial["measurement_errors"] and initial["tree_rss_bytes"] is not None
    with (output / "samples.jsonl").open("x", encoding="utf-8") as samples:
        def sample(event, **context):
            nonlocal measurement_complete
            value = resources(process)
            measurement_complete &= not value["measurement_errors"] and value["tree_rss_bytes"] is not None
            samples.write(json.dumps({"event": event, "elapsed_s": time.perf_counter() - started,
                                      **context, **value}, allow_nan=False) + "\n")
            samples.flush()
            return value

        sample("before_init")
        try:
            readiness = probe_static(config)
            report["readiness"] = asdict(readiness)
            if readiness.status != ReadinessStatus.READY:
                raise RuntimeError(f"Static readiness failed: {readiness.status.value}: {readiness.reason_code}")
            with sampler:
                extractor = PdfTextExtractor(config)
                for index in range(warmup + repeat):
                    phase = "warmup" if index < warmup else "measured"
                    iteration_start = time.perf_counter()
                    def progress(current, total):
                        sample("page_start", iteration=index, phase=phase, current=current, total=total)
                    document = extractor.extract(pdf, progress_callback=progress)
                    markdown = render_markdown(document)
                    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                    if expected_digest is None:
                        expected_digest = digest
                        (output / "first.md").write_text(markdown, encoding="utf-8")
                        with (output / "first.features.json").open("x", encoding="utf-8") as stream:
                            json.dump(document.to_dict(), stream, ensure_ascii=False, allow_nan=False)
                            stream.write("\n")
                    engine = extractor.ocr_engine
                    identity = asdict(engine.identity) if engine is not None else None
                    if expected_identity is None and identity is not None:
                        expected_identity = identity
                    expected_pages = (set(config.page_indices) if config.page_indices is not None
                                      else set(range(document.metadata.page_count)))
                    iteration = {"index": index, "phase": phase, "pages": len(document.pages),
                                 "status": document.diagnostics.status.value,
                                 "markdown_sha256": digest, "repeatable_content": digest == expected_digest,
                                 "requested_pages_present": {page.page_index for page in document.pages} == expected_pages,
                                 "identity": identity, "elapsed_s": time.perf_counter() - iteration_start,
                                 "stable_effective_identity": identity is not None and identity == expected_identity,
                                 "page_statuses": [
                                     {"page": page.page_index + 1,
                                      "ocr_requested": page.diagnostics.facts.get("ocr_requested"),
                                      "ocr_outcome": page.diagnostics.facts.get("ocr_outcome"),
                                      "partial_reasons": page.diagnostics.facts.get("partial_reasons", []),
                                      "warnings": page.diagnostics.warnings}
                                     for page in document.pages]}
                    del document, markdown
                    gc.collect()
                    iteration["resources"] = sample("iteration_end", iteration=index, phase=phase)
                    report["iterations"].append(iteration)
                    print(json.dumps({"iteration": index, "phase": phase, "pages": iteration["pages"],
                                      "status": iteration["status"], "elapsed_s": iteration["elapsed_s"]}), flush=True)
                report["workload_status"] = "completed"
        except (Exception, KeyboardInterrupt) as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["workload_status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "error"
        finally:
            try:
                if extractor is not None:
                    extractor.close()
            except Exception as exc:
                report["close_error"] = f"{type(exc).__name__}: {exc}"
                report["workload_status"] = "error"
            gc.collect()
            final = sample("after_close")
            report["after_close"] = final
            report["remaining_new_children"] = [item for item in final["children"]
                if (item["pid"], item["created"]) not in original_children]
            report["measurements_complete"] = measurement_complete
            report["memory_sampler"] = sampler.as_dict()
            report["source_after"] = source_identity()
            report["source_unchanged"] = report["source_before"] == report["source_after"]
            report["pdf_unchanged"] = report["pdf_sha256"] == sha256(pdf)
            report["elapsed_s"] = time.perf_counter() - started
            measured = [item for item in report["iterations"] if item["phase"] == "measured"]
            report["measured_pages"] = sum(item["pages"] for item in measured)
            report["operational_checks"] = {
                "all_extractions_successful": bool(measured) and all(item["status"] == "success" for item in measured),
                "repeatable_content": bool(measured) and all(item["repeatable_content"] for item in measured),
                "all_iterations_completed": len(measured) == repeat,
                "requested_pages_present": bool(measured) and all(item["requested_pages_present"] for item in measured),
                "stable_effective_identity": bool(measured) and all(item["stable_effective_identity"] for item in measured),
                "ocr_was_exercised": bool(measured) and any(page["ocr_requested"]
                    for item in measured for page in item["page_statuses"]),
                "cleanup_children": not report["remaining_new_children"],
                "source_unchanged": report["source_unchanged"], "pdf_unchanged": report["pdf_unchanged"],
                "measurements_complete": measurement_complete,
            }
            with (output / "report.json").open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2, default=str, allow_nan=False)
                stream.write("\n")
    return report


def count(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("count must be nonnegative")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--engine", choices=public_engines(), required=True)
    parser.add_argument("--provider", choices=public_providers())
    parser.add_argument("--gpu", action="store_true", help="Request CUDA; identity records the device actually used")
    parser.add_argument("--model-size", choices=("medium", "small"))
    parser.add_argument("--cache-home", type=Path)
    parser.add_argument("--pages", help="One-based page selection, e.g. 19,21,24,31")
    parser.add_argument("--repeat", type=count, default=250)
    parser.add_argument("--warmup", type=count, default=1)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.repeat:
        parser.error("--repeat must be positive")
    if not args.pdf.is_file():
        parser.error(f"PDF not found: {args.pdf}")
    try:
        config = ExtractorConfig(
            mode="balanced", ocr_engine=args.engine, ocr_provider=args.provider,
            rapidocr_model_size=args.model_size, ocr_cache_home=args.cache_home,
            page_indices=_parse_pages_arg(args.pages) if args.pages else None,
            language="pt-BR", num_threads=args.threads, enable_tables=True,
            enable_table_cell_ocr=True, ocr_quality_policy="adaptive",
            use_gpu=args.gpu,
        )
        report = run(config, args.pdf, args.output_dir, repeat=args.repeat, warmup=args.warmup)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    print(json.dumps({"workload_status": report["workload_status"],
                      "measured_pages": report["measured_pages"],
                      "operational_checks": report["operational_checks"],
                      "release_accepted": False}, ensure_ascii=False))
    return 0 if report["workload_status"] == "completed" and all(report["operational_checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
