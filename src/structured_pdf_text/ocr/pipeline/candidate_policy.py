"""Candidate calibration owned independently by each OCR pipeline."""
from __future__ import annotations
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
import unicodedata
from typing import TYPE_CHECKING
from structured_pdf_text.document import OcrProvenance, OcrToken
if TYPE_CHECKING:
    from structured_pdf_text.ocr.candidate_fusion import OcrCandidateResult


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True, slots=True)
class CandidateFusionPolicy:
    replacement_margin: float = .08
    unknown_confidence: float = .5
    character_cap: int = 400
    character_divisor: int = 4000
    replacement_penalty: float = .40
    duplicate_cap: float = .5
    duplicate_weight: float = .30
    low_confidence_threshold: float = .50
    low_confidence_weight: float = .15
    single_character_cap: float = .5
    single_character_weight: float = .10
    invalid_geometry_weight: float = .40
    coverage_threshold: float = .20
    coverage_weight: float = .75
    independent_coverage: float = .25
    novel_minimum_score: float = -.30
    single_confirmed: tuple[float, float] = (.55, .35)
    single_unconfirmed: tuple[float, float] = (.70, .20)
    extreme_gap: float = .50
    moderate_gap: float = .25
    extreme_confirmed: tuple[float, float] = (.60, .40)
    moderate_confirmed: tuple[float, float] = (.40, .20)
    moderate_unconfirmed: tuple[float, float] = (.50, .30)
    modest_confirmed: tuple[float, float] = (.25, .10)
    modest_unconfirmed: tuple[float, float] = (.30, .15)
    fragment_coverage: float = .30
    suppressed_coverage: float = .65
    high_iou: float = .72
    moderate_iou: float = .28
    text_similarity: float = .68
    containment_coverage: float = .65
    token_confidence_weight: float = .65
    token_character_cap: int = 80
    token_character_divisor: int = 400
    printable_weight: float = .20
    suspicious_weight: float = .50
    detector_families: tuple[str, ...] = ()
    decoder_markers: tuple[tuple[str, str], ...] = ()

    def provenance(self, token, candidate):
        return token.ocr_provenance or OcrProvenance(
            engine=candidate.engine, candidate_id=candidate.candidate_id,
            detector=candidate.family if candidate.family in self.detector_families else None,
            decoder=next((decoder for marker, decoder in self.decoder_markers if marker in candidate.candidate_id), None),
            preprocessing=(candidate.candidate_id,) if candidate.candidate_id not in {"default", "full-page"} else (),
            tile_id=candidate.candidate_id.removeprefix("tile:") if candidate.candidate_id.startswith("tile:") else None,
            rotation=float(token.rotation) or None,
        )

    def score_candidate(self, tokens: list[OcrToken], target_area: float | None = None) -> float:
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
        mean_conf = sum(confidence) / len(confidence) if confidence else self.unknown_confidence
        texts = [token.text.strip() for token in tokens if token.text.strip()]
        char_bonus = min(sum(len(t) for t in texts), self.character_cap) / self.character_divisor
        # D8: penalise hallucination signals that EasyOCR's main scorer also flags.
        all_chars = "".join(texts)
        repl_penalty = len(re.findall(r"â€|�", all_chars)) / max(len(all_chars), 1) * self.replacement_penalty
        dup_penalty = min((len(texts) - len(set(texts))) / max(len(texts), 1), self.duplicate_cap) * self.duplicate_weight
        # Low-confidence ratio: fraction of tokens below 50% confidence.
        low_conf_penalty = sum(1 for c in confidence if c < self.low_confidence_threshold) / max(len(confidence), 1) * self.low_confidence_weight
        # Single-character hallucinations inflate token count without real content.
        single_char_penalty = min(
            sum(1 for t in texts if len(t) == 1) / max(len(texts), 1), self.single_character_cap
        ) * self.single_character_weight
        base = mean_conf + char_bonus - repl_penalty - dup_penalty - low_conf_penalty - single_char_penalty - invalid_ratio * self.invalid_geometry_weight
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
            if coverage < self.coverage_threshold:
                base -= (self.coverage_threshold - coverage) * self.coverage_weight
        return base

    def has_independent_confirmation(self, 
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
            if intersection.area / smaller < self.independent_coverage:
                continue
            # Must have been placed by a different family
            if current_family not in families_by_token[i]:
                return True
        return False

    def can_admit_novel_token(self, 
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
        if not math.isfinite(candidate.score) or candidate.score < self.novel_minimum_score:
            return False
        norm_text = _norm(token.text)
        confidence = token.confidence if token.confidence is not None else 0.0
        score_gap = best_score - candidate.score
        # Single-character tokens are a common hallucination pattern.
        if len(norm_text) <= 1:
            # Independent confirmation relaxes the threshold significantly.
            if has_independent_confirmation:
                return confidence >= self.single_confirmed[0] and score_gap <= self.single_confirmed[1]
            return confidence >= self.single_unconfirmed[0] and score_gap <= self.single_unconfirmed[1]
        # Candidates far below the best need strong evidence and confirmation.
        if score_gap > self.extreme_gap:
            # With independent confirmation, accept if the token itself is strong.
            return has_independent_confirmation and confidence >= self.extreme_confirmed[0] and self.score_token(token) >= self.extreme_confirmed[1]
        if score_gap > self.moderate_gap:
            if has_independent_confirmation:
                return confidence >= self.moderate_confirmed[0] and self.score_token(token) >= self.moderate_confirmed[1]
            return confidence >= self.moderate_unconfirmed[0] and self.score_token(token) >= self.moderate_unconfirmed[1]
        # Modest score gap: independent confirmation allows relaxed per-token threshold.
        if has_independent_confirmation:
            return confidence >= self.modest_confirmed[0] and self.score_token(token) >= self.modest_confirmed[1]
        return confidence >= self.modest_unconfirmed[0] and self.score_token(token) >= self.modest_unconfirmed[1]

    def is_explained_fragment(self, token: OcrToken, selected: list[OcrToken]) -> bool:
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
                if intersection.area / smaller >= self.fragment_coverage:
                    return True
        return False

    def suppressed_fragment_indices(self, selected: list[OcrToken]) -> set[int]:
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
                if intersection.area / small_area >= self.suppressed_coverage:
                    to_suppress.add(i)
                    break
        return to_suppress

    def same_evidence(self, first: OcrToken, second: OcrToken) -> bool:
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
        if overlap >= self.high_iou:
            # Same glyph region — must still verify text agrees enough to be
            # considered a duplicate rather than a conflicting reading.
            return similarity >= self.text_similarity
        if overlap >= self.moderate_iou:
            # Moderate overlap — require text similarity to confirm identity.
            return similarity >= self.text_similarity
        intersection = first.bbox.intersection(second.bbox)
        smaller_area = min(first.bbox.area, second.bbox.area)
        if intersection is None or smaller_area <= 0:
            return False
        return similarity >= self.text_similarity and intersection.area / smaller_area >= self.containment_coverage

    def score_token(self, token: OcrToken) -> float:
        text = token.text.strip()
        if not text:
            return -1.0
        confidence = self.unknown_confidence if token.confidence is None else max(0.0, min(1.0, token.confidence))
        printable = sum(ch.isprintable() and unicodedata.category(ch)[0] != "C" for ch in text) / len(text)
        suspicious = len(re.findall(r"â€|�", text)) / len(text)
        return confidence * self.token_confidence_weight + min(len(text), self.token_character_cap) / self.token_character_divisor + printable * self.printable_weight - suspicious * self.suspicious_weight


# Compatibility for direct consumers of the historical fusion helper. Production
# pipelines always provide their own policy, including component provenance.
LEGACY_FUSION_POLICY = CandidateFusionPolicy(
    detector_families=("craft_greedy", "craft_beam", "dbnet"),
    decoder_markers=(("wordbeam", "wordbeamsearch"), ("beam", "beamsearch")),
)
