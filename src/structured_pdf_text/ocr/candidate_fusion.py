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
        candidates_by_token: list[set[str]] = []
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
                    if candidate.candidate_id not in candidates_by_token[index]
                    and _same_evidence(current, token)
                ]
                if not overlaps:
                    # R55: new tokens with no spatial overlap must meet a
                    # minimum quality bar before admission, to suppress
                    # garbage detections that land in empty regions.
                    if _token_score(token) < 0.10:
                        continue
                    selected.append(token)
                    families_by_token.append({candidate.family})
                    candidates_by_token.append({candidate.candidate_id})
                    continue
                index = max(overlaps, key=lambda i: _token_score(selected[i]))
                current = selected[index]
                candidates_by_token[index].add(candidate.candidate_id)
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
    page_rotation: int = 0,
) -> tuple[list[OcrToken], dict[str, int]]:
    """Run overlapping page tiles and fuse their evidence with the full page."""
    from inspect import signature, Parameter
    from structured_pdf_text.ocr.image_views import canonicalize_page_image, tile_image_views
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
    canonical_image = canonicalize_page_image(image, page_rotation)
    for view in tile_image_views(canonical_image, page_bbox, rows=rows, columns=columns, overlap=overlap):
        kwargs = {"quality_policy": quality_policy} if accepts_policy else {}
        if "page_rotation" in params or any(p.kind is Parameter.VAR_KEYWORD for p in params.values()):
            kwargs["page_rotation"] = 0
        try:
            tokens = method(view.image, page_index, view.source_bbox, **kwargs)
        except Exception as exc:
            from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
            if isinstance(exc, FatalExtractionError):
                raise
            raise_if_resource_exhausted(exc, page_index=page_index, stage="ocr_tile")
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
    mean_conf = sum(confidence) / len(confidence) if confidence else 0.5
    texts = [token.text.strip() for token in tokens if token.text.strip()]
    char_bonus = min(sum(len(t) for t in texts), 400) / 4000
    # D8: penalise hallucination signals — replacement chars and duplicate spans.
    all_chars = "".join(texts)
    repl_penalty = len(re.findall(r"�", all_chars)) / max(len(all_chars), 1) * 0.40
    dup_penalty = min((len(texts) - len(set(texts))) / max(len(texts), 1), 0.5) * 0.30
    return mean_conf + char_bonus - repl_penalty - dup_penalty


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _same_evidence(first: OcrToken, second: OcrToken) -> bool:
    overlap = first.bbox.iou(second.bbox)
    if overlap >= 0.28:
        return True
    intersection = first.bbox.intersection(second.bbox)
    smaller_area = min(first.bbox.area, second.bbox.area)
    if intersection is None or smaller_area <= 0:
        return False
    similarity = SequenceMatcher(None, _norm(first.text), _norm(second.text)).ratio()
    return similarity >= 0.68 and intersection.area / smaller_area >= 0.65


def _token_score(token: OcrToken) -> float:
    text = token.text.strip()
    if not text:
        return -1.0
    confidence = 0.5 if token.confidence is None else max(0.0, min(1.0, token.confidence))
    printable = sum(ch.isprintable() and unicodedata.category(ch)[0] != "C" for ch in text) / len(text)
    suspicious = len(re.findall(r"�|\ufffd", text)) / len(text)
    return confidence * 0.65 + min(len(text), 80) / 400 + printable * 0.2 - suspicious * 0.5
