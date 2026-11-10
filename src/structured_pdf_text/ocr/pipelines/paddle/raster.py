"""Paddle-owned raster preparation restoring the pre-decoupling pixel path.

See docs/adr/0002-paddleocr-restauracao-raster-regional.md. Before the engine
decoupling (54885e69) regional recovery cropped the visual-orientation page
raster, enlarged the crop with ``resize_image`` and turned it with
``rotate_image_expanded``. Here the same operations run on leases issued by the
authorized services, so authorization, masking, budgets, cancellation and the
single RasterFrame mapping back to canonical PDF points are all preserved.

The core states the historical origin of each request in
``native_context["raster_preparation"]``; requests without it keep the shared
direct source render.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from structured_pdf_text.ocr.pipeline.policies import SOURCE_RASTER_PREPARATION

# Same threshold rotate_image_expanded uses to return the identity transform.
_MIN_LOCAL_ROTATION = 0.001


@dataclass(frozen=True, slots=True)
class LegacyRasterPreparation:
    preparation_id: str = "paddle-legacy-raster-v1"

    def prepare(self, request, services, authorization_id, box, *, base_scale, boundary_policy, stack):
        kind = request.native_context.get("raster_preparation")
        if kind is None:
            return SOURCE_RASTER_PREPARATION.prepare(
                request, services, authorization_id, box,
                base_scale=base_scale, boundary_policy=boundary_policy, stack=stack,
            )
        render_scale = (
            base_scale if kind == "page_crop"
            else request.render_scale / request.native_context["raster_enlargement"] if kind == "enlarged_source"
            else request.render_scale
        )
        lease = stack.enter_context(services.render(
            request, authorization_id, box, scale=render_scale, boundary_policy=boundary_policy,
        ))
        page_rotation = request.scope.page_geometry.rotation
        details = {
            "strategy": self.preparation_id, "origin": kind, "render_scale": render_scale,
            "visual_rotation": page_rotation if kind != "enlarged_source" else 0,
            "local_upsample": 1.0, "local_rotation": 0.0,
        }
        if kind != "enlarged_source" and page_rotation:
            # Services hand out canonical-orientation pixels; the historical
            # page raster was in visual orientation (PDFium applies /Rotate).
            lease = stack.enter_context(services.rotate(lease, -page_rotation))
        if kind == "page_crop":
            factor = round(request.render_scale / base_scale, 9)
            if factor > 1.0:
                lease = stack.enter_context(_resize(services, lease, factor))
                details["local_upsample"] = factor
            angle = float(request.preprocessing_rotation)
            if abs(angle) >= _MIN_LOCAL_ROTATION:
                lease = stack.enter_context(_rotate(services, lease, angle))
                details["local_rotation"] = angle
        else:
            if kind == "enlarged_source":
                factor = float(request.native_context["raster_enlargement"])
                lease = stack.enter_context(_enlarge(services, lease, factor))
                details["local_upsample"] = factor
            if request.preprocessing_rotation % 360:
                lease = stack.enter_context(services.rotate(lease, request.preprocessing_rotation))
        details["size_px"] = [lease.frame.width_px, lease.frame.height_px]
        return lease, details


def _resize(services, lease, factor):
    """``resize_image`` (LANCZOS, ``max(dim + 1, round(dim * factor))``)."""
    from structured_pdf_text.ocr.recovery import resize_image

    width, height = lease.frame.width_px, lease.frame.height_px

    def operation(image):
        resized = resize_image(image, factor)
        if resized is image:
            raise ValueError("Local raster resampling is unavailable")
        return resized

    return services.derive(
        lease, max(width + 1, round(width * factor)), max(height + 1, round(height * factor)),
        operation, lambda image: (width / image.width, 0, 0, 0, height / image.height, 0),
    )


def _enlarge(services, lease, factor):
    """Exact LANCZOS enlargement used by the historical weak-cell attempt."""
    width, height = lease.frame.width_px, lease.frame.height_px
    size = (round(width * factor), round(height * factor))

    def operation(image):
        from PIL import Image

        return image.resize(size, Image.Resampling.LANCZOS)

    return services.derive(
        lease, *size, operation,
        lambda image: (width / image.width, 0, 0, 0, height / image.height, 0),
    )


def _rotate(services, lease, angle):
    """``rotate_image_expanded``; its returned inverse maps back to the parent."""
    from structured_pdf_text.ocr.recovery import rotate_image_expanded

    width, height = lease.frame.width_px, lease.frame.height_px
    radians = math.radians(angle)
    cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
    inverse = {}

    def operation(image):
        rotated, inverse["matrix"] = rotate_image_expanded(image, angle)
        return rotated

    # One spare pixel absorbs floating-point ceil differences with OpenCV.
    return services.derive(
        lease,
        max(1, math.ceil(height * sine + width * cosine)) + 1,
        max(1, math.ceil(height * cosine + width * sine)) + 1,
        operation, lambda _image: inverse["matrix"],
    )


LEGACY_RASTER_PREPARATION = LegacyRasterPreparation()
