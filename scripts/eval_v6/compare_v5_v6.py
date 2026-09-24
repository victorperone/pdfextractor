"""Controlled PP-OCRv5 x PP-OCRv6 evaluation matrix.

This is an evaluation runner, not part of the extraction pipeline. It keeps
the PDF, OCR policies and render settings identical while recording identity
and resource cost. Byte counts and diffs remain observational; CER/WER need a
verified regional reference.

The PDF is positional by design. The default matrix is:

    pt x baseline, pt x adaptive,
    pt-v6-medium x baseline, pt-v6-medium x adaptive
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("pt", "pt-v6-medium")
POLICIES = ("baseline", "adaptive")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(path: Path) -> str | None:
    """Hash model files deterministically without following cache symlinks."""
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


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("paddlepaddle", "paddleocr", "paddlex", "pypdfium2", "Pillow", "numpy", "psutil"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def git_identity() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    return {
        "branch": run("branch", "--show-current"),
        "sha": run("rev-parse", "HEAD"),
        "status": run("status", "--short"),
    }


def model_inventory(cache_home: Path, profile: str, *, compute_hashes: bool = True) -> list[dict[str, Any]]:
    """Return names, paths, file counts and hashes for one OCR profile."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from structured_pdf_text.ocr.models import get_profile

    selected = get_profile(profile)
    root = cache_home.expanduser().resolve() / "official_models"
    models = []
    for name in (
        selected.doc_orientation,
        selected.textline_orientation,
        selected.detection,
        selected.recognition,
    ):
        directory = root / name
        files = [p for p in directory.rglob("*") if p.is_file()] if directory.is_dir() else []
        models.append(
            {
                "name": name,
                "directory": str(directory),
                "exists": directory.is_dir(),
                "file_count": len(files),
                "bytes": sum(p.stat().st_size for p in files),
                "sha256": sha256_tree(directory) if compute_hashes else None,
                "hash_skipped": not compute_hashes,
            }
        )
    return models


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = (int(item.strip()) for item in part.split("-", 1))
            if left < 1 or right < left:
                raise ValueError("page ranges must be positive and ascending")
            pages.extend(range(left, right + 1))
        else:
            page = int(part)
            if page < 1:
                raise ValueError("pages are human-facing and 1-based")
            pages.append(page)
    if not pages:
        raise ValueError("at least one page is required")
    return sorted(set(pages))


