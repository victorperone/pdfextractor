"""Explicit conversion of legacy domain and raw benchmark tokens."""
from dataclasses import asdict

from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox, Point
from structured_pdf_text.ocr.contracts import OCRToken
from .contracts import TokenEvidence
from .geometry import RasterFrame


def from_domain(token: OcrToken, *, token_id: str, authorization_id: str, attempt_id: str) -> TokenEvidence:
    return TokenEvidence(
        token_id, authorization_id, attempt_id, token.text, token.bbox,
        confidence_normalized=token.confidence,
        normalization="domain_0..1" if token.confidence is not None else None,
        polygon=token.polygon, language=token.language, level=token.level,
        metadata={"domain": {"source": token.source.value, "rotation": token.rotation,
                             "provenance": token.provenance,
                             "ocr_provenance": asdict(token.ocr_provenance) if token.ocr_provenance else None}},
    )


def to_domain(token: TokenEvidence) -> OcrToken:
    metadata = token.metadata.get("domain", {})
    provenance = metadata.get("ocr_provenance")
    return OcrToken(token.text, token.bbox, token.confidence_normalized, token.language,
                    SourceKind(metadata.get("source", SourceKind.OCR_REGION.value)),
                    rotation=metadata.get("rotation", 0), provenance=metadata.get("provenance"),
                    polygon=token.polygon, level=token.level,
                    ocr_provenance=OcrProvenance(**provenance) if provenance is not None else None)


def from_raw(token: OCRToken, frame: RasterFrame, *, token_id: str, authorization_id: str, attempt_id: str) -> TokenEvidence:
    confidence = token.confidence_native
    conversion = {"0..1": "identity", "0..100": "divide_by_100"}.get(token.confidence_scale)
    normalized = (confidence / 100 if token.confidence_scale == "0..100" else confidence) if conversion and confidence is not None else None
    return TokenEvidence(token_id, authorization_id, attempt_id, token.text,
                         frame.bbox_to_page(BBox(*token.bbox_px)),
                         confidence_native=confidence, confidence_scale=token.confidence_scale,
                         confidence_normalized=normalized, normalization=conversion if normalized is not None else None,
                         polygon=tuple(frame.point_to_page(Point(*p)) for p in token.polygon_px) if token.polygon_px else None,
                         level=token.level, metadata={"raw": {"source_engine": token.source_engine}})


def to_raw(token: TokenEvidence, frame: RasterFrame) -> OCRToken:
    box = frame.page_bbox_to_pixel(token.bbox)
    return OCRToken(token.text, tuple((p.x, p.y) for p in (frame.page_point_to_pixel(p) for p in token.polygon)) if token.polygon else (),
                    (box.x0, box.y0, box.x1, box.y1), token.confidence_native,
                    token.confidence_scale, token.level, token.metadata.get("raw", {}).get("source_engine", "unknown"))
