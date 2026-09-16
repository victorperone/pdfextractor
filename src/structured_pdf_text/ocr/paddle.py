from __future__ import annotations

import json
import os
from numbers import Real
from pathlib import Path
from typing import Any, Callable

from structured_pdf_text.document import OcrToken, SourceKind
from structured_pdf_text.geometry import BBox


class PaddleOcrUnavailable(RuntimeError):
    """Raised when the optional PaddleOCR runtime is not installed."""


_LOCAL_MODEL_DIRECTORIES = {
    "doc_orientation_classify_model_dir": "PP-LCNet_x1_0_doc_ori",
    "textline_orientation_model_dir": "PP-LCNet_x1_0_textline_ori",
    "text_detection_model_dir": "PP-OCRv5_server_det",
    "text_recognition_model_dir": "latin_PP-OCRv5_mobile_rec",
}


def _local_model_root(
    cache_home: str | Path,
) -> Path:
    return (
        Path(cache_home)
        .expanduser()
        .resolve()
        / "official_models"
    )


def _resolve_required_local_models(
    cache_home: str | Path,
    *,
    language: str,
    options: dict[str, Any],
) -> dict[str, str]:
    root = _local_model_root(cache_home)

    resolved = dict(options)

    resolved.setdefault(
        "doc_orientation_classify_model_dir",
        str(
            root
            / "PP-LCNet_x1_0_doc_ori"
        ),
    )

    resolved.setdefault(
        "textline_orientation_model_dir",
        str(
            root
            / "PP-LCNet_x1_0_textline_ori"
        ),
    )

    resolved.setdefault(
        "text_detection_model_dir",
        str(
            root
            / "PP-OCRv5_server_det"
        ),
    )

    if language == "pt":
        resolved.setdefault(
            "text_recognition_model_dir",
            str(
                root
                / "latin_PP-OCRv5_mobile_rec"
            ),
        )

    required_keys = (
        "doc_orientation_classify_model_dir",
        "textline_orientation_model_dir",
        "text_detection_model_dir",
        "text_recognition_model_dir",
    )

    missing: list[str] = []

    for key in required_keys:
        value = resolved.get(key)

        if not value:
            missing.append(
                f"{key}=<not configured>"
            )
            continue

        model_dir = (
            Path(str(value))
            .expanduser()
        )

        if (
            not model_dir.is_dir()
            or not any(model_dir.iterdir())
        ):
            missing.append(
                f"{key}={model_dir}"
            )

    if missing:
        details = "\n".join(
            f"  - {item}"
            for item in missing
        )

        raise PaddleOcrUnavailable(
            "Local OCR model setup is incomplete.\n"
            "Runtime model downloads are disabled.\n"
            "Missing or empty model directories:\n"
            f"{details}\n"
            "Install the OCR models while online "
            "before extracting PDFs."
        )

    return {
        key: str(
            Path(str(resolved[key]))
            .expanduser()
            .resolve()
        )
        for key in required_keys
    }


def validate_local_ocr_models(
    *,
    language: str = "pt",
    cache_home: str | Path | None = None,
) -> None:
    """Verify that all required local OCR model directories are present.

    Does not import paddle, paddleocr, or open any network connection.
    Raises PaddleOcrUnavailable if any model directory is missing or empty.
    """
    effective_cache = cache_home or os.environ.get(
        "PADDLE_PDX_CACHE_HOME",
        str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
    )
    _resolve_required_local_models(
        effective_cache,
        language=language,
        options={},
    )


