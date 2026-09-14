from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenConflict:
    chosen: str
    alternatives: list[str]
    reason: str
