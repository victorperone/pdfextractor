"""Neutral recovery-profile mechanism; each engine owns its profile values."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re

from .contracts import IntegrationProposal, ProposalKind, WorkGoal


@dataclass(frozen=True, slots=True)
class ConservativeIntegrationPolicy:
    """Engine-owned proposal policy; mandatory conservation stays in the core."""

    policy_id: str

    def propose(self, request, authorization_id: str, evidence_ids):
        kind = ProposalKind.COMPLEMENT
        replaced = ()
        if request.goal is WorkGoal.FIGURE:
            kind = ProposalKind.ASSOCIATE_FIGURE
            replaced = tuple(request.native_context.get("replacement_evidence_ids", ()))
        elif request.goal is WorkGoal.CELL:
            kind = ProposalKind.CORRECT_CELL
            replaced = tuple(request.native_context.get("replacement_evidence_ids", ()))
        elif "replacement_evidence_ids" in request.native_context:
            kind = ProposalKind.REPLACE
            replaced = tuple(request.native_context["replacement_evidence_ids"])
        return IntegrationProposal(
            f"{request.request_id}:{authorization_id}:{kind.value}",
            authorization_id, request.scope.target, kind, tuple(evidence_ids),
            f"{self.policy_id}:core_authorized_{request.goal.value}", replaced,
            composition="join_critical" if request.goal is WorkGoal.CRITICAL_DATA else "preserve",
        )

    def prepare_token(self, token, request):
        if request.goal is WorkGoal.TABLE and request.native_context.get("visual_table_refinement"):
            return replace(token, provenance="visual_table_refinement")
        if request.goal is WorkGoal.FIGURE and request.native_context.get("dedicated_refinement"):
            return replace(token, provenance="figure_ocr")
        if request.goal is WorkGoal.NOTE:
            return replace(token, provenance="footnote_high_scale")
        return token


LEGACY_INTEGRATION_POLICY = ConservativeIntegrationPolicy("legacy-compat-v1")


@dataclass(frozen=True, slots=True)
class RasterBoundaryPolicy:
    """Local choice of authorized raster boundary representation."""

    mode: str = "mask"

    def __post_init__(self):
        if self.mode not in ("mask", "trim"):
            raise ValueError("Raster boundary policy must be mask or trim")


LEGACY_RASTER_BOUNDARY_POLICY = RasterBoundaryPolicy()


@dataclass(frozen=True, slots=True)
class EvidenceAdmissionPolicy:
    """Default admission preserves all validated observations."""

    policy_id: str = "admit-observed-evidence-v1"

    def rejection_reason(self, token, request):
        return None


LEGACY_EVIDENCE_ADMISSION_POLICY = EvidenceAdmissionPolicy()


@dataclass(frozen=True, slots=True)
class CellRecoveryPolicy:
    """Candidate selection values owned independently by each engine pipeline."""

    minimum_existing_confidence: float = .70
    render_scale_factor: float = 1.75
    enlarged_scale_factor: float = 2.0
    empty_minimum_confidence: float = .75
    empty_minimum_characters: int = 2

    def select(self, candidates, *, current_text, cell_bbox, header_text):
        from dataclasses import replace
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner

        candidates = [tuple(candidate) for candidate in candidates if candidate]
        if not candidates:
            return ()
        refiner = CriticalDataRefiner()
        data_type = refiner.detect_type_from_context([header_text]) if header_text else None
        texts = [" ".join(token.text.strip() for token in group if token.text.strip()) for group in candidates]

        def rank(index):
            confidence = sum(token.confidence or 0.0 for token in candidates[index]) / len(candidates[index])
            score = refiner.score_token(replace(candidates[index][0], text=texts[index]), data_type) if data_type else 1.0
            return score, confidence

        index = max(range(len(candidates)), key=rank)
        chosen, text = candidates[index], texts[index]
        if not text:
            return ()
        if not current_text.strip():
            confidence = sum(token.confidence or 0.0 for token in chosen) / len(chosen)
            if confidence < self.empty_minimum_confidence or len(text.strip()) < self.empty_minimum_characters:
                return ()
            if not any(token.text.strip() and token.bbox.overlap_ratio(cell_bbox) >= .30 for token in chosen):
                return ()
        if data_type and current_text.strip():
            old_score = refiner.score_token(replace(chosen[0], text=current_text), data_type)
            if rank(index)[0] < old_score:
                return ()
        return chosen


LEGACY_CELL_POLICY = CellRecoveryPolicy()


@dataclass(frozen=True, slots=True)
class TokenRefinementPolicy:
    footnote_scale_factor: float = 2.0
    footnote_score_gain: float = .05
    footnote_character_ratio: float = 1.2
    footnote_confidence_tolerance: float = .10
    weak_line_score_gain: float = .03
    unknown_confidence: float = .5
    character_bonus_cap: int = 400
    character_bonus_divisor: int = 4000
    duplicate_iou: float = .50
    critical_render_scale_factor: float = 1.75
    critical_sufficient_score: float = .98
    critical_confidence_weight: float = .01

    def needs_critical(self, token, data_type):
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner

        return data_type is not None and CriticalDataRefiner().score_token(token, data_type) < self.critical_sufficient_score

    def select_critical(self, candidates, original, data_type):
        from structured_pdf_text.ocr.critical_data import CriticalDataRefiner

        refiner = CriticalDataRefiner()
        candidates = [tuple(items) for items in candidates if items]
        if not candidates:
            return ()
        def format_score(items):
            text = " ".join(item.text.strip() for item in items if item.text.strip())
            return refiner.score_token(replace(original, text=text), data_type)
        def rank(items):
            return format_score(items) + sum(item.confidence or 0. for item in items) / len(items) * self.critical_confidence_weight
        selected = max(candidates, key=rank)
        return selected if format_score(selected) > refiner.score_token(original, data_type) else ()

    def token_score(self, tokens):
        if not tokens:
            return -math.inf
        confidences = [token.confidence for token in tokens if token.confidence is not None]
        average = sum(confidences) / len(confidences) if confidences else self.unknown_confidence
        characters = sum(len(token.text.strip()) for token in tokens)
        return average + min(characters, self.character_bonus_cap) / self.character_bonus_divisor

    def deduplicate(self, tokens):
        from unicodedata import normalize
        kept = []
        for token in tokens:
            text = " ".join(normalize("NFKC", token.text).casefold().split())
            duplicate = next((index for index, old in enumerate(kept)
                              if " ".join(normalize("NFKC", old.text).casefold().split()) == text
                              and token.bbox.iou(old.bbox) >= self.duplicate_iou), None)
            if duplicate is None:
                kept.append(token)
            elif (token.confidence or 0.) > (kept[duplicate].confidence or 0.):
                kept[duplicate] = token
        return kept

    def accept_footnote(self, old, new):
        if not new:
            return False
        old_score, new_score = self.token_score(old), self.token_score(new)
        old_chars = len(re.sub(r"\s+", "", " ".join(token.text.strip() for token in old)))
        new_chars = len(re.sub(r"\s+", "", " ".join(token.text.strip() for token in new)))
        return (new_score > old_score + self.footnote_score_gain
                or (new_chars >= old_chars * self.footnote_character_ratio
                    and new_score >= old_score - self.footnote_confidence_tolerance))

    def accept_weak_line(self, old, new, thresholds):
        from structured_pdf_text.ocr.quality import assess_ocr_quality

        before = assess_ocr_quality(old, thresholds=thresholds)
        after = assess_ocr_quality(new, thresholds=thresholds)
        accepted = bool(new) and (not old or after.score >= before.score + self.weak_line_score_gain
                                  or (after.sufficient and not before.sufficient))
        return accepted, before, after


LEGACY_TOKEN_REFINEMENT_POLICY = TokenRefinementPolicy()


@dataclass(frozen=True, slots=True)
class FigureSegmentationPolicy:
    """Local splitting preferences; the core owns geometry and authorization."""

    minimum_width: int = 240
    minimum_height: int = 80
    minimum_aspect: float = 2.0
    dark_threshold: int = 210
    occupied_ratio: float = .025
    minimum_gap: int = 8
    gap_width_ratio: float = .025
    minimum_segment_ratio: float = .18
    maximum_segments: int = 4

    def segments(self, crop, *, aspect):
        width, height = crop.size
        whole = ((0, width),)
        if width < self.minimum_width or height < self.minimum_height or aspect < self.minimum_aspect:
            return whole
        try:
            with crop.convert("L") as gray:
                pixels = list(gray.getdata())
        except MemoryError:
            raise
        except Exception:
            return whole
        occupied = [
            sum(pixels[y * width + x] < self.dark_threshold for y in range(height))
            / max(height, 1) > self.occupied_ratio
            for x in range(width)
        ]
        gaps = []
        start = None
        for index, value in enumerate(occupied + [True]):
            if not value and start is None:
                start = index
            elif value and start is not None:
                if index - start >= max(self.minimum_gap, int(width * self.gap_width_ratio)):
                    gaps.append((start, index))
                start = None
        segments = []
        left = 0
        for gap_left, gap_right in gaps:
            if gap_left - left >= int(width * self.minimum_segment_ratio):
                segments.append((left, gap_left))
            left = gap_right
        if width - left >= int(width * self.minimum_segment_ratio):
            segments.append((left, width))
        return tuple(segments) if 2 <= len(segments) <= self.maximum_segments else whole


LEGACY_FIGURE_SEGMENTATION_POLICY = FigureSegmentationPolicy()


@dataclass(frozen=True, slots=True)
class RegionalCandidatePolicy:
    """Local calibration for regional filtering, ranking and numeric consensus."""

    character_bonus_cap: float = .08
    character_log_weight: float = .012
    horizontal_weight: float = .02
    numeric_token_bonus_cap: float = .05
    numeric_token_weight: float = .005
    numeric_syntax_weight: float = .03
    numeric_digit_weight: float = .002
    numeric_digit_cap: int = 6
    position_iou: float = .25
    position_coverage: float = .50
    textual_minimum_characters: int = 2

    def compose_tiles(self, tile_groups):
        """Select observations from completed tiles in canonical coordinates.

        Each pair contains the tile's authorized rectangle and its observed
        tokens. Selection never edits the underlying authorized observations.
        Engines may specialize composition without changing other pipelines.
        """
        from structured_pdf_text.ocr.recovery import _deduplicate_tiled_tokens

        return _deduplicate_tiled_tokens([
            token for _bounds, tokens in tile_groups for token in tokens
        ])

    def filter(self, tokens, request):
        filtered = [token for token in tokens if token.text.strip()
                    and (token.confidence is None or token.confidence >= request.min_confidence)]
        goal = getattr(request.goal, "value", request.goal)
        if goal == "numeric":
            return [token for token in filtered if any(ch.isdigit() for ch in token.text) or "%" in token.text]
        if goal == "textual":
            return [token for token in filtered if len(token.text.strip()) >= self.textual_minimum_characters
                    and any(ch.isalpha() for ch in token.text)]
        return filtered

    def score(self, tokens, goal):
        if not tokens:
            return -math.inf, 0.0
        confidences = [token.confidence for token in tokens if token.confidence is not None]
        average = sum(confidences) / len(confidences) if confidences else 0.0
        characters = sum(len("".join(token.text.split())) for token in tokens)
        horizontal = sum(token.bbox.width >= token.bbox.height for token in tokens) / len(tokens)
        score = average + min(self.character_bonus_cap, math.log1p(characters) * self.character_log_weight)
        score += horizontal * self.horizontal_weight
        if getattr(goal, "value", goal) == "numeric":
            score += min(self.numeric_token_bonus_cap, len(tokens) * self.numeric_token_weight)
        return score, average

    def same_position(self, first, second):
        if first.bbox.iou(second.bbox) >= self.position_iou:
            return True
        intersection = first.bbox.intersection(second.bbox)
        return intersection is not None and (
            intersection.area / max(first.bbox.area, 1.0) >= self.position_coverage
            or intersection.area / max(second.bbox.area, 1.0) >= self.position_coverage
        )

    def numeric_score(self, token):
        text = "".join(token.text.split())
        digits = sum(ch.isdigit() for ch in text)
        accepted = sum(ch.isdigit() or ch in ".,%+-/R$" for ch in text)
        return ((token.confidence or 0.0) + accepted / max(len(text), 1) * self.numeric_syntax_weight
                + min(digits, self.numeric_digit_cap) * self.numeric_digit_weight)

    def merge_numeric(self, candidates):
        merged = []
        for _score, _scale, _rotation, tokens in sorted(candidates, key=lambda item: item[0], reverse=True):
            for token in tokens:
                overlaps = [index for index, existing in enumerate(merged) if self.same_position(existing, token)]
                if not overlaps:
                    merged.append(token)
                    continue
                best = max(overlaps, key=lambda index: self.numeric_score(merged[index]))
                if self.numeric_score(token) > self.numeric_score(merged[best]):
                    merged[best] = token
        return sorted(merged, key=lambda token: (token.bbox.y0, token.bbox.x0))

    def select(self, candidates, goal):
        score, scale, rotation, tokens = max(candidates, key=lambda item: item[0])
        if getattr(goal, "value", goal) == "numeric" and len(candidates) > 1:
            tokens = self.merge_numeric(candidates)
        return score, scale, rotation, tokens


LEGACY_CANDIDATE_POLICY = RegionalCandidatePolicy()


@dataclass(frozen=True, slots=True)
class RegionalRecoveryProfile:
    """Scale schedules for core-selected work, without engine-name branching.

    This object contains policy values only. It does not select targets, mutate
    evidence, or execute OCR. The legacy-compatible defaults are deliberately
    explicit in each engine pipeline until that engine's equivalence gates pass.
    """
    weak_region_markers: tuple[str, ...] = ("small", "sparse", "missing", "damaged")
    weak_region_scales: tuple[float, ...] = (1.0, 1.5, 2.0)
    ordinary_region_scales: tuple[float, ...] = (1.0,)
    critical_data_scales: tuple[float, ...] = (1.5, 2.0, 3.0)
    weak_line_area_threshold: float = 0.12
    weak_line_small_scales: tuple[float, ...] = (1.5, 2.0, 3.0)
    weak_line_large_scales: tuple[float, ...] = (1.5, 2.0)
    candidates: RegionalCandidatePolicy = field(default_factory=RegionalCandidatePolicy)

    def scale_factors(self, stage: str, *, quality_policy: str | None = None,
                      reasons: tuple[str, ...] = (), area_ratio: float = 1.0) -> tuple[float, ...]:
        policy = str(quality_policy or "").lower()
        if stage == "critical_data":
            return self.critical_data_scales
        if stage == "weak_line":
            return (self.weak_line_small_scales if area_ratio <= self.weak_line_area_threshold
                    else self.weak_line_large_scales)
        if stage == "region":
            if policy == "baseline":
                return self.ordinary_region_scales
            reason_text = " ".join(reasons).lower()
            if any(marker in reason_text for marker in self.weak_region_markers):
                return self.weak_region_scales
            return self.ordinary_region_scales
        return self.ordinary_region_scales


# Compatibility schedule for caller-injected legacy engines. Production engine
# pipelines own separate profile instances under ``ocr/pipelines/<engine>``.
LEGACY_COMPATIBILITY_RECOVERY_PROFILE = RegionalRecoveryProfile(
    weak_region_markers=("small", "sparse", "missing", "damaged"),
    weak_region_scales=(1.0, 1.5, 2.0),
    ordinary_region_scales=(1.0,),
    critical_data_scales=(1.5, 2.0, 3.0),
    weak_line_area_threshold=0.12,
    weak_line_small_scales=(1.5, 2.0, 3.0),
    weak_line_large_scales=(1.5, 2.0),
)