def _peak_rss_mb(process: Any) -> float | None:
    try:
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def run_extract(
    pdf: Path,
    profile: str,
    policy: str,
    cache_home: Path,
    out_file: Path,
    log_file: Path,
    *,
    threads: int = 0,
) -> dict[str, Any]:
    """Run one extraction and return auditable process metadata."""
    cmd = [
        sys.executable,
        "-m",
        "structured_pdf_text.cli",
        "extract",
        str(pdf),
        "--output",
        "markdown",
        "--output-file",
        str(out_file),
        "--mode",
        "balanced",
        "--ocr-model-profile",
        profile,
        "--ocr-quality-policy",
        policy,
        "--cache-home",
        str(cache_home),
        "--threads",
        str(threads),
    ]
    if policy == "baseline":
        # Keep this experiment explicit; protected recovery code is untouched.
        cmd.append("--no-ocr-quality-variants")
    env = os.environ.copy()
    env["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    env["PADDLE_PDX_CACHE_HOME"] = str(cache_home.expanduser().resolve())

    started = time.perf_counter()
    peak: float | None = None
    with log_file.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n")
        log.flush()
        process = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            import psutil

            child = psutil.Process(process.pid)
        except Exception:
            child = None
        while process.poll() is None:
            if child is not None:
                current = _peak_rss_mb(child)
                if current is not None:
                    peak = max(peak or current, current)
            time.sleep(0.1)
        returncode = int(process.returncode or 0)
        if child is not None:
            current = _peak_rss_mb(child)
            if current is not None:
                peak = max(peak or current, current)
    elapsed = time.perf_counter() - started
    return {
        "profile": profile,
        "policy": policy,
        "command": cmd,
        "returncode": returncode,
        "status": "success" if returncode == 0 and out_file.exists() else "failed",
        "elapsed_s": round(elapsed, 3),
        "peak_rss_mb": round(peak, 2) if peak is not None else None,
        "output": str(out_file),
        "log": str(log_file),
        "output_bytes": out_file.stat().st_size if out_file.exists() else 0,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="PDF to compare; positional by design")
    parser.add_argument("--v5-cache", type=Path, default=Path.home() / ".cache/pdfextractor/paddlex")
    parser.add_argument("--v6-cache", type=Path, default=Path.home() / ".cache/pdfextractor/paddlex-v6-eval")
    parser.add_argument("--output-dir", type=Path, default=Path("output/compare_v5_v6"))
    parser.add_argument("--pages", help="Human-facing 1-based pages to record, e.g. 12,27-30")
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--no-model-hashes", action="store_true", help="Skip potentially expensive model hashes")
    args = parser.parse_args(argv)

    pdf = args.pdf.expanduser().resolve()
    if not pdf.is_file():
        print(f"[FAIL] PDF nao encontrado: {pdf}", file=sys.stderr)
        return 1
    try:
        pages = parse_pages(args.pages) if args.pages else None
    except ValueError as exc:
        parser.error(str(exc))
    out = args.output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    cache_by_profile = {"pt": args.v5_cache, "pt-v6-medium": args.v6_cache}
    assert set(cache_by_profile) == set(PROFILES), (
        f"cache_by_profile keys {set(cache_by_profile)} must match PROFILES {set(PROFILES)}"
    )
    metadata: dict[str, Any] = {
        "schema": "structured-pdf-text.ocr-v5-v6-evaluation.v2",
        "pdf": str(pdf),
        "pdf_sha256": sha256_file(pdf),
        "pages_human_1_based": pages,
        "git": git_identity(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": sys.version,
        "packages": package_versions(),
        "profiles": PROFILES,
        "profile_labels": _profile_labels(),
        "policies": POLICIES,
        "render_scale": 2.0,
        "rgb_budget_mib": os.environ.get("PDFEXTRACTOR_OCR_RGB_BUDGET_MIB", "8.0"),
        "model_inventory": {},
        "runs": [],
        "quality_metrics": {
            "status": "not_computed",
            "reason": "No region-level verified ground truth was supplied; byte/line diffs are observational only.",
        },
    }
    for profile in PROFILES:
        cache = Path(cache_by_profile[profile]).expanduser().resolve()
        metadata["model_inventory"][profile] = model_inventory(
            cache, profile, compute_hashes=not args.no_model_hashes
        )

    failures = 0
    output_paths: dict[str, Path] = {}
    for profile in PROFILES:
        for policy in POLICIES:
            stem = f"{profile}_{policy}"
            run = run_extract(
                pdf,
                profile,
                policy,
                Path(cache_by_profile[profile]),
                out / f"{stem}.md",
                out / f"{stem}.log",
                threads=args.threads,
            )
            metadata["runs"].append(run)
            if run["status"] == "success":
                output_paths[f"{profile}/{policy}"] = Path(run["output"])
            if run["status"] != "success":
                failures += 1
            print(
                f"[{run['status'].upper()}] {profile}/{policy} "
                f"{run['output_bytes']:,} bytes | {run['elapsed_s']:.1f}s | "
                f"rss={run['peak_rss_mb'] or 'n/a'} MB"
            )

    diffs: dict[str, Any] = {}
    for policy in POLICIES:
        v5 = output_paths.get(f"pt/{policy}")
        v6 = output_paths.get(f"pt-v6-medium/{policy}")
        if v5 is None or v6 is None:
            diffs[policy] = {"status": "not_computed", "reason": "one or both runs failed"}
            continue
        diff = list(
            difflib.unified_diff(
                v5.read_text(encoding="utf-8").splitlines(),
                v6.read_text(encoding="utf-8").splitlines(),
                fromfile=f"pt/{policy}",
                tofile=f"pt-v6-medium/{policy}",
                lineterm="",
            )
        )
        diff_path = out / f"diff_{policy}.txt"
        diff_path.write_text("\n".join(diff) + ("\n" if diff else ""), encoding="utf-8")
        diffs[policy] = {
            "status": "identical" if not diff else "different",
            "path": str(diff_path),
            "added_lines": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
            "removed_lines": sum(1 for line in diff if line.startswith("-") and not line.startswith("---")),
        }
    metadata["diffs"] = diffs
    _write_json(out / "evaluation.json", metadata)
    print(f"[INFO] Manifest: {out / 'evaluation.json'}")
    if failures:
        print(f"[FAIL] {failures} de {len(metadata['runs'])} execucoes falharam", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
