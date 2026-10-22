"""Spatial fusion of independent OCR candidate results."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from .pipeline.candidate_policy import LEGACY_FUSION_POLICY

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

    def __init__(self, policy=None):
        self.policy = policy or LEGACY_FUSION_POLICY

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
                    ocr_provenance=self.policy.provenance(raw_token, candidate),
                )
                overlaps = [
                    index for index, current in enumerate(selected)
                    if candidate.candidate_id not in candidates_by_token[index]
                    and self.policy.same_evidence(current, token)
                ]
                if not overlaps:
                    # R55: non-primary candidates must pass a stricter, candidate-
                    # aware gate before admitting novel (spatially unmatched) evidence.
                    if not is_primary:
                        # VQ-12: check whether an independent candidate (different
                        # family) has already placed a spatially overlapping token
                        # with the same text — that counts as independent confirmation.
                        independent_confirmation = self.policy.has_independent_confirmation(
                            token, selected, families_by_token, candidate.family
                        )
                        if not self.policy.can_admit_novel_token(
                            token, candidate, best_score,
                            has_independent_confirmation=independent_confirmation,
                        ):
                            continue
                    # R56: suppress tokens whose text is already fully explained
                    # by a larger selected span at the same location. This
                    # prevents 1:N segmentation (e.g. "ABC DEF" vs "ABC"+"DEF")
                    # from duplicating content when the larger span was selected first.
                    if self.policy.is_explained_fragment(token, selected):
                        continue
                    selected.append(token)
                    families_by_token.append({candidate.family})
                    candidates_by_token.append({candidate.candidate_id})
                    contributor_by_index.append(candidate.candidate_id)
                    continue
                index = max(overlaps, key=lambda i: self.policy.score_token(selected[i]))
                current = selected[index]
                candidates_by_token[index].add(candidate.candidate_id)
                if _norm(current.text) == _norm(token.text):
                    if candidate.family not in families_by_token[index]:
                        consensus += 1
                        families_by_token[index].add(candidate.family)
                    if self.policy.score_token(token) > self.policy.score_token(current):
                        selected[index] = token
                        contributor_by_index[index] = candidate.candidate_id
                    continue
                conflicts += 1
                # A surviving baseline span has a strong preservation bonus.
                # The later candidate replaces it only with clear evidence.
                if self.policy.score_token(token) > self.policy.score_token(current) + self.policy.replacement_margin:
                    selected[index] = token
                    families_by_token[index] = {candidate.family}
                    contributor_by_index[index] = candidate.candidate_id
        # R56: post-fusion pass — remove tokens that became fragments of a larger
        # span promoted during the loop (handles the N:1 → duplicate case).
        suppressed = self.policy.suppressed_fragment_indices(selected)
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
    policy = getattr(engine, "fusion_policy", None) or LEGACY_FUSION_POLICY
    method = getattr(engine, "recognize_page", None)
    if not callable(method):
        return baseline_tokens, {"tiles": 0, "tile_tokens": 0, "fusion_conflicts": 0}
    page_area = page_bbox.width * page_bbox.height if hasattr(page_bbox, "width") else None
    # VQ-13: score the baseline against the full-page area so its coverage
    # denominator is consistent with tile candidates scored against their tile area.
    baseline_score = policy.score_candidate(baseline_tokens, target_area=page_area)
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
            score=policy.score_candidate(tokens, target_area=tile_area),
            engine=engine_name,
        ))
    evidence = OcrCandidateFusionEngine(policy).fuse(candidates)
    return list(evidence.tokens), {
        "tiles": len(candidates) - 1,
        "tile_tokens": total_tokens,
        "fusion_conflicts": evidence.conflict_count,
    }


# Historical helper signatures retain the compatibility calibration.
def _candidate_score(tokens, target_area=None):
    return LEGACY_FUSION_POLICY.score_candidate(tokens, target_area)


def _has_independent_confirmation(token, selected, families_by_token, current_family):
    return LEGACY_FUSION_POLICY.has_independent_confirmation(token, selected, families_by_token, current_family)


def _can_admit_novel_token(token, candidate, best_score, *, has_independent_confirmation=False):
    return LEGACY_FUSION_POLICY.can_admit_novel_token(token, candidate, best_score, has_independent_confirmation=has_independent_confirmation)


def _is_explained_fragment(token, selected):
    return LEGACY_FUSION_POLICY.is_explained_fragment(token, selected)


def _suppressed_fragment_indices(selected):
    return LEGACY_FUSION_POLICY.suppressed_fragment_indices(selected)


def _same_evidence(first, second):
    return LEGACY_FUSION_POLICY.same_evidence(first, second)


def _token_score(token):
    return LEGACY_FUSION_POLICY.score_token(token)


def _norm(text):
    from .pipeline.candidate_policy import _norm as normalize
    return normalize(text)
