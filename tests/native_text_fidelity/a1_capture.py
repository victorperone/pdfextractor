#!/usr/bin/env python3
"""Capture raw PDFium characters and the production native adapter for A1.

The raw collector intentionally duplicates the small amount of pypdfium2
access needed by the audit.  It does not call production conversion helpers;
that keeps ``reference -> raw PDFium`` independent from
``raw PDFium -> NativePageEvidence``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import math
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping

try:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c
except Exception as exc:  # pragma: no cover - exercised in environments without PDFium
    pdfium = None
    pdfium_c = None
    _PDFIUM_IMPORT_ERROR = exc
else:
    _PDFIUM_IMPORT_ERROR = None

try:
    from structured_pdf_text.native.pdfium_source import PdfiumNativeEvidenceSource
except Exception:  # pragma: no cover - CLI reports a useful error later
    PdfiumNativeEvidenceSource = None  # type: ignore[assignment,misc]

try:
    from .a1_compare import ComparisonPolicy, compare_capture
    from .a1_schema import RawPdfiumCharacter, jsonable, write_json, write_jsonl
except ImportError:  # script execution from this directory
    from a1_compare import ComparisonPolicy, compare_capture
    from a1_schema import RawPdfiumCharacter, jsonable, write_json, write_jsonl


CORPUS_PREFIX = "Document_Text_Stress_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(command: list[str], fallback: str = "unknown") -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return fallback
    return result.stdout.strip() or fallback


def _box(page: Any, method_name: str) -> tuple[float, float, float, float] | None:
    method = getattr(page, method_name, None)
    if not callable(method):
        return None
    try:
        values = tuple(float(value) for value in method())
    except Exception:
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    if values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values  # type: ignore[return-value]


def _geometry(page: Any) -> dict[str, Any]:
    width, height = (float(value) for value in page.get_size())
    media = _box(page, "get_mediabox")
    crop = _box(page, "get_cropbox")
    effective = _box(page, "get_bbox") or crop or media or (0.0, 0.0, width, height)
    origin_x, origin_y = effective[0], effective[1]
    effective_width = max(effective[2] - origin_x, 0.0) or width
    effective_height = max(effective[3] - origin_y, 0.0) or height
    return {
        "page_size_pt": [width, height],
        "page_bbox_pdfium": list(effective),
        "crop_bbox_pdfium": list(crop) if crop else None,
        "media_bbox_pdfium": list(media) if media else None,
        "coordinate_origin_pdfium": [origin_x, origin_y],
        "effective_width_pt": effective_width,
        "effective_height_pt": effective_height,
        "rotation": int(getattr(page, "get_rotation", lambda: 0)() or 0),
        "coordinate_conversion": "x-origin_subtracted; y=(origin_y+effective_height)-pdfium_top_or_bottom_max/min",
    }


def _top_bbox(
    rect: tuple[float, float, float, float] | None,
    geometry: Mapping[str, Any],
) -> tuple[float, float, float, float] | None:
    if rect is None:
        return None
    left, bottom, right, top = rect
    origin_x, origin_y = geometry["coordinate_origin_pdfium"]
    height = float(geometry["effective_height_pt"])
    x0, x1 = min(left, right) - origin_x, max(left, right) - origin_x
    page_top = origin_y + height
    y0 = page_top - max(bottom, top)
    y1 = page_top - min(bottom, top)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _origin(
    textpage: Any,
    index: int,
    geometry: Mapping[str, Any],
) -> tuple[float, float] | None:
    function = getattr(pdfium_c, "FPDFText_GetCharOrigin", None) if pdfium_c else None
    if function is None:
        return None
    import ctypes

    try:
        x, y = ctypes.c_double(), ctypes.c_double()
        result = function(textpage.raw, index, x, y)
        if result is False or result == 0:
            return None
        origin_x, origin_y = geometry["coordinate_origin_pdfium"]
        height = float(geometry["effective_height_pt"])
        return (float(x.value) - origin_x, origin_y + height - float(y.value))
    except Exception:
        return None


def _unicode(textpage: Any, index: int, fallback: str) -> tuple[int | None, str | None]:
    function = getattr(pdfium_c, "FPDFText_GetUnicode", None) if pdfium_c else None
    if function is None:
        return (ord(fallback[0]) if fallback else None, "unicode_function_unavailable")
    try:
        value = int(function(textpage.raw, index))
        return (value if value >= 0 else None, None if value >= 0 else "unicode_invalid")
    except Exception as exc:
        return (None, f"unicode_error:{type(exc).__name__}")


def _angle(textpage: Any, index: int) -> tuple[float | None, str | None]:
    function = getattr(pdfium_c, "FPDFText_GetCharAngle", None) if pdfium_c else None
    if function is None:
        return None, "angle_function_unavailable"
    try:
        return float(function(textpage.raw, index)), None
    except Exception as exc:
        return None, f"angle_error:{type(exc).__name__}"


def _raw_character(textpage: Any, index: int, geometry: Mapping[str, Any], page_index: int) -> RawPdfiumCharacter:
    errors: list[str] = []
    try:
        try:
            text = textpage.get_text_range(index, 1, errors="ignore") or ""
        except TypeError:
            text = textpage.get_text_range(index, 1) or ""
    except Exception as exc:
        text = ""
        errors.append(f"text_error:{type(exc).__name__}")
    unicode_codepoint, unicode_error = _unicode(textpage, index, text)
    if unicode_error:
        errors.append(unicode_error)
    try:
        pdfium_bbox = tuple(float(value) for value in textpage.get_charbox(index))
        if len(pdfium_bbox) != 4 or not all(math.isfinite(value) for value in pdfium_bbox):
            raise ValueError("non-finite-or-wrong-length-bbox")
        bbox_pdfium = pdfium_bbox  # type: ignore[assignment]
    except Exception as exc:
        bbox_pdfium = None
        errors.append(f"bbox_error:{type(exc).__name__}")
    bbox_top = _top_bbox(bbox_pdfium, geometry)
    if bbox_pdfium is not None and bbox_top is None:
        errors.append("bbox_invalid_dimensions")
    origin_pdfium: tuple[float, float] | None = None
    function = getattr(pdfium_c, "FPDFText_GetCharOrigin", None) if pdfium_c else None
    if function is not None:
        import ctypes

        try:
            x, y = ctypes.c_double(), ctypes.c_double()
            result = function(textpage.raw, index, x, y)
            if result is not False and result != 0:
                origin_pdfium = (float(x.value), float(y.value))
            else:
                errors.append("origin_invalid")
        except Exception as exc:
            errors.append(f"origin_error:{type(exc).__name__}")
    angle, angle_error = _angle(textpage, index)
    if angle_error:
        errors.append(angle_error)
    origin_top = _origin(textpage, index, geometry)
    return RawPdfiumCharacter(
        page_index=page_index,
        char_index=index,
        text=text,
        unicode_codepoint=unicode_codepoint,
        bbox_pdfium=bbox_pdfium,
        bbox_top_origin_pt=bbox_top,
        bbox_valid=bbox_top is not None,
        origin_pdfium=origin_pdfium,
        origin_top_origin_pt=origin_top,
        angle=angle,
        errors=tuple(errors),
    )


def capture_raw(pdf_path: Path, pages: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if pdfium is None:
        raise RuntimeError(f"pypdfium2 is unavailable: {_PDFIUM_IMPORT_ERROR}")
    document = pdfium.PdfDocument(str(pdf_path))
    chars: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []
    try:
        for page_number in pages:
            page_index = page_number - 1
            page = document[page_index]
            try:
                geometry = _geometry(page)
                textpage = page.get_textpage()
                try:
                    count = int(textpage.count_chars() or 0)
                    page_chars = [_raw_character(textpage, index, geometry, page_index) for index in range(count)]
                    try:
                        text_range = textpage.get_text_range(0, -1, errors="ignore") or ""
                    except TypeError:
                        text_range = textpage.get_text_range(0, -1) or ""
                finally:
                    close = getattr(textpage, "close", None)
                    if callable(close):
                        close()
                chars.extend(jsonable(page_char) for page_char in page_chars)
                page_rows.append({"page": page_number, "page_index": page_index, "geometry": geometry, "char_count": count, "text_range": text_range})
            finally:
                page.close()
    finally:
        document.close()
    return chars, page_rows


def capture_adapter(pdf_path: Path, pages: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if PdfiumNativeEvidenceSource is None:
        raise RuntimeError("production PdfiumNativeEvidenceSource could not be imported")
    chars: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []
    with PdfiumNativeEvidenceSource(pdf_path) as source:
        for page_number in pages:
            evidence = source.extract_page(page_number - 1)
            page_rows.append({
                "page": page_number,
                "page_index": evidence.page_index,
                "bbox": evidence.bbox,
                "extracted_text": evidence.extracted_text,
                "capabilities": evidence.capabilities,
                "objects": evidence.objects,
            })
            for character in evidence.characters:
                row = jsonable(character)
                row["page_index"] = character.page_index
                row["char_index"] = character.char_index
                chars.append(row)
    return chars, page_rows


def select_pages(reference: Mapping[str, Any], *, variants_per_family: int, explicit: list[int] | None = None, all_pages: bool = False) -> list[int]:
    pages = reference["pages"]
    if explicit:
        available = {int(page["page"]) for page in pages}
        missing = sorted(set(explicit) - available)
        if missing:
            raise ValueError(f"requested page(s) not present in reference: {missing}")
        return sorted(set(explicit))
    if all_pages:
        return [int(page["page"]) for page in pages]
    if variants_per_family < 1:
        raise ValueError("--variants-per-family must be at least 1")
    groups: dict[str, list[int]] = {}
    for page in pages:
        groups.setdefault(str(page["family"]), []).append(int(page["page"]))
    selected: list[int] = []
    for family in sorted(groups):
        family_pages = sorted(groups[family])
        count = min(variants_per_family, len(family_pages))
        if count == 1:
            positions = [0]
        else:
            positions = [round(index * (len(family_pages) - 1) / (count - 1)) for index in range(count)]
        selected.extend(family_pages[position] for position in positions)
    return sorted(set(selected))


def _load_reference(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        raise ValueError(f"reference must be an object with pages: {path}")
    return data


def _report(output: Path, metadata: Mapping[str, Any], summary: Mapping[str, Any], records: list[Any]) -> None:
    failures = [row for row in records if row.category not in {"matched_exact", "fragmented_but_complete", "spacing_changed", "unicode_substitution", "unmatched_native"}]
    notable = [row for row in records if row.category not in {"matched_exact", "fragmented_but_complete"}]
    examples = failures[:20]
    notable_examples = notable[:20]
    unmatched_examples = [row for row in records if row.category == "unmatched_native"][:5]
    lines = [
        "# A1 native text fidelity report",
        "",
        f"- Code commit: `{metadata['code_commit']}`",
        f"- Executed at (UTC): `{metadata['executed_at_utc']}`",
        f"- Selected pages: `{metadata['selected_pages']}`",
        f"- Strict unit coverage: `{summary['strict']['numerator']}/{summary['strict']['denominator']}` (not assessable excluded)",
        f"- Permissive diagnostic coverage: `{summary['permissive']['numerator']}/{summary['permissive']['denominator']}`",
        "",
        "## Interpretation",
        "",
        "The raw PDFium and adapter stages are compared independently. `unmatched_native` is retained as evidence and is not a reference-unit success. Whitespace-only differences are reported as `spacing_changed`; reading order, OCR, assembler fallback, and table structure are outside A1.",
        "",
        "## Examples requiring review",
        "",
    ]
    if not examples:
        lines.append("No non-permissive unit examples were recorded.")
    else:
        for row in examples:
            lines.append(f"- page {row.page}, stage `{row.source_stage}`, unit `{row.unit_id}`, category `{row.category}`: expected={row.expected_text!r}; observed={row.observed_text!r}; reasons={list(row.reasons)!r}")
    lines.extend(["", "## Permissive differences and native extras", ""])
    for row in notable_examples:
        if row.category == "unmatched_native":
            continue
        lines.append(f"- page {row.page}, stage `{row.source_stage}`, unit `{row.unit_id}`, category `{row.category}`: expected={row.expected_text!r}; observed={row.observed_text!r}; reasons={list(row.reasons)!r}")
    if unmatched_examples:
        lines.extend(["", "Representative `unmatched_native` records (not counted as unit success):"])
        for row in unmatched_examples:
            lines.append(f"- page {row.page}, stage `{row.source_stage}`, char `{row.candidate_char_indices[0]}`: observed={row.observed_text!r}")
    lines.extend(["", "## Reproduction", "", "See `README_A1.md` for the exact capture and comparison commands. The full evidence is in the JSONL/JSON files adjacent to this report.", ""])
    (output / "A1_report.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    corpus_root = args.root
    pdf_path = args.pdf or corpus_root / f"{CORPUS_PREFIX}.pdf"
    reference_path = args.reference or corpus_root / f"{CORPUS_PREFIX}.reference.json"
    reference = _load_reference(reference_path)
    pages = select_pages(reference, variants_per_family=args.variants_per_family, explicit=args.pages, all_pages=args.all)
    output = args.output or Path("output/native_text_fidelity/a1") / _git(["git", "rev-parse", "HEAD"])
    output.mkdir(parents=True, exist_ok=True)
    raw_chars, raw_pages = capture_raw(pdf_path, pages)
    adapter_chars, adapter_pages = capture_adapter(pdf_path, pages)
    write_jsonl(output / "raw_pdfium_chars.jsonl", raw_chars)
    write_jsonl(output / "raw_pdfium_pages.jsonl", raw_pages)
    write_jsonl(output / "native_adapter_chars.jsonl", adapter_chars)
    write_jsonl(output / "native_adapter_pages.jsonl", adapter_pages)
    selected_reference = [page for page in reference["pages"] if int(page["page"]) in pages]
    policy = ComparisonPolicy()
    records, summary = compare_capture(selected_reference, raw_chars, adapter_chars, output_dir=output, policy=policy)
    metadata = {
        "schema": "pdfextractor.native_text_fidelity.a1.run.v1",
        "branch": _git(["git", "branch", "--show-current"]),
        "code_commit": _git(["git", "rev-parse", "HEAD"]),
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "pypdfium2": _package_version("pypdfium2"),
        "pdfium_info": str(getattr(pdfium, "PDFIUM_INFO", None)),
        "pdf_path": str(pdf_path),
        "reference_path": str(reference_path),
        "pdf_sha256": sha256(pdf_path),
        "reference_sha256": sha256(reference_path),
        "selected_pages": pages,
        "selection": {"variants_per_family": args.variants_per_family, "explicit_pages": args.pages, "all": args.all},
        "capture": {"ocr": False, "rasterization": False, "get_text_range": "aggregate inspection only", "raw_char_source": "pypdfium2 direct binding"},
        "native_character_fields_exposed": ["page_index", "char_index", "text", "unicode_codepoint", "bbox", "origin", "angle", "font_name", "font_size", "font_weight", "fill_color", "stroke_color", "text_render_mode", "marked_content_id", "generated", "hyphen", "unicode_mapping_failed", "visible_candidate"],
        "coordinate_conversion": "production adapter uses BBox.from_pdfium_rect: y-down top-origin within effective page height and x/y origin subtraction; raw capture stores both PDFium and converted values",
        "policy": jsonable(policy),
        "summary": summary,
    }
    write_json(output / "run_metadata.json", metadata)
    _report(output, metadata, summary, records)
    return output


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent / "corpus" / "native_text_stress")
    command.add_argument("--pdf", type=Path)
    command.add_argument("--reference", type=Path)
    command.add_argument("--output", type=Path)
    command.add_argument("--variants-per-family", type=int, default=1)
    command.add_argument("--pages", type=int, nargs="*")
    command.add_argument("--all", action="store_true", help="capture all 200 reference pages")
    return command


if __name__ == "__main__":
    destination = run(parser().parse_args())
    print(destination)
