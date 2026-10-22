"""Pinned RapidOCR PP-OCRv6 model artifacts from RapidOCR 3.9.2.

The upstream manifest serves the same ONNX files to ONNX Runtime and OpenVINO.
Model paths are always explicit so extraction never downloads weights and never
falls back to a different model family or size.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path


RAPIDOCR_PACKAGE_VERSION = "3.9.2"
RAPIDOCR_MANIFEST_REVISION = "RapidAI/RapidOCR@v3.9.2"
PORTUGUESE_REQUIRED_CHARS = frozenset("ãõáéíóúçêô")


@dataclass(frozen=True, slots=True)
class Artifact:
    filename: str
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RapidOCRArtifacts:
    size: str
    detector: Artifact
    recognizer: Artifact
    dictionary: Artifact
    classifier: Artifact

    @property
    def profile(self) -> str:
        return f"pp-ocrv6-{self.size}"


_BASE = f"https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v{RAPIDOCR_PACKAGE_VERSION}"


def _profile(size: str) -> RapidOCRArtifacts:
    if size not in {"medium", "small"}:
        raise ValueError("RapidOCR PP-OCRv6 size must be 'medium' or 'small'")
    return RapidOCRArtifacts(
        size,
        Artifact(
            f"PP-OCRv6_det_{size}.onnx",
            f"{_BASE}/onnx/PP-OCRv6/det/PP-OCRv6_det_{size}.onnx",
            {
                "medium": "92078b7355007ccfffcd4c8cd441a3afd4538904d06881b29a155e1e679907c2",
                "small": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
            }[size],
        ),
        Artifact(
            f"PP-OCRv6_rec_{size}.onnx",
            f"{_BASE}/onnx/PP-OCRv6/rec/PP-OCRv6_rec_{size}.onnx",
            {
                "medium": "eef444829dbbe18d7fea59a3f6eb75647518d2b3a9568d27c92e42940204894b",
                "small": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
            }[size],
        ),
        Artifact(
            f"ppocrv6_{size}_dict.txt",
            f"{_BASE}/paddle/PP-OCRv6/rec/PP-OCRv6_rec_{size}/ppocrv6_dict.txt",
            "b5f2bfe2bdd9448429e3e82b51c789775d9b42f2403d082b00662eb77e401c5d",
        ),
        Artifact(
            "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            f"{_BASE}/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
        ),
    )


PROFILES = {size: _profile(size) for size in ("medium", "small")}


def cache_directory(cache_home: str | Path | None = None) -> Path:
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache" / "pdfextractor"
    return root / "rapidocr"


def artifact_paths(size: str, cache_home: str | Path | None = None) -> tuple[Path, Path, Path, Path]:
    profile = PROFILES[size]
    root = cache_directory(cache_home)
    return tuple(root / item.filename for item in (profile.detector, profile.recognizer, profile.dictionary, profile.classifier))


def selected_paths(size: str, cache_home: str | Path | None = None, *, environment=None):
    """Resolve one immutable model set, rejecting legacy or partial overrides."""
    env = os.environ if environment is None else environment
    override_names = ("RAPIDOCR_DET_MODEL", "RAPIDOCR_REC_MODEL", "RAPIDOCR_REC_KEYS", "RAPIDOCR_CLS_MODEL")
    overrides = tuple(env.get(name) for name in override_names)
    if any(overrides) and not all(overrides):
        raise ValueError(
            "RapidOCR PP-OCRv6 requires detector, recognizer, dictionary and classifier overrides together; "
            "remove legacy partial RAPIDOCR_* model variables or configure all four v6 artifacts."
        )
    if all(overrides):
        return tuple(Path(value).expanduser() for value in overrides)
    return artifact_paths(size, cache_home)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifacts(size: str, paths=None) -> dict[str, str]:
    profile = PROFILES[size]
    detector, recognizer, dictionary, classifier = tuple(Path(p) for p in (paths or artifact_paths(size)))
    results = {}
    for kind, path, artifact in zip(
        ("detector", "recognizer", "dictionary", "classifier"),
        (detector, recognizer, dictionary, classifier),
        (profile.detector, profile.recognizer, profile.dictionary, profile.classifier),
    ):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"RapidOCR {profile.profile} {kind} is missing or empty: {path}")
        actual = sha256_file(path)
        if artifact.sha256 and actual != artifact.sha256:
            raise ValueError(
                f"RapidOCR {profile.profile} {kind} SHA-256 mismatch: expected {artifact.sha256}, got {actual}"
            )
        results[kind] = actual
    chars = set(dictionary.read_text(encoding="utf-8-sig").casefold())
    missing = sorted(PORTUGUESE_REQUIRED_CHARS - chars)
    if missing:
        raise ValueError(f"RapidOCR {profile.profile} dictionary cannot represent Portuguese: missing {''.join(missing)}")
    return results
