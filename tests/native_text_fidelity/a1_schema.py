"""Small, dependency-light schemas used by the A1 native-text audit.

The schemas intentionally live under ``tests``.  The benchmark must not become
an implicit input to production extraction code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


ALIGNMENT_CATEGORIES = (
    "matched_exact",
    "fragmented_but_complete",
    "text_missing",
    "non_space_character_missing",
    "unicode_substitution",
    "spacing_changed",
    "geometry_ambiguous",
    "duplicate_occurrence",
    "native_mapping_invalid",
    "unmatched_native",
    "not_assessable",
)


@dataclass(frozen=True, slots=True)
class RawPdfiumCharacter:
    page_index: int
    char_index: int
    text: str
    unicode_codepoint: int | None
    bbox_pdfium: tuple[float, float, float, float] | None
    bbox_top_origin_pt: tuple[float, float, float, float] | None
    bbox_valid: bool
    origin_pdfium: tuple[float, float] | None = None
    origin_top_origin_pt: tuple[float, float] | None = None
    angle: float | None = None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AlignmentRecord:
    page: int
    source_stage: str
    unit_id: str | None
    expected_text: str | None
    observed_text: str | None
    category: str
    candidate_char_indices: tuple[int, ...] = ()
    consumed_char_indices: tuple[int, ...] = ()
    candidate_count: int = 0
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, Any] | None = None


def jsonable(value: Any) -> Any:
    """Convert project dataclasses and pypdfium values to stable JSON data."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(jsonable(item) for item in value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(jsonable(row), ensure_ascii=False, sort_keys=True))
            stream.write("\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