class PaddleOcrEngine:
    """Lazy PaddleOCR adapter with page-coordinate token output.

    PaddleOCR is intentionally optional. Importing this module does not load
    PaddlePaddle or download models; that only happens when the first page is
    recognized. The adapter accepts both the legacy ``ocr`` result shape and
    the newer ``predict`` result fields used by PaddleOCR 3.x.
    """

    def __init__(self, language: str = "pt", num_threads: int = 0, **options: Any) -> None:
        self.language = language
        self.num_threads = num_threads
        self.cache_home = options.pop("cache_home", None) or os.environ.get(
            "PADDLE_PDX_CACHE_HOME",
            str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
        )
        self.options = options
        self.batch_size = max(1, int(options.pop("ocr_batch_size", 3)))
        self.quality_variants = bool(options.pop("quality_variants", True))
        self._ocr: Any | None = None
        self._init_error: Exception | None = None
        self.last_pass_count = 0
        self.last_batch_count = 0
        self.last_deskew_angle: float = 0.0

    def recognize_page(
        self,
        page_image: object,
        page_index: int,
        page_bbox: BBox | None = None,
        *,
        quality_variants: bool | None = None,
    ) -> list[OcrToken]:
        if quality_variants is None:
            quality_variants = self.quality_variants
        self.last_pass_count = 0
        self.last_batch_count = 0
        ocr = self._get_ocr()
        page_image, self.last_deskew_angle = _deskew_image(page_image)
        raw = self._predict_counted(ocr, page_image)
        tokens = _tokens_from_result(raw, page_index, page_image, page_bbox)
        # F25: when the first pass returns nothing, try all four orientations
        # before giving up. A sideways or upside-down scan would otherwise
        # produce an empty result with no rotation fallback.
        if not tokens:
            rotation_candidates = [
                (90, _counterclockwise_box_to_original),
                (180, _half_turn_box_to_original),
                (270, _clockwise_box_to_original),
            ]
        else:
            rotation_candidates = _rotation_candidates(tokens, page_image, page_bbox)
        if not rotation_candidates:
            candidates = [tokens]
            if quality_variants:
                enhanced_images = _enhancement_variants(page_image)
                for enhanced, raw_enhanced in zip(
                    enhanced_images,
                    self._predict_many_counted(ocr, enhanced_images),
                ):
                    candidates.append(
                        _tokens_from_result(
                            raw_enhanced,
                            page_index,
                            enhanced,
                            page_bbox,
                        )
                    )
            return _select_best_candidate(candidates, page_bbox)

        original_size = _image_size(page_image)
        candidates = [tokens]
        rotated_candidates: list[
            tuple[
                list[OcrToken],
                object,
                Callable[[float, float, float, float, int, int], tuple[float, float, float, float]],
                int,
            ]
        ] = []
        for angle, transform in rotation_candidates:
            rotated = _rotate_image(page_image, angle)
            if rotated is None:
                continue
            rotated_tokens = _tokens_from_result(
                self._predict_counted(ocr, rotated),
                page_index,
                rotated,
                page_bbox,
                coordinate_size=original_size,
                box_transform=transform,
                rotation=angle,
            )
            candidates.append(rotated_tokens)
            rotated_candidates.append((rotated_tokens, rotated, transform, angle))
            if quality_variants:
                enhanced_images = _enhancement_variants(rotated)
                for enhanced, raw_enhanced in zip(
                    enhanced_images,
                    self._predict_many_counted(ocr, enhanced_images),
                ):
                    candidates.append(
                        _tokens_from_result(
                            raw_enhanced,
                            page_index,
                            enhanced,
                            page_bbox,
                            coordinate_size=original_size,
                            box_transform=transform,
                            rotation=angle,
                        )
                    )
        best = _select_best_candidate(candidates, page_bbox)
        best_quality = _candidate_quality(best, page_bbox)
        # A global rotation can produce nearly identical confidence while
        # recovering additional lines. Prefer that fuller candidate only when
        # it is within the normal confidence noise of the current winner.
        for candidate in candidates:
            if len(candidate) > len(best) and _candidate_quality(candidate, page_bbox) >= best_quality - 0.0025:
                best = candidate
                best_quality = _candidate_quality(candidate, page_bbox)
        return best

    def _predict_counted(self, ocr: Any, page_image: object) -> Any:
        self.last_pass_count += 1
        self.last_batch_count += 1
        return self._predict(ocr, page_image)

    def _predict_many_counted(self, ocr: Any, images: list[object]) -> list[Any]:
        """Predict quality variants in bounded batches while preserving order."""
        outputs: list[Any] = []
        for start in range(0, len(images), self.batch_size):
            chunk = images[start : start + self.batch_size]
            self.last_pass_count += len(chunk)
            self.last_batch_count += 1
            try:
                raw = self._predict(ocr, chunk)
                raw_items = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, (dict, str, bytes)) else [raw]
                if len(raw_items) == len(chunk):
                    outputs.extend(raw_items)
                    continue
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
            # Some older PaddleOCR backends do not support list input.
            for image in chunk:
                self.last_batch_count += 1
                outputs.append(self._predict(ocr, image))
        return outputs

    def recognize_region(
        self,
        page_image: object,
        page_index: int,
        region_bbox: BBox,
    ) -> list[OcrToken]:
        image = _crop_image(page_image, region_bbox)
        return self.recognize_page(image, page_index, region_bbox)

    def _get_ocr(self) -> Any:
        if self._ocr is not None:
            return self._ocr

        if self._init_error is not None:
            raise self._init_error

        cache_home = (
            Path(self.cache_home)
            .expanduser()
            .resolve()
        )

        # Runtime policy:
        # extraction must never perform model-source discovery.
        os.environ[
            "PADDLE_PDX_CACHE_HOME"
        ] = str(cache_home)

        os.environ[
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"
        ] = "True"

        options = {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
            "enable_mkldnn": False,
            **self.options,
        }

        try:
            local_models = (
                _resolve_required_local_models(
                    cache_home,
                    language=self.language,
                    options=options,
                )
            )
        except Exception as exc:
            self._init_error = exc
            raise

        options.update(
            local_models
        )

        options[
            "doc_orientation_classify_model_name"
        ] = "PP-LCNet_x1_0_doc_ori"

        options[
            "textline_orientation_model_name"
        ] = "PP-LCNet_x1_0_textline_ori"

        options[
            "text_detection_model_name"
        ] = "PP-OCRv5_server_det"

        options[
            "text_recognition_model_name"
        ] = "latin_PP-OCRv5_mobile_rec"

        effective_threads = self.num_threads

        if effective_threads > 0:
            options.setdefault(
                "cpu_threads",
                effective_threads,
            )

            try:
                import paddle

                paddle.set_num_threads(
                    effective_threads
                )
            except (
                ImportError,
                AttributeError,
            ):
                pass

        # Import only after local model validation and
        # after disabling remote model-source checks.
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            error = PaddleOcrUnavailable(
                "PaddleOCR is not installed; "
                "install the OCR dependencies "
                "during environment setup."
            )

            self._init_error = error
            raise error from exc

        try:
            self._ocr = PaddleOCR(
                **options
            )
        except Exception as exc:
            self._init_error = exc
            raise

        return self._ocr

    @staticmethod
    def _predict(ocr: Any, page_image: object) -> Any:
        input_image = page_image
        if isinstance(page_image, (list, tuple)):
            try:
                import numpy as np

                input_image = [
                    np.asarray(item) if hasattr(item, "mode") else item
                    for item in page_image
                ]
            except ImportError:
                pass
        elif hasattr(page_image, "mode"):
            try:
                import numpy as np

                input_image = np.asarray(page_image)
            except ImportError:
                pass
        if hasattr(ocr, "predict"):
            return ocr.predict(input=input_image)
        return ocr.ocr(input_image, cls=True)


