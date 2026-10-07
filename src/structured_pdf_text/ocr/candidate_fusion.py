"""Spatial fusion of independent OCR candidate results."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
import math
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
    # D9: maps candidate_id to the number of tokens that candidate contributed
    # to the final fused result. Distinct from "selected" (the primary candidate)
    # since multiple candidates can contribute tokens to the same fusion output.
    contribution_counts: dict[str, int] = field(default_factory=dict)


class OcrCandidateFusionEngine:
    """Choose OCR evidence per spatial cluster while retaining unique regions."""

    def fuse(self, candidates: list[OcrCandidateResult]) -> OcrEvidence:
        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        best_score = ranked[0].score if ranked else float("-inf")
        selected: list[OcrToken] = []
        families_by_token: list[set[str]] = []
        candidates_by_token: list[set[str]] = []
        # D9: track which candidate_id contributed each selected slot.
        contributor_by_index: list[str] = []
        consensus = conflicts = 0
        for rank_idx, candidate in enumerate(ranked):
            is_primary = rank_idx == 0
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
                        rotation=float(raw_token.rotation) or None,
                    ),
                )
                overlaps = [
                    index for index, current in enumerate(selected)
                    if candidate.candidate_id not in candidates_by_token[index]
                    and _same_evidence(current, token)
                ]
                if not overlaps:
                    # R55: non-primary candidates must pass a stricter, candidate-
                    # aware gate before admitting novel (spatially unmatched) evidence.
                    if not is_primary and not _can_admit_novel_token(
                        token, candidate, best_score
                    ):
                        continue
                    # R56: suppress tokens whose text is already fully explained
                    # by a larger selected span at the same location. This
                    # prevents 1:N segmentation (e.g. "ABC DEF" vs "ABC"+"DEF")
                    # from duplicating content when the larger span was selected first.
                    if _is_explained_fragment(token, selected):
                        continue
                    selected.append(token)
                    families_by_token.append({candidate.family})
                    candidates_by_token.append({candidate.candidate_id})
                    contributor_by_index.append(candidate.candidate_id)
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
                        contributor_by_index[index] = candidate.candidate_id
                    continue
                conflicts += 1
                # A surviving baseline span has a strong preservation bonus.
                # The later candidate replaces it only with clear evidence.
                if _token_score(token) > _token_score(current) + 0.08:
                    selected[index] = token
                    families_by_token[index] = {candidate.family}
                    contributor_by_index[index] = candidate.candidate_id
        # R56: post-fusion pass — remove tokens that became fragments of a larger
        # span promoted during the loop (handles the N:1 → duplicate case).
        suppressed = _suppressed_fragment_indices(selected)
        order = [
            i for i in sorted(
                range(len(selected)),
                key=lambda i: (selected[i].bbox.y0, selected[i].bbox.x0),
            )
            if i not in suppressed
        ]
        # D9: aggregate per-candidate contribution counts over non-suppressed slots.
        contribution_counts: dict[str, int] = {}
        for i, cid in enumerate(contributor_by_index):
            if i not in suppressed:
                contribution_counts[cid] = contribution_counts.get(cid, 0) + 1
        return OcrEvidence(
            tokens=tuple(selected[i] for i in order),
            candidate_count=len(candidates),
            consensus_count=consensus,
            conflict_count=conflicts,
            contribution_counts=contribution_counts,
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
    # D8: penalise hallucination signals that EasyOCR's main scorer also flags.
    all_chars = "".join(texts)
    repl_penalty = len(re.findall(r"â€|�", all_chars)) / max(len(all_chars), 1) * 0.40
    dup_penalty = min((len(texts) - len(set(texts))) / max(len(texts), 1), 0.5) * 0.30
    # Low-confidence ratio: fraction of tokens below 50% confidence.
    low_conf_penalty = sum(1 for c in confidence if c < 0.50) / max(len(confidence), 1) * 0.15
    # Single-character hallucinations inflate token count without real content.
    single_char_penalty = min(
        sum(1 for t in texts if len(t) == 1) / max(len(texts), 1), 0.5
    ) * 0.10
    return mean_conf + char_bonus - repl_penalty - dup_penalty - low_conf_penalty - single_char_penalty


def _can_admit_novel_token(
    token: OcrToken,
    candidate: OcrCandidateResult,
    best_score: float,
) -> bool:
    """Return True when a non-primary candidate may insert a spatially novel token.

    Applies a candidate-aware gate that goes beyond the token-level score used
    for the primary candidate. The goal is to distinguish new, reliable coverage
    (e.g. a tile recovering a missed region) from isolated garbage from a bad
    hypothesis.
    """
    # Candidates with non-finite or catastrophically bad scores cannot introduce
    # novel evidence — they are likely noise or empty recognition passes.
    if not math.isfinite(candidate.score) or candidate.score < -0.30:
        return False
    norm_text = _norm(token.text)
    confidence = token.confidence if token.confidence is not None else 0.0
    score_gap = best_score - candidate.score
    # Single-character tokens are a common hallucination pattern; require very
    # high confidence and a modest score gap.
    if len(norm_text) <= 1:
        return confidence >= 0.70 and score_gap <= 0.20
    # Candidates far below the best need strong individual token evidence.
    if score_gap > 0.50:
        return False
    if score_gap > 0.25:
        return confidence >= 0.50 and _token_score(token) >= 0.30
    # Modest score gap: standard token quality sufficient.
    return confidence >= 0.30 and _token_score(token) >= 0.15


def _is_explained_fragment(token: OcrToken, selected: list[OcrToken]) -> bool:
    """Return True when token is a strict spatial fragment of an already-selected span.

    Used to suppress 1:N fragmentation duplicates: when a secondary candidate
    splits an already-selected span (e.g. "ABC" and "DEF" from a candidate that
    fused them as "ABC DEF"), the fragments should not be re-inserted.

    Requires actual spatial overlap — adjacency alone is not sufficient, because a
    legitimate separate occurrence of the same word may appear adjacent on the same
    line without being a duplicate of the selected span.
    """
    norm_text = _norm(token.text)
    if not norm_text:
        return False
    for existing in selected:
        norm_existing = _norm(existing.text)
        if norm_text not in norm_existing or norm_text == norm_existing:
            continue
        # Require meaningful spatial overlap — adjacency alone is not sufficient.
        intersection = token.bbox.intersection(existing.bbox)
        if intersection is not None and intersection.area > 0:
            smaller = max(min(token.bbox.area, existing.bbox.area), 1.0)
            if intersection.area / smaller >= 0.30:
                return True
    return False


def _suppressed_fragment_indices(selected: list[OcrToken]) -> set[int]:
    """Return the indices of tokens that are strict spatial fragments of a larger span.

    Handles the N:1 case: two small tokens from the primary ("ABC", "DEF") plus a
    large secondary token ("ABC DEF") — after "ABC" is replaced by "ABC DEF" in
    the overlap resolution, "DEF" may remain as a fragment. This post-fusion pass
    removes such stragglers.
    """
    to_suppress: set[int] = set()
    for i, small in enumerate(selected):
        if i in to_suppress:
            continue
        norm_small = _norm(small.text)
        if not norm_small:
            continue
        for j, big in enumerate(selected):
            if i == j or j in to_suppress:
                continue
            norm_big = _norm(big.text)
            if norm_small not in norm_big or norm_small == norm_big:
                continue
            # Require the smaller token to be substantially inside the larger span.
            intersection = small.bbox.intersection(big.bbox)
            if intersection is None:
                continue
            small_area = max(small.bbox.area, 1.0)
            if intersection.area / small_area >= 0.65:
                to_suppress.add(i)
                break
    return to_suppress


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
    suspicious = len(re.findall(r"â€|�", text)) / len(text)
    return confidence * 0.65 + min(len(text), 80) / 400 + printable * 0.2 - suspicious * 0.5
