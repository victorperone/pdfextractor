#!/usr/bin/env python3
"""
E2E benchmark runner — Fase 8.

Runs PdfTextExtractor with the specified OCR engine on a corpus PDF,
saves the extracted Markdown and a run manifest.

Usage (Windows server):
    python scripts\\evaluate_e2e.py ^
        corpus\\Corpus_Stress_OCR_Markdown_V4.pdf ^
        --engine paddle ^
        --output-dir output\\fase8

    python scripts\\evaluate_e2e.py ^
        corpus\\Corpus_Stress_OCR_Markdown_V4.pdf ^
        --engine tesseract ^
        --output-dir output\\fase8

Engines: paddle | rapidocr-onnx | rapidocr-openvino | tesseract | easyocr

Output:
    output/fase8/extracted_{engine}_{run_id}.md
    output/fase8/manifesto_e2e_{engine}_{run_id}.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _git_sha() -> str:
    """Return the current git commit SHA, or 'unknown' on failure."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    """Return True if the working tree has uncommitted changes."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        )
        return bool(out.strip())
    except Exception:
        return False


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_page_sections(md_text: str) -> dict[int, str]:
    """Split markdown into per-page sections.

    Handles both formats:
      '## Página 1'          (extractor output)
      '## Página 001 | Title' (reference format)
    Returns {1-based page number: page text (including header line)}.
    """
    pattern = re.compile(
        r"^##\s+P[áa]gina\s+0*(\d+)",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(pattern.finditer(md_text))
    if not matches:
        return {}
    pages: dict[int, str] = {}
    for i, m in enumerate(matches):
        page_num = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        pages[page_num] = md_text[start:end].strip()
    return pages


def _build_config(engine: str, mode: str, language: str, page_indices: tuple | None):
    """Build an ExtractorConfig for the given engine and extraction mode."""
    from structured_pdf_text.config import (
        ExtractionMode,
        best_extraction_config,
    )
    base = best_extraction_config(language=language, preserve_headers=False)
    config = replace(base, ocr_engine=engine, mode=ExtractionMode(mode))
    if page_indices is not None:
        config = replace(config, page_indices=page_indices)
    return config


def _patch_torch_for_paddle() -> None:
    """Inject a minimal torch stub so modelscope doesn't crash with DLL 0xC0000139.

    paddleocr → modelscope → torch (module-level import). On Windows, the CPU
    torch DLL installed by EasyOCR can fail at DLL load time with a fatal
    0xC0000139 (entry-point not found) before Python can catch anything.
    PaddlePaddle never actually uses PyTorch; only modelscope probes it.
    Stubbing torch in sys.modules before paddleocr is imported prevents the
    DLL from being loaded at all, while keeping PaddlePaddle fully functional.
    """
    import sys
    import types

    if "torch" in sys.modules:
        return  # already loaded or previously stubbed

    def _stub(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__version__ = "0.0.0+stub"
        return m

    # Sub-packages that modelscope and paddleocr probe at import time
    _subpackages = [
        "torch.nn", "torch.nn.functional", "torch.nn.modules",
        "torch.optim", "torch.optim.lr_scheduler",
        "torch.utils", "torch.utils.data", "torch.utils.data.dataloader",
        "torch.distributed", "torch.multiprocessing",
        "torch.cuda", "torch.cuda.amp",
        "torch.backends", "torch.backends.cudnn",
        "torch.jit", "torch.autograd", "torch.autograd.function",
        "torch.hub",
    ]
    for name in _subpackages:
        sys.modules[name] = _stub(name)

    cuda_stub = sys.modules["torch.cuda"]
    cuda_stub.is_available = lambda: False          # type: ignore[attr-defined]
    cuda_stub.device_count = lambda: 0              # type: ignore[attr-defined]

    cudnn_stub = sys.modules["torch.backends.cudnn"]
    cudnn_stub.enabled = False                       # type: ignore[attr-defined]
    cudnn_stub.version = lambda: 0                   # type: ignore[attr-defined]

    torch_stub = _stub("torch")
    torch_stub.cuda = cuda_stub                      # type: ignore[attr-defined]
    torch_stub.backends = sys.modules["torch.backends"]  # type: ignore[attr-defined]
    torch_stub.Tensor = object                       # type: ignore[attr-defined]
    torch_stub.device = str                          # type: ignore[attr-defined]
    torch_stub.float32 = "float32"                   # type: ignore[attr-defined]
    torch_stub.float16 = "float16"                   # type: ignore[attr-defined]
    torch_stub.int64 = "int64"                       # type: ignore[attr-defined]
    torch_stub.no_grad = lambda f=None: (f if f else lambda g: g)  # type: ignore[attr-defined]

    sys.modules["torch"] = torch_stub


def _parse_pages_arg(pages_arg: str) -> tuple[int, ...]:
    """Convert a page range string ('1-5' or '1,3,5') to a tuple of 0-based page indices."""
    indices: list[int] = []
    for part in pages_arg.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.extend(range(int(lo) - 1, int(hi)))  # 1-based → 0-based
        else:
            indices.append(int(part) - 1)
    return tuple(sorted(set(indices)))


def main() -> int:
    """Entry point: extract a PDF with one OCR engine and save Markdown + run manifest."""
    ap = argparse.ArgumentParser(
        description="Run pdfextractor E2E pipeline and save output (Fase 8)."
    )
    ap.add_argument("pdf", type=Path, help="PDF corpus to extract")
    ap.add_argument(
        "--engine",
        default="paddle",
        choices=["paddle", "rapidocr-onnx", "rapidocr-openvino", "tesseract", "easyocr"],
        help="OCR engine (default: paddle)",
    )
    ap.add_argument(
        "--mode",
        default="balanced",
        choices=["balanced", "native", "ocr"],
        help="Extraction mode (default: balanced)",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/fase8"),
    )
    ap.add_argument("--run-id", default=None, help="Run ID (auto-generated if omitted)")
    ap.add_argument("--pages", default=None, help="Page range: '1-10' or '1,5,10'")
    ap.add_argument("--language", default="pt")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"{ts}-{args.engine}-e2e-001"
    engine_slug = args.engine.replace("-", "_")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.output_dir / f"extracted_{engine_slug}_{run_id}.md"
    manifest_path = args.output_dir / f"manifesto_e2e_{engine_slug}_{run_id}.json"

    if not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1

    # Stub torch BEFORE any paddleocr import to avoid DLL crash on Windows (CF-4).
    # Must run before PdfTextExtractor is imported, which triggers lazy paddleocr load.
    if args.engine == "paddle":
        _patch_torch_for_paddle()

    try:
        from structured_pdf_text.api import PdfTextExtractor
        from structured_pdf_text.renderers.markdown import render_markdown
    except ImportError as exc:
        print(f"ERROR: cannot import structured_pdf_text: {exc}", file=sys.stderr)
        return 1

    page_indices = _parse_pages_arg(args.pages) if args.pages else None

    try:
        config = _build_config(args.engine, args.mode, args.language, page_indices)
    except Exception as exc:
        print(f"ERROR: cannot build config: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"=== E2E Benchmark — {args.engine} ===")
        print(f"  PDF    : {args.pdf}")
        print(f"  Run ID : {run_id}")
        print(f"  Mode   : {args.mode}")
        print(f"  Output : {args.output_dir}")
        print()

    # Per-page timing tracking
    page_times: list[float] = []
    _tick: list[float] = [time.perf_counter()]

    def on_progress(current: int, total: int) -> None:
        now = time.perf_counter()
        if current > 1:
            page_times.append(now - _tick[0])
        _tick[0] = now
        if not args.quiet:
            print(f"  Processando página {current}/{total}...", end="\r")

    t0 = time.perf_counter()
    document = None
    error_msg: str | None = None

    try:
        extractor = PdfTextExtractor(config)
        document = extractor.extract(args.pdf, progress_callback=on_progress)
        page_times.append(time.perf_counter() - _tick[0])  # last page
    except Exception as exc:
        error_msg = str(exc)
        if not args.quiet:
            print(f"\n  ERROR: {exc}", file=sys.stderr)

    elapsed_s = time.perf_counter() - t0

    # --- Save error manifest if extraction failed ---
    if document is None:
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "git_sha": _git_sha(),
            "git_dirty": _git_dirty(),
            "engine": args.engine,
            "mode": args.mode,
            "language": args.language,
            "pdf_path": str(args.pdf),
            "pdf_sha256": _sha256_file(args.pdf),
            "status": "error",
            "error": error_msg,
            "elapsed_s": round(elapsed_s, 3),
            "page_count": 0,
            "pages": [],
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nERROR manifest: {manifest_path}")
        return 1

    # --- Render markdown and save ---
    from structured_pdf_text.renderers.markdown import render_markdown
    md_text = render_markdown(document)
    md_path.write_text(md_text, encoding="utf-8")

    page_sections = _parse_page_sections(md_text)

    # --- Build per-page entries ---
    page_entries = []
    for i, page in enumerate(document.pages):
        page_num = page.page_index + 1
        elapsed_page = page_times[i] if i < len(page_times) else 0.0
        content = page_sections.get(page_num, "")
        page_entries.append({
            "page": page_num,
            "status": "ok" if content.strip() else "empty",
            "elapsed_s": round(elapsed_page, 4),
            "char_count": len(content),
            "strategy": str(getattr(page, "strategy", "unknown")),
        })

    # --- Engine identity ---
    engine_identity: dict = {}
    ocr_eng = getattr(extractor, "ocr_engine", None)
    if ocr_eng is not None and hasattr(ocr_eng, "identity"):
        try:
            ident = ocr_eng.identity
            engine_identity = {
                "engine": ident.engine,
                "runtime": ident.runtime,
                "profile": ident.profile,
                "language": ident.language,
                "package_versions": dict(ident.package_versions),
            }
        except Exception:
            pass

    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "git_sha": _git_sha(),
        "git_dirty": _git_dirty(),
        "engine": args.engine,
        "engine_identity": engine_identity,
        "mode": args.mode,
        "language": args.language,
        "pdf_path": str(args.pdf),
        "pdf_sha256": _sha256_file(args.pdf),
        "status": "ok",
        "elapsed_s": round(elapsed_s, 3),
        "page_count": len(document.pages),
        "extracted_markdown": str(md_path),
        "pages": page_entries,
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        n_pages = len(document.pages)
        n_empty = sum(1 for e in page_entries if e["status"] == "empty")
        s_per_page = elapsed_s / n_pages if n_pages else 0
        print(f"\n  Concluído: {n_pages} páginas em {elapsed_s:.1f}s ({s_per_page:.2f}s/pág)")
        if n_empty:
            print(f"  Atenção: {n_empty} páginas vazias")
        print(f"\n  Markdown  : {md_path}")
        print(f"  Manifesto : {manifest_path}")
        print()
        print("  Próximo passo — calcular métricas:")
        print(f"  python scripts\\compute_metrics.py \\")
        print(f"    --hypothesis {md_path} \\")
        print(f"    --manifesto corpus\\Corpus_Stress_OCR_Markdown_V4_MANIFESTO.json \\")
        print(f"    --engine {args.engine} \\")
        print(f"    --output-dir {args.output_dir}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
