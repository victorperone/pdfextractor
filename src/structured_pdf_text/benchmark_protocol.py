"""Versioned definition of the shared inputs for deployment-profile runs."""
from __future__ import annotations

import hashlib
import json
from typing import Any


PROTOCOL_NAME = "ocr-deployment-profile-v1"


def protocol_manifest(*, language: str, mode: str, quality_policy: str) -> dict[str, Any]:
    body = {
        "protocol": PROTOCOL_NAME,
        "language": language,
        "mode": mode,
        "quality_policy": quality_policy,
        "page_selection_policy": "identical-explicit-pages-or-all-pages",
        "corpus_policy": "identical-pdf-reference-and-manifest-hashes",
        "comparison_kind": "deployment_profile",
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {**body, "benchmark_protocol_id": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
