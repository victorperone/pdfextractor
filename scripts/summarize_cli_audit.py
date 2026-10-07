"""Summarize completed CLI audit runs; partial runs never receive quality scores."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_cli_execution import save
from compute_metrics import (
    _strip_md, _strip_page_header, _normalize, _lev_distance,
    compute_critical_data_metrics,
)


def lines(path):
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # An observer can be in the middle of its last write.
    return result


def kb(value):
    return int(str(value or "0").split()[0])


def summarize(directory):
    events = lines(directory / "events.jsonl")
    samples = lines(directory / "resources.jsonl")
    calls = [e for e in events if e["event"] == "ocr_call_end"]
    starts = {e["call"]: e for e in events if e["event"] == "ocr_call_start"}
    phases = defaultdict(lambda: {"calls": 0, "seconds": 0.0})
    pages = defaultdict(lambda: {"calls": 0, "ocr_call_s": 0.0})
    for call in calls:
        phases[call["phase"]]["calls"] += 1
        phases[call["phase"]]["seconds"] += call["seconds"]
        pages[call["page"]]["calls"] += 1
        pages[call["page"]]["ocr_call_s"] += call["seconds"]
    result = {"run": json.loads((directory / "run.json").read_text()),
              "pages_completed_before_document_assembly": sum(e["event"] == "page_end" for e in events),
              "last_event": events[-1] if events else None, "ocr_completed_calls": len(calls),
              "ocr_call_errors": [e for e in events if e["event"] == "ocr_call_error"],
              "phases": dict(phases), "pages": dict(pages),
              "slowest_calls": sorted([{**call, "start": starts.get(call["call"])} for call in calls],
                                      key=lambda e: e["seconds"], reverse=True)[:30]}
    if samples:
        result["resources"] = {
            "rss_peak_kib": max(kb(s.get("VmHWM")) for s in samples),
            "rss_sample_peak_kib": max(kb(s.get("VmRSS")) for s in samples),
            "process_swap_peak_kib": max(kb(s.get("VmSwap")) for s in samples),
            "host_mem_available_min_kib": min(kb(s["host_memory"]["MemAvailable"]) for s in samples if "host_memory" in s),
            "host_swap_in_delta_pages": samples[-1].get("host_swap", {}).get("pswpin", 0) - samples[0].get("host_swap", {}).get("pswpin", 0),
            "host_swap_out_delta_pages": samples[-1].get("host_swap", {}).get("pswpout", 0) - samples[0].get("host_swap", {}).get("pswpout", 0),
            "major_faults_delta": samples[-1].get("major_faults", 0) - samples[0].get("major_faults", 0),
            "cpu_ticks_delta": sum(samples[-1].get(k, 0) - samples[0].get(k, 0) for k in ("user_ticks", "system_ticks")),
            "sample_interval_s": 2,
        }
    doc_path = directory / "document.json"
    if doc_path.exists():
        document = json.loads(doc_path.read_text())
        result["final_document"] = {"page_count": len(document["pages"]),
                                    "diagnostics": document["diagnostics"]}
        result["page_facts"] = [{"page": p["page_index"] + 1, "diagnostics": p["diagnostics"]}
                                for p in document["pages"]]
    return result


def quality(directory, reference_pages, page_modes, expected_controls):
    doc_path = directory / "document.json"
    if not doc_path.exists():
        return None
    document = json.loads(doc_path.read_text())
    hypothesis = (directory / "diagnostic.md").read_text()
    pattern = re.compile(r"^## Página (\d+)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(hypothesis))
    hyp_pages = {int(m.group(1)): hypothesis[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(hypothesis)].strip()
                 for i, m in enumerate(matches)}
    entries = []
    totals = defaultdict(Counter)
    critical_by_group = defaultdict(list)
    for number, reference in reference_pages.items():
        hyp = hyp_pages.get(number, "")
        ref = _strip_page_header(reference)
        text_ref, text_hyp = _strip_md(ref), _strip_md(hyp)
        edits = _lev_distance(list(text_hyp), list(text_ref))
        ref_words, hyp_words = _normalize(ref).split(), _normalize(hyp).split()
        word_edits = _lev_distance(hyp_words, ref_words)
        reference_control = f"QA-P{number:03}"
        critical = compute_critical_data_metrics(text_hyp, text_ref)
        controls = hyp.count(reference_control)
        expected_control = expected_controls.get(number, True)
        entries.append({"page": number, "mode": page_modes.get(number),
                        "reference_chars": len(text_ref), "hypothesis_chars": len(text_hyp),
                        "char_edits": edits, "cer_text_only": edits / max(1, len(text_ref)),
                        "word_edits": word_edits, "reference_words": len(ref_words),
                        "wer": word_edits / max(1, len(ref_words)),
                        "control_expected": expected_control,
                        "control_occurrences": controls,
                        "critical_data": critical,
                        "hypothesis_sha256": hashlib.sha256(hyp.encode()).hexdigest()})
        for group in ("all", page_modes.get(number, "unknown")):
            totals[group].update(char_edits=edits, reference_chars=len(text_ref),
                                 word_edits=word_edits, reference_words=len(ref_words),
                                 pages=1, control_pages_expected=int(expected_control),
                                 control_pages_present=int(expected_control and controls > 0),
                                 control_pages_duplicated=int(expected_control and controls > 1))
            critical_by_group[group].append(critical)
    aggregated = {group: {**values, "cer_text_only": values["char_edits"] / max(1, values["reference_chars"]),
                          "wer": values["word_edits"] / max(1, values["reference_words"])}
                  for group, values in totals.items()}
    for group, records in critical_by_group.items():
        aggregated[group]["critical_data_macro"] = {
            key: sum(record[key] for record in records) / len(records)
            for key in records[0]
        }
    return {"normalization": "Existing compute_metrics._strip_md and _normalize; decorative furniture retained as requested; page delimiters removed; no ground-truth-specific text corrections. Critical metrics use existing multiset regex matching per page, then macro averaging (including its perfect-score convention when a reference category is absent). Controls count exact literal substrings; only manifest-expected controls enter coverage. These metrics do not isolate OCR from title/furniture/configuration differences.",
            "aggregate": aggregated, "pages": entries,
            "document_status": document["diagnostics"]["status"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--quality", action="store_true", help="Run only after OCR benchmarks finish, to avoid CPU contention")
    args = parser.parse_args()
    reference = Path("corpus/V3/Document_AI_V3.md").read_text()
    pattern = re.compile(r"^##\s+Página\s+0*(\d+)\b[^\n]*", re.MULTILINE)
    matches = list(pattern.finditer(reference))
    ref_pages = {int(m.group(1)): reference[m.start():matches[i + 1].start() if i + 1 < len(matches) else len(reference)].strip()
                 for i, m in enumerate(matches)}
    manifesto = json.loads(Path("corpus/V3/Document_AI_V3_MANIFESTO.json").read_text())
    modes = {p["page"]: p["mode"] for p in manifesto["pages"]}
    expected_controls = {p["page"]: p.get("expected_page_control", True)
                         for p in manifesto["pages"]}
    summaries, qualities = {}, {}
    for policy in ("baseline", "adaptive", "exhaustive"):
        directory = args.directory / policy
        if not (directory / "run.json").exists():
            continue
        summaries[policy] = summarize(directory)
        save(directory / "performance_summary.json", summaries[policy])
        if args.quality:
            qualities[policy] = quality(directory, ref_pages, modes, expected_controls)
            save(directory / "quality_summary.json", qualities[policy])
        print(json.dumps({"policy": policy, "state": summaries[policy]["run"]["state"],
                          "exit_code": summaries[policy]["run"].get("exit_code"),
                          "wall_s": summaries[policy]["run"].get("wall_s"),
                          "pages_completed": summaries[policy]["pages_completed_before_document_assembly"],
                          "ocr_calls": summaries[policy]["ocr_completed_calls"],
                          "resources": summaries[policy].get("resources"),
                          "quality": qualities.get(policy, {}).get("aggregate") if qualities.get(policy) else None}, ensure_ascii=False))
    save(args.directory / "comparison.json", {"performance": summaries, "quality": qualities})


if __name__ == "__main__":
    main()
