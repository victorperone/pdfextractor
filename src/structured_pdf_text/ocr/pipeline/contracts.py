"""Versioned, deeply immutable OCR work in canonical PDF points.

These types contain neither document objects nor image/source handles. Runtime
and evaluation contracts remain independent and retain their existing versions.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Protocol

from structured_pdf_text.config import OcrQualityPolicy
from structured_pdf_text.geometry import BBox, Point

CONTRACT_VERSION = 1


@dataclass(frozen=True, slots=True)
class FrozenMap(Mapping[str, object]):
    _items: tuple[tuple[str, object], ...] = ()

    def __getitem__(self, key: str) -> object:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __post_init__(self):
        values = tuple((identifier(k), freeze(v)) for k, v in self._items)
        if len(dict(values)) != len(values):
            raise ValueError("Duplicate snapshot key")
        object.__setattr__(self, "_items", values)


def freeze(value):
    """Copy JSON-shaped snapshots; reject arbitrary mutable domain objects."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, Mapping):
        return FrozenMap(tuple((identifier(k), freeze(v)) for k, v in value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    raise ValueError(f"Snapshot must contain finite JSON values, got {type(value).__name__}")


def snapshot(value) -> FrozenMap:
    result = freeze(value)
    if not isinstance(result, FrozenMap):
        raise ValueError("Expected a snapshot mapping")
    return result


def identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty identifier")
    return value


def integer(value: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"Expected integer >= {minimum}")
    return value


def finite(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite coordinate or observation")
    return value


def valid_bbox(box: BBox) -> BBox:
    if not isinstance(box, BBox):
        raise ValueError("Expected a canonical BBox")
    values = tuple(finite(getattr(box, name)) for name in ("x0", "y0", "x1", "y1"))
    result = BBox(*values)
    if result.width <= 0 or result.height <= 0 or not math.isfinite(result.area):
        raise ValueError("Expected a nonempty finite bbox")
    return result


def contains(outer: BBox, inner: BBox) -> bool:
    return outer.x0 <= inner.x0 and outer.y0 <= inner.y0 and inner.x1 <= outer.x1 and inner.y1 <= outer.y1


def strings(values) -> tuple[str, ...]:
    return tuple(identifier(value) for value in values)


class WorkStatus(str, Enum):
    OK = "ok"
    NO_TEXT = "no_text"
    PARTIAL = "partial"
    BUDGET_BLOCKED = "budget_blocked"
    INVALID_INPUT = "invalid_input"
    INVALID_REGION = "invalid_region"
    MODEL_MISSING = "model_missing"
    RUNTIME_ERROR = "runtime_error"
    CANCELLED = "cancelled"


class WorkGoal(str, Enum):
    PAGE = "page"
    REGION = "region"
    FIGURE = "figure"
    EMBEDDED_IMAGE = "embedded_image"
    CELL = "cell"
    TABLE = "table"
    NOTE = "note"
    CRITICAL_DATA = "critical_data"


class ProposalKind(str, Enum):
    COMPLEMENT = "complement_region"
    REPLACE = "replace_tokens"
    CORRECT_CELL = "correct_cell"
    ASSOCIATE_FIGURE = "associate_figure"
    PRIORITIZE_OCR = "prioritize_ocr"


@dataclass(frozen=True, slots=True)
class EngineSelection:
    engine: str
    provider: str | None = None

    def __post_init__(self):
        identifier(self.engine)
        if self.provider is not None:
            identifier(self.provider)


@dataclass(frozen=True, slots=True)
class PipelineDescriptor:
    selection: EngineSelection
    declared_capabilities: tuple[str, ...] = ()
    available_capabilities: tuple[str, ...] | None = None

    def __post_init__(self):
        if not isinstance(self.selection, EngineSelection):
            raise ValueError("Expected immutable engine selection")
        object.__setattr__(self, "declared_capabilities", strings(self.declared_capabilities))
        if self.available_capabilities is not None:
            available = strings(self.available_capabilities)
            if not set(available) <= set(self.declared_capabilities):
                raise ValueError("Available capabilities must be declared")
            object.__setattr__(self, "available_capabilities", available)


@dataclass(frozen=True, slots=True)
class EffectiveIdentity:
    selection: EngineSelection
    runtime: str = "unknown"
    profile: str = "unknown"
    language: str = "unknown"
    device: str = "unknown"
    package_versions: FrozenMap = field(default_factory=FrozenMap)
    artifact_hashes: FrozenMap = field(default_factory=FrozenMap)
    extra: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        if not isinstance(self.selection, EngineSelection):
            raise ValueError("Expected immutable engine selection")
        for name in ("runtime", "profile", "language", "device"):
            identifier(getattr(self, name))
        for name in ("package_versions", "artifact_hashes", "extra"):
            object.__setattr__(self, name, snapshot(getattr(self, name)))
        for value in self.package_versions.values():
            identifier(value)
        for value in self.artifact_hashes.values():
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("Model hashes must be lowercase SHA-256 values")


@dataclass(frozen=True, slots=True)
class TargetRef:
    kind: str
    target_id: str
    revision: int

    def __post_init__(self):
        identifier(self.kind)
        identifier(self.target_id)
        integer(self.revision)


@dataclass(frozen=True, slots=True)
class PageGeometry:
    bbox: BBox
    rotation: int = 0

    def __post_init__(self):
        object.__setattr__(self, "bbox", valid_bbox(self.bbox))
        integer(self.rotation)
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("Page rotation must be 0, 90, 180 or 270")


@dataclass(frozen=True, slots=True)
class WorkScope:
    extraction_id: str
    document_id: str
    page_index: int
    page_geometry: PageGeometry
    target: TargetRef
    evidence_version: str

    def __post_init__(self):
        identifier(self.extraction_id)
        identifier(self.document_id)
        integer(self.page_index)
        identifier(self.evidence_version)
        if not isinstance(self.page_geometry, PageGeometry) or not isinstance(self.target, TargetRef):
            raise ValueError("Expected immutable page geometry and target")


@dataclass(frozen=True, slots=True)
class Authorization:
    authorization_id: str
    scope: WorkScope
    regions: tuple[BBox, ...]

    def __post_init__(self):
        identifier(self.authorization_id)
        if not isinstance(self.scope, WorkScope):
            raise ValueError("Expected immutable work scope")
        regions = tuple(valid_bbox(box) for box in self.regions)
        if not regions or any(not contains(self.scope.page_geometry.bbox, box) for box in regions):
            raise ValueError("Authorization must be nonempty and inside the page")
        object.__setattr__(self, "regions", regions)


@dataclass(frozen=True, slots=True)
class ResourceAllocation:
    max_live_raster_bytes: int = 8 * 1024 * 1024
    max_raster_pixels: int = 100_000_000
    max_work_pixels: int = 400_000_000
    max_attempts: int = 32
    max_rasters: int = 64

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            integer(getattr(self, name), minimum=1)


@dataclass(frozen=True, slots=True)
class OCRWorkRequest:
    request_id: str
    scope: WorkScope
    selection: EngineSelection
    authorizations: tuple[Authorization, ...]
    goal: WorkGoal = WorkGoal.REGION
    selection_stage: str = "region"
    quality_policy: OcrQualityPolicy = OcrQualityPolicy.BASELINE
    render_scale: float = 1.0
    preprocessing_rotation: float = 0.0
    resource_allocation: ResourceAllocation = field(default_factory=ResourceAllocation)
    native_context: FrozenMap = field(default_factory=FrozenMap)
    decision_reasons: tuple[str, ...] = ()
    parent_request_id: str | None = None
    mandatory: bool = True
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self):
        identifier(self.request_id)
        identifier(self.selection_stage)
        if finite(self.render_scale) <= 0:
            raise ValueError("Render scale must be positive")
        finite(self.preprocessing_rotation)
        if type(self.mandatory) is not bool:
            raise ValueError("mandatory must be boolean")
        if self.parent_request_id is not None:
            identifier(self.parent_request_id)
            if self.parent_request_id == self.request_id:
                raise ValueError("A request cannot be its own parent")
        if type(self.contract_version) is not int or self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported OCR pipeline contract version")
        authorizations = tuple(self.authorizations)
        ids = [auth.authorization_id for auth in authorizations]
        if not authorizations or len(set(ids)) != len(ids) or any(auth.scope != self.scope for auth in authorizations):
            raise ValueError("Authorizations must match the request scope and be unique")
        if not isinstance(self.selection, EngineSelection) or not isinstance(self.resource_allocation, ResourceAllocation):
            raise ValueError("Expected immutable engine selection and allocation")
        object.__setattr__(self, "authorizations", authorizations)
        object.__setattr__(self, "goal", WorkGoal(self.goal))
        object.__setattr__(self, "quality_policy", OcrQualityPolicy(self.quality_policy))
        object.__setattr__(self, "native_context", snapshot(self.native_context))
        object.__setattr__(self, "decision_reasons", strings(self.decision_reasons))


@dataclass(frozen=True, slots=True)
class TokenEvidence:
    token_id: str
    authorization_id: str
    attempt_id: str
    text: str
    bbox: BBox
    confidence_native: float | None = None
    confidence_scale: str = "unknown"
    confidence_normalized: float | None = None
    normalization: str | None = None
    polygon: tuple[Point, ...] | None = None
    language: str | None = None
    level: str = "line"
    metadata: FrozenMap = field(default_factory=FrozenMap)

    def __post_init__(self):
        for name in ("token_id", "authorization_id", "attempt_id", "level"):
            identifier(getattr(self, name))
        if not isinstance(self.text, str):
            raise ValueError("Token text must be a string")
        if self.language is not None:
            identifier(self.language)
        object.__setattr__(self, "bbox", valid_bbox(self.bbox))
        if self.confidence_scale not in ("0..1", "0..100", "unknown"):
            raise ValueError("Unknown confidence scale")
        if self.confidence_native is not None:
            finite(self.confidence_native)
            ceiling = {"0..1": 1, "0..100": 100}.get(self.confidence_scale)
            if ceiling is not None and not 0 <= self.confidence_native <= ceiling:
                raise ValueError("Confidence outside declared native scale")
        if self.confidence_normalized is not None:
            finite(self.confidence_normalized)
            if not 0 <= self.confidence_normalized <= 1:
                raise ValueError("Normalized confidence outside 0..1")
            if self.normalization == "domain_0..1":
                if self.confidence_native is not None or self.confidence_scale != "unknown":
                    raise ValueError("Domain conversion does not invent native confidence")
            elif self.normalization in ("identity", "divide_by_100"):
                scale = "0..1" if self.normalization == "identity" else "0..100"
                expected = self.confidence_native if scale == "0..1" else (self.confidence_native / 100 if self.confidence_native is not None else None)
                if self.confidence_scale != scale or expected != self.confidence_normalized:
                    raise ValueError("Confidence conversion does not match its declaration")
            else:
                raise ValueError("Normalized confidence requires a declared conversion")
        elif self.normalization is not None:
            raise ValueError("Conversion without a normalized confidence")
        if self.polygon is not None:
            if any(not isinstance(p, Point) for p in self.polygon):
                raise ValueError("Expected immutable polygon points")
            polygon = tuple(Point(finite(p.x), finite(p.y)) for p in self.polygon)
            if len(polygon) < 3:
                raise ValueError("Polygon must have at least three points")
            object.__setattr__(self, "polygon", polygon)
        object.__setattr__(self, "metadata", snapshot(self.metadata))


@dataclass(frozen=True, slots=True)
class LocalFailure:
    status: WorkStatus
    reason: str
    attempt_id: str | None = None
    mandatory: bool = True

    def __post_init__(self):
        object.__setattr__(self, "status", WorkStatus(self.status))
        if self.status in (WorkStatus.OK, WorkStatus.NO_TEXT):
            raise ValueError("A failure cannot have a successful status")
        identifier(self.reason)
        if self.attempt_id is not None:
            identifier(self.attempt_id)
        if type(self.mandatory) is not bool:
            raise ValueError("mandatory must be boolean")


@dataclass(frozen=True, slots=True)
class IntegrationProposal:
    proposal_id: str
    authorization_id: str
    target: TargetRef
    kind: ProposalKind
    evidence_ids: tuple[str, ...]
    reason: str
    replaces_evidence_ids: tuple[str, ...] = ()
    composition: str = "preserve"

    def __post_init__(self):
        for name in ("proposal_id", "authorization_id", "reason"):
            identifier(getattr(self, name))
        if not isinstance(self.target, TargetRef):
            raise ValueError("Expected immutable proposal target")
        object.__setattr__(self, "kind", ProposalKind(self.kind))
        if self.composition not in ("preserve", "join_critical"):
            raise ValueError("Unsupported evidence composition")
        if self.composition != "preserve" and self.kind is not ProposalKind.REPLACE:
            raise ValueError("Evidence composition requires a replacement proposal")
        for name in ("evidence_ids", "replaces_evidence_ids"):
            values = strings(getattr(self, name))
            if len(set(values)) != len(values):
                raise ValueError("Duplicate proposal evidence")
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class WorkSuggestion:
    target: TargetRef
    bbox: BBox
    goal: WorkGoal
    reason: str

    def __post_init__(self):
        object.__setattr__(self, "bbox", valid_bbox(self.bbox))
        object.__setattr__(self, "goal", WorkGoal(self.goal))
        identifier(self.reason)
        if not isinstance(self.target, TargetRef):
            raise ValueError("Expected immutable suggestion target")


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    candidate_id: str
    attempt_id: str
    token_ids: tuple[str, ...]
    selected: bool
    reason: str

    def __post_init__(self):
        identifier(self.candidate_id)
        identifier(self.attempt_id)
        identifier(self.reason)
        if type(self.selected) is not bool:
            raise ValueError("selected must be boolean")
        object.__setattr__(self, "token_ids", strings(self.token_ids))


@dataclass(frozen=True, slots=True)
class OCRPipelineResult:
    request_id: str
    scope: WorkScope
    selection: EngineSelection
    identity: EffectiveIdentity
    status: WorkStatus
    authorization_ids: tuple[str, ...]
    tokens: tuple[TokenEvidence, ...] = ()
    proposals: tuple[IntegrationProposal, ...] = ()
    candidates: tuple[CandidateEvidence, ...] = ()
    failures: tuple[LocalFailure, ...] = ()
    additional_work_suggested: tuple[WorkSuggestion, ...] = ()
    counters: FrozenMap = field(default_factory=FrozenMap)
    timings_ms: FrozenMap = field(default_factory=FrozenMap)
    engine_diagnostics: FrozenMap = field(default_factory=FrozenMap)
    contract_version: int = CONTRACT_VERSION

    def __post_init__(self):
        identifier(self.request_id)
        if type(self.contract_version) is not int or self.contract_version != CONTRACT_VERSION:
            raise ValueError("Unsupported OCR pipeline result version")
        if not isinstance(self.scope, WorkScope) or not isinstance(self.identity, EffectiveIdentity) or self.identity.selection != self.selection:
            raise ValueError("Result identity and selection must match")
        object.__setattr__(self, "status", WorkStatus(self.status))
        object.__setattr__(self, "authorization_ids", strings(self.authorization_ids))
        for name, cls in (("tokens", TokenEvidence), ("proposals", IntegrationProposal), ("candidates", CandidateEvidence), ("failures", LocalFailure), ("additional_work_suggested", WorkSuggestion)):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, cls) for value in values):
                raise ValueError(f"Invalid immutable {name}")
            object.__setattr__(self, name, values)
        for name in ("counters", "timings_ms", "engine_diagnostics"):
            object.__setattr__(self, name, snapshot(getattr(self, name)))
        for value in self.counters.values():
            integer(value)
        for value in self.timings_ms.values():
            if finite(value) < 0:
                raise ValueError("Observed durations must be nonnegative")
        if self.status == WorkStatus.NO_TEXT and self.tokens:
            raise ValueError("no_text cannot carry tokens")
        if self.failures and self.status in (WorkStatus.OK, WorkStatus.NO_TEXT):
            raise ValueError("Local failures must remain visible in the work status")

    @property
    def unselected_token_ids(self) -> frozenset[str]:
        selected = {i for c in self.candidates if c.selected for i in c.token_ids}
        return frozenset(i for c in self.candidates if not c.selected for i in c.token_ids) - selected

    def validate_for(self, request: OCRWorkRequest) -> None:
        if self.request_id != request.request_id or self.scope != request.scope or self.selection != request.selection:
            raise ValueError("Result does not belong to this request/scope/engine/provider")
        auth_ids = {a.authorization_id for a in request.authorizations}
        if set(self.authorization_ids) != auth_ids or len(self.authorization_ids) != len(auth_ids):
            raise ValueError("Result authorization set does not match request")
        tokens = {t.token_id: t for t in self.tokens}
        if len(tokens) != len(self.tokens) or any(t.authorization_id not in auth_ids for t in self.tokens):
            raise ValueError("Duplicate token or unknown token authorization")
        if len({p.proposal_id for p in self.proposals}) != len(self.proposals):
            raise ValueError("Repeated proposal ID")
        for proposal in self.proposals:
            if proposal.composition == "join_critical" and request.goal is not WorkGoal.CRITICAL_DATA:
                raise ValueError("Critical composition requires a core-authorized critical-data request")
            if proposal.authorization_id not in auth_ids or proposal.target != request.scope.target:
                raise ValueError("Proposal authorization/target revision mismatch")
            if any(i not in tokens or tokens[i].authorization_id != proposal.authorization_id for i in proposal.evidence_ids):
                raise ValueError("Proposal refers to unknown or differently authorized evidence")
        for candidate in self.candidates:
            if any(i not in tokens or tokens[i].attempt_id != candidate.attempt_id for i in candidate.token_ids):
                raise ValueError("Candidate lineage mismatch")
        if any(i in self.unselected_token_ids for p in self.proposals for i in p.evidence_ids):
            raise ValueError("Integration proposal refers to unselected candidate evidence")
        for suggestion in self.additional_work_suggested:
            if not contains(request.scope.page_geometry.bbox, suggestion.bbox):
                raise ValueError("Suggested work lies outside the page")


class OCRServices(Protocol):
    def render(self, request: OCRWorkRequest, authorization_id: str, bbox: BBox, *, scale: float, boundary_policy: str = "mask"): ...
    def consume_attempt(self, request: OCRWorkRequest) -> str: ...
    def check_cancelled(self) -> None: ...
    def validate_token(self, request: OCRWorkRequest, token: TokenEvidence) -> None: ...
    def validate_result(self, request: OCRWorkRequest, result: OCRPipelineResult) -> None: ...
    def resize(self, lease, width: int, height: int): ...
    def tile(self, lease, bbox_px: BBox): ...
    def rotate(self, lease, degrees: float, *, padding: int = 0): ...
    def embedded_image(self, request: OCRWorkRequest, authorization_id: str, bbox: BBox, *, object_index: int, scale: float = 1.0): ...
    def emit(self, request: OCRWorkRequest, kind: str, details: Mapping | None = None) -> None: ...


class OCRPipeline(Protocol):
    @property
    def descriptor(self) -> PipelineDescriptor: ...
    def execute(self, request: OCRWorkRequest, services: OCRServices) -> OCRPipelineResult: ...
    def close(self) -> None: ...
