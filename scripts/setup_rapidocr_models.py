#!/usr/bin/env python3
"""Download and verify the four pinned RapidOCR PP-OCRv6 model profiles."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.request

from structured_pdf_text.ocr.pipelines.rapidocr.models import (
    PROFILES,
    artifact_paths,
    verify_artifacts,
)


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        curl = shutil.which("curl")
        if curl:
            subprocess.run([curl, "-fL", "--retry", "3", "--output", str(partial), url], check=True)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "structured-pdf-text-model-setup/1"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
        partial.replace(destination)
    except (OSError, urllib.error.URLError, subprocess.CalledProcessError, KeyboardInterrupt):
        partial.unlink(missing_ok=True)
        raise


def provision(size: str, cache_home: str | Path | None = None) -> dict[str, str]:
    profile = PROFILES[size]
    paths = artifact_paths(size, cache_home)
    for artifact, path in zip((profile.detector, profile.recognizer, profile.dictionary, profile.classifier), paths):
        if path.is_file():
            continue
        print(f"Downloading {artifact.filename} for {profile.profile}...")
        _download(artifact.url, path)
    hashes = verify_artifacts(size, paths)
    return dict(zip(("detector", "recognizer", "dictionary", "classifier"), hashes.values()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-home", type=Path, help="Project cache root; models are stored under <root>/rapidocr")
    parser.add_argument("--size", choices=("medium", "small", "all"), default="all")
    args = parser.parse_args()
    sizes = ("medium", "small") if args.size == "all" else (args.size,)
    try:
        for size in sizes:
            hashes = provision(size, args.cache_home)
            print(f"{PROFILES[size].profile}: verified {hashes}")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
