"""Shared, contextual parsing for OCR environment variables."""
from __future__ import annotations

import math
import os

from structured_pdf_text.errors import ConfigurationError


def env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = os.environ.get(name)
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError, OverflowError):
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be a number") from None
    if not math.isfinite(value):
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be finite")
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be <= {maximum}")
    return value


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    allowed: set[int] | None = None,
) -> int:
    raw = os.environ.get(name)
    try:
        value = int(default if raw is None else raw)
    except (TypeError, ValueError, OverflowError):
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be an integer") from None
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be <= {maximum}")
    if allowed is not None and value not in allowed:
        raise ConfigurationError(f"Environment variable {name}={raw!r} must be one of {sorted(allowed)}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Environment variable {name}={raw!r} must be a boolean")
