"""Authorized raster service over the unchanged native region renderer.

PDFium's integer crop rounding is modelled explicitly. Border pixels whose
footprint crosses the requested boundary are whitened, rather than granting
extra reading area. Original embedded bitmaps are never exposed: without a
visibility proof the service uses the visible PDF composition.
"""
from __future__ import annotations

import inspect
import math
from threading import Event, RLock

from structured_pdf_text.config import SecurityLimits
from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted
from structured_pdf_text.geometry import BBox
from structured_pdf_text.ocr.image_views import canonicalize_page_image
from .authorization import AuthorizationRegistry
from .budgets import BudgetBlocked, ResourcePool, WorkCancelled
from .contracts import EngineSelection, OCRWorkRequest, ResourceAllocation, finite, integer, snapshot, valid_bbox
from .geometry import RasterFrame, affine


class RasterLease:
    """Owned pixels and geometry only; no source, parent image or PDF handles."""
    def __init__(self, image, frame, release, lock):
        self._image = image
        self.frame = frame
        self._release = release
        self._closed = False
        self._lock = lock

    @property
    def image(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Raster lease is closed")
            return self._image

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._image.close()
                finally:
                    self._release(self)

    def __enter__(self):
        self.image
        return self

    def __exit__(self, *_):
        self.close()


class AuthorizedOCRServices:
    """One selected deployment, with a core-owned source and grant registry.

The private source boundary adapts the legacy render signature without editing
the native protocol. Source coordinates are page-local; a nonzero canonical
origin is translated here. Operations are serialized per service instance.
"""
    def __init__(self, source, registry: AuthorizationRegistry, selection: EngineSelection, *,
                 limits: SecurityLimits | None = None, allocation: ResourceAllocation | None = None,
                 parent_pool: ResourcePool | None = None, close_source: bool = False,
                 cancellation_token=None):
        self._source = source
        self._registry = registry
        self._selection = selection
        self._limits = limits or SecurityLimits()
        self._pool = ResourcePool(allocation or ResourceAllocation(), self._limits, parent=parent_pool)
        self._requests = {}
        self._leases = {}
        self._events = []
        self._cancelled = Event()
        self._lock = RLock()
        self._closed = False
        self._close_source = close_source
        self._cancellation_token = cancellation_token
        parameters = inspect.signature(source.render_region).parameters
        self._render_rotation = "page_rotation" in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values())

    def cancel(self):
        self._cancelled.set()

    def check_cancelled(self):
        if self._cancellation_token is not None:
            self._cancellation_token.check()
        if self._cancelled.is_set():
            raise WorkCancelled("OCR work explicitly cancelled")

    def _request_pool(self, request: OCRWorkRequest):
        if self._closed:
            raise RuntimeError("OCR services are closed")
        self.check_cancelled()
        if request.selection != self._selection:
            raise ValueError("Request selects another engine/provider")
        for auth in request.authorizations:
            self._registry.validate(request, auth.authorization_id)
        previous = self._requests.get(request.request_id)
        if previous is not None:
            if previous[0] != request:
                raise ValueError("Request ID reused with a different immutable payload")
            return previous[1]
        pool = self._pool.child(request.resource_allocation)
        self._requests[request.request_id] = request, pool
        return pool

    def emit(self, request, kind, details=None):
        with self._lock:
            self._request_pool(request)
            self._events.append(snapshot({"request_id": request.request_id,
                                          "extraction_id": request.scope.extraction_id,
                                          "document_id": request.scope.document_id,
                                          "page_index": request.scope.page_index,
                                          "target_id": request.scope.target.target_id,
                                          "target_revision": request.scope.target.revision,
                                          "evidence_version": request.scope.evidence_version,
                                          "engine": request.selection.engine,
                                          "provider": request.selection.provider,
                                          "kind": kind, "details": details or {}}))

    @property
    def events(self):
        with self._lock:
            return tuple(self._events)

    def consume_attempt(self, request: OCRWorkRequest) -> str:
        with self._lock:
            number = self._request_pool(request).consume_attempt()
            self.emit(request, "attempt", {"number": number})
            return f"{request.request_id}:attempt:{number}"

    def observations(self):
        return self._pool.observations()

    def validate_result(self, request, result):
        with self._lock:
            self._request_pool(request)
            self._registry.validate_result(request, result)

    def validate_token(self, request, token):
        with self._lock:
            self._request_pool(request)
            self._registry.validate_token(request, token)

    def _render_plan(self, request, box, scale):
        finite(scale)
        if scale <= 0:
            raise ValueError("Render scale must be positive")
        page = request.scope.page_geometry
        local_page = BBox(0, 0, page.bbox.width, page.bbox.height)
        local = BBox(box.x0-page.bbox.x0, box.y0-page.bbox.y0, box.x1-page.bbox.x0, box.y1-page.bbox.y0)
        # The native renderer has a protective scale cap. Reject work that would
        # trigger it, rather than silently changing resolution in this new path.
        native_limits = getattr(getattr(self._source, "config", None), "security_limits", self._limits)
        area = finite(max(local.width, 1.0) * max(local.height, 1.0))
        for limits in (self._limits, native_limits):
            if (area * scale * scale > limits.max_render_pixels
                    or 3 * area * scale * scale > limits.max_render_bytes
                    or math.ceil(local.width*scale)*math.ceil(local.height*scale) > limits.max_render_pixels
                    or 3*math.ceil(local.width*scale)*math.ceil(local.height*scale) > limits.max_render_bytes):
                raise BudgetBlocked("Requested scale exceeds absolute native render limits")
        visual = local.rotate_to_visual(page.rotation, local_page.width, local_page.height)
        vw, vh = (local_page.height, local_page.width) if page.rotation in (90, 270) else (local_page.width, local_page.height)
        fw, fh = (math.ceil(finite(v*scale)) for v in (vw, vh))
        if max(fw, fh) > 2**31-1:
            raise BudgetBlocked("Render dimensions exceed the PDFium integer range")
        left, top, right, bottom = (math.ceil(finite(v*scale)) for v in (visual.x0, visual.y0, vw-visual.x1, vh-visual.y1))
        width, height = fw-left-right, fh-top-bottom
        if width <= 0 or height <= 0:
            raise ValueError("Region contains no renderable pixel at this scale")
        full_frame = RasterFrame(page.bbox, fw, fh, page.rotation)
        actual_box = full_frame.bbox_to_page(BBox(left, top, left+width, top+height))
        return local, local_page, width, height, actual_box

    def render(self, request: OCRWorkRequest, authorization_id: str, bbox: BBox, *, scale: float = 1.0) -> RasterLease:
        with self._lock:
            pool = self._request_pool(request)
            box = valid_bbox(bbox)
            self._registry.validate(request, authorization_id, box)
            local, local_page, width, height, actual_box = self._render_plan(request, box, scale)
            reservation = pool.reserve_raster(width, height, copies=4)
            raw = owned = image = None
            try:
                from PIL import Image, ImageDraw

                kwargs = {"page_bbox": local_page}
                if self._render_rotation:
                    kwargs["page_rotation"] = request.scope.page_geometry.rotation
                raw = self._source.render_region(request.scope.page_index, local, scale, **kwargs)
                self.check_cancelled()
                if raw.size != (width, height) or raw.mode not in ("RGB", "RGBA", "L"):
                    raise ValueError("Source returned unexpected dimensions or pixel format")
                owned = raw.convert("RGB")
                raw.close()
                raw = None
                canonical = canonicalize_page_image(owned, request.scope.page_geometry.rotation)
                image = canonical.copy() if isinstance(canonical, Image.Image) else Image.fromarray(canonical).copy()
                owned.close()
                owned = None
                frame = RasterFrame(actual_box, *image.size)
                # Only retain pixels whose complete footprint is authorized.
                safe = frame.page_bbox_to_pixel(box)
                x0, y0 = max(0, math.ceil(safe.x0)), max(0, math.ceil(safe.y0))
                x1, y1 = min(image.width, math.floor(safe.x1)), min(image.height, math.floor(safe.y1))
                draw = ImageDraw.Draw(image)
                if x1 <= x0 or y1 <= y0:
                    draw.rectangle((0, 0, image.width, image.height), fill="white")
                else:
                    if x0: draw.rectangle((0, 0, x0-1, image.height), fill="white")
                    if y0: draw.rectangle((0, 0, image.width, y0-1), fill="white")
                    if x1 < image.width: draw.rectangle((x1, 0, image.width, image.height), fill="white")
                    if y1 < image.height: draw.rectangle((0, y1, image.width, image.height), fill="white")
                self.check_cancelled()
                self.emit(request, "raster", {"authorization_id": authorization_id, "requested_scale": scale,
                                               "width_px": frame.width_px, "height_px": frame.height_px,
                                               "effective_scale_x": frame.width_px/frame.bbox.width,
                                               "effective_scale_y": frame.height_px/frame.bbox.height,
                                               "boundary_policy": "white_partial_pixel_footprints"})
                lease = self._publish(request, authorization_id, image, frame, reservation)
                image = None
                return lease
            except Exception as exc:
                reservation.release()
                if isinstance(exc, FatalExtractionError):
                    raise
                raise_if_resource_exhausted(exc, page_index=request.scope.page_index, stage="ocr_pipeline_raster")
                raise
            finally:
                for value in (raw, owned, image):
                    if value is not None:
                        value.close()

    def embedded_image(self, request, authorization_id, bbox, *, object_index: int, scale=1.0):
        # The core supplies/authorizes the placement. No original bitmap can
        # bypass occlusion, clipping, transparency, or composition visibility.
        integer(object_index)
        if not callable(getattr(self._source, "extract_page", None)):
            raise ValueError("Embedded-image placement cannot be verified by this source")
        evidence = self._source.extract_page(request.scope.page_index)
        image = next((item for item in evidence.objects.images if item.object_index == object_index), None)
        box = valid_bbox(bbox)
        if image is None or image.bbox is None:
            raise ValueError("Embedded image object does not exist or has no placement bounds")
        if not (box.x0 < image.bbox.x1 and image.bbox.x0 < box.x1
                and box.y0 < image.bbox.y1 and image.bbox.y0 < box.y1):
            raise ValueError("Embedded image placement does not intersect the authorized region")
        return self.render(request, authorization_id, bbox, scale=scale)

    def _publish(self, request, authorization_id, image, frame, reservation):
        reservation.retain_rgb(*image.size)
        def release(lease):
            reservation.release()
            with self._lock:
                self._leases.pop(lease, None)
        lease = RasterLease(image, frame, release, self._lock)
        self._leases[lease] = request, authorization_id
        return lease

    def _derive(self, lease, width, height, operation, transform):
        with self._lock:
            if lease not in self._leases:
                raise ValueError("Lease is closed or belongs to another service")
            request, authorization_id = self._leases[lease]
            pool = self._request_pool(request)
            reservation = pool.reserve_raster(width, height, copies=2)
            image = None
            try:
                image = operation(lease.image)
                self.check_cancelled()
                if image.width > width or image.height > height:
                    raise ValueError("Derived raster exceeded its preflight dimensions")
                frame = lease.frame.derived(*image.size, transform(image))
                self.emit(request, "raster_transform", {"width_px": frame.width_px, "height_px": frame.height_px})
                result = self._publish(request, authorization_id, image, frame, reservation)
                image = None
                return result
            except Exception as exc:
                reservation.release()
                if isinstance(exc, FatalExtractionError):
                    raise
                raise_if_resource_exhausted(exc, page_index=request.scope.page_index, stage="ocr_pipeline_raster_transform")
                raise
            finally:
                if image is not None:
                    image.close()

    def resize(self, lease, width: int, height: int):
        integer(width, minimum=1)
        integer(height, minimum=1)
        return self._derive(lease, width, height, lambda image: image.resize((width, height)),
                            lambda image: affine((lease.frame.width_px/image.width, 0, 0, 0, lease.frame.height_px/image.height, 0)))

    def tile(self, lease, bbox_px: BBox):
        box = valid_bbox(bbox_px)
        values = (box.x0, box.y0, box.x1, box.y1)
        if any(v != int(v) for v in values) or not (0 <= box.x0 < box.x1 <= lease.frame.width_px and 0 <= box.y0 < box.y1 <= lease.frame.height_px):
            raise ValueError("Tile must contain whole pixels inside its parent raster")
        return self._derive(lease, int(box.width), int(box.height), lambda image: image.crop(tuple(int(v) for v in values)),
                            lambda _: affine((1, 0, box.x0, 0, 1, box.y0)))

    def rotate(self, lease, degrees: float, *, padding: int = 0):
        finite(degrees)
        integer(padding)
        angle = math.radians(degrees % 360)
        c, s = round(math.cos(angle), 15), round(math.sin(angle), 15)
        w, h = lease.frame.width_px, lease.frame.height_px
        width = math.ceil(finite(abs(c)*w + abs(s)*h)) + 2 + 2*padding
        height = math.ceil(finite(abs(s)*w + abs(c)*h)) + 2 + 2*padding
        def operation(image):
            from PIL import ImageOps
            rotated = image.rotate(degrees, expand=True, fillcolor="white")
            if not padding:
                return rotated
            try:
                return ImageOps.expand(rotated, border=padding, fill="white")
            finally:
                rotated.close()
        return self._derive(lease, width, height, operation,
                            lambda image: affine((c, -s, w/2-c*image.width/2+s*image.height/2,
                                                  s, c, h/2-s*image.width/2-c*image.height/2)))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            first_error = None
            try:
                for lease in tuple(self._leases):
                    try:
                        lease.close()
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc
            finally:
                if self._close_source:
                    try:
                        self._source.close()
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc
            if first_error is not None:
                raise first_error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
