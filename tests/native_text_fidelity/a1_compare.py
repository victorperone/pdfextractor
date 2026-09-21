"""Occurrence-preserving comparison for the A1 native-text audit.

This module has no dependency on the corpus or on the production assembler.
It compares reference units to character evidence supplied by either the raw
PDFium capture or the production adapter.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

try:
    from .a1_schema import AlignmentRecord, write_json, write_jsonl
except ImportError:  # script execution from this directory
    from a1_schema import AlignmentRecord, write_json, write_jsonl


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """Explicit comparison choices; these are recorded in run metadata."""

    whitespace_normalization: str = "unicode_whitespace_to_single_ascii_space"
    unicode_normalization: str = "NFKC_only_for_diagnostic"
    geometry_tolerance_factor: float = 1.75
    minimum_geometry_tolerance_pt: float = 2.0
    ambiguity_score_delta: float = 0.12


@dataclass(frozen=True, slots=True)
class _Char:
    index: int
    text: str
    bbox: tuple[float, float, float, float] | None
    font_size: float | None
    angle: float | None
    mapping_invalid: bool
    raw: Mapping[str, Any]


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        values = [value.get(key) for key in ("x0", "y0", "x1", "y1")]
    else:
        values = list(value)
    if len(values) != 4 or any(item is None for item in values):
        return None
    try:
        result = tuple(float(item) for item in values)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    x0, y0, x1, y1 = result
    return result if x1 > x0 and y1 > y0 else None


def _char_index(row: Mapping[str, Any], fallback: int) -> int:
    value = row.get("char_index", row.get("index", fallback))
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_char(row: Mapping[str, Any], fallback: int) -> _Char:
    bbox = row.get("bbox_top_origin_pt", row.get("bbox"))
    return _Char(
        index=_char_index(row, fallback),
        text=str(row.get("text", "")),
        bbox=_bbox(bbox) if row.get("bbox_valid", True) is not False else None,
        font_size=_number(row.get("font_size", row.get("font_size_pt"))),
        angle=_number(row.get("angle", row.get("rotation_deg"))),
        mapping_invalid=bool(
            row.get("unicode_mapping_failed", False)
            or row.get("mapping_invalid", False)
            or (row.get("unicode_codepoint") is None and row.get("text", "") != "")
        ),
        raw=row,
    )


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _unit_bbox(unit: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    return _bbox(unit.get("bbox_top_origin_pt", unit.get("bbox")))


def _expand(box: tuple[float, float, float, float], amount: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    return x0 - amount, y0 - amount, x1 + amount, y1 + amount


def _area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _geometry_score(unit_box: tuple[float, float, float, float], char_box: tuple[float, float, float, float]) -> float:
    inter = _intersection(unit_box, char_box)
    union = _area(unit_box) + _area(char_box) - inter
    iou = inter / union if union else 0.0
    ux, uy = (unit_box[0] + unit_box[2]) / 2, (unit_box[1] + unit_box[3]) / 2
    cx, cy = (char_box[0] + char_box[2]) / 2, (char_box[1] + char_box[3]) / 2
    scale = max(1.0, unit_box[2] - unit_box[0], unit_box[3] - unit_box[1])
    distance = math.hypot(ux - cx, uy - cy) / scale
    return iou + max(0.0, 1.0 - distance)


def _normal_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _non_space(text: str) -> str:
    return "".join(character for character in text if not character.isspace())


def _is_subsequence(shorter: str, longer: str) -> bool:
    position = 0
    for character in longer:
        if position < len(shorter) and shorter[position] == character:
            position += 1
    return position == len(shorter)


def _text_category(expected: str, observed: str, *, fragmented: bool) -> tuple[str, tuple[str, ...]]:
    if expected == observed:
        return ("fragmented_but_complete" if fragmented else "matched_exact", ())
    if _normal_space(expected) == _normal_space(observed):
        return "spacing_changed", ("only_authorized_whitespace_view_matches",)
    expected_nfkc = unicodedata.normalize("NFKC", expected)
    observed_nfkc = unicodedata.normalize("NFKC", observed)
    if expected_nfkc == observed_nfkc:
        return "unicode_substitution", ("NFKC_diagnostic_view_matches",)
    expected_non_space, observed_non_space = _non_space(expected), _non_space(observed)
    if len(observed_non_space) < len(expected_non_space) and _is_subsequence(observed_non_space, expected_non_space):
        return "non_space_character_missing", ("observed_non_space_is_a_subsequence",)
    if not observed_non_space:
        return "text_missing", ("candidate_has_no_non_space_characters",)
    return "text_missing", ("literal_and_authorized_views_differ",)


def _candidate_chars(
    unit: Mapping[str, Any],
    chars: Sequence[_Char],
    policy: ComparisonPolicy,
) -> tuple[list[_Char], list[tuple[float, _Char]], tuple[str, ...]]:
    box = _unit_bbox(unit)
    if box is None:
        return [], [], ("reference_bbox_invalid_or_missing",)
    font_size = _number(unit.get("font_size_pt")) or 8.0
    tolerance = max(policy.minimum_geometry_tolerance_pt, font_size * policy.geometry_tolerance_factor)
    expanded = _expand(box, tolerance)
    strict: list[tuple[float, _Char]] = []
    relaxed: list[tuple[float, _Char]] = []
    for char in chars:
        if char.bbox is None:
            continue
        if _intersection(expanded, char.bbox) <= 0:
            continue
        score = _geometry_score(box, char.bbox)
        relaxed.append((score, char))
        overlap = _intersection(box, char.bbox)
        char_area = _area(char.bbox)
        center_inside = (
            box[0] <= (char.bbox[0] + char.bbox[2]) / 2 <= box[2]
            and box[1] <= (char.bbox[1] + char.bbox[3]) / 2 <= box[3]
        )
        # Font-metric reference boxes often meet the descender/ascender of an
        # adjacent line by a fraction of a point.  Require a meaningful glyph
        # overlap unless the glyph centre is in the unit box.
        if center_inside or (char_area and overlap / char_area >= 0.25):
            strict.append((score, char))
    scored = strict or relaxed
    scored.sort(key=lambda item: item[1].index)
    reasons: list[str] = []
    if len(scored) > max(8, len(str(unit.get("exact_text", ""))) * 2):
        reasons.append("large_candidate_window")
    if len(scored) >= 2:
        best = max(score for score, _ in scored)
        near = sum(score >= best - policy.ambiguity_score_delta for score, _ in scored)
        if near > max(1, len(str(unit.get("exact_text", ""))) // 2):
            reasons.append("multiple_nearby_candidates")
    return [char for _, char in scored], scored, tuple(reasons)


def compare_page_units(
    units: Sequence[Mapping[str, Any]],
    characters: Sequence[Mapping[str, Any]],
    *,
    page: int,
    source_stage: str,
    policy: ComparisonPolicy | None = None,
) -> list[AlignmentRecord]:
    """Align reference units and character occurrences one-to-one.

    The returned records include one row per reference unit and one row per
    unconsumed native character.  ``source_stage`` should be ``raw_pdfium`` or
    ``native_adapter``; no reading-order decision is made here.
    """

    if source_stage not in {"raw_pdfium", "native_adapter"}:
        raise ValueError(f"unsupported source stage: {source_stage}")
    policy = policy or ComparisonPolicy()
    chars = [_as_char(row, index) for index, row in enumerate(characters)]
    consumed: set[int] = set()
    records: list[AlignmentRecord] = []

    for unit in units:
        unit_id = str(unit.get("unit_id", ""))
        expected = str(unit.get("exact_text", ""))
        if not unit.get("visible", True):
            records.append(AlignmentRecord(page, source_stage, unit_id, expected, None, "not_assessable", reasons=("reference_unit_not_visible",)))
            continue
        box = _unit_bbox(unit)
        if box is None:
            records.append(AlignmentRecord(page, source_stage, unit_id, expected, None, "not_assessable", reasons=("reference_bbox_invalid_or_missing",)))
            continue
        candidates, scored, candidate_reasons = _candidate_chars(unit, chars, policy)
        available = [char for char in candidates if char.index not in consumed]
        if not available:
            category = "duplicate_occurrence" if candidates else "text_missing"
            reasons = ("all_geometry_candidates_already_consumed",) if candidates else ("no_geometry_candidate",)
            records.append(AlignmentRecord(page, source_stage, unit_id, expected, None, category, tuple(char.index for char in candidates), reasons=reasons))
            continue

        # Preserve native/source draw order for evidence, while geometry—not
        # that order—is the only basis for the association.
        observed = "".join(char.text for char in available)
        indices = tuple(char.index for char in available)
        mapping_invalid = any(char.mapping_invalid for char in available)
        category, text_reasons = _text_category(expected, observed, fragmented=len(available) > 1)
        reasons = tuple(dict.fromkeys((*candidate_reasons, *text_reasons)))
        if mapping_invalid:
            category = "native_mapping_invalid"
            reasons = (*reasons, "character_unicode_mapping_flagged_or_unavailable")
        ambiguous = "multiple_nearby_candidates" in candidate_reasons and len(available) != len(candidates)
        if ambiguous and category == "matched_exact":
            category = "geometry_ambiguous"
            reasons = (*reasons, "one_to_one_choice_depends_on_geometry")
        consumed.update(indices)
        records.append(
            AlignmentRecord(
                page,
                source_stage,
                unit_id,
                expected,
                observed,
                category,
                tuple(char.index for char in candidates),
                indices,
                len(candidates),
                reasons,
                evidence={
                    "font_size_pt": unit.get("font_size_pt"),
                    "rotation_deg": unit.get("rotation_deg", 0),
                    "logical_reading_order_ignored": True,
                    "candidate_scores": [
                        {"char_index": char.index, "score": round(score, 6)}
                        for score, char in scored[:20]
                    ],
                },
            )
        )

    for char in chars:
        if char.index not in consumed and char.text:
            records.append(
                AlignmentRecord(
                    page,
                    source_stage,
                    None,
                    None,
                    char.text,
                    "unmatched_native",
                    (char.index,),
                    (),
                    1,
                    ("native_character_not_consumed_by_reference_unit",),
                    evidence={"raw_character": dict(char.raw)},
                )
            )
    return records


def summarize_alignments(records: Iterable[AlignmentRecord]) -> dict[str, Any]:
    rows = list(records)
    by_category = Counter(row.category for row in rows)
    by_page: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_stage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    units = [row for row in rows if row.unit_id is not None]
    for row in rows:
        by_page[str(row.page)][row.category] += 1
        by_stage[row.source_stage][row.category] += 1
    assessable = [row for row in units if row.category != "not_assessable"]
    exact = sum(row.category in {"matched_exact", "fragmented_but_complete"} for row in assessable)
    permissive = sum(row.category in {"matched_exact", "fragmented_but_complete", "spacing_changed", "unicode_substitution"} for row in assessable)
    return {
        "records": len(rows),
        "unit_records": len(units),
        "unmatched_native_records": sum(row.category == "unmatched_native" for row in rows),
        "by_category": dict(sorted(by_category.items())),
        "by_page": {page: dict(sorted(values.items())) for page, values in sorted(by_page.items())},
        "by_stage": {stage: dict(sorted(values.items())) for stage, values in sorted(by_stage.items())},
        "coverage_by_stage": {
            stage: {
                "strict": {
                    "numerator": sum(row.category in {"matched_exact", "fragmented_but_complete"} for row in stage_units if row.category != "not_assessable"),
                    "denominator": sum(row.category != "not_assessable" for row in stage_units),
                    "excludes_not_assessable": True,
                },
                "permissive": {
                    "numerator": sum(row.category in {"matched_exact", "fragmented_but_complete", "spacing_changed", "unicode_substitution"} for row in stage_units if row.category != "not_assessable"),
                    "denominator": sum(row.category != "not_assessable" for row in stage_units),
                    "excludes_not_assessable": True,
                },
            }
            for stage in sorted({row.source_stage for row in units})
            for stage_units in [[row for row in units if row.source_stage == stage]]
        },
        "strict": {"numerator": exact, "denominator": len(assessable), "excludes_not_assessable": True},
        "permissive": {"numerator": permissive, "denominator": len(assessable), "excludes_not_assessable": True},
    }


def compare_capture(
    reference_pages: Sequence[Mapping[str, Any]],
    raw_rows: Sequence[Mapping[str, Any]],
    adapter_rows: Sequence[Mapping[str, Any]],
    *,
    output_dir: Any,
    policy: ComparisonPolicy | None = None,
) -> tuple[list[AlignmentRecord], dict[str, Any]]:
    """Compare captured rows and write the audit alignment/summary artifacts."""

    policy = policy or ComparisonPolicy()
    raw_by_page: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    adapter_by_page: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        raw_by_page[int(row["page_index"]) + 1].append(row)
    for row in adapter_rows:
        adapter_by_page[int(row["page_index"]) + 1].append(row)
    all_records: list[AlignmentRecord] = []
    page_families = {int(reference["page"]): str(reference.get("family", "unknown")) for reference in reference_pages}
    for reference in reference_pages:
        page = int(reference["page"])
        units = reference.get("units", [])
        all_records.extend(compare_page_units(units, raw_by_page.get(page, []), page=page, source_stage="raw_pdfium", policy=policy))
        all_records.extend(compare_page_units(units, adapter_by_page.get(page, []), page=page, source_stage="native_adapter", policy=policy))
    output_path = output_dir
    write_jsonl(output_path / "unit_alignment.jsonl", all_records)
    summary = summarize_alignments(all_records)
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for row in all_records:
        by_family[page_families.get(row.page, "unknown")][row.category] += 1
    summary["by_family"] = {
        family: dict(sorted(counts.items())) for family, counts in sorted(by_family.items())
    }
    summary["policy"] = {key: getattr(policy, key) for key in policy.__dataclass_fields__}
    write_json(output_path / "page_summary.json", summary)
    return all_records, summary