def _tokens_from_result(
    raw: Any,
    page_index: int,
    image: object,
    page_bbox: BBox | None,
    coordinate_size: tuple[int, int] | None = None,
    box_transform: Callable[[float, float, float, float, int, int], tuple[float, float, float, float]] | None = None,
    rotation: int = 0,
) -> list[OcrToken]:
    width, height = _image_size(image)
    coordinate_width, coordinate_height = coordinate_size or (width, height)
    target = page_bbox or BBox(0.0, 0.0, float(coordinate_width), float(coordinate_height))
    scale_x = target.width / max(float(coordinate_width), 1.0)
    scale_y = target.height / max(float(coordinate_height), 1.0)
    records = _records(raw)
    tokens: list[OcrToken] = []
    record_rotation = _dominant_record_rotation(records)
    effective_rotation = rotation or record_rotation
    for text, confidence, box, _ in records:
        if not text or box is None:
            continue
        x0, y0, x1, y1 = _box_coordinates(box)
        if box_transform is not None:
            x0, y0, x1, y1 = box_transform(
                x0,
                y0,
                x1,
                y1,
                coordinate_width,
                coordinate_height,
            )
        bbox = BBox(
            target.x0 + x0 * scale_x,
            target.y0 + y0 * scale_y,
            target.x0 + x1 * scale_x,
            target.y0 + y1 * scale_y,
        )
        tokens.append(
            OcrToken(
                text=str(text),
                bbox=bbox,
                confidence=float(confidence) if confidence is not None else None,
                language=None,
                source=SourceKind.OCR_PAGE,
                rotation=effective_rotation,
            )
        )
    return tokens


