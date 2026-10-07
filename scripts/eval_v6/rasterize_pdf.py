"""Create a raster-only synthetic PDF variant for OCR evaluation.

Each source page is rendered to pixels and placed in a new PDF with no text
layer. The output and a manifest are ignored by the repository. This utility
is for evaluation data preparation only and does not change production
rendering or the protected OCR recovery path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_extractable_text(path: Path) -> list[int]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    non_empty: list[int] = []
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range() or ""
                finally:
                    textpage.close()
                if text.strip():
                    non_empty.append(index + 1)
            finally:
                page.close()
    finally:
        document.close()
    return non_empty


def rasterize_pdf(source: Path, output: Path, manifest: Path, scale: float = 2.0) -> dict[str, Any]:
    if scale <= 0:
        raise ValueError("scale must be positive")
    import pypdfium2 as pdfium
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    manifest = manifest.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    document = pdfium.PdfDocument(str(source))
    source_page_facts: list[dict[str, Any]] = []
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        source_page_count = len(document)
        with TemporaryDirectory(prefix="pdfextractor-raster-"):
            canvas_pdf = None
            for index in range(source_page_count):
                page = document[index]
                try:
                    width, height = page.get_size()
                    textpage = page.get_textpage()
                    try:
                        source_text_chars = len(textpage.get_text_range() or "")
                    finally:
                        textpage.close()
                    source_page_facts.append({
                        "source_size_pt": [width, height],
                        "source_text_chars": source_text_chars,
                    })
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                    try:
                        if canvas_pdf is None:
                            canvas_pdf = canvas.Canvas(str(output), pagesize=(width, height))
                        else:
                            canvas_pdf.setPageSize((width, height))
                        canvas_pdf.drawImage(ImageReader(image), 0, 0, width=width, height=height, mask="auto")
                        canvas_pdf.showPage()
                    finally:
                        image.close()
                finally:
                    page.close()
            if canvas_pdf is None:
                raise ValueError("source PDF has no pages")
            canvas_pdf.save()
    finally:
        document.close()

    remaining_text_pages = _has_extractable_text(output)
    if remaining_text_pages:
        raise RuntimeError(
            "raster output still exposes extractable text on pages: "
            + ", ".join(map(str, remaining_text_pages))
        )

    entries = []
    output_document = pdfium.PdfDocument(str(output))
    try:
        for index in range(source_page_count):
            out_page = output_document[index]
            try:
                output_textpage = out_page.get_textpage()
                try:
                    output_text_chars = len(output_textpage.get_text_range() or "")
                finally:
                    output_textpage.close()
                entries.append({
                    "page": index + 1,
                    "source_page": index + 1,
                    **source_page_facts[index],
                    "output_size_pt": list(out_page.get_size()),
                    "output_text_chars": output_text_chars,
                })
            finally:
                out_page.close()
    finally:
        output_document.close()
    result = {
        "schema": "structured-pdf-text.raster-evaluation-input.v1",
        "source_pdf": str(source),
        "source_sha256": sha256_file(source),
        "output_pdf": str(output),
        "output_sha256": sha256_file(output),
        "scale": scale,
        "page_count": len(entries),
        "text_layer_validation": {"passed": True, "non_empty_pages": []},
        "pages": entries,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        result = rasterize_pdf(args.source, args.output, args.manifest, args.scale)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(f"[PASS] {result['page_count']} pages rasterized: {result['output_pdf']}")
    print(f"[INFO] manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
