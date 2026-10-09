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
                    if not is_primary:
                        # VQ-12: check whether an independent candidate (different
                        # family) has already placed a spatially overlapping token
                        # with the same text — that counts as independent confirmation.
                        independent_confirmation = _has_independent_confirmation(
                            token, selected, families_by_token, candidate.family
                        )
                        if not _can_admit_novel_token(
                            token, candidate, best_score,
                            has_independent_confirmation=independent_confirmation,
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
    page_area = page_bbox.width * page_bbox.height if hasattr(page_bbox, "width") else None
    # VQ-13: score the baseline against the full-page area so its coverage
    # denominator is consistent with tile candidates scored against their tile area.
    baseline_score = _candidate_score(baseline_tokens, target_area=page_area)
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
        # VQ-13: score each tile against its own tile area so that a small
        # high-confidence tile is not unfairly penalised relative to the page.
        tile_area = view.source_bbox.width * view.source_bbox.height if hasattr(view.source_bbox, "width") else None
        candidates.append(OcrCandidateResult(
            candidate_id=f"tile:{view.tile_id}",
            family="tile",
            tokens=tuple(tokens),
            score=_candidate_score(tokens, target_area=tile_area),
            engine=engine_name,
        ))
    evidence = OcrCandidateFusionEngine().fuse(candidates)
    return list(evidence.tokens), {
        "tiles": len(candidates) - 1,
        "tile_tokens": total_tokens,
        "fusion_conflicts": evidence.conflict_count,
    }


def _candidate_score(tokens: list[OcrToken], target_area: float | None = None) -> float:
    """Score a candidate's quality, optionally penalising low coverage of a target region.

    VQ-13: When ``target_area`` is provided (e.g. the area of the full page for a
    full-page candidate, or the tile area for a tile candidate), a coverage ratio is
    computed and used to discount scores from candidates whose bounding envelope is
    much smaller than the region they are meant to cover.  A crop that recognises 5
    high-confidence characters cannot legitimately beat a full-page pass that found
    200 characters at slightly lower average confidence — the char_bonus cap of 400
    chars already helps, but an explicit coverage penalty is more principled.
    """
    if not tokens:
        return float("-inf")
    # B4: exclude tokens with degenerate bboxes from confidence so a 0×0
    # high-confidence token cannot inflate the score and beat a valid tile.
    valid = [t for t in tokens if t.bbox.area > 0]
    invalid_ratio = (len(tokens) - len(valid)) / len(tokens)
    confidence = [token.confidence for token in valid if token.confidence is not None]
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
    base = mean_conf + char_bonus - repl_penalty - dup_penalty - low_conf_penalty - single_char_penalty - invalid_ratio * 0.40
    # VQ-13: coverage penalty — if the candidate's token envelope covers much less
    # than the target region, apply a penalty proportional to the deficit. This
    # prevents a tiny high-confidence crop from outscoring a full-page candidate.
    # Cap the penalty at 0.15 to avoid completely disqualifying valid partial tiles.
    if target_area and target_area > 0 and valid:
        xs = [t.bbox.x0 for t in valid] + [t.bbox.x1 for t in valid]
        ys = [t.bbox.y0 for t in valid] + [t.bbox.y1 for t in valid]
        envelope = (max(xs) - min(xs)) * (max(ys) - min(ys))
        coverage = min(envelope / target_area, 1.0)
        # A candidate covering < 20% of the target area incurs a penalty.
        if coverage < 0.20:
            base -= (0.20 - coverage) * 0.75
    return base


def _has_independent_confirmation(
    token: OcrToken,
    selected: list[OcrToken],
    families_by_token: list[set[str]],
    current_family: str,
) -> bool:
    """Return True when a spatially similar token with matching text exists in selected,
    placed by a *different* OCR family — i.e. independent confirmation.

    VQ-12: tiles from the same image are not truly independent (they share the source
    pixels). Only a different OCR family (craft vs dbnet, full-page vs tile) counts as
    independent evidence. Single-family consensus cannot overcome a novel-token gate.
    """
    norm_text = _norm(token.text)
    if not norm_text:
        return False
    for i, existing in enumerate(selected):
        # Must match text (same reading)
        if _norm(existing.text) != norm_text:
            continue
        # Must have spatial overlap (same visual location)
        intersection = token.bbox.intersection(existing.bbox)
        if intersection is None:
            continue
        smaller = max(min(token.bbox.area, existing.bbox.area), 1.0)
        if intersection.area / smaller < 0.25:
            continue
        # Must have been placed by a different family
        if current_family not in families_by_token[i]:
            return True
    return False


def _can_admit_novel_token(
    token: OcrToken,
    candidate: OcrCandidateResult,
    best_score: float,
    *,
    has_independent_confirmation: bool = False,
) -> bool:
    """Return True when a non-primary candidate may insert a spatially novel token.

    VQ-12: tokens with independent confirmation from a different OCR family pass
    at lower thresholds; tokens without confirmation require stronger evidence,
    especially when they are single characters or the candidate score is far below
    the best.
    """
    # Candidates with non-finite or catastrophically bad scores cannot introduce
    # novel evidence — they are likely noise or empty recognition passes.
    if not math.isfinite(candidate.score) or candidate.score < -0.30:
        return False
    norm_text = _norm(token.text)
    confidence = token.confidence if token.confidence is not None else 0.0
    score_gap = best_score - candidate.score
    # Single-character tokens are a common hallucination pattern.
    if len(norm_text) <= 1:
        # Independent confirmation relaxes the threshold significantly.
        if has_independent_confirmation:
            return confidence >= 0.55 and score_gap <= 0.35
        return confidence >= 0.70 and score_gap <= 0.20
    # Candidates far below the best need strong evidence and confirmation.
    if score_gap > 0.50:
        # With independent confirmation, accept if the token itself is strong.
        return has_independent_confirmation and confidence >= 0.60 and _token_score(token) >= 0.40
    if score_gap > 0.25:
        if has_independent_confirmation:
            return confidence >= 0.40 and _token_score(token) >= 0.20
        return confidence >= 0.50 and _token_score(token) >= 0.30
    # Modest score gap: independent confirmation allows relaxed per-token threshold.
    if has_independent_confirmation:
        return confidence >= 0.25 and _token_score(token) >= 0.10
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
    """Return True when two tokens represent the same visual evidence.

    VQ-11: IoU alone is insufficient — two tokens with high positional
    overlap but different text (e.g. ``R$ 120,00`` vs ``R$ 128,00``) must
    not be considered the same evidence, as merging them silently discards
    the distinction.

    Strategy:
    * High IoU (≥ 0.72): assume same glyph region and check text similarity.
      Accept if texts are at least 68% similar (covers OCR variations of the
      same characters).  Reject if texts are clearly distinct (e.g. digits
      differ), keeping both as a conflict.
    * Low IoU (0.28–0.72): require both spatial overlap *and* text similarity.
    * Very low IoU (< 0.28): check containment overlap with text similarity.
    """
    overlap = first.bbox.iou(second.bbox)
    similarity = SequenceMatcher(None, _norm(first.text), _norm(second.text)).ratio()
    if overlap >= 0.72:
        # Same glyph region — must still verify text agrees enough to be
        # considered a duplicate rather than a conflicting reading.
        return similarity >= 0.68
    if overlap >= 0.28:
        # Moderate overlap — require text similarity to confirm identity.
        return similarity >= 0.68
    intersection = first.bbox.intersection(second.bbox)
    smaller_area = min(first.bbox.area, second.bbox.area)
    if intersection is None or smaller_area <= 0:
        return False
    return similarity >= 0.68 and intersection.area / smaller_area >= 0.65


def _token_score(token: OcrToken) -> float:
    text = token.text.strip()
    if not text:
        return -1.0
    confidence = 0.5 if token.confidence is None else max(0.0, min(1.0, token.confidence))
    printable = sum(ch.isprintable() and unicodedata.category(ch)[0] != "C" for ch in text) / len(text)
    suspicious = len(re.findall(r"â€|�", text)) / len(text)
    return confidence * 0.65 + min(len(text), 80) / 400 + printable * 0.2 - suspicious * 0.5
