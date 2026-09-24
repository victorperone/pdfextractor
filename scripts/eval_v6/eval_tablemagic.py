"""Offline, isolated PP-TableMagic evaluation.

The evaluator accepts an identified table crop or an explicitly selected PDF
page plus crop. A PDF page number is always human-facing and 1-based; the
internal PDFium index is never exposed as the input contract. Full-page PDF
evaluation is intentionally rejected because a page can contain more than one
table.

No output from this script is an approval for production integration. HTML,
cell boxes and OCR text are preserved as evidence, while conversion to the
project's StructuredTable contract remains out of scope.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any


TABLE_PROFILES: dict[str, dict[str, str]] = {
    "tm-v5": {
        "text_detection": "PP-OCRv5_server_det",
        "text_recognition": "latin_PP-OCRv5_mobile_rec",
    },
    "tm-v6": {
        "text_detection": "PP-OCRv6_medium_det",
        "text_recognition": "PP-OCRv6_medium_rec",
    },
}

COMMON_TABLE_MODELS = {
    "table_classification": "PP-LCNet_x1_0_table_cls",
    "wired_structure": "SLANeXt_wired",
    "wireless_structure": "SLANeXt_wireless",
    "wired_cells": "RT-DETR-L_wired_table_cell_det",
    "wireless_cells": "RT-DETR-L_wireless_table_cell_det",
}


def _add_src_to_path() -> None:
    src = Path(__file__).parent.parent.parent / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _set_offline_mode(cache_home: str) -> None:
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(Path(cache_home).expanduser().resolve())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ("paddlepaddle", "paddleocr", "paddlex", "pypdfium2", "Pillow", "numpy", "psutil"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("bbox must be x0,y0,x1,y1") from exc
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ValueError("bbox must contain four finite numbers")
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        raise ValueError("bbox must have positive width and height")
    return (x0, y0, x1, y1)


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        page = int(part)
        if page < 1:
            raise ValueError("PDF pages are human-facing and 1-based")
        pages.append(page)
    if not pages:
        raise ValueError("at least one page is required")
    return sorted(set(pages))


def model_names(profile: str) -> dict[str, str]:
    try:
        ocr = TABLE_PROFILES[profile]
    except KeyError as exc:
        raise ValueError(f"unknown TableMagic profile: {profile}") from exc
    return {**COMMON_TABLE_MODELS, **ocr}


def model_inventory(cache_home: Path, profile: str) -> list[dict[str, Any]]:
    root = cache_home.expanduser().resolve() / "official_models"
    inventory = []
    for role, name in model_names(profile).items():
        directory = root / name
        files = [p for p in directory.rglob("*") if p.is_file()] if directory.is_dir() else []
        weights = [
            p for p in files
            if p.suffix.lower() in {".pdiparams", ".pdparams", ".onnx", ".bin", ".nb", ".pt"}
            or p.name.endswith(".pdiparams.info")
        ]
        metadata = [p for p in files if p.suffix.lower() in {".json", ".yml", ".yaml"}]
        inventory.append(
            {
                "role": role,
                "name": name,
                "directory": str(directory),
                "exists": directory.is_dir(),
                "file_count": len(files),
                "bytes": sum(p.stat().st_size for p in files),
                "has_weight": bool(weights),
                "has_metadata": bool(metadata),
                "sha256": _hash_tree(directory),
            }
        )
    return inventory


def _hash_tree(path: Path) -> str | None:
    if not path.is_dir():
        return None
    digest = hashlib.sha256()
    found = False
    for child in sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink()):
        found = True
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        with child.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest() if found else None


def validate_model_inventory(inventory: list[dict[str, Any]]) -> None:
    missing = [
        f"{item['name']} ({item['role']})"
        for item in inventory
        if not item["exists"] or not item["file_count"] or not item["has_weight"] or not item["has_metadata"]
    ]
    if missing:
        raise FileNotFoundError(
            "TableMagic requires complete local submodels; no download is allowed. "
            "Missing/incomplete: " + ", ".join(missing)
        )


def pipeline_kwargs(cache_home: Path, profile: str) -> dict[str, Any]:
    root = cache_home.expanduser().resolve() / "official_models"
    names = model_names(profile)
    kwargs: dict[str, Any] = {
        "device": "cpu",
        "enable_mkldnn": False,
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_layout_detection": False,
        "use_ocr_model": True,
        # Disable cell-level OCR refinement so the only OCR is the one
        # declared in the profile (text_detection_model / text_recognition_model).
        # Without this, the pipeline applies a second OCR pass per cell using
        # library-default models that are not part of the experiment manifest.
        "use_ocr_results_with_table_cells": False,
        "table_classification_model_name": names["table_classification"],
        "table_classification_model_dir": str(root / names["table_classification"]),
        "wired_table_structure_recognition_model_name": names["wired_structure"],
        "wired_table_structure_recognition_model_dir": str(root / names["wired_structure"]),
        "wireless_table_structure_recognition_model_name": names["wireless_structure"],
        "wireless_table_structure_recognition_model_dir": str(root / names["wireless_structure"]),
        "wired_table_cells_detection_model_name": names["wired_cells"],
        "wired_table_cells_detection_model_dir": str(root / names["wired_cells"]),
        "wireless_table_cells_detection_model_name": names["wireless_cells"],
        "wireless_table_cells_detection_model_dir": str(root / names["wireless_cells"]),
        "text_detection_model_name": names["text_detection"],
        "text_detection_model_dir": str(root / names["text_detection"]),
        "text_recognition_model_name": names["text_recognition"],
        "text_recognition_model_dir": str(root / names["text_recognition"]),
    }
    return kwargs


def _loaded_model_names(pipeline: Any) -> list[str]:
    names: set[str] = set()

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 4 or value is None:
            return
        name = getattr(value, "model_name", None)
        if isinstance(name, str):
            names.add(name)
        if isinstance(value, (str, bytes, int, float, bool, Path)):
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item, depth + 1)
            return
        for attr in ("paddlex_pipeline", "table_cls_model", "wired_table_rec_model", "wireless_table_rec_model",
                     "wired_table_cells_detection_model", "wireless_table_cells_detection_model",
                     "general_ocr_pipeline"):
            try:
                visit(getattr(value, attr), depth + 1)
            except Exception:
                pass

    visit(pipeline)
    return sorted(names)


def _result_json(item: Any) -> dict[str, Any]:
    raw = getattr(item, "json", None)
    if callable(raw):
        raw = raw()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {"raw_json": raw}
    if isinstance(raw, dict):
        # PaddleOCR 3.x serializes PipelineResult as {"res": {...}}.
        # Normalize that wrapper before reading table_res_list while still
        # preserving the complete normalized payload in result.json.
        payload = raw.get("res")
        return payload if isinstance(payload, dict) else raw
    for method_name in ("to_json", "json"):
        method = getattr(item, method_name, None)
        if callable(method):
            try:
                candidate = method()
                if isinstance(candidate, str):
                    candidate = json.loads(candidate)
                if isinstance(candidate, dict):
                    return candidate
            except Exception:
                pass
    return {"raw_result_repr": repr(raw)}


def _write_optional_artifact(item: Any, method_name: str, directory: Path) -> str | None:
    method = getattr(item, method_name, None)
    if not callable(method):
        return None
    try:
        method(str(directory))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _save_result_artifacts(item: Any, raw: dict[str, Any], directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    tables = raw.get("table_res_list", [])
    html_paths: list[str] = []
    for index, table in enumerate(tables):
        html = table.get("pred_html", "") if isinstance(table, dict) else ""
        path = directory / f"table_{index:03d}.html"
        path.write_text(str(html), encoding="utf-8")
        html_paths.append(str(path))
    optional_errors = {}
    for method_name in ("save_to_html", "save_to_xlsx", "save_to_json"):
        error = _write_optional_artifact(item, method_name, directory)
        if error:
            optional_errors[method_name] = error
    return {
        "directory": str(directory),
        "html": html_paths,
        "optional_errors": optional_errors,
        "cell_data_preserved_in": str(directory / "result.json"),
    }


def _pdf_page_to_image(pdf_path: Path, page_number: int, bbox: tuple[float, float, float, float], scale: float = 2.0):
    """Render and crop one human-facing page using a top-left PDF bbox."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    if page_number < 1 or page_number > len(pdf):
        raise ValueError(f"page {page_number} is outside the PDF ({len(pdf)} pages)")
    page = pdf[page_number - 1]
    page_width, page_height = page.get_size()
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 > page_width or y1 > page_height:
        raise ValueError(
            f"bbox {bbox} is outside page {page_number} bounds "
            f"(0, 0, {page_width:.2f}, {page_height:.2f})"
        )
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil()
    crop = (
        round(x0 * scale),
        round(y0 * scale),
        round(x1 * scale),
        round(y1 * scale),
    )
    return image.crop(crop)


