#!/usr/bin/env python3
"""Compare saved extraction features; never extract or change reference artifacts.

This is a refactoring equivalence check, not an OCR quality evaluator. Text,
order, cells, provenance, decisions and statuses are exact. An explicit bbox
tolerance applies only to coordinates, never to text, confidence or counts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import re
from typing import Any


def _cost_field(path: tuple[str | int, ...], key: str) -> bool:
    # Only diagnostic costs are excluded. A content field called "memory" or
    # "elapsed_s" must not disappear from the comparison.
    return "diagnostics" in path and (
        key == "processing_time_ms"
        or (path[-1:] == ("facts",) and key in {
            "memory", "timings_ms", "assemble_ms", "total_ms", "open_pdf_ms",
            "content_assembly_ms", "cross_page_table_ms",
        })
        or (path[-2:] == ("facts", "env_probe") and key == "cpu_load_1m")
    )


_LINEAGE_FIELDS = frozenset({
    "request_id", "parent_request_id", "authorization_id", "authorization_ids",
    "attempt_id", "proposal_id", "proposal_ids", "token_id", "token_ids",
    "evidence_id", "evidence_ids", "derived_from_ids", "source_token_ids",
    "raster_id", "raster_ids", "observation_id", "observation_ids",
})
_OPAQUE_UUID4 = re.compile(
    r"(?<![0-9a-f])(?:[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}"
    r"|[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})(?![0-9a-f])",
    re.IGNORECASE,
)


def _canonicalize_lineage_ids(document: dict) -> tuple[dict, int]:
    """Rename opaque UUID components consistently, preserving reference edges.

    Only explicit lineage fields participate. Text, native identifiers, model
    hashes and meaningful attempt/token suffixes remain exact. Internal tuples
    distinguish renamed components from any literal string in the JSON input.
    """
    names: dict[str, int] = {}

    def walk(value, field=None):
        if isinstance(value, dict):
            return {key: walk(value[key], key) for key in sorted(value)}
        if isinstance(value, (list, tuple)):
            return [walk(item, field) for item in value]
        if isinstance(value, str) and field in _LINEAGE_FIELDS:
            matches = list(_OPAQUE_UUID4.finditer(value))
            if matches:
                parts = []
                start = 0
                for match in matches:
                    parts.append(value[start:match.start()])
                    opaque = match.group()
                    parts.append(names.setdefault(opaque, len(names)))
                    start = match.end()
                parts.append(value[start:])
                return tuple(parts)
        return value

    result = walk(document)
    return result, len(names)


def compare_features(
    before: dict[str, Any], after: dict[str, Any], *, bbox_tolerance_pt: float = 0.0,
    max_examples: int = 100, canonicalize_lineage_ids: bool = False,
) -> dict[str, Any]:
    if not math.isfinite(bbox_tolerance_pt) or bbox_tolerance_pt < 0:
        raise ValueError("bbox_tolerance_pt must be finite and non-negative")
    if max_examples < 1:
        raise ValueError("max_examples must be positive")
    for document in (before, after):
        if not isinstance(document, dict) or not isinstance(document.get("pages"), list) or not document["pages"]:
            raise ValueError("Expected a structured document with non-empty pages")
        # Reject NaN/Inf even when present only in excluded costs.
        json.dumps(document, allow_nan=False)
    normalization = None
    if canonicalize_lineage_ids:
        before, before_count = _canonicalize_lineage_ids(before)
        after, after_count = _canonicalize_lineage_ids(after)
        normalization = {"policy": "uuid4-components-in-explicit-lineage-fields-v1",
                         "before_uuid_components": before_count, "after_uuid_components": after_count}
    counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    page_differences: dict[str, dict[str, Any]] = {}
    costs: dict[str, dict[str, Any]] = {}

    def pointer(path):
        return "/" + "/".join(str(x).replace("~", "~0").replace("/", "~1") for x in path)

    def record(path, old, new, *, missing=None):
        if any(key in path for key in ("bbox", "polygon", "polygon_px", "bbox_px")):
            category = "geometry"
        elif path and path[-1] in {"text", "reading_text", "raw_text", "normalized_text"}:
            category = "content"
        elif path and path[-1] in {"status", "ocr_outcome", "partial_reasons", "warnings"}:
            category = "status"
        elif "diagnostics" in path or "quality" in path:
            category = "decisions"
        else:
            category = "structure"
        counts[category] += 1
        item = {"path": pointer(path), "category": category, "before": old, "after": new}
        if missing:
            item["missing_in"] = missing
        if len(path) >= 2 and path[0] == "pages" and type(path[1]) is int:
            index = path[1]
            old_page = before["pages"][index].get("page_index") if index < len(before["pages"]) else None
            new_page = after["pages"][index].get("page_index") if index < len(after["pages"]) else None
            old_number = old_page + 1 if type(old_page) is int else None
            new_number = new_page + 1 if type(new_page) is int else None
            key = str(old_number) if old_number == new_number and old_number is not None else f"index:{index}"
            page = page_differences.setdefault(key, {
                "before_page": old_number, "after_page": new_number,
                "difference_count": 0, "differences_by_category": {}, "examples_by_category": {},
            })
            page["difference_count"] += 1
            by_category = page["differences_by_category"]
            by_category[category] = by_category.get(category, 0) + 1
            category_examples = page["examples_by_category"].setdefault(category, [])
            if len(category_examples) < min(3, max_examples):
                category_examples.append(item)
        if len(examples) < max_examples:
            examples.append(item)

    def walk(old, new, path=()):
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                child = (*path, key)
                if _cost_field(path, key):
                    costs[pointer(child)] = {"before": old.get(key), "after": new.get(key)}
                elif key not in old or key not in new:
                    record(child, old.get(key), new.get(key), missing="before" if key not in old else "after")
                else:
                    walk(old[key], new[key], child)
        elif isinstance(old, list) and isinstance(new, list):
            if len(old) != len(new):
                record((*path, "length"), len(old), len(new))
            for index, (left, right) in enumerate(zip(old, new)):
                walk(left, right, (*path, index))
        elif type(old) is not type(new) and not (
            type(old) in (int, float) and type(new) in (int, float)
        ):
            record(path, old, new)
        elif old != new:
            coordinate = any(key in path for key in ("bbox", "polygon"))
            if coordinate and type(old) in (int, float) and type(new) in (int, float):
                if abs(old - new) <= bbox_tolerance_pt:
                    return
            record(path, old, new)

    walk(before, after)
    result = {
        "schema_version": "ocr-equivalence-1.0",
        "equivalent": not counts,
        "bbox_tolerance_pt": bbox_tolerance_pt,
        "difference_count": sum(counts.values()),
        "differences_by_category": dict(counts),
        "differences_by_page": page_differences,
        "examples": examples,
        "examples_truncated": sum(counts.values()) > len(examples),
        "costs": costs,
    }
    if normalization is not None:
        result["lineage_id_normalization"] = normalization
    return result


def check_run_identity(before: dict, after: dict) -> list[str]:
    """Refuse to call changed models, inputs or settings refactoring equivalence."""
    required = (
        "pdf_sha256", "selected_pages", "engine", "engine_identity", "mode",
        "language", "quality_policy", "runtime_environment",
    )
    errors = []
    for key in required:
        if key not in before or key not in after or before[key] is None or after[key] is None:
            errors.append(f"missing_identity:{key}")
            continue
        old, new = before[key], after[key]
        if key == "engine_identity":
            if not isinstance(old, dict) or not isinstance(new, dict) or not old or not new:
                errors.append("missing_identity:engine_identity")
                continue
            valid = True
            for side, identity in (("before", old), ("after", new)):
                for field in ("engine", "runtime", "profile", "language", "device"):
                    if not isinstance(identity.get(field), str) or not identity[field].strip():
                        errors.append(f"missing_identity:{side}:engine_identity.{field}")
                        valid = False
                for field in ("package_versions", "artifact_hashes"):
                    if not isinstance(identity.get(field), dict) or not identity[field]:
                        errors.append(f"missing_identity:{side}:engine_identity.{field}")
                        valid = False
                if not isinstance(identity.get("extra"), dict):
                    errors.append(f"missing_identity:{side}:engine_identity.extra")
                    valid = False
                if isinstance(identity.get("artifact_hashes"), dict) and any(
                    not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                    for value in identity["artifact_hashes"].values()
                ):
                    errors.append(f"invalid_identity:{side}:artifact_hashes")
                    valid = False
            if not valid:
                continue
            # CPU load is observational; threads, models, flags and versions
            # are retained. Copy rather than mutating the saved manifests.
            old, new = json.loads(json.dumps(old)), json.loads(json.dumps(new))
            for identity in (old, new):
                extra = identity.get("extra", {})
                for observational in ("env_cpu_load_1m", "adapter", "legacy_engine"):
                    extra.pop(observational, None)
        if old != new:
            errors.append(f"identity_mismatch:{key}")
    return errors


def _parse_pages(value: str) -> tuple[int, ...]:
    pages = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise ValueError("--pages cannot contain an empty item")
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start < 1 or end < start:
                raise ValueError("--pages ranges must satisfy 1 <= start <= end")
            pages.update(range(start, end + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError("--pages uses positive, 1-based page numbers")
            pages.add(page)
    if not pages:
        raise ValueError("--pages must select at least one page")
    return tuple(sorted(pages))


def _select_feature_pages(document: dict[str, Any], pages: tuple[int, ...]) -> dict[str, Any]:
    page_map = {}
    for page in document.get("pages", ()):
        index = page.get("page_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Structured page is missing a valid zero-based page_index")
        number = index + 1
        if number in page_map:
            raise ValueError(f"Structured document repeats page {number}")
        page_map[number] = page
    missing = [number for number in pages if number not in page_map]
    if missing:
        raise ValueError(f"Selected pages are absent from a structured document: {missing}")
    return {"pages": [page_map[number] for number in pages]}


def _select_manifest_pages(manifest: dict[str, Any], pages: tuple[int, ...]) -> dict[str, Any]:
    selected = manifest.get("selected_pages")
    if not isinstance(selected, list) or not set(pages) <= set(selected):
        raise ValueError("Selected pages are not covered by a run manifest")
    result = deepcopy(manifest)
    result["selected_pages"] = list(pages)
    return result


def _normalize_config_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize fields added after legacy baseline snapshots were captured."""
    result = deepcopy(config)
    # These fields did not exist in the original baseline schema. Their null
    # value means the same as omission; a populated value remains significant.
    for field in ("ocr_settings", "rapidocr_model_size"):
        if result.get(field) is None:
            result.pop(field, None)
    # Omission in pre-GPU captures meant the same CPU default. True remains
    # significant: GPU and CPU executions must not pass as refactoring parity.
    if result.get("use_gpu") is False:
        result.pop("use_gpu")
    return result


