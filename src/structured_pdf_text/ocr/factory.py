"""Lazy composition for OCR backends and pipelines."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from importlib import import_module
from typing import TYPE_CHECKING, Any

from structured_pdf_text.ocr.contracts import OCRBackend, UnsupportedOCREngine
from structured_pdf_text.ocr.registry import ENGINE_REGISTRY, PUBLIC_ENGINES, resolve_selection

if TYPE_CHECKING:
    from structured_pdf_text.config import ExtractorConfig
    from structured_pdf_text.ocr.pipeline.contracts import OCRPipeline
    from structured_pdf_text.ocr.pipeline.settings import CentralSettings, EngineSettings, ResolvedOCRConfig


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """An extraction facade plus settings for exactly one selected engine."""

    config: "ExtractorConfig"
    engine: str
    provider: str | None
    settings: "EngineSettings"
    central: "CentralSettings"

    @property
    def resolution(self) -> "ResolvedOCRConfig":
        from structured_pdf_text.ocr.pipeline.settings import ResolvedOCRConfig
        return ResolvedOCRConfig(self.central, self.settings)


def import_object(path: str) -> Any:
    """Resolve ``package.module:object`` only at the point of use."""
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"Invalid lazy import path: {path!r}")
    module = import_module(module_name)
    value = module
    for part in attribute.split("."):
        value = getattr(value, part)
    return value


def resolve_config(config: "ExtractorConfig") -> ResolvedConfig:
    """Resolve selection and immutable settings without constructing a runtime."""
    from structured_pdf_text.ocr.pipeline.settings import CentralSettings, resolve_engine_settings

    try:
        entry, provider = resolve_selection(config.ocr_engine, config.ocr_provider, resolve_default=True)
    except (KeyError, ValueError) as exc:
        raise UnsupportedOCREngine(str(exc)) from exc
    settings = resolve_engine_settings(config, entry)
    if settings.selection.engine != entry.name or settings.selection.provider != provider:
        raise UnsupportedOCREngine("Resolved engine settings do not match the selected descriptor")
    return ResolvedConfig(config, entry.name, provider, settings, CentralSettings.from_config(config))


def build_ocr_backend(config: "ExtractorConfig") -> OCRBackend:
    """Build the legacy/raw backend selected by config using lazy metadata."""
    resolved = resolve_config(config)
    entry = ENGINE_REGISTRY.resolve(resolved.engine, warn_alias=False)[0]
    if not entry.backend_factory:
        raise UnsupportedOCREngine(f"OCR engine {entry.name!r} has no legacy backend factory")
    factory = import_object(entry.backend_factory)
    backend = factory(replace(
        config,
        ocr_engine=resolved.engine,
        ocr_provider=resolved.provider,
        ocr_settings=resolved.settings,
        use_gpu=resolved.settings.use_gpu,
    ))
    return backend


def build_ocr_pipeline(resolved_config: ResolvedConfig | "ExtractorConfig") -> "OCRPipeline":
    """Build the selected engine pipeline without loading it until first work."""
    resolved = resolve_config(resolved_config) if not isinstance(resolved_config, ResolvedConfig) else resolved_config
    entry = ENGINE_REGISTRY.resolve(resolved.engine, warn_alias=False)[0]
    pipeline_factory = import_object(entry.pipeline_factory)
    return pipeline_factory(resolved)


def build_legacy_ocr_pipeline(resolved: ResolvedConfig):
    """Compatibility pipeline used until each engine's policy is migrated."""
    from structured_pdf_text.ocr.pipeline.adapters import LegacyInjectedPipeline
    from structured_pdf_text.ocr.pipeline.contracts import EngineSelection

    backend = build_ocr_backend(replace(resolved.config, ocr_settings=resolved.settings))
    return LegacyInjectedPipeline(
        backend,
        selection=EngineSelection(resolved.engine, resolved.provider),
        render_scale=resolved.settings.render_scale,
        capabilities=ENGINE_REGISTRY.resolve(resolved.engine, warn_alias=False)[0].capabilities,
        settings=resolved.settings,
    )
