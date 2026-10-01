"""Central Paddle runtime policy shared by direct and subprocess execution."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PaddleRuntimePolicy:
    enable_mkldnn: bool
    disable_pir_api: bool
    reason: str


def resolve_paddle_runtime_policy(
    *, env: dict[str, str] | None = None, system: str | None = None
) -> PaddleRuntimePolicy:
    """Resolve a conservative CPU default while preserving explicit opt in."""
    environment = os.environ if env is None else env
    operating_system = platform.system() if system is None else system
    override = environment.get("PADDLE_ENABLE_MKLDNN")
    enabled = override == "1" if override is not None else False
    reason = (
        f"PADDLE_ENABLE_MKLDNN={override} override"
        if override is not None
        else "safe CPU default; oneDNN is opt-in"
    )
    return PaddleRuntimePolicy(
        enable_mkldnn=enabled,
        disable_pir_api=operating_system == "Windows",
        reason=reason,
    )


def apply_paddle_runtime_policy(policy: PaddleRuntimePolicy) -> None:
    """Set Paddle environment flags before importing any Paddle package."""
    if policy.disable_pir_api:
        os.environ.setdefault("FLAGS_enable_pir_api", "0")
