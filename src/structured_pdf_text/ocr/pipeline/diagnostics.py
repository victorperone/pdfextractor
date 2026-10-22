"""Normalize engine-local diagnostics at the legacy adapter boundary."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import math


_SNAPSHOT_FIELDS = {
    "baseline_quality": "last_baseline_quality",
    "image_profile": "last_image_profile",
    "recovery_triggered": "last_recovery_triggered",
    "recovery_reasons": "last_recovery_reasons",
    "selected_variant": "last_selected_variant",
    "variants_attempted": "last_variants_attempted",
    "variant_scores": "last_variant_scores",
    "variants_succeeded": "last_variants_succeeded",
    "candidate_count": "last_candidate_count",
    "candidate_metrics": "last_candidate_metrics",
    "fusion_replacements_attempted": "last_fusion_replacements_attempted",
    "fusion_replacements_accepted": "last_fusion_replacements_accepted",
    "fusion_replacements_rolled_back": "last_fusion_replacements_rolled_back",
    "fusion_lost_clusters": "last_fusion_lost_clusters",
    "fusion_conflict_clusters": "last_fusion_conflict_clusters",
    "fusion_duplicate_clusters": "last_fusion_duplicate_clusters",
    "consensus_replacements": "last_consensus_replacements",
    "consensus_insertions": "last_consensus_insertions",
    "orientation_selected": "last_orientation_selected",
    "orientation_attempts": "last_orientation_attempts",
    "enhancement_orientation": "last_enhancement_orientation",
    "early_stop": "last_early_stop",
    "deskew_angle": "last_deskew_angle",
}


def _plain(value):
    """Copy supported diagnostic values into finite JSON-shaped data."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _plain(getattr(value, item.name)) for item in fields(value)}
    return None


class LegacyDiagnosticsProjector:
    """Project mutable engine state into a stable vocabulary outside core."""

    @staticmethod
    def snapshot_attributes(engine) -> dict:
        """Finite copies for an isolated runtime; never serialize live objects."""
        names = (*_SNAPSHOT_FIELDS.values(), "last_pass_count", "last_batch_count", "last_attempt_errors")
        return {name: _plain(getattr(engine, name)) for name in names if hasattr(engine, name)}

    @staticmethod
    def reset(engine) -> None:
        reset = getattr(engine, "reset_page_diagnostics", None)
        if callable(reset):
            reset()

    @staticmethod
    def operation_metrics(engine) -> dict:
        return {
            "passes": int(_plain(getattr(engine, "last_pass_count", 0)) or 0),
            "batches": int(_plain(getattr(engine, "last_batch_count", 0)) or 0),
            "attempt_errors": list(_plain(getattr(engine, "last_attempt_errors", [])) or []),
        }

    @staticmethod
    def snapshot(engine, *, engine_name: str, provider: str | None = None) -> dict:
        """Capture per-request evidence without consuming page accumulators."""
        result = {"engine": engine_name, "provider": provider}
        for public_name, attribute in _SNAPSHOT_FIELDS.items():
            if hasattr(engine, attribute):
                result[public_name] = _plain(getattr(engine, attribute))
        return result

    @staticmethod
    def consume(engine, *, engine_name: str, provider: str | None = None) -> dict:
        result = LegacyDiagnosticsProjector.snapshot(engine, engine_name=engine_name, provider=provider)

        consume = getattr(engine, "consume_page_diagnostics", None)
        legacy = consume() if callable(consume) else {}
        if isinstance(legacy, Mapping):
            # Keep historical keys at the public projection boundary.
            normalized = _plain(legacy)
            result["engine_specific"] = normalized
            result.update(normalized)

        fallback_counts = [
            value for key, value in result.items()
            if key.endswith("fallback_count") and isinstance(value, (int, float))
        ]
        fallback_reasons = [
            reason for key, value in result.items()
            if key.endswith("fallback_reasons") and isinstance(value, list)
            for reason in value
        ]
        calls = sum(
            value for key, value in result.items()
            if key.endswith("_calls") and isinstance(value, (int, float))
        )
        result["fallback_count"] = sum(fallback_counts)
        result["fallback_reasons"] = fallback_reasons
        result["fallback_rate"] = result["fallback_count"] / calls if calls else 0.0
        return result

    @staticmethod
    def public_compatibility_aliases(diagnostics: Mapping) -> dict:
        """Project historical EasyOCR-named fields at the compatibility edge.

        These names remain part of the serialized diagnostics contract even
        when a different engine is selected. Keep that legacy vocabulary out
        of the extraction coordinator and derive it from normalized fields.
        """
        reasons = diagnostics.get("fallback_reasons", ())
        if not isinstance(reasons, (tuple, list)):
            reasons = ()
        return {
            "easyocr_fallback_used": bool(diagnostics.get("fallback_count", 0)),
            "easyocr_fallback_reason": "; ".join(str(reason) for reason in reasons) or None,
        }
