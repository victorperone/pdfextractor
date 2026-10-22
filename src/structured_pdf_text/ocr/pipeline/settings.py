"""Immutable, engine-local settings resolved lazily from the legacy facade.

This module imports neither OCR frameworks nor model code. Environment values
are read only by the selected resolver and are captured once per extractor.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from structured_pdf_text.ocr.pipeline.contracts import EngineSelection, FrozenMap

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig, OcrQualityPolicy, OcrQualityThresholds


@dataclass(frozen=True, slots=True)
class EngineSettings:
    selection: EngineSelection
    language: str
    render_scale: float
    quality_policy: Any
    quality_thresholds: Any
    rgb_budget_mib: float


@dataclass(frozen=True, slots=True)
class EasyOCRSettings(EngineSettings):
    cache_home: Path | None
    num_threads: int
    max_quality: bool
    tiling: bool
    tile_rows: int
    tile_columns: int
    tile_overlap: float
    environment: FrozenMap


@dataclass(frozen=True, slots=True)
class PaddleSettings(EngineSettings):
    model_profile: str
    cache_home: Path | None
    num_threads: int
    batch_size: int
    environment: FrozenMap


@dataclass(frozen=True, slots=True)
class RapidOCRSettings(EngineSettings):
    model_size: str
    cache_home: Path | None
    num_threads: int
    detector_path: str | None
    recognizer_path: str | None
    dictionary_path: str | None
    classifier_path: str | None
    unclip_ratio: float
    box_threshold: float
    detector_threshold: float
    text_score: float
    environment: FrozenMap


@dataclass(frozen=True, slots=True)
class TesseractSettings(EngineSettings):
    executable: str | None
    tessdata_dir: str | None
    language_code: str
    dpi: int | None
    psm: int | None
    oem: int | None
    environment: FrozenMap


@dataclass(frozen=True, slots=True)
class CentralSettings:
    """Immutable settings owned by extraction policy rather than an engine."""

    mode: Any
    language: str
    page_indices: tuple[int, ...] | None
    enable_ocr: bool | None
    enable_layout: bool
    enable_tables: bool
    merge_cross_page_tables: bool
    preserve_headers_footers: bool
    quality_policy: Any
    security_limits: Any

    @classmethod
    def from_config(cls, config: "ExtractorConfig") -> "CentralSettings":
        return cls(
            config.normalized_mode(), config.language, config.page_indices,
            config.enable_ocr, config.enable_layout, config.enable_tables,
            config.merge_cross_page_tables, config.preserve_headers_footers,
            config.effective_ocr_quality_policy(), config.security_limits,
        )


@dataclass(frozen=True, slots=True)
class ResolvedOCRConfig:
    """Central extraction settings plus exactly one selected engine settings."""

    central: CentralSettings
    settings: EngineSettings

    @property
    def selection(self) -> EngineSelection:
        return self.settings.selection


def _common(config: "ExtractorConfig", engine: str, provider: str | None) -> dict[str, Any]:
    from structured_pdf_text.config import effective_ocr_quality_thresholds
    from structured_pdf_text.ocr.env import env_float
    from structured_pdf_text.ocr.registry import get_entry

    entry = get_entry(engine)
    return {
        "selection": EngineSelection(engine, provider),
        "language": config.language,
        "render_scale": config.effective_ocr_render_scale(),
        "quality_policy": config.effective_ocr_quality_policy(),
        "quality_thresholds": effective_ocr_quality_thresholds(config),
        "rgb_budget_mib": env_float(
            entry.rgb_budget_env,
            entry.default_rgb_budget_mib,
            minimum=0.01,
        ),
    }


def _path(value: str | Path | None) -> Path | None:
    return Path(value).expanduser() if value is not None else None


def resolve_generic_settings(config: "ExtractorConfig", entry=None) -> EngineSettings:
    """Minimal immutable settings for a registered extension/test engine."""
    if entry is None:
        raise ValueError("A registry descriptor is required for generic settings")
    provider = config.ocr_provider or entry.default_provider
    return EngineSettings(
        **_common(config, entry.name, provider),
    )


def resolve_engine_settings(config: "ExtractorConfig", entry) -> EngineSettings:
    """Resolve one descriptor's settings while preserving legacy fields."""
    from structured_pdf_text.ocr.factory import import_object
    from structured_pdf_text.ocr.registry import resolve_selection

    supplied = getattr(config, "ocr_settings", None)
    if supplied is not None:
        if not isinstance(supplied, EngineSettings):
            raise ValueError(f"ocr_settings must be an EngineSettings instance, got {type(supplied).__name__}")
        _, provider = resolve_selection(config.ocr_engine, config.ocr_provider, resolve_default=True)
        if supplied.selection != EngineSelection(entry.name, provider):
            raise ValueError("ocr_settings selection does not match ocr_engine/provider")
        return supplied

    if not entry.settings_resolver:
        raise ValueError(f"Engine {entry.name!r} has no settings resolver")
    resolver = import_object(entry.settings_resolver)
    settings = resolver(config, entry)
    return settings

