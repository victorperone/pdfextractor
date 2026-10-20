"""Validated metadata and scalar access for canonical page diagnostics."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

from .aggregation import _is_applicable_value
from .reference_inputs import page_number

REPORTING_POLICY = "canonical-page-diagnostics-v1"


def _page_entries(data: dict, source: str) -> dict[int, dict]:
    entries = data.get("pages", [])
    if not isinstance(entries, list):
        raise ValueError(f"{source} pages must be a list")
    result: dict[int, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{source} page entries must be objects")
        number = page_number(entry.get("page", entry.get("page_num")))
        if "page_num" in entry and page_number(entry["page_num"]) != number:
            raise ValueError(f"Conflicting page IDs in {source} page {number}")
        if number in result:
            raise ValueError(f"Duplicate page {number} in {source}")
        result[number] = deepcopy(entry)
    return result


def corpus_page_metadata(path: Path | None) -> dict[int, dict]:
    """Read labels only from the corpus manifesto, including metadata-only V3."""
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Corpus manifesto must be a JSON object")
    result = {}
    for page, entry in _page_entries(data, "corpus manifesto").items():
        family, conditions = entry.get("family"), entry.get("conditions")
        if family is not None and not isinstance(family, str):
            raise ValueError(f"Corpus page {page} family must be a string or null")
        if isinstance(conditions, str):
            conditions = [conditions]
        if conditions is None:
            conditions = []
        if not isinstance(conditions, list) or any(not isinstance(c, str) for c in conditions):
            raise ValueError(f"Corpus page {page} conditions must contain strings")
        result[page] = {"family": family or None,
                        "conditions": sorted(set(c for c in conditions if c))}
    return result


def run_page_metadata(metadata: dict) -> dict[int, dict]:
    """Preserve observed page outcomes without substituting corpus labels."""
    return _page_entries(metadata, "run manifest")


def finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def metric_value(data: dict, path: str) -> float | None:
    """Only finite, applicable canonical scalar outcomes may enter statistics."""
    parts = path.split(".")
    node = data if parts[0] == "integrity" else data.get("quality", {})
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    return float(value) if _is_applicable_value(value, node.get("status")) else None


def word_deletion_rate(data: dict) -> float | None:
    """Use actual WER D/N counts; unavailable edits are never zero omissions."""
    metric = data.get("quality", {}).get("text", {}).get("wer_sequential", {})
    if not _is_applicable_value(metric.get("value"), metric.get("status")):
        return None
    counts = metric.get("counts", {})
    deletions, total = counts.get("D"), counts.get("ref_words")
    if (not finite_number(deletions) or not finite_number(total)
            or deletions < 0 or total <= 0 or deletions > total):
        return None
    return deletions / total
