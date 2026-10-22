"""Lazy OCR engine composition registry.

Descriptors contain only strings and immutable metadata. Runtime backends,
settings resolvers, pipelines and probes are imported only when requested.
The project registry is explicit; external entry-point discovery is not used.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import Iterator
import warnings


@dataclass(frozen=True, slots=True)
class EngineAlias:
    name: str
    provider: str | None = None
    deprecated: bool = True

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("Engine aliases must be nonempty and trimmed")
        if self.provider is not None and (not self.provider or self.provider.strip() != self.provider):
            raise ValueError("Alias providers must be nonempty and trimmed")


@dataclass(frozen=True, slots=True)
class BackendEntry:
    """Static descriptor for one engine family and its deployment profiles."""

    name: str
    display_name: str
    default_runtime: str
    default_profile: str
    extras: str | None
    setup_hint: str
    quality_thresholds_resolver: str = ""
    rgb_budget_env: str = "PDFEXTRACTOR_OCR_RGB_BUDGET_MIB"
    default_rgb_budget_mib: float = 8.0
    backend_factory: str = ""
    pipeline_factory: str = "structured_pdf_text.ocr.factory:build_legacy_ocr_pipeline"
    settings_resolver: str = ""
    static_probe: str = ""
    aliases: tuple[EngineAlias, ...] = ()
    providers: tuple[str, ...] = ()
    default_provider: str | None = None
    profiles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    default_render_scale: float = 1.0
    legacy_runtime_values: tuple[str, ...] = ()
    legacy_config_fields: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not self.name or self.name.strip() != self.name:
            raise ValueError("Engine names must be nonempty and trimmed")
        if self.default_provider is not None and self.default_provider not in self.providers:
            raise ValueError("The default provider must be listed in providers")
        if self.default_render_scale <= 0:
            raise ValueError("default_render_scale must be positive")
        if self.default_rgb_budget_mib <= 0:
            raise ValueError("default_rgb_budget_mib must be positive")
        if len({alias.name for alias in self.aliases}) != len(self.aliases):
            raise ValueError("Engine aliases must be unique")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("Engine capabilities must be unique")


class EngineRegistry:
    """Ordered registry that can also be isolated in tests."""

    def __init__(self, entries: tuple[BackendEntry, ...] = ()) -> None:
        self._entries: dict[str, BackendEntry] = {}
        self._aliases: dict[str, tuple[BackendEntry, EngineAlias]] = {}
        self._lock = RLock()
        for entry in entries:
            self.register(entry)

    @property
    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._entries)

    @property
    def entries(self) -> tuple[BackendEntry, ...]:
        with self._lock:
            return tuple(self._entries.values())

    def register(self, entry: BackendEntry) -> None:
        if not isinstance(entry, BackendEntry):
            raise TypeError("entry must be a BackendEntry")
        with self._lock:
            if entry.name in self._entries or entry.name in self._aliases:
                raise ValueError(f"OCR engine name is already registered: {entry.name}")
            alias_names = {alias.name for alias in entry.aliases}
            if entry.name in alias_names:
                raise ValueError("An engine cannot alias its own canonical name")
            if alias_names & (set(self._entries) | set(self._aliases)):
                raise ValueError("OCR engine alias is already registered")
            self._entries[entry.name] = entry
            for alias in entry.aliases:
                self._aliases[alias.name] = (entry, alias)

    def unregister(self, name: str) -> BackendEntry:
        """Remove an explicitly registered descriptor; intended for tests."""
        with self._lock:
            entry = self._entries.pop(name)
            for alias in entry.aliases:
                self._aliases.pop(alias.name, None)
            return entry

    def resolve(self, name: str, *, warn_alias: bool = True) -> tuple[BackendEntry, EngineAlias | None]:
        with self._lock:
            if name in self._entries:
                return self._entries[name], None
            if name in self._aliases:
                entry, alias = self._aliases[name]
            else:
                raise KeyError(f"Unknown OCR engine: {name!r}. Available: {', '.join(self._entries)}")
        if warn_alias and alias.deprecated:
            warnings.warn(
                f"{name!r} is deprecated; use ocr_engine={entry.name!r}"
                + (f" and set ocr_provider={alias.provider!r} explicitly" if alias.provider else ""),
                DeprecationWarning,
                stacklevel=2,
            )
        return entry, alias


ENGINE_REGISTRY = EngineRegistry((
    BackendEntry(
        name="easyocr", display_name="EasyOCR + português (CPU)",
        default_runtime="torch-cpu", default_profile="latin-g2-pt", extras="ocr-easyocr",
        setup_hint='pip install "structured-pdf-text[ocr-easyocr]"',
        backend_factory="structured_pdf_text.ocr.backends.easyocr:build_backend",
        pipeline_factory="structured_pdf_text.ocr.pipelines.easyocr.pipeline:build_pipeline",
        settings_resolver="structured_pdf_text.ocr.pipelines.easyocr.settings:resolve_settings",
        quality_thresholds_resolver="structured_pdf_text.ocr.pipelines.easyocr.settings:resolve_quality_thresholds",
        rgb_budget_env="PDFEXTRACTOR_OCR_RGB_BUDGET_MIB_EASYOCR",
        default_rgb_budget_mib=32.0,
        static_probe="structured_pdf_text.ocr.readiness:probe_static_easyocr",
        capabilities=("page", "region", "direct_recognition", "multiple_detectors", "orientation_search"),
        default_render_scale=3.0,
        legacy_config_fields=(("paddle_model_profile", "pt"),),
    ),
    BackendEntry(
        name="paddle", display_name="PaddleOCR PP-OCRv6 medium",
        default_runtime="paddle_static", default_profile="pt", extras="ocr",
        setup_hint="pdftext setup-paddle-models --paddle-model-profile pt",
        backend_factory="structured_pdf_text.ocr.backends.paddle:build_backend",
        pipeline_factory="structured_pdf_text.ocr.pipelines.paddle.pipeline:build_pipeline",
        settings_resolver="structured_pdf_text.ocr.pipelines.paddle.settings:resolve_settings",
        quality_thresholds_resolver="structured_pdf_text.ocr.pipelines.paddle.settings:resolve_quality_thresholds",
        static_probe="structured_pdf_text.ocr.readiness:probe_static_paddle",
        profiles=("pt", "pt-v6-medium", "pt-v6-small"),
        capabilities=("page", "region", "batch", "orientation_search"),
        default_render_scale=2.0,
        legacy_runtime_values=("paddle_static",),
    ),
    BackendEntry(
        name="rapidocr", display_name="RapidOCR", default_runtime="onnxruntime",
        default_profile="pp-ocrv6-medium", extras="ocr-rapidocr-onnx",
        setup_hint='pip install "structured-pdf-text[ocr-rapidocr-onnx]"',
        backend_factory="structured_pdf_text.ocr.backends.rapidocr:build_backend",
        pipeline_factory="structured_pdf_text.ocr.pipelines.rapidocr.pipeline:build_pipeline",
        settings_resolver="structured_pdf_text.ocr.pipelines.rapidocr.settings:resolve_settings",
        static_probe="structured_pdf_text.ocr.readiness:probe_static_rapidocr",
        aliases=(EngineAlias("rapidocr-onnx", "onnxruntime"), EngineAlias("rapidocr-openvino", "openvino")),
        providers=("onnxruntime", "openvino"), default_provider="onnxruntime",
        profiles=("v6-medium", "v6-small"),
        capabilities=("page", "region", "polygons"),
        default_render_scale=3.0, legacy_runtime_values=("onnxruntime", "openvino"),
        legacy_config_fields=(("paddle_model_profile", "pt"),),
    ),
    BackendEntry(
        name="tesseract", display_name="Tesseract 5 + português",
        default_runtime="tesseract-cli", default_profile="por-best", extras=None,
        setup_hint="Install Tesseract 5 system package and download por.traineddata (tessdata_best).",
        backend_factory="structured_pdf_text.ocr.backends.tesseract:build_backend",
        pipeline_factory="structured_pdf_text.ocr.pipelines.tesseract.pipeline:build_pipeline",
        settings_resolver="structured_pdf_text.ocr.pipelines.tesseract.settings:resolve_settings",
        static_probe="structured_pdf_text.ocr.readiness:probe_static_tesseract",
        profiles=("por-best", "eng-best", "financial", "scan"),
        capabilities=("page", "region", "native_confidence", "orientation_search"),
        default_render_scale=4.0,
        legacy_config_fields=(("paddle_model_profile", "pt"),),
    ),
))

# Compatibility names are stable snapshots in the same public order.
REGISTRY = {entry.name: entry for entry in ENGINE_REGISTRY.entries}
LEGACY_ENGINE_ALIASES = {
    alias.name: (entry.name, alias.provider)
    for entry in ENGINE_REGISTRY.entries
    for alias in entry.aliases
}
PUBLIC_ENGINES = ENGINE_REGISTRY.names


def public_engines() -> tuple[str, ...]:
    return ENGINE_REGISTRY.names


def public_providers() -> tuple[str, ...]:
    return tuple(dict.fromkeys(provider for entry in ENGINE_REGISTRY.entries for provider in entry.providers))


def get_entry(engine: str) -> BackendEntry:
    return ENGINE_REGISTRY.resolve(engine)[0]


def resolve_selection(
    engine: str, provider: str | None = None, *, resolve_default: bool = False
) -> tuple[BackendEntry, str | None]:
    entry, alias = ENGINE_REGISTRY.resolve(engine)
    if alias is not None:
        if provider not in (None, alias.provider):
            raise ValueError(f"{engine!r} conflicts with provider={provider!r}")
        provider = alias.provider
    if provider is not None and provider not in entry.providers:
        expected = ", ".join(entry.providers) or "no provider option"
        raise ValueError(f"Engine {entry.name!r} does not support provider {provider!r}; expected {expected}")
    if provider is None and resolve_default:
        provider = entry.default_provider
    return entry, provider


@contextmanager
def registered_for_test(entry: BackendEntry) -> Iterator[EngineRegistry]:
    """Temporarily register a fake engine without mutating production metadata."""
    ENGINE_REGISTRY.register(entry)
    try:
        yield ENGINE_REGISTRY
    finally:
        ENGINE_REGISTRY.unregister(entry.name)
