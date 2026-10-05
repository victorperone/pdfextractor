"""Spatial fusion of independent OCR candidate results."""
from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import unicodedata

from structured_pdf_text.document import OcrProvenance, OcrToken


@dataclass(frozen=True, slots=True)
class OcrCandidateResult:
    candidate_id: str
    family: str
    tokens: tuple[OcrToken, ...]
    score: float = 0.0
    engine: str = "unknown"


@dataclass(frozen=True, slots=True)
class OcrEvidence:
    """Consolidated evidence, separated from the individual OCR hypotheses."""
    tokens: tuple[OcrToken, ...]
    candidate_count: int
    consensus_count: int
    conflict_count: int


class OcrCandidateFusionEngine:
    """Choose OCR evidence per spatial cluster while retaining unique regions."""

    def fuse(self, candidates: list[OcrCandidateResult]) -> OcrEvidence:
        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        selected: list[OcrToken] = []
        families_by_token: list[set[str]] = []
        consensus = conflicts = 0
        for candidate in ranked:
            for raw_token in candidate.tokens:
                token = replace(
                    raw_token,
                    provenance=raw_token.provenance or f"candidate:{candidate.candidate_id}",
                    ocr_provenance=raw_token.ocr_provenance or OcrProvenance(
                        engine=candidate.engine,
                        candidate_id=candidate.candidate_id,
                        detector=candidate.family if candidate.family in {"craft_greedy", "craft_beam", "dbnet"} else None,
                        decoder="wordbeamsearch" if "wordbeam" in candidate.candidate_id else (
                            "beamsearch" if "beam" in candidate.candidate_id else None
                        ),
                        preprocessing=(candidate.candidate_id,) if candidate.candidate_id not in {"default", "full-page"} else (),
                        tile_id=candidate.candidate_id.removeprefix("tile:") if candidate.candidate_id.startswith("tile:") else None,
                    ),
                )
                overlaps = [
                    index for index, current in enumerate(selected)
                    if _same_evidence(current, token)
                ]
                if not overlaps:
                    selected.append(token)
                    families_by_token.append({candidate.family})
                    continue
                index = max(overlaps, key=lambda i: _token_score(selected[i]))
                current = selected[index]
                if _norm(current.text) == _norm(token.text):
                    if candidate.family not in families_by_token[index]:
                        consensus += 1
                        families_by_token[index].add(candidate.family)
                    if _token_score(token) > _token_score(current):
                        selected[index] = token
                    continue
                conflicts += 1
                # A surviving baseline span has a strong preservation bonus.
                # The later candidate replaces it only with clear evidence.
                if _token_score(token) > _token_score(current) + 0.08:
                    selected[index] = token
                    families_by_token[index] = {candidate.family}
        order = sorted(range(len(selected)), key=lambda i: (selected[i].bbox.y0, selected[i].bbox.x0))
        return OcrEvidence(
            tokens=tuple(selected[i] for i in order),
            candidate_count=len(candidates),
            consensus_count=consensus,
            conflict_count=conflicts,
        )


def recognize_page_with_tiles(
    engine: object,
    image: object,
    page_index: int,
    page_bbox: object,
    baseline_tokens: list[OcrToken],
    *,
    quality_policy: str,
    rows: int = 2,
    columns: int = 2,
    overlap: float = 0.15,
) -> tuple[list[OcrToken], dict[str, int]]:
    """Run overlapping page tiles and fuse their evidence with the full page."""
    from inspect import signature, Parameter
    from structured_pdf_text.ocr.image_views import tile_image_views
    method = getattr(engine, "recognize_page", None)
    if not callable(method):
        return baseline_tokens, {"tiles": 0, "tile_tokens": 0, "fusion_conflicts": 0}
    baseline_score = _candidate_score(baseline_tokens)
    identity = getattr(engine, "identity", None)
    engine_name = getattr(identity, "engine", type(engine).__name__)
    candidates = [OcrCandidateResult("full-page", "full-page", tuple(baseline_tokens), baseline_score, engine_name)]
    total_tokens = 0
    try:
        params = signature(method).parameters
    except (TypeError, ValueError):
        params = {}
    accepts_policy = "quality_policy" in params or any(p.kind is Parameter.VAR_KEYWORD for p in params.values())
    for view in tile_image_views(image, page_bbox, rows=rows, columns=columns, overlap=overlap):
        kwargs = {"quality_policy": quality_policy} if accepts_policy else {}
        try:
            tokens = method(view.image, page_index, view.source_bbox, **kwargs)
        except Exception:
            continue
        total_tokens += len(tokens)
        candidates.append(OcrCandidateResult(
            candidate_id=f"tile:{view.tile_id}",
            family="tile",
            tokens=tuple(tokens),
            score=_candidate_score(tokens),
            engine=engine_name,
        ))
    evidence = OcrCandidateFusionEngine().fuse(candidates)
    return list(evidence.tokens), {
        "tiles": len(candidates) - 1,
        "tile_tokens": total_tokens,
        "fusion_conflicts": evidence.conflict_count,
    }


def _candidate_score(tokens: list[OcrToken]) -> float:
    if not tokens:
        return float("-inf")
    confidence = [token.confidence for token in tokens if token.confidence is not None]
    return (sum(confidence) / len(confidence) if confidence else 0.5) + min(
        sum(len(token.text.strip()) for token in tokens), 400
    ) / 4000


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _same_evidence(first: OcrToken, second: OcrToken) -> bool:
    overlap = first.bbox.iou(second.bbox)
    if overlap >= 0.28:
        return True
    distance = ((first.bbox.cx - second.bbox.cx) ** 2 + (first.bbox.cy - second.bbox.cy) ** 2) ** 0.5
    line_height = max(first.bbox.height, second.bbox.height, 1.0)
    similarity = SequenceMatcher(None, _norm(first.text), _norm(second.text)).ratio()
    return similarity >= 0.68 and distance <= 1.8 * line_height


def _token_score(token: OcrToken) -> float:
    text = token.text.strip()
    if not text:
        return -1.0
    confidence = 0.5 if token.confidence is None else max(0.0, min(1.0, token.confidence))
    printable = sum(ch.isprintable() and unicodedata.category(ch)[0] != "C" for ch in text) / len(text)
    suspicious = len(re.findall(r"�|\ufffd", text)) / len(text)
    return confidence * 0.65 + min(len(text), 80) / 400 + printable * 0.2 - suspicious * 0.5
