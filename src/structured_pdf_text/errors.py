"""Typed extraction failures and resource-error classification."""

from __future__ import annotations

import errno
from typing import Any, Iterator


class ExtractionError(RuntimeError):
    """Base exception for extraction failures."""


class FatalExtractionError(ExtractionError):
    """An extraction failure that makes the document incomplete."""

    code = "fatal_extraction_error"

    def __init__(
        self,
        message: str,
        *,
        page_index: int | None = None,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.page_index = page_index
        self.stage = stage
        self.details = dict(details or {})


class ResourceExhaustedExtractionError(FatalExtractionError):
    """The runtime could not allocate the resources needed for extraction."""

    code = "resource_exhausted"


class RequiredRuntimeUnavailableError(FatalExtractionError):
    """A required local extraction runtime or model is unavailable."""

    code = "required_runtime_unavailable"


class PaddleOcrUnavailable(RequiredRuntimeUnavailableError):
    """Raised when the required local PaddleOCR runtime is unavailable."""

    code = "paddle_ocr_unavailable"


_RESOURCE_MESSAGE_MARKERS = (
    "resourceexhaustederror",
    "fail to alloc memory",
    "failed to alloc memory",
    "failed to allocate memory",
    "cannot allocate memory",
    "out of memory",
    "memory allocation failed",
    "std::bad_alloc",
    "bad allocation",
    "error code is 12",
    "errno 12",
    "cuda out of memory",
)


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield an exception and its explicit/implicit causes without cycles."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None:
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        yield current
        current = current.__cause__ or current.__context__


def is_resource_exhaustion(exc: BaseException) -> bool:
    """Return whether *exc* contains concrete evidence of resource exhaustion."""

    for current in _exception_chain(exc):
        if isinstance(current, MemoryError):
            return True
        if isinstance(current, OSError) and current.errno == errno.ENOMEM:
            return True
        if "resourceexhausted" in type(current).__name__.casefold():
            return True
        message = str(current).casefold()
        if any(marker in message for marker in _RESOURCE_MESSAGE_MARKERS):
            return True
    return False


def raise_if_resource_exhausted(
    exc: BaseException,
    *,
    page_index: int | None = None,
    stage: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise the typed fatal error when *exc* proves resource exhaustion."""

    if not is_resource_exhaustion(exc):
        return
    error_details = dict(details or {})
    error_details.update(
        {
            "cause_type": type(exc).__name__,
            "cause_message": str(exc),
        }
    )
    raise ResourceExhaustedExtractionError(
        "Extraction aborted because the runtime could not allocate the memory "
        "required for the operation.",
        page_index=page_index,
        stage=stage,
        details=error_details,
    ) from exc