def _rotation_candidates(
    tokens: list[OcrToken], image: object, page_bbox: BBox | None = None,
) -> list[tuple[int, Callable[[float, float, float, float, int, int], tuple[float, float, float, float]]]]:
    """Return corrective rotations suggested by geometry and text-line orientation."""
    if not tokens:
        return []
    width, height = _image_size(image)
    if width <= 0 or height <= 0:
        return []
    vertical = sum(
        1
        for token in tokens
        if token.bbox.height > max(token.bbox.width * 1.35, 4.0)
    )
    # Small OCR pages may contain only a handful of lines. Requiring six
    # vertical boxes would miss those pages, while the ratio keeps ordinary
    # portrait scans from being rotated speculatively.
    is_vertical = vertical >= 3 and vertical / len(tokens) >= 0.15
    dominant_rotation = _dominant_token_rotation(tokens)
    candidates: list[tuple[int, Callable[[float, float, float, float, int, int], tuple[float, float, float, float]]]] = []
    if is_vertical:
        candidates.extend(
            (
                (90, _counterclockwise_box_to_original),
                (270, _clockwise_box_to_original),
            )
        )
    elif dominant_rotation == 180:
        candidates.append((180, _half_turn_box_to_original))
    elif page_bbox is not None and _edge_order_is_incoherent(tokens, page_bbox):
        candidates.append((180, _half_turn_box_to_original))
    return candidates


def _edge_order_is_incoherent(tokens: list[OcrToken], page_bbox: BBox) -> bool:
    """Detect likely upside-down OCR when orientation metadata is absent."""
    return _candidate_quality(tokens, page_bbox) < _ocr_quality(tokens) - 0.05


def _dominant_token_rotation(tokens: list[OcrToken]) -> int:
    counts: dict[int, int] = {}
    for token in tokens:
        rotation = token.rotation % 360
        if rotation in (90, 180, 270):
            counts[rotation] = counts.get(rotation, 0) + 1
    if not counts:
        return 0
    rotation, count = max(counts.items(), key=lambda item: item[1])
    return rotation if count / max(len(tokens), 1) >= 0.60 else 0


def _ocr_quality(tokens: list[OcrToken]) -> float:
    if not tokens:
        return 0.0
    confidences = [token.confidence for token in tokens if token.confidence is not None]
    average = sum(confidences) / len(confidences) if confidences else 0.0
    horizontal = sum(
        1
        for token in tokens
        if token.bbox.width >= token.bbox.height * 1.15
    ) / len(tokens)
    return average + 0.05 * horizontal