def _image_input(image_path: Path, output_dir: Path) -> tuple[Path, dict[str, Any]]:
    if not image_path.is_file():
        raise FileNotFoundError(f"image not found: {image_path}")
    destination = output_dir / "input.png"
    shutil.copy2(image_path, destination)
    return destination, {"image": str(image_path.resolve()), "page": None, "bbox": None}


def _pdf_input(pdf_path: Path, page_number: int, bbox: tuple[float, float, float, float], scale: float, output_dir: Path):
    image = _pdf_page_to_image(pdf_path, page_number, bbox, scale)
    destination = output_dir / "input.png"
    image.save(destination)
    return destination, {
        "pdf": str(pdf_path.resolve()),
        "document_sha256": sha256_file(pdf_path),
        "page_human_1_based": page_number,
        "bbox_pdf_points_top_left": list(bbox),
        "render_scale": scale,
    }


def eval_tablemagic_on_image(
    image_path: str,
    cache_home: str,
    profile: str = "tm-v5",
    *,
    metadata: dict[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run one identified crop and preserve complete result artifacts."""
    from paddleocr import TableRecognitionPipelineV2

    cache = Path(cache_home).expanduser().resolve()
    inventory = model_inventory(cache, profile)
    validate_model_inventory(inventory)

    required_signature = {
        "table_classification_model_dir",
        "wired_table_structure_recognition_model_dir",
        "wireless_table_structure_recognition_model_dir",
        "wired_table_cells_detection_model_dir",
        "wireless_table_cells_detection_model_dir",
        "text_detection_model_dir",
        "text_recognition_model_dir",
    }
    signature = inspect.signature(TableRecognitionPipelineV2)
    missing_parameters = required_signature - set(signature.parameters)
    if missing_parameters:
        raise RuntimeError(
            "installed TableRecognitionPipelineV2 cannot express the explicit "
            f"configuration: {sorted(missing_parameters)}"
        )

    kwargs = pipeline_kwargs(cache, profile)
    started = time.perf_counter()
    pipeline = TableRecognitionPipelineV2(**kwargs)
    init_ms = (time.perf_counter() - started) * 1000
    inference_started = time.perf_counter()
    output = list(
        pipeline.predict(
            image_path,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_layout_detection=False,
            use_ocr_model=True,
            use_table_orientation_classify=False,
        )
    )
    infer_ms = (time.perf_counter() - inference_started) * 1000

    tables: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    root = output_dir or Path(image_path).parent / "tablemagic_results"
    for item_index, item in enumerate(output):
        raw = _result_json(item)
        item_dir = root / f"result_{item_index:03d}"
        artifact = _save_result_artifacts(item, raw, item_dir)
        artifacts.append(artifact)
        for table in raw.get("table_res_list", []):
            if not isinstance(table, dict):
                continue
            scores = table.get("table_ocr_pred", {}).get("rec_scores", [])
            tables.append(
                {
                    "cell_box_list": table.get("cell_box_list", []),
                    "pred_html": table.get("pred_html", ""),
                    "table_ocr_pred": table.get("table_ocr_pred", {}),
                    "cell_count": len(table.get("cell_box_list", [])),
                    "text_count": len(table.get("table_ocr_pred", {}).get("rec_texts", [])),
                    "avg_score": round(sum(scores) / max(len(scores), 1), 4),
                }
            )

    return {
        "status": "success",
        "input": metadata or {"image": str(Path(image_path).resolve())},
        "profile": profile,
        "configured_models": {item["role"]: item["name"] for item in inventory},
        "model_inventory": inventory,
        "loaded_model_names_observed": _loaded_model_names(pipeline),
        "pipeline_signature": str(signature),
        "init_ms": round(init_ms, 1),
        "infer_ms": round(infer_ms, 1),
        "table_count": len(tables),
        "tables": tables,
        "artifacts": artifacts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="identified table crop image")
    source.add_argument("--pdf", type=Path, help="PDF used only for explicit crop extraction")
    parser.add_argument("--document-sha", help="SHA-256 of the source PDF (required for --image)")
    parser.add_argument("--page", type=int, help="one human-facing 1-based PDF page")
    parser.add_argument("--pages", help="comma-separated human-facing 1-based PDF pages")
    parser.add_argument("--bbox", help="x0,y0,x1,y1 in PDF points, top-left origin (required for --pdf; optional metadata for --image)")
    parser.add_argument("--profile", choices=tuple(TABLE_PROFILES), default="tm-v5")
    parser.add_argument("--cache-home", default=str(Path.home() / ".cache/pdfextractor/paddlex"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/tablemagic_eval"))
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args(argv)

    if not args.image and args.page is None and not args.pages:
        parser.error("--page or --pages is required when using --pdf")

    try:
        if args.scale <= 0:
            raise ValueError("--scale must be positive")
        if args.image:
            if args.page is None or not args.document_sha:
                raise ValueError("--image requires --page and --document-sha")
            if args.pages:
                raise ValueError("--pages is only valid with --pdf")
            bbox = parse_bbox(args.bbox) if args.bbox else None
            pages = [args.page]
        else:
            if not args.bbox:
                raise ValueError("--bbox is required when using --pdf")
            bbox = parse_bbox(args.bbox)
            if not args.pdf.is_file():
                raise FileNotFoundError(f"PDF not found: {args.pdf}")
            if args.page is not None and args.pages:
                raise ValueError("use either --page or --pages, not both")
            pages = [args.page] if args.page is not None else parse_pages(args.pages or "")
            actual_sha = sha256_file(args.pdf)
            if args.document_sha and args.document_sha.lower() != actual_sha:
                raise ValueError("--document-sha does not match the PDF")
    except (ValueError, FileNotFoundError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    cache_home = str(Path(args.cache_home).expanduser().resolve())
    _set_offline_mode(cache_home)
    output_root = args.output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    print(f"Profile: {args.profile}")
    print(f"Cache: {cache_home}")
    print("Internal OCR models:")
    for role, name in model_names(args.profile).items():
        print(f"  {role}: {name} -> {Path(cache_home) / 'official_models' / name}")

    results: list[dict[str, Any]] = []
    for page_number in pages:
        item_dir = output_root / f"page_{page_number:04d}"
        item_dir.mkdir(parents=True, exist_ok=True)
        try:
            if args.image:
                image_path, input_meta = _image_input(args.image, item_dir)
                input_meta.update({
                    "document_sha256": args.document_sha.lower(),
                    "page_human_1_based": page_number,
                    "bbox_image_coordinates": list(bbox) if bbox is not None else None,
                })
            else:
                image_path, input_meta = _pdf_input(args.pdf, page_number, bbox, args.scale, item_dir)
            result = eval_tablemagic_on_image(
                str(image_path),
                cache_home,
                args.profile,
                metadata=input_meta,
                output_dir=item_dir / "results",
            )
            results.append(result)
            print(f"[OK] page={page_number} tables={result['table_count']} init={result['init_ms']:.0f}ms infer={result['infer_ms']:.0f}ms")
        except Exception as exc:
            failure = {
                "status": "failed",
                "page_human_1_based": page_number,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            results.append(failure)
            print(f"[FAIL] page={page_number}: {type(exc).__name__}: {exc}", file=sys.stderr)

    report = {
        "schema": "structured-pdf-text.tablemagic-evaluation.v2",
        "status": "success" if results and all(item["status"] == "success" for item in results) else "failed",
        "profile": args.profile,
        "packages": package_versions(),
        "cache_home": cache_home,
        "pages_human_1_based": pages,
        "bbox_pdf_points_top_left": list(bbox) if bbox is not None else None,
        "pipeline_flags": {
            "use_ocr_model": True,
            "use_ocr_results_with_table_cells": False,
        },
        "results": results,
        "quality_metrics": {
            "status": "not_computed",
            "reason": "A verified per-cell reference is required; cell count and average score are not fidelity metrics.",
        },
    }
    report_path = output_root / "tablemagic_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"[INFO] Report: {report_path}")
    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
