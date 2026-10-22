"""Opt-in compatibility for an injected OcrEngine, without activating migration."""
from dataclasses import replace
import inspect
from contextlib import ExitStack
from threading import RLock

from structured_pdf_text.document import OcrProvenance, OcrToken
from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
from .budgets import BudgetBlocked, WorkCancelled
from .contracts import (
    CandidateEvidence, EffectiveIdentity, EngineSelection,
    LocalFailure, OCRPipelineResult, PipelineDescriptor, WorkStatus,
)
from .converters import from_domain
from .diagnostics import LegacyDiagnosticsProjector
from .policies import LEGACY_CELL_POLICY, LEGACY_COMPATIBILITY_RECOVERY_PROFILE, LEGACY_INTEGRATION_POLICY, LEGACY_RASTER_BOUNDARY_POLICY


class AuthorizedBackendPipeline:
    """Own an engine-local backend and execute it through authorized services.

Every call receives an owned authorized crop in canonical orientation. Legacy
signature compatibility is decided once; internal TypeError never causes retry.
No other engine or readiness probe is loaded by the execution adapter.
"""
    integration_policy = LEGACY_INTEGRATION_POLICY
    cell_policy = LEGACY_CELL_POLICY
    raster_boundary_policy = LEGACY_RASTER_BOUNDARY_POLICY

    def __init__(self, engine, *, selection: EngineSelection | None = None, render_scale: float = 1.0,
                 capabilities: tuple[str, ...] = ("page", "region"), settings=None,
                 adapter_label: str = "authorized_backend"):
        from .contracts import finite
        if finite(render_scale) <= 0:
            raise ValueError("Render scale must be positive")
        self._engine = engine
        self._selection = selection or EngineSelection("custom")
        self._scale = render_scale
        self._capabilities = tuple(capabilities)
        self.settings = settings
        self._adapter_label = adapter_label
        self._closed = False
        self._lock = RLock()
        self._methods = {}
        for name in ("recognize_page", "recognize_region", "recognize_direct"):
            method = getattr(engine, name, None)
            if callable(method):
                try:
                    parameters = inspect.signature(method).parameters
                except (TypeError, ValueError):
                    parameters = {}
                self._methods[name.removeprefix("recognize_")] = (method, parameters)

    @property
    def descriptor(self):
        available = None
        if self._adapter_label == "authorized_backend":
            capability_source = getattr(self._engine, "capabilities", None)
            capability_source = capability_source() if callable(capability_source) else capability_source
            if capability_source is not None:
                names = {
                    "page": ("recognition",),
                    "region": ("recognition",),
                    "polygons": ("polygons", "quadrilateral_boxes"),
                    "direct_recognition": ("direct_recognition",),
                    "orientation_search": ("orientation_search",),
                    "multiple_detectors": ("multiple_detectors",),
                    "detector_profiles": ("detector_profiles",),
                    "decoder_profiles": ("decoder_profiles",),
                    "native_confidence": ("native_confidence",),
                    "batch": ("batch",),
                }
                resolved = []
                for name in self._capabilities:
                    attributes = names.get(name)
                    if attributes is None:
                        enabled = True
                    elif isinstance(capability_source, dict):
                        enabled = any(bool(capability_source.get(attr, False)) for attr in attributes)
                    else:
                        enabled = any(bool(getattr(capability_source, attr, False)) for attr in attributes)
                    if enabled:
                        resolved.append(name)
                available = tuple(resolved)
        return PipelineDescriptor(self._selection, self._capabilities, available)

    @property
    def raw_engine(self):
        """Explicit escape hatch for raw OCR/benchmark consumers."""
        return self._engine

    @property
    def identity(self):
        return self._identity()

    @property
    def render_scale(self):
        return self._scale

    def reset_page_diagnostics(self):
        LegacyDiagnosticsProjector.reset(self._engine)

    def consume_page_diagnostics(self):
        return LegacyDiagnosticsProjector.consume(
            self._engine, engine_name=self._selection.engine, provider=self._selection.provider,
        )

    def operation_metrics(self):
        return LegacyDiagnosticsProjector.operation_metrics(self._engine)

    def reset_cancellation(self):
        reset = getattr(self._engine, "reset_cancellation", None)
        if callable(reset):
            reset()

    def _supports(self, name):
        method = self._methods.get("page")
        if method is None:
            return False
        parameters = method[1]
        return name in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values())

    def _invoke(self, request, image, page_index, bbox):
        stage = request.selection_stage
        selected = self._methods.get(stage) or self._methods.get("page")
        # Builtin pipelines receive a crop that OCRServices has already
        # rendered to the exact authorized region. Calling the legacy
        # recognize_region method would crop that crop again using page-point
        # coordinates (and would reintroduce an out-of-scope coordinate path).
        # Run the engine's page recognizer on the authorized crop instead; its
        # raster outputs are mapped once by the lease's RasterFrame below.
        if self._adapter_label == "authorized_backend" and stage == "region":
            selected = self._methods.get("page")
        if selected is None:
            raise TypeError("Legacy OCR engine has no recognize_page method")
        method, parameters = selected
        var_keywords = any(p.kind == p.VAR_KEYWORD for p in parameters.values())
        kwargs = {}
        if "quality_policy" in parameters or var_keywords:
            kwargs["quality_policy"] = request.quality_policy.value
        if "quality_variants" in parameters:
            kwargs["quality_variants"] = request.quality_policy.value != "baseline"
        if "page_rotation" in parameters:
            kwargs["page_rotation"] = 0
        args = [image, page_index]
        if stage == "region" and selected is self._methods.get("region"):
            if "region_bbox" in parameters and parameters["region_bbox"].kind == inspect.Parameter.KEYWORD_ONLY:
                kwargs["region_bbox"] = bbox
            else:
                args.append(bbox)
            if "page_bbox" in parameters or var_keywords:
                kwargs["page_bbox"] = bbox
        elif stage == "direct" and selected is self._methods.get("direct"):
            args.append(bbox)
        else:
            positional = [p for p in parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
            variadic = any(p.kind == p.VAR_POSITIONAL for p in parameters.values())
            if len(positional) >= 3 or variadic:
                args.append(bbox)
            elif "page_bbox" in parameters or var_keywords:
                kwargs["page_bbox"] = bbox
        return method(*args, **kwargs)

    def _identity(self):
        legacy = getattr(self._engine, "identity", None)
        if legacy is None:
            return EffectiveIdentity(self._selection, extra={"adapter": self._adapter_label, "native_identity": "unknown"})
        if self._selection.engine != "custom" and legacy.engine != self._selection.engine:
            raise ValueError("Injected engine identity does not match the declared selection")
        if self._selection.provider is not None and legacy.runtime != self._selection.provider:
            raise ValueError("Injected engine provider does not match the declared selection")
        return EffectiveIdentity(self._selection, legacy.runtime, legacy.profile, legacy.language, legacy.device,
                                 legacy.package_versions, legacy.artifact_hashes,
                                 dict(legacy.extra, adapter=self._adapter_label, legacy_engine=legacy.engine))

    def _attach_provenance(self, token: OcrToken) -> OcrToken:
        if token.ocr_provenance is not None:
            return token
        extra = dict(self._identity().extra)
        candidate_id = None
        if token.provenance and token.provenance.startswith("candidate:"):
            candidate_id = token.provenance.split(":", 1)[1]
        # Component identity is copied only when explicitly reported by the
        # backend; a candidate label is never used to infer model identity.
        return replace(token, ocr_provenance=OcrProvenance(
            engine=self._selection.engine,
            candidate_id=candidate_id or "baseline",
            detector=extra.get("detector"),
            recognizer=extra.get("recognizer") or extra.get("recognition_network"),
            decoder=extra.get("decoder"),
            preprocessing=(candidate_id,) if candidate_id and candidate_id not in {"default", "full-page"} else (),
            rotation=float(token.rotation),
            tile_id=candidate_id.removeprefix("tile:") if candidate_id and candidate_id.startswith("tile:") else None,
            refinement_kind=(token.provenance if token.provenance and token.provenance not in {"baseline", f"candidate:{candidate_id}"} else None),
        ))

    def execute(self, request, services):
        with self._lock:
            if self._closed:
                raise RuntimeError("Legacy OCR pipeline is closed")
            if request.selection != self._selection:
                raise ValueError("Injected pipeline selection does not match request")
            tokens, failures, candidates, proposals = [], [], [], []
            calls = 0
            attempt_diagnostics = []
            for auth in request.authorizations:
                for box in auth.regions:
                    attempt_id = None
                    try:
                        services.check_cancelled()
                        with ExitStack() as stack:
                            lease = stack.enter_context(services.render(
                                request, auth.authorization_id, box, scale=request.render_scale,
                                boundary_policy=self.raster_boundary_policy.mode,
                            ))
                            if request.preprocessing_rotation % 360:
                                lease = stack.enter_context(services.rotate(lease, request.preprocessing_rotation))
                            attempt_id = services.consume_attempt(request)
                            services.check_cancelled()
                            calls += 1
                            # Engine-local adapters return raster coordinates. The
                            # composed RasterFrame is the single authority that maps
                            # scale/rotation variants back into canonical PDF points.
                            invoke_bbox = None if self._adapter_label == "authorized_backend" else lease.frame.bbox
                            try:
                                observed = self._invoke(request, lease.image, request.scope.page_index, invoke_bbox)
                            finally:
                                attempt_diagnostics.append({
                                    "attempt_id": attempt_id,
                                    "metrics": LegacyDiagnosticsProjector.operation_metrics(self._engine),
                                    "diagnostics": LegacyDiagnosticsProjector.snapshot(
                                        self._engine, engine_name=self._selection.engine, provider=self._selection.provider,
                                    ),
                                })
                            services.check_cancelled()
                            attempt_tokens = []
                            for number, token in enumerate(observed):
                                try:
                                    if not isinstance(token, OcrToken):
                                        raise ValueError("Legacy engine returned an unexpected token type")
                                    token = self.integration_policy.prepare_token(token, request)
                                    token = self._attach_provenance(token)
                                    selected = self._methods.get(request.selection_stage) or self._methods.get("page")
                                    selected_parameters = selected[1] if selected else {}
                                    has_bbox_argument = (
                                        len([p for p in selected_parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 3
                                        or "page_bbox" in selected_parameters
                                        or "region_bbox" in selected_parameters
                                        or any(p.kind == p.VAR_POSITIONAL for p in selected_parameters.values())
                                        or any(p.kind == p.VAR_KEYWORD for p in selected_parameters.values())
                                    )
                                    if self._adapter_label == "authorized_backend":
                                        token = replace(
                                            token,
                                            bbox=lease.frame.bbox_to_page(token.bbox),
                                            polygon=tuple(lease.frame.point_to_page(p) for p in token.polygon)
                                            if token.polygon else None,
                                        )
                                    elif not has_bbox_argument:
                                        token = replace(token, bbox=lease.frame.bbox_to_page(token.bbox),
                                                        polygon=tuple(lease.frame.point_to_page(p) for p in token.polygon) if token.polygon else None)
                                    evidence = from_domain(token, token_id=f"{attempt_id}:token:{number}",
                                                           authorization_id=auth.authorization_id, attempt_id=attempt_id)
                                    services.validate_token(request, evidence)
                                    tokens.append(evidence)
                                    attempt_tokens.append(evidence.token_id)
                                except ValueError as exc:
                                    failures.append(LocalFailure(WorkStatus.INVALID_REGION, str(exc), attempt_id, request.mandatory))
                            candidates.append(CandidateEvidence(attempt_id, attempt_id, tuple(attempt_tokens), True, "legacy_result"))
                    except (FatalExtractionError, WorkCancelled):
                        raise
                    except BudgetBlocked as exc:
                        failures.append(LocalFailure(WorkStatus.BUDGET_BLOCKED, str(exc), attempt_id, request.mandatory))
                    except ValueError as exc:
                        failures.append(LocalFailure(WorkStatus.INVALID_REGION, str(exc), attempt_id, request.mandatory))
                    except Exception as exc:
                        services.check_cancelled()
                        raise_if_resource_exhausted(exc, page_index=request.scope.page_index, stage="ocr_pipeline_legacy")
                        failures.append(LocalFailure(WorkStatus.RUNTIME_ERROR, f"{type(exc).__name__}: {exc}", attempt_id, request.mandatory))
                ids = tuple(t.token_id for t in tokens if t.authorization_id == auth.authorization_id)
                if ids:
                    proposals.append(self.integration_policy.propose(request, auth.authorization_id, ids))
            status = WorkStatus.PARTIAL if tokens and failures else failures[0].status if failures else WorkStatus.OK if tokens else WorkStatus.NO_TEXT
            diagnostics = attempt_diagnostics[-1]["diagnostics"] if attempt_diagnostics else {}
            result = OCRPipelineResult(request.request_id, request.scope, self._selection,
                                       self._identity() if calls else EffectiveIdentity(self._selection), status,
                                       tuple(a.authorization_id for a in request.authorizations), tuple(tokens), tuple(proposals),
                                       tuple(candidates), tuple(failures), counters={
                                           "legacy_calls": calls,
                                           "passes": sum(item["metrics"]["passes"] for item in attempt_diagnostics),
                                           "batches": sum(item["metrics"]["batches"] for item in attempt_diagnostics),
                                       },
                                       engine_diagnostics={self._selection.engine: {
                                           "adapter": self._adapter_label, **diagnostics,
                                           "attempts": attempt_diagnostics,
                                       }})
            services.validate_result(request, result)
            return result

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                closer = getattr(self._engine, "close", None)
                if callable(closer):
                    closer()

    def cancel(self):
        cancel = getattr(self._engine, "cancel", None)
        if callable(cancel):
            cancel()


class LegacyInjectedPipeline(AuthorizedBackendPipeline):
    """Compatibility adapter for one caller-supplied engine instance."""

    recovery_policy = LEGACY_COMPATIBILITY_RECOVERY_PROFILE

    def __init__(self, engine, *, selection: EngineSelection | None = None, render_scale: float = 1.0,
                 capabilities: tuple[str, ...] = ("page", "region"), settings=None):
        super().__init__(engine, selection=selection, render_scale=render_scale,
                         capabilities=capabilities, settings=settings,
                         adapter_label="legacy_injected")


def adapt_injection(*, pipeline=None, ocr_engine=None, selection: EngineSelection | None = None, config=None):
    """Composition helper for PR-04; existing PdfTextExtractor is unchanged."""
    if pipeline is not None and ocr_engine is not None:
        raise ValueError("Pass either pipeline or ocr_engine, never both")
    if pipeline is not None:
        return pipeline
    if ocr_engine is None:
        return None
    if selection is None:
        identity = getattr(ocr_engine, "identity", None)
        engine = getattr(identity, "engine", "custom") if identity is not None else "custom"
        provider = getattr(identity, "runtime", None) if engine == "rapidocr" else None
        selection = EngineSelection(engine, provider)
    settings = None
    render_scale = 1.0
    if config is not None:
        from structured_pdf_text.ocr.factory import resolve_config
        from structured_pdf_text.ocr.registry import ENGINE_REGISTRY

        try:
            ENGINE_REGISTRY.resolve(selection.engine, warn_alias=False)
        except KeyError:
            pass
        else:
            resolved = resolve_config(replace(
                config, ocr_engine=selection.engine, ocr_provider=selection.provider,
            ))
            settings = resolved.settings
            render_scale = settings.render_scale
    return LegacyInjectedPipeline(
        ocr_engine, selection=selection, settings=settings, render_scale=render_scale,
    )
