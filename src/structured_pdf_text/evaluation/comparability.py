"""One fail-closed input/configuration contract for API and CLI comparison."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .reference_hash import REFERENCE_HASH_PROTOCOL

COMPARABILITY_POLICY = "canonical-inputs-and-policies-v1"
CANONICAL_SCHEMA = "evaluation-3.0.0"

# Outcomes (including partial runs and missing pages) are deliberately absent.
_INPUT_FIELDS = (
    "reference_effective_sha256", "reference_serialization_version",
    "pdf_sha256", "manifest_sha256", "mode", "selected_pages",
)
_EVALUATOR_FIELDS = {
    "evaluator_version": "metric_definitions_version",
    "normalization_policy": "normalization_policy",
    "parser_id": "parser",
    "page_syntax_policy": "page_syntax_policy",
    "paragraph_policy": "paragraph_policy",
    "alignment_policy": "alignment_policy",
    "entity_policy": "entity_policy",
    "text_projection_policy": "text_projection_policy",
    "edit_distance_policy": "edit_distance_policy",
    "html_table_policy": "html_table_policy",
    "table_metric_policy": "table_metric_policy",
    "aggregation_policy": "aggregation_policy",
    "comparability_policy": "comparability_policy",
    "resource_policy": "resource_policy",
}
_DIGEST_FIELDS = {"reference_effective_sha256", "pdf_sha256", "manifest_sha256"}
_RESOURCE_FIELDS = {
    "max_text_edit_cells", "max_table_edit_cells", "max_alignment_pairs",
    "max_similarity_text_product",
}
COMPARABILITY_FIELDS = (
    "schema_version", *_INPUT_FIELDS, "benchmark_protocol_id", *_EVALUATOR_FIELDS,
)


def _section(data: Mapping, key: str) -> Mapping:
    value = data.get(key)
    return value if isinstance(value, Mapping) else {}


def _pages(value: Any) -> tuple[int, ...] | None:
    # JSON arrays of unique positive integers; never coerce 1.5, True or '1'.
    if not isinstance(value, list) or not value:
        return None
    if any(type(page) is not int or page <= 0 for page in value):
        return None
    if len(set(value)) != len(value):
        return None
    return tuple(sorted(value))


def comparability_key(data: Any) -> dict[str, Any]:
    """Extract identities without coercing malformed inputs into valid values.

    Call validate_comparison to validate a key, even when comparing it to itself.
    Source-file hashes, engine identities and run outcomes are not part of it.
    """
    if not isinstance(data, Mapping):
        data = {}
    inputs = _section(data, "inputs")
    evaluator = _section(data, "evaluator")
    operational = _section(data, "operational")
    key = {"schema_version": data.get("schema_version")}
    key.update({field: inputs.get(field) for field in _INPUT_FIELDS})
    key["selected_pages"] = _pages(inputs.get("selected_pages"))
    for field in _DIGEST_FIELDS:
        value = key[field]
        if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
            key[field] = value.lower()
    key["benchmark_protocol_id"] = operational.get("benchmark_protocol_id")
    key.update({alias: evaluator.get(field) for alias, field in _EVALUATOR_FIELDS.items()})
    return key


def validate_comparison(all_data: Sequence[tuple[str, Any]]) -> list[str]:
    """Validate every result and compare all identities with the first result.

    A single run must also have valid provenance. Legacy/raw reference hashes
    cannot replace the effective hash; schemas other than canonical v3 need
    regrading. Missing policies never establish equivalence via None == None.
    """
    errors: list[str] = []
    keys = [(label, comparability_key(data)) for label, data in all_data]
    for label, key in keys:
        for field in COMPARABILITY_FIELDS:
            value = key[field]
            prefix = f"Engine '{label}': comparability input '{field}'"
            if value is None or value == "":
                errors.append(f"{prefix} is missing or invalid. Runs are not comparable.")
            elif field == "schema_version":
                if value != CANONICAL_SCHEMA:
                    errors.append(f"{prefix} is unsupported: {value!r}. Re-evaluate with regrade_existing.py.")
            elif field == "reference_serialization_version":
                if value != REFERENCE_HASH_PROTOCOL:
                    errors.append(f"Engine '{label}': unsupported reference serialization protocol {value!r}.")
            elif field in _DIGEST_FIELDS:
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
                    errors.append(f"{prefix} is not a 64-character SHA-256 digest.")
            elif field == "resource_policy":
                if (not isinstance(value, Mapping) or not _RESOURCE_FIELDS <= value.keys()
                        or any(not isinstance(k, str) or type(v) is not int or v <= 0
                               for k, v in value.items())):
                    errors.append(f"{prefix} must declare positive integer resource limits.")
            elif field != "selected_pages":
                if not isinstance(value, str) or not value.strip() or value != value.strip():
                    errors.append(f"{prefix} must be a nonempty, unpadded string.")
        if (key["comparability_policy"] is not None
                and key["comparability_policy"] != COMPARABILITY_POLICY):
            errors.append(f"Engine '{label}': unsupported comparability_policy {key['comparability_policy']!r}.")

    if keys:
        first_label, first = keys[0]
        for label, key in keys[1:]:
            for field in COMPARABILITY_FIELDS:
                if first[field] != key[field]:
                    errors.append(
                        f"Comparability mismatch on '{field}': '{first_label}'={first[field]!r} "
                        f"vs '{label}'={key[field]!r}. Runs are not comparable."
                    )
    return errors
