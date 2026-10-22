"""Finite JSON projection for pipeline artifacts, independent of evaluation."""
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json

from .contracts import CONTRACT_VERSION, OCRPipelineResult, OCRWorkRequest, finite


def to_payload(value: OCRWorkRequest | OCRPipelineResult) -> dict:
    """Project immutable contracts without dataclasses.asdict on frozen maps.

This is an artifact projection, not an unvalidated wire deserializer. Consumers
construct and validate typed requests/results at the boundary. Evaluation schema
3.0.0 and extraction manifest schema 2.0 are unaffected.
"""
    if not isinstance(value, (OCRWorkRequest, OCRPipelineResult)) or value.contract_version != CONTRACT_VERSION:
        raise ValueError("Expected a supported pipeline request or result")

    def project(item):
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Mapping):
            return {key: project(nested) for key, nested in item.items()}
        if is_dataclass(item):
            return {field.name: project(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, tuple):
            return [project(nested) for nested in item]
        if isinstance(item, float):
            finite(item)
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        raise ValueError(f"Unsupported pipeline artifact value: {type(item).__name__}")

    result = {"schema_version": "ocr-pipeline-1.0", "kind": "work_request" if isinstance(value, OCRWorkRequest) else "pipeline_result", **project(value)}
    json.dumps(result, allow_nan=False)
    return result