def _select_best_candidate(
    candidates: list[list[OcrToken]], page_bbox: BBox | None = None,
) -> list[OcrToken]:
    if not candidates:
        return []
    best = max(candidates, key=lambda candidate: _candidate_quality(candidate, page_bbox))
    best_quality = _candidate_quality(best, page_bbox)
    # Preserve fuller detections when confidence is within normal OCR noise.
    for candidate in candidates:
        if len(candidate) > len(best) and _candidate_quality(candidate, page_bbox) >= best_quality - 0.0025:
            best = candidate
            best_quality = _candidate_quality(candidate, page_bbox)
    return _merge_compact_candidate_tokens(best, candidates)


def _merge_compact_candidate_tokens(
    best: list[OcrToken], candidates: list[list[OcrToken]],
) -> list[OcrToken]:
    """Recover compact high-confidence tokens split by a noisier candidate.

    Raster noise can make one value become several adjacent OCR tokens (for
    example ``R$`` + ``3`` + ``8`` + ``36,00``). A thresholded candidate may
    recognize the same region as one exact token while being unusable
    elsewhere. Merge only that narrow case; the selected full-page candidate
    remains authoritative for ordinary text.
    """
    merged = list(best)
    for candidate in candidates:
        if candidate is best:
            continue
        for token in candidate:
            if token.confidence is None or token.confidence < 0.97:
                continue
            text = " ".join(token.text.split())
            if len(text) < 5 and not any(character.isdigit() for character in text):
                continue
            overlaps = [
                index
                for index, existing in enumerate(merged)
                if _candidate_token_overlap(existing, token)
            ]
            if len(overlaps) < 2:
                continue
            existing_text = "".join(
                word
                for index in overlaps
                for word in merged[index].text.split()
            )
            if len(text) < max(5, len(existing_text) - 2):
                continue
            merged = [
                existing
                for index, existing in enumerate(merged)
                if index not in overlaps
            ]
            merged.append(token)
    return _remove_contained_fragments(merged)


def _candidate_token_overlap(existing: OcrToken, candidate: OcrToken) -> bool:
    if existing.bbox.iou(candidate.bbox) >= 0.15:
        return True
    horizontal = max(
        0.0,
        min(existing.bbox.x1, candidate.bbox.x1)
        - max(existing.bbox.x0, candidate.bbox.x0),
    )
    vertical = max(
        0.0,
        min(existing.bbox.y1, candidate.bbox.y1)
        - max(existing.bbox.y0, candidate.bbox.y0),
    )
    return (
        horizontal >= existing.bbox.width * 0.50
        and vertical >= min(existing.bbox.height, candidate.bbox.height) * 0.50
    )


def _remove_contained_fragments(tokens: list[OcrToken]) -> list[OcrToken]:
    """Drop short OCR fragments contained by a longer replacement token."""
    keep = [True] * len(tokens)
    for index, token in enumerate(tokens):
        token_text = "".join(token.text.split())
        for other_index, other in enumerate(tokens):
            if index == other_index or not keep[other_index]:
                continue
            other_text = "".join(other.text.split())
            if len(token_text) < len(other_text) + 3:
                continue
            if (
                token.bbox.x0 <= other.bbox.x0 + 1.0
                and token.bbox.x1 >= other.bbox.x1 - 1.0
                and token.bbox.y0 <= other.bbox.y0 + max(1.0, other.bbox.height * 0.35)
                and token.bbox.y1 >= other.bbox.y1 - max(1.0, other.bbox.height * 0.35)
            ):
                keep[other_index] = False
    return [token for index, token in enumerate(tokens) if keep[index]]


