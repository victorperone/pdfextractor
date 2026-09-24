"""PDFium-backed native evidence source.

Wraps ``pypdfium2`` to extract immutable, loss-minimising evidence from a PDF
file. The source deliberately does not perform layout, OCR, deduplication, or
reading-order decisions — those are downstream consumers of the evidence it
produces.

Key responsibilities:
- PDF header validation and security limit enforcement
- Per-page character extraction with full typographic metadata
- Page object enumeration (images, paths, annotations)
- PDF structure-tree harvesting
- Coordinate-system normalisation to a top-left origin (y grows down)
"""
from __future__ import annotations

import ctypes
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from structured_pdf_text.config import DocumentContext, ExtractorConfig
from structured_pdf_text.document import (
    AnnotationEvidence,
    ImageEvidence,
    NativeCharacter,
    NativeObjectEvidence,
    NativeObjectSummary,
    NativePageEvidence,
    PathEvidence,
    StructureTreeEvidence,
)
from structured_pdf_text.geometry import BBox, Point

try:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c
except Exception:  # pragma: no cover - exercised only when dependency is missing
    pdfium = None
    pdfium_c = None


class PdfiumUnavailableError(RuntimeError):
    pass


class PdfiumNativeEvidenceSource:
    """Collect immutable, loss-minimizing evidence exposed by PDFium.

    This class deliberately does not perform layout, OCR, deduplication or
    reading-order decisions. Those are consumers of this evidence and must
    not mutate it.
    """

    def __init__(
        self,
        path: str | Path,
        config: ExtractorConfig | None = None,
        password: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.config = config or ExtractorConfig()
        self.password = password
        self._doc: Any | None = None
        self._context: DocumentContext | None = None
        self._metrics: dict[str, int] = {
            "document_acquisitions": 0,
            "page_acquisitions": 0,
            "textpage_acquisitions": 0,
            "render_calls": 0,
            "character_iterations": 0,
            "object_iterations": 0,
            "annotation_iterations": 0,
            "ffi_calls_estimated": 0,
        }

    def __enter__(self) -> PdfiumNativeEvidenceSource:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> DocumentContext:
        """Open and validate the PDF, returning a ``DocumentContext``.

        Validates the ``%PDF-`` file header, checks file size and page count
        against the configured security limits, and loads the document via
        ``pypdfium2``. Idempotent: calling ``open`` more than once returns the
        cached context. Raises ``PdfiumUnavailableError`` when ``pypdfium2`` is
        not installed.
        """
        if self._doc is not None and self._context is not None:
            return self._context
        if pdfium is None:
            raise PdfiumUnavailableError("pypdfium2 is not installed")
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._validate_header()
        size = self.path.stat().st_size
        if size > self.config.security_limits.max_file_size_bytes:
            raise ValueError(
                f"PDF exceeds max_file_size_bytes: {size} > "
                f"{self.config.security_limits.max_file_size_bytes}"
            )
        kwargs: dict[str, Any] = {}
        if self.password is not None:
            kwargs["password"] = self.password
        self._doc = pdfium.PdfDocument(str(self.path), **kwargs)
        self._record_metric("document_acquisitions")
        self._record_metric("ffi_calls_estimated")
        page_count = len(self._doc)
        if page_count > self.config.security_limits.max_pages:
            self.close()
            raise ValueError(
                f"PDF exceeds max_pages: {page_count} > {self.config.security_limits.max_pages}"
            )
        self._context = DocumentContext(
            path=self.path,
            page_count=page_count,
            pdfium_version=_pdfium_version(),
            config=self.config,
        )
        return self._context

    def extract_page(self, page_index: int) -> NativePageEvidence:
        """Extract immutable native evidence for a single page.

        Acquires the PDFium text-page, iterates every character to populate
        ``NativeCharacter`` objects with bounding boxes, typography, and flags,
        then enumerates page objects (images, paths) and annotations. Coordinates
        are transformed from PDFium's bottom-left origin to the canonical top-left
        origin (y grows down). Closes the text-page and page objects before
        returning; the returned ``NativePageEvidence`` is safe to hold
        indefinitely without holding the document open.
        """
        if self._doc is None:
            self.open()
        if self._doc is None:
            raise RuntimeError("Document failed to open")
        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(f"page_index out of range: {page_index}")

        page = self._doc[page_index]
        self._record_metric("page_acquisitions")
        try:
            width, height = (float(value) for value in page.get_size())
            media_box = _get_page_box(page, "get_mediabox")
            crop_box = _get_page_box(page, "get_cropbox")
            page_box = _get_page_box(page, "get_bbox") or crop_box or media_box
            if page_box is None:
                page_box = (0.0, 0.0, width, height)
            if crop_box is None:
                crop_box = page_box
            if media_box is None:
                media_box = crop_box

            # PDFium's effective page bbox is the coordinate frame used by
            # text/object bounds and rendering. Some third-party PDFs retain
            # stale inherited MediaBox/CropBox values that disagree with this
            # effective frame; using those values shifts every native object
            # relative to the rendered image. A valid non-zero CropBox is
            # already reflected in get_bbox(), so it remains supported here.
            origin_x = float(page_box[0])
            origin_y = float(page_box[1])
            canvas_width = max(float(page_box[2]) - origin_x, 0.0) or width
            canvas_height = max(float(page_box[3]) - origin_y, 0.0) or height
            canonical_page_bbox = _canonical_page_box(page_box, origin_x, origin_y, canvas_width, canvas_height)
            canonical_crop_bbox = _canonical_page_box(crop_box, origin_x, origin_y, canvas_width, canvas_height)
            canonical_media_bbox = _canonical_page_box(media_box, origin_x, origin_y, canvas_width, canvas_height)
            rotation = int(getattr(page, "get_rotation", lambda: 0)() or 0)

            textpage = page.get_textpage()
            self._record_metric("textpage_acquisitions")
            try:
                characters, character_capabilities = self._extract_characters(
                    textpage,
                    page_index,
                    canvas_height,
                    origin_x,
                    origin_y,
                )
                extracted_text = _get_full_text(textpage)
                objects = self._extract_objects(
                    page,
                    textpage,
                    page_index,
                    canonical_page_bbox,
                    canonical_crop_bbox,
                    canonical_media_bbox,
                    canvas_height,
                    origin_x,
                    origin_y,
                    rotation,
                )
            finally:
                close = getattr(textpage, "close", None)
                if callable(close):
                    close()

            structure_tree = _extract_structure_tree(page)
            objects = _with_structure_tree(objects, structure_tree)
            self._record_extraction_metrics(
                len(characters),
                character_capabilities,
                len(objects.objects),
                len(objects.annotations),
                structure_tree.node_count or 0,
            )
            capabilities = {
                **character_capabilities,
                **objects.capabilities,
                "page_media_box": media_box is not None,
                "page_crop_box": crop_box is not None,
                "page_bbox": page_box is not None,
                "page_rotation": hasattr(page, "get_rotation"),
                "structure_tree": structure_tree.available,
            }
            return NativePageEvidence(
                page_index=page_index,
                bbox=canonical_page_bbox,
                characters=tuple(characters),
                objects=objects,
                extracted_text=extracted_text,
                capabilities=capabilities,
            )
        finally:
            page.close()

    def render_page(self, page_index: int, scale: float = 0.5) -> Any:
        """Render a low-resolution diagnostic image while the document is open."""
        if self._doc is None:
            self.open()
        if self._doc is None:
            raise RuntimeError("Document failed to open")
        if page_index < 0 or page_index >= len(self._doc):
            raise IndexError(f"page_index out of range: {page_index}")
        page = self._doc[page_index]
        self._record_metric("page_acquisitions")
        self._record_metric("render_calls")
        # Page acquisition, render, bitmap conversion and two closes are
        # explicit binding transitions made by this method.
        self._record_metric("ffi_calls_estimated", 5)
        try:
            bitmap = page.render(scale=float(scale), rev_byteorder=True)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._record_metric("ffi_calls_estimated")
        self._doc = None
        self._context = None

    def metrics_snapshot(self) -> dict[str, int]:
        """Return source counters without exposing mutable internal state."""
        return dict(self._metrics)

    def _record_metric(self, name: str, amount: int = 1) -> None:
        self._metrics[name] = self._metrics.get(name, 0) + max(0, int(amount))

    def _record_extraction_metrics(
        self,
        character_count: int,
        capabilities: dict[str, bool],
        object_count: int,
        annotation_count: int,
        structure_node_count: int,
    ) -> None:
        """Count explicit acquisitions and estimate binding/FFI transitions.

        pypdfium2 may perform more than one native transition inside a Python
        method. The estimate deliberately counts calls visible at this adapter
        boundary and is labelled as such in diagnostics.
        """
        self._record_metric("character_iterations", character_count)
        self._record_metric("object_iterations", object_count)
        self._record_metric("annotation_iterations", annotation_count)
        per_character = 2  # text range and bbox
        per_character += sum(
            bool(capabilities.get(name))
            for name in (
                "character_unicode",
                "character_angle",
                "character_origin",
                "character_font",
                "character_fill_color",
                "character_stroke_color",
                "text_render_mode",
                "marked_content_id",
                "is_generated",
                "is_hyphen",
                "unicode_map_error",
            )
        )
        fixed_page_calls = 12
        object_calls = object_count * 4
        annotation_calls = annotation_count * 10
        structure_calls = structure_node_count * 3
        self._record_metric(
            "ffi_calls_estimated",
            fixed_page_calls
            + character_count * per_character
            + object_calls
            + annotation_calls
            + structure_calls,
        )

    def _validate_header(self) -> None:
        with self.path.open("rb") as file:
            header = file.read(5)
        if header != b"%PDF-":
            raise ValueError(f"File does not start with %PDF- header: {self.path}")

    def _extract_characters(
        self,
        textpage: Any,
        page_index: int,
        page_height: float,
        origin_x: float,
        origin_y: float,
    ) -> tuple[list[NativeCharacter], dict[str, bool]]:
        """Iterate the PDFium text-page and build one ``NativeCharacter`` per glyph.

        Collects text, bounding box, angle, origin point, font metadata, fill/stroke
        colours, render mode, and boolean flags (generated, hyphen, unicode mapping
        failure). Returns the character list together with a capabilities dict that
        records which optional PDFium APIs were available for this build.
        """
        count = int(textpage.count_chars() or 0)
        chars: list[NativeCharacter] = []
        style_cache: dict[
            Any,
            tuple[str | None, float | None, int | None, int | None, int | None],
        ] = {}
        capabilities = {
            "character_unicode": _has_raw_function("FPDFText_GetUnicode"),
            "character_bbox": hasattr(textpage, "get_charbox"),
            "character_angle": _has_raw_function("FPDFText_GetCharAngle"),
            "character_origin": _has_raw_function("FPDFText_GetCharOrigin"),
            "character_font": hasattr(textpage, "get_textobj"),
            "character_fill_color": _has_raw_function("FPDFText_GetFillColor"),
            "character_stroke_color": _has_raw_function("FPDFText_GetStrokeColor"),
            "text_render_mode": _has_raw_function("FPDFTextObj_GetTextRenderMode"),
            "marked_content_id": _has_raw_function("FPDFPageObj_GetMarkedContentID"),
            "is_generated": _has_raw_function("FPDFText_IsGenerated"),
            "is_hyphen": _has_raw_function("FPDFText_IsHyphen"),
            "unicode_map_error": _has_raw_function("FPDFText_HasUnicodeMapError"),
        }
        for index in range(count):
            text = _get_char_text(textpage, index)
            unicode_codepoint = _get_unicode(textpage, index, text)
            bbox = _get_char_bbox(textpage, index, page_height, origin_x, origin_y)
            angle = _get_char_angle(textpage, index)
            origin = _get_char_origin(textpage, index, page_height, origin_x, origin_y)
            generated = _get_bool_feature("FPDFText_IsGenerated", textpage, index)
            hyphen = _get_bool_feature("FPDFText_IsHyphen", textpage, index)
            mapping_failed = _get_bool_feature("FPDFText_HasUnicodeMapError", textpage, index)
            fill_color = _get_character_color("FPDFText_GetFillColor", textpage, index)
            stroke_color = _get_character_color("FPDFText_GetStrokeColor", textpage, index)
            (
                font_name,
                font_size,
                font_weight,
                text_render_mode,
                marked_content_id,
            ) = _get_character_style(textpage, index, style_cache)
            visible_candidate = bool(text.strip()) and bbox.area > 0
            chars.append(
                NativeCharacter(
                    page_index=page_index,
                    char_index=index,
                    text=text,
                    unicode_codepoint=unicode_codepoint,
                    bbox=bbox,
                    origin=origin,
                    angle=angle,
                    font_name=font_name,
                    font_size=font_size,
                    font_weight=font_weight,
                    fill_color=fill_color,
                    stroke_color=stroke_color,
                    text_render_mode=text_render_mode,
                    marked_content_id=marked_content_id,
                    generated=generated,
                    hyphen=hyphen,
                    unicode_mapping_failed=mapping_failed,
                    visible_candidate=visible_candidate,
                )
            )
        return chars, capabilities

    def _extract_objects(
        self,
        page: Any,
        textpage: Any,
        page_index: int,
        page_bbox: BBox,
        crop_bbox: BBox,
        media_bbox: BBox,
        page_height: float,
        origin_x: float,
        origin_y: float,
        rotation: int,
    ) -> NativeObjectEvidence:
        """Enumerate page objects (images, paths) and build ``NativeObjectEvidence``.

        Iterates all page objects to collect ``ImageEvidence`` and
        ``PathEvidence`` with normalised bounding boxes, then calls
        ``_extract_annotations`` for annotation evidence. Returns a
        ``NativeObjectEvidence`` that records the coordinate origin, page
        boxes, rotation, and a capabilities dict.
        """
        images: list[ImageEvidence] = []
        paths: list[PathEvidence] = []
        summaries: list[NativeObjectSummary] = []
        objects = _get_page_objects(page, textpage)
        image_type = _constant("FPDF_PAGEOBJ_IMAGE", 3)
        path_type = _constant("FPDF_PAGEOBJ_PATH", 2)
        for object_index, obj in enumerate(objects):
            bbox = _get_object_bbox(obj, page_height, origin_x, origin_y)
            object_type = getattr(obj, "type", "unknown")
            level = _safe_int(getattr(obj, "level", None))
            summaries.append(
                NativeObjectSummary(
                    page_index,
                    object_index,
                    object_type,
                    bbox,
                    level,
                    marked_content_id=_get_marked_content_id(getattr(obj, "raw", None)),
                    matrix=_get_object_matrix(getattr(obj, "raw", None)),
                )
            )
            if object_type == image_type:
                images.append(
                    ImageEvidence(
                        page_index,
                        object_index,
                        bbox,
                        *_get_image_metadata(obj),
                    )
                )
            elif object_type == path_type:
                paths.append(PathEvidence(page_index, object_index, bbox, level))

        capabilities = {
            "page_objects": hasattr(page, "get_objects"),
            "image_metadata": any(callable(getattr(obj, "get_px_size", None)) for obj in objects),
            "annotations": _has_raw_function("FPDFPage_GetAnnotCount"),
            "annotation_appearance_streams": _has_raw_function("FPDFAnnot_GetAP"),
            "annotation_objects": _has_raw_function("FPDFAnnot_GetObjectCount"),
            "object_marked_content_id": _has_raw_function("FPDFPageObj_GetMarkedContentID"),
            "object_matrix": _has_raw_function("FPDFPageObj_GetMatrix"),
        }
        annotations = _extract_annotations(page, page_index, page_height, origin_x, origin_y)
        return NativeObjectEvidence(
            images=tuple(images),
            paths=tuple(paths),
            annotations=tuple(annotations),
            structure_tree=None,
            page_bbox=page_bbox,
            crop_bbox=crop_bbox,
            rotation=rotation,
            objects=tuple(summaries),
            media_bbox=media_bbox,
            coordinate_origin=Point(origin_x, origin_y),
            capabilities=capabilities,
        )


def _pdfium_version() -> str | None:
    try:
        pypdfium_version = version("pypdfium2")
    except PackageNotFoundError:
        pypdfium_version = "unknown"
    info = getattr(pdfium, "PDFIUM_INFO", None) if pdfium is not None else None
    return f"pypdfium2 {pypdfium_version}; PDFium {info}" if info else f"pypdfium2 {pypdfium_version}"


def _get_page_box(page: Any, method_name: str) -> tuple[float, float, float, float] | None:
    method = getattr(page, method_name, None)
    if not callable(method):
        return None
    try:
        values = tuple(float(value) for value in method())
    except Exception:
        return None
    if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
        return None
    return values  # type: ignore[return-value]


def _canonical_page_box(
    box: tuple[float, float, float, float],
    origin_x: float,
    origin_y: float,
    fallback_width: float,
    fallback_height: float,
) -> BBox:
    """Convert a PDFium page box (bottom-left origin) to a canonical top-left BBox.

    Subtracts the crop origin so that all coordinate values are relative to the
    effective page frame exposed by PDFium. Fallback dimensions are used when
    the computed width or height would be zero.
    """
    x0, y0, x1, y1 = box
    width = max(0.0, x1 - x0) or fallback_width
    height = max(0.0, y1 - y0) or fallback_height
    return BBox(x0 - origin_x, y1 - origin_y - height, x0 - origin_x + width, y1 - origin_y)


def _get_char_text(textpage: Any, index: int) -> str:
    try:
        return textpage.get_text_range(index, 1, errors="ignore") or ""
    except TypeError:
        try:
            return textpage.get_text_range(index, 1) or ""
        except Exception:
            return ""
    except Exception:
        return ""


def _get_full_text(textpage: Any) -> str:
    try:
        # This is the native range view. Per-character Unicode values remain
        # authoritative if PDFium omits or inserts code units in a range.
        return textpage.get_text_range(0, -1, errors="ignore") or ""
    except TypeError:
        try:
            return textpage.get_text_range(0, -1) or ""
        except Exception:
            return ""
    except Exception:
        return ""


def _get_char_bbox(
    textpage: Any,
    index: int,
    page_height: float,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> BBox:
    try:
        left, bottom, right, top = textpage.get_charbox(index)
        return BBox.from_pdfium_rect(
            float(left),
            float(bottom),
            float(right),
            float(top),
            page_height,
            origin_x=origin_x,
            origin_y=origin_y,
        )
    except Exception:
        return BBox(0.0, 0.0, 0.0, 0.0)


def _get_char_origin(
    textpage: Any,
    index: int,
    page_height: float,
    origin_x: float,
    origin_y: float,
) -> Point | None:
    if pdfium_c is None:
        return None
    function = getattr(pdfium_c, "FPDFText_GetCharOrigin", None)
    if function is None:
        return None
    try:
        x = ctypes.c_double()
        y = ctypes.c_double()
        ok = function(textpage.raw, index, x, y)
        if ok is False or ok == 0:
            return None
        return Point(float(x.value) - origin_x, origin_y + page_height - float(y.value))
    except Exception:
        return None


def _get_object_bbox(
    obj: Any,
    page_height: float,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> BBox | None:
    try:
        left, bottom, right, top = obj.get_bounds()
        return BBox.from_pdfium_rect(
            float(left),
            float(bottom),
            float(right),
            float(top),
            page_height,
            origin_x=origin_x,
            origin_y=origin_y,
        )
    except Exception:
        return None


def _get_object_matrix(raw_object: Any) -> tuple[float, float, float, float, float, float] | None:
    if pdfium_c is None or raw_object is None:
        return None
    function = getattr(pdfium_c, "FPDFPageObj_GetMatrix", None)
    if function is None:
        return None
    try:
        matrix = pdfium_c.FS_MATRIX()
        if not function(raw_object, matrix):
            return None
        return tuple(
            float(getattr(matrix, name))
            for name in ("a", "b", "c", "d", "e", "f")
        )  # type: ignore[return-value]
    except Exception:
        return None


def _get_marked_content_id(raw_object: Any) -> int | None:
    if pdfium_c is None or raw_object is None:
        return None
    function = getattr(pdfium_c, "FPDFPageObj_GetMarkedContentID", None)
    if function is None:
        return None
    try:
        value = int(function(raw_object))
        return value if value >= 0 else None
    except Exception:
        return None


def _get_unicode(textpage: Any, index: int, fallback_text: str) -> int | None:
    if pdfium_c is not None and hasattr(pdfium_c, "FPDFText_GetUnicode"):
        try:
            value = int(pdfium_c.FPDFText_GetUnicode(textpage.raw, index))
            return value if value >= 0 else None
        except Exception:
            pass
    return ord(fallback_text[0]) if fallback_text else None


def _get_char_angle(textpage: Any, index: int) -> float | None:
    if pdfium_c is not None and hasattr(pdfium_c, "FPDFText_GetCharAngle"):
        try:
            return float(pdfium_c.FPDFText_GetCharAngle(textpage.raw, index))
        except Exception:
            return None
    return None


def _get_bool_feature(function_name: str, textpage: Any, index: int) -> bool | None:
    if pdfium_c is None or not hasattr(pdfium_c, function_name):
        return None
    try:
        result = getattr(pdfium_c, function_name)(textpage.raw, index)
        # PDFium returns -1 on error; bool(-1) would be True, which is wrong.
        if result == -1:
            return None
        return bool(result)
    except Exception:
        return None


def _get_character_color(
    function_name: str,
    textpage: Any,
    index: int,
) -> tuple[int, int, int, int] | None:
    if pdfium_c is None:
        return None
    function = getattr(pdfium_c, function_name, None)
    if function is None:
        return None
    try:
        red = ctypes.c_uint()
        green = ctypes.c_uint()
        blue = ctypes.c_uint()
        alpha = ctypes.c_uint()
        if not function(textpage.raw, index, red, green, blue, alpha):
            return None
        return red.value, green.value, blue.value, alpha.value
    except Exception:
        return None


def _get_character_style(
    textpage: Any,
    index: int,
    cache: dict[
        Any,
        tuple[str | None, float | None, int | None, int | None, int | None],
    ],
) -> tuple[str | None, float | None, int | None, int | None, int | None]:
    """Extract font name, size, weight, render mode, and marked-content ID for a character.

    Results are keyed by the raw text-object pointer and cached to avoid
    redundant FFI round-trips for consecutive characters in the same run.
    Returns ``(None, None, None, None, None)`` when the text object is
    unavailable.
    """
    get_textobj = getattr(textpage, "get_textobj", None)
    if not callable(get_textobj):
        return None, None, None, None, None
    try:
        textobj = get_textobj(index)
    except Exception:
        return None, None, None, None, None
    if textobj is None:
        return None, None, None, None, None
    raw = getattr(textobj, "raw", None)
    key: Any = None
    try:
        hash(raw)
        key = ("raw", raw)
    except TypeError:
        # raw is a ctypes pointer — try to get its integer address as a stable key.
        try:
            ptr_int = int(raw)
            key = ("ptr", ptr_int)
        except (TypeError, ValueError):
            pass  # no stable key available; skip cache for this object
    if key is not None and key in cache:
        return cache[key]
    font_name: str | None = None
    font_size: float | None = None
    font_weight: int | None = None
    text_render_mode: int | None = None
    marked_content_id: int | None = None
    try:
        font_size = float(textobj.get_font_size())
    except Exception:
        pass
    try:
        font = textobj.get_font()
        get_name = getattr(font, "get_base_name", None) or getattr(font, "get_family_name", None)
        if callable(get_name):
            try:
                font_name = str(get_name(errors="replace"))
            except TypeError:
                font_name = str(get_name())
        get_weight = getattr(font, "get_weight", None)
        if callable(get_weight):
            font_weight = int(get_weight())
    except Exception:
        pass
    if pdfium_c is not None and raw is not None:
        render_function = getattr(pdfium_c, "FPDFTextObj_GetTextRenderMode", None)
        if render_function is not None:
            try:
                render_value = int(render_function(raw))
                text_render_mode = render_value if render_value >= 0 else None
            except Exception:
                pass
        marked_content_id = _get_marked_content_id(raw)
    value = (
        font_name,
        font_size,
        font_weight,
        text_render_mode,
        marked_content_id,
    )
    if key is not None:
        cache[key] = value
    return value


def _get_page_objects(page: Any, textpage: Any) -> list[Any]:
    get_objects = getattr(page, "get_objects", None)
    if not callable(get_objects):
        return []
    try:
        return list(get_objects(textpage=textpage))
    except TypeError:
        try:
            return list(get_objects())
        except Exception:
            return []
    except Exception:
        return []


def _get_image_metadata(
    obj: Any,
) -> tuple[int | None, int | None, float | None, float | None, int | None, int | str | None]:
    pixel_width: int | None = None
    pixel_height: int | None = None
    dpi_x: float | None = None
    dpi_y: float | None = None
    bits_per_pixel: int | None = None
    color_space: int | str | None = None
    try:
        width, height = obj.get_px_size()
        pixel_width, pixel_height = int(width), int(height)
    except Exception:
        pass
    try:
        metadata = obj.get_metadata()
        for target, names in (
            ("dpi_x", ("dpi_x", "horizontal_dpi")),
            ("dpi_y", ("dpi_y", "vertical_dpi")),
            ("bits_per_pixel", ("bits_per_pixel", "bpp")),
            ("color_space", ("color_space", "colorspace")),
        ):
            for name in names:
                if hasattr(metadata, name):
                    value = getattr(metadata, name)
                    if target == "color_space":
                        color_space = value
                    elif target == "bits_per_pixel":
                        bits_per_pixel = _safe_int(value)
                    elif target == "dpi_x":
                        dpi_x = _safe_float(value)
                    else:
                        dpi_y = _safe_float(value)
                    break
    except Exception:
        pass
    return pixel_width, pixel_height, dpi_x, dpi_y, bits_per_pixel, color_space


def _extract_annotations(
    page: Any,
    page_index: int,
    page_height: float,
    origin_x: float,
    origin_y: float,
) -> list[AnnotationEvidence]:
    """Collect all annotations on a page as ``AnnotationEvidence`` objects.

    Returns an empty list when the required PDFium raw functions are
    unavailable. Each annotation records its subtype, bounding box, text
    content, appearance streams, and object count.
    """
    if pdfium_c is None:
        return []
    count_function = getattr(pdfium_c, "FPDFPage_GetAnnotCount", None)
    get_function = getattr(pdfium_c, "FPDFPage_GetAnnot", None)
    close_function = getattr(pdfium_c, "FPDFPage_CloseAnnot", None)
    rect_function = getattr(pdfium_c, "FPDFAnnot_GetRect", None)
    subtype_function = getattr(pdfium_c, "FPDFAnnot_GetSubtype", None)
    if not all((count_function, get_function, close_function, rect_function, subtype_function)):
        return []
    try:
        count = max(0, int(count_function(page.raw)))
    except Exception:
        return []
    annotations: list[AnnotationEvidence] = []
    for index in range(count):
        annot = None
        try:
            annot = get_function(page.raw, index)
            if not annot:
                continue
            bbox: BBox | None = None
            try:
                rect = pdfium_c.FS_RECTF()
                if rect_function(annot, rect):
                    bbox = BBox.from_pdfium_rect(
                        float(rect.left),
                        float(rect.bottom),
                        float(rect.right),
                        float(rect.top),
                        page_height,
                        origin_x=origin_x,
                        origin_y=origin_y,
                    )
            except Exception:
                pass
            try:
                subtype = _annotation_subtype_name(subtype_function(annot))
            except Exception:
                subtype = None
            contents = _get_annotation_contents(annot)
            annotations.append(
                AnnotationEvidence(
                    page_index,
                    index,
                    subtype,
                    bbox,
                    contents,
                    appearance_streams=_get_annotation_appearance_streams(annot),
                    object_count=_get_annotation_object_count(annot),
                )
            )
        except Exception:
            continue
        finally:
            if annot:
                try:
                    close_function(annot)
                except Exception:
                    pass
    return annotations


def _annotation_subtype_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if pdfium_c is not None:
        # PDFium exposes several unrelated enums with the FPDF_ANNOT_ prefix
        # (for example appearance modes). Restrict the reverse lookup to the
        # annotation subtype enum so a LINK is not reported as DOWN.
        subtype_names = {
            "UNKNOWN", "TEXT", "LINK", "FREETEXT", "LINE", "SQUARE", "CIRCLE",
            "POLYGON", "POLYLINE", "HIGHLIGHT", "UNDERLINE", "SQUIGGLY", "STRIKEOUT",
            "STAMP", "CARET", "INK", "POPUP", "FILEATTACHMENT", "SOUND", "MOVIE",
            "WIDGET", "SCREEN", "PRINTERMARK", "TRAPNET", "WATERMARK", "THREED",
            "RICHMEDIA", "XFAWIDGET",
        }
        for subtype in subtype_names:
            name = f"FPDF_ANNOT_{subtype}"
            if hasattr(pdfium_c, name) and getattr(pdfium_c, name) == value:
                return subtype.lower()
    return str(value) if value is not None else None


def _get_annotation_contents(annot: Any) -> str | None:
    if pdfium_c is None:
        return None
    function = getattr(pdfium_c, "FPDFAnnot_GetStringValue", None)
    if function is None:
        return None
    try:
        size = int(function(annot, b"Contents", None, 0))
        if size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        function(annot, b"Contents", buffer, size)
        raw = bytes(buffer.raw[:size])
        if raw.startswith(b"\xff\xfe"):
            return raw[2:].decode("utf-16-le", errors="replace").rstrip("\x00")
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
    except Exception:
        return None


def _get_annotation_appearance_streams(annot: Any) -> dict[str, str]:
    if pdfium_c is None:
        return {}
    function = getattr(pdfium_c, "FPDFAnnot_GetAP", None)
    if function is None:
        return {}
    modes = {
        "normal": getattr(pdfium_c, "FPDF_ANNOT_APPEARANCEMODE_NORMAL", 0),
        "rollover": getattr(pdfium_c, "FPDF_ANNOT_APPEARANCEMODE_ROLLOVER", 1),
        "down": getattr(pdfium_c, "FPDF_ANNOT_APPEARANCEMODE_DOWN", 2),
    }
    streams: dict[str, str] = {}
    for name, mode in modes.items():
        try:
            size = int(function(annot, int(mode), None, 0))
            if size <= 2:
                continue
            buffer = ctypes.create_string_buffer(size)
            pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))
            function(annot, int(mode), pointer, size)
            value = bytes(buffer.raw[:size]).decode(
                "utf-16-le",
                errors="replace",
            ).rstrip("\x00")
            if value:
                streams[name] = value
        except Exception:
            continue
    return streams


def _get_annotation_object_count(annot: Any) -> int | None:
    if pdfium_c is None:
        return None
    function = getattr(pdfium_c, "FPDFAnnot_GetObjectCount", None)
    if function is None:
        return None
    try:
        count = int(function(annot))
        return count if count >= 0 else None
    except Exception:
        return None


def _extract_structure_tree(page: Any) -> StructureTreeEvidence:
    """Walk the PDF logical structure tree and return a ``StructureTreeEvidence`` summary.

    Records root element count, total node count, and a recursive JSON-compatible
    summary of element types. Returns ``StructureTreeEvidence(available=False)``
    when the tree API is unavailable or the page has no structure tree.
    """
    if pdfium_c is None:
        return StructureTreeEvidence(available=False)
    get_tree = getattr(pdfium_c, "FPDF_StructTree_GetForPage", None)
    close_tree = getattr(pdfium_c, "FPDF_StructTree_Close", None)
    count_children = getattr(pdfium_c, "FPDF_StructTree_CountChildren", None)
    get_child = getattr(pdfium_c, "FPDF_StructTree_GetChildAtIndex", None)
    count_element_children = getattr(pdfium_c, "FPDF_StructElement_CountChildren", None)
    get_element_child = getattr(pdfium_c, "FPDF_StructElement_GetChildAtIndex", None)
    get_type = getattr(pdfium_c, "FPDF_StructElement_GetType", None)
    if not all((get_tree, close_tree, count_children, get_child)):
        return StructureTreeEvidence(available=False)
    tree = None
    try:
        tree = get_tree(page.raw)
        if not tree:
            return StructureTreeEvidence(available=False)
        root_count = max(0, int(count_children(tree)))
        elements: list[dict[str, Any]] = []
        for index in range(root_count):
            child = get_child(tree, index)
            if child:
                elements.append(_structure_element_summary(child, count_element_children, get_element_child, get_type))
        return StructureTreeEvidence(
            available=True,
            node_count=_count_structure_nodes(elements),
            raw_summary={"root_count": root_count, "elements": elements},
        )
    except Exception:
        return StructureTreeEvidence(available=False)
    finally:
        if tree:
            try:
                close_tree(tree)
            except Exception:
                pass


def _structure_element_summary(
    element: Any,
    count_children: Any,
    get_child: Any,
    get_type: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if get_type is not None:
        result["type"] = _read_pdfium_wide_string(get_type, element)
    marked_content = getattr(pdfium_c, "FPDF_StructElement_GetMarkedContentID", None)
    if marked_content is not None:
        try:
            result["marked_content_id"] = int(marked_content(element))
        except Exception:
            pass
    children: list[dict[str, Any]] = []
    if count_children is not None and get_child is not None:
        try:
            child_count = max(0, int(count_children(element)))
            for index in range(child_count):
                child = get_child(element, index)
                if child:
                    children.append(_structure_element_summary(child, count_children, get_child, get_type))
        except Exception:
            pass
    if children:
        result["children"] = children
    return result


def _read_pdfium_wide_string(function: Any, handle: Any) -> str | None:
    try:
        size = int(function(handle, None, 0))
        if size <= 0:
            return None
        buffer = ctypes.create_string_buffer(size)
        function(handle, buffer, size)
        return bytes(buffer.raw[:size]).decode("utf-16-le", errors="replace").rstrip("\x00")
    except Exception:
        return None


def _with_structure_tree(objects: NativeObjectEvidence, structure_tree: StructureTreeEvidence) -> NativeObjectEvidence:
    capabilities = dict(objects.capabilities)
    capabilities["structure_tree"] = structure_tree.available
    return NativeObjectEvidence(
        images=objects.images,
        paths=objects.paths,
        annotations=objects.annotations,
        structure_tree=structure_tree,
        page_bbox=objects.page_bbox,
        crop_bbox=objects.crop_bbox,
        rotation=objects.rotation,
        objects=objects.objects,
        media_bbox=objects.media_bbox,
        coordinate_origin=objects.coordinate_origin,
        capabilities=capabilities,
    )


def _has_raw_function(name: str) -> bool:
    return pdfium_c is not None and hasattr(pdfium_c, name)


def _constant(name: str, fallback: int) -> int:
    value = getattr(pdfium_c, name, fallback) if pdfium_c is not None else fallback
    try:
        return int(value)
    except Exception:
        return fallback


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count_structure_nodes(elements: list[dict[str, Any]]) -> int:
    return sum(1 + _count_structure_nodes(item.get("children", [])) for item in elements)