def _markdown_page_sections(markdown: str) -> dict[int, str]:
    matches = list(re.finditer(r"^##\s+P[áa]gina\s+0*(\d+)[^\n]*", markdown, re.MULTILINE | re.IGNORECASE))
    sections = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[number] = markdown[match.start():end].strip()
    return sections


def _select_markdown_pages(markdown: str, pages: tuple[int, ...]) -> bytes:
    sections = _markdown_page_sections(markdown)
    missing = [number for number in pages if number not in sections]
    if missing:
        raise ValueError(f"Selected Markdown pages are absent: {missing}")
    return "\n\n".join(sections[number] for number in pages).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--before-manifest", required=True, type=Path)
    parser.add_argument("--after-manifest", required=True, type=Path)
    parser.add_argument("--before-config", required=True, type=Path, help="Resolved ExtractorConfig JSON")
    parser.add_argument("--after-config", required=True, type=Path, help="Resolved ExtractorConfig JSON")
    parser.add_argument("--pages", help="Compare selected 1-based pages only, e.g. '19,24,31' or '10-12'")
    parser.add_argument("--bbox-tolerance-pt", type=float, default=0.0)
    parser.add_argument("--canonicalize-lineage-ids", action="store_true",
                        help="Compare opaque UUID lineage by consistent renaming; preserve reference relationships")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        before_manifest = json.loads(args.before_manifest.read_text(encoding="utf-8"))
        after_manifest = json.loads(args.after_manifest.read_text(encoding="utf-8"))
        pages = _parse_pages(args.pages) if args.pages else None
        if pages:
            before_manifest = _select_manifest_pages(before_manifest, pages)
            after_manifest = _select_manifest_pages(after_manifest, pages)
        identity_errors = check_run_identity(before_manifest, after_manifest)
        before_config = json.loads(args.before_config.read_text(encoding="utf-8"))
        after_config = json.loads(args.after_config.read_text(encoding="utf-8"))
        if not isinstance(before_config, dict) or not before_config or not isinstance(after_config, dict) or not after_config:
            raise ValueError("Resolved config snapshots must be non-empty objects")
        before_config = _normalize_config_snapshot(before_config)
        after_config = _normalize_config_snapshot(after_config)
        if pages:
            before_config.pop("page_indices", None)
            after_config.pop("page_indices", None)
        before_features = json.loads(args.before.read_text(encoding="utf-8"))
        after_features = json.loads(args.after.read_text(encoding="utf-8"))
        if pages:
            before_features = _select_feature_pages(before_features, pages)
            after_features = _select_feature_pages(after_features, pages)
        if json.dumps(before_config, sort_keys=True, allow_nan=False) != json.dumps(after_config, sort_keys=True, allow_nan=False):
            identity_errors.append("identity_mismatch:resolved_config")
        result = compare_features(
            before_features,
            after_features,
            bbox_tolerance_pt=args.bbox_tolerance_pt,
            canonicalize_lineage_ids=args.canonicalize_lineage_ids,
        )
        markdown = []
        for manifest, path in ((before_manifest, args.before_manifest), (after_manifest, args.after_manifest)):
            artifact = Path(manifest["extracted_markdown"])
            if not artifact.is_absolute() and not artifact.is_file():
                artifact = path.parent / artifact
            contents = artifact.read_text(encoding="utf-8")
            markdown.append(_select_markdown_pages(contents, pages) if pages else contents.encode("utf-8"))
        result["markdown_identical"] = markdown[0] == markdown[1]
        result["compared_pages"] = list(pages) if pages else None
        result["identity_errors"] = identity_errors
        result["equivalent"] = result["equivalent"] and result["markdown_identical"] and not identity_errors
        # Exclusive create keeps baseline comparisons append-only by namespace.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"equivalent={result['equivalent']}; differences={result['difference_count']}; identity_errors={len(identity_errors)}")
    return 0 if result["equivalent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