def _candidate_quality(tokens: list[OcrToken], page_bbox: BBox | None) -> float:
    """Score OCR confidence together with generic page-edge coherence."""
    score = _ocr_quality(tokens)
    if not tokens or page_bbox is None:
        return score
    try:
        from .reconstruct import reconstruct_ocr_lines

        lines = reconstruct_ocr_lines(tokens, 0, page_bbox)
    except (ImportError, TypeError, ValueError):
        return score
    if len(lines) < 2:
        return score
    top_band = " ".join(line.text for line in lines[:2]).casefold()
    bottom_band = " ".join(line.text for line in lines[-2:]).casefold()
    header_markers = ("header", "cabeçalho")
    footer_markers = ("footer", "rodapé", "rodape", "página ", "pagina ", "page ")
    header_marker = any(marker in top_band for marker in header_markers)
    footer_marker = any(marker in bottom_band for marker in footer_markers)
    misplaced_footer = any(marker in top_band for marker in footer_markers)
    misplaced_header = any(marker in bottom_band for marker in header_markers)
    if header_marker:
        score += 0.05
    if footer_marker:
        score += 0.10
    if misplaced_footer:
        score -= 0.15
    if misplaced_header:
        score -= 0.10
    return score


def _enhancement_variants(image: object) -> list[object]:
    """Return quality-oriented image variants with the original geometry."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        pil_image = image if hasattr(image, "mode") else Image.fromarray(image)
        if pil_image.mode not in ("L", "RGB"):
            pil_image = pil_image.convert("RGB")
        gray = ImageOps.grayscale(pil_image).convert("RGB")
        variants = [
            ImageOps.autocontrast(gray),
            ImageEnhance.Sharpness(pil_image).enhance(2.0),
            ImageEnhance.Contrast(pil_image).enhance(1.5),
            pil_image.filter(ImageFilter.UnsharpMask(radius=1, percent=140, threshold=3)),
        ]
        # Keep every variant at the same dimensions so OCR boxes retain the
        # original page coordinate transform. These are especially useful for
        # low contrast scans and synthetic raster noise.
        try:
            import cv2
            import numpy as np

            gray_array = np.asarray(ImageOps.grayscale(pil_image))
            denoised = cv2.medianBlur(gray_array, 3)
            _, otsu = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            clahe_filter = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            clahe_applied = clahe_filter.apply(gray_array)
            variants.extend(
                [
                    Image.fromarray(denoised).convert("RGB"),
                    Image.fromarray(otsu).convert("RGB"),
                    Image.fromarray(clahe_applied).convert("RGB"),
                ]
            )
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
        return variants
    except (ImportError, TypeError, ValueError):
        return []


def _rotate_image(image: object, angle: int) -> object | None:
    if hasattr(image, "rotate"):
        return image.rotate(angle, expand=True)
    try:
        from PIL import Image

        return Image.fromarray(image).rotate(angle, expand=True)
    except (ImportError, TypeError, ValueError):
        return None


def _clockwise_box_to_original(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    original_width: int,
    original_height: int,
) -> tuple[float, float, float, float]:
    """Map a box from a clockwise-rotated image back to source coordinates."""
    return y0, original_height - x1, y1, original_height - x0


def _counterclockwise_box_to_original(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    original_width: int,
    original_height: int,
) -> tuple[float, float, float, float]:
    """Map a box from a counterclockwise-rotated image to source coordinates."""
    return original_width - y1, x0, original_width - y0, x1


def _half_turn_box_to_original(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    original_width: int,
    original_height: int,
) -> tuple[float, float, float, float]:
    """Map a box from a 180-degree-rotated image back to source coordinates."""
    return original_width - x1, original_height - y1, original_width - x0, original_height - y0


def _records(raw: Any) -> list[tuple[str, float | None, Any, int]]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        if len(raw) == 1 and isinstance(raw[0], (list, tuple)):
            nested = _records(raw[0])
            if nested:
                return nested
        records: list[tuple[str, float | None, Any, int]] = []
        for item in raw:
            if isinstance(item, dict):
                records.extend(_records(item))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                box = item[0]
                payload = item[1]
                if isinstance(payload, (list, tuple)) and payload:
                    records.append(
                        (
                            str(payload[0]),
                            payload[1] if len(payload) > 1 else None,
                            box,
                            _as_rotation(payload[2]) if len(payload) > 2 else 0,
                        )
                    )
        return records
    if hasattr(raw, "json"):
        value = raw.json
        value = value() if callable(value) else value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        return _records(value)
    if isinstance(raw, dict):
        if "res" in raw:
            return _records(raw["res"])
        texts = _field(raw, "rec_texts", "texts")
        scores = _field(raw, "rec_scores", "scores")
        boxes = _field(raw, "rec_boxes", "rec_polys", "boxes", "polys")
        angles = _field(raw, "textline_orientation_angles", "orientation_angles", "angles")
        texts = [] if texts is None else texts
        scores = [] if scores is None else scores
        boxes = [] if boxes is None else boxes
        angles = [] if angles is None else angles
        return [
            (
                str(text),
                _as_float(scores[index]) if index < len(scores) else None,
                boxes[index],
                _as_rotation(angles[index]) if index < len(angles) else 0,
            )
            for index, text in enumerate(texts)
            if index < len(boxes)
        ]
    if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes)):
        return _records(list(raw))
    return []


def _dominant_record_rotation(records: list[tuple[str, float | None, Any, int]]) -> int:
    usable = [rotation for text, confidence, _, rotation in records if text and (confidence is None or confidence >= 0.50)]
    if not usable:
        return 0
    upside_down = sum(rotation == 180 for rotation in usable)
    return 180 if upside_down / len(usable) >= 0.60 else 0


def _as_rotation(value: Any) -> int:
    try:
        return 180 if int(value) == 1 else 0
    except (TypeError, ValueError):
        return 180 if "180" in str(value) else 0


def _field(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return None


def _box_coordinates(box: Any) -> tuple[float, float, float, float]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    if len(box) == 4 and all(isinstance(value, Real) for value in box):
        return float(box[0]), float(box[1]), float(box[2]), float(box[3])
    points = [point for point in box if len(point) >= 2]
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _image_size(image: object) -> tuple[int, int]:
    if hasattr(image, "size") and isinstance(image.size, tuple):
        return int(image.size[0]), int(image.size[1])
    shape = getattr(image, "shape", None)
    if shape is not None and len(shape) >= 2:
        return int(shape[1]), int(shape[0])
    return int(getattr(image, "width")), int(getattr(image, "height"))


def _crop_image(image: object, region_bbox: BBox) -> object:
    if hasattr(image, "crop"):
        return image.crop((region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1))
    raise TypeError("PaddleOcrEngine region recognition requires a crop-capable image")


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _deskew_image(
    image: object,
    min_angle_deg: float = 0.5,
    max_angle_deg: float = 20.0,
) -> tuple[object, float]:
    """Detecta e corrige inclinação pequena em imagens de página (scan ou foto).

    Cobre todas as 4 orientações base (0/90/180/270°) porque cv2.minAreaRect
    detecta o desvio em relação ao eixo mais próximo, não só ao horizontal.

    Retorna (imagem_corrigida, angulo_aplicado).
    angulo_aplicado == 0.0 significa que nenhuma correção foi aplicada.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps

        pil = image if hasattr(image, "mode") else Image.fromarray(image)
        gray = np.asarray(ImageOps.grayscale(pil))

        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        coords = np.column_stack(np.where(closed > 0))
        if len(coords) < 50:
            return image, 0.0

        coords_xy = coords[:, ::-1].astype(np.float32)

        angle = cv2.minAreaRect(coords_xy)[-1]

        if angle < -45.0:
            angle = 90.0 + angle

        if abs(angle) < min_angle_deg:
            return image, 0.0
        if abs(angle) > max_angle_deg:
            return image, 0.0

        h, w = gray.shape
        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -angle, 1.0)
        rgb_array = np.asarray(pil.convert("RGB"))
        corrected = cv2.warpAffine(
            rgb_array,
            M,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return Image.fromarray(corrected), round(angle, 2)

    except Exception:  # noqa: BLE001
        return image, 0.0
