"""Core-owned grants and validation; IDs alone confer no pixel access."""
from threading import RLock
from uuid import uuid4

from .contracts import Authorization, BBox, OCRPipelineResult, OCRWorkRequest, WorkScope, contains, identifier, valid_bbox


# Coordinates derived through different float paths (rectification, scale
# round trips) differ by ~1e-13 pt; that is rounding, not new area.
_ROUNDING_TOLERANCE = 1e-6


def union_contains(regions: tuple[BBox, ...], box: BBox) -> bool:
    """Exact rectangle union containment using a sweep; never envelope/center.

    Only floating-point rounding (``_ROUNDING_TOLERANCE``) is forgiven.
    """
    valid_bbox(box)
    eps = _ROUNDING_TOLERANCE
    if box.width > 2 * eps and box.height > 2 * eps:
        box = BBox(box.x0 + eps, box.y0 + eps, box.x1 - eps, box.y1 - eps)
    xs = sorted({box.x0, box.x1} | {x for r in regions for x in (r.x0, r.x1) if box.x0 < x < box.x1})
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((max(box.y0, r.y0), min(box.y1, r.y1)) for r in regions if r.x0 <= left and right <= r.x1 and r.y0 < box.y1 and box.y0 < r.y1)
        covered = box.y0
        for start, end in intervals:
            if start > covered:
                return False
            covered = max(covered, end)
        if covered < box.y1:
            return False
    return True


class AuthorizationRegistry:
    """Retained by the core, never handed to the pipeline through services."""
    def __init__(self, extraction_id: str, document_id: str):
        self._extraction_id = identifier(extraction_id)
        self._document_id = identifier(document_id)
        self._records: dict[str, Authorization] = {}
        self._current: dict[tuple, WorkScope] = {}
        self._lock = RLock()

    def _key(self, scope):
        if scope.extraction_id != self._extraction_id or scope.document_id != self._document_id:
            raise ValueError("Scope belongs to another extraction/document")
        return scope.page_index, scope.target.kind, scope.target.target_id

    def advance_scope(self, scope: WorkScope) -> None:
        with self._lock:
            key = self._key(scope)
            previous = self._current.get(key)
            if previous is not None and scope.target.revision < previous.target.revision:
                raise ValueError("Cannot restore a stale target revision")
            self._current[key] = scope

    def issue(self, scope: WorkScope, regions: tuple[BBox, ...]) -> Authorization:
        with self._lock:
            key = self._key(scope)
            if key in self._current and self._current[key] != scope:
                raise ValueError("Advance the target evidence explicitly before authorizing")
            result = Authorization(uuid4().hex, scope, regions)
            self._current[key] = scope
            self._records[result.authorization_id] = result
            return result

    def validate(self, request: OCRWorkRequest, authorization_id: str, box: BBox | None = None) -> Authorization:
        with self._lock:
            key = self._key(request.scope)
            if self._current.get(key) != request.scope:
                raise ValueError("Stale page geometry, target revision or evidence version")
            auth = self._records.get(authorization_id)
            supplied = next((a for a in request.authorizations if a.authorization_id == authorization_id), None)
            if auth is None or supplied != auth or auth.scope != request.scope:
                raise ValueError("Unknown, forged or mismatched authorization")
            if box is not None and not union_contains(auth.regions, valid_bbox(box)):
                raise ValueError(
                    "Requested geometry is outside the exact authorization union: "
                    f"requested={box!r}, authorized={auth.regions!r}"
                )
            return auth

    def validate_result(self, request: OCRWorkRequest, result: OCRPipelineResult) -> None:
        result.validate_for(request)
        for auth in request.authorizations:
            self.validate(request, auth.authorization_id)
        for token in result.tokens:
            self.validate_token(request, token)

    def validate_token(self, request, token):
        self.validate(request, token.authorization_id, token.bbox)
        if token.polygon and any(not (token.bbox.x0 <= p.x <= token.bbox.x1 and token.bbox.y0 <= p.y <= token.bbox.y1) for p in token.polygon):
            raise ValueError("Token polygon extends outside its validated bbox")


class ResultReceiptLedger:
    """Idempotent receipt for future integrators; performs no document mutation.

Call only after full service validation. Applying proposals to domain objects
remains the responsibility of the later central integrator (PR-06).
"""
    def __init__(self):
        self._results = {}
        self._lock = RLock()

    def record(self, request: OCRWorkRequest, result: OCRPipelineResult) -> bool:
        result.validate_for(request)
        key = request.scope.extraction_id, request.request_id
        with self._lock:
            if key in self._results:
                if self._results[key] != result:
                    raise ValueError("A request ID cannot publish a different result twice")
                return False
            self._results[key] = result
            return True
