"""Versioned metadata transport for the isolated Paddle runtime."""
from dataclasses import asdict

from structured_pdf_text.document import OcrProvenance, OcrToken, SourceKind
from structured_pdf_text.geometry import BBox, Point


# Both sides are shipped together. Version 5 adds detector geometry, structured
# provenance, operation diagnostics and typed fatal-error details to version 4.
PROTOCOL_VERSION = 5


def serialize_tokens(tokens):
    return [
        {
            "text": token.text,
            "confidence": float(token.confidence) if token.confidence is not None else None,
            "bbox": [token.bbox.x0, token.bbox.y0, token.bbox.x1, token.bbox.y1],
            "source": token.source.value,
            "language": token.language,
            "rotation": token.rotation,
            "provenance": token.provenance,
            "polygon": [[point.x, point.y] for point in token.polygon] if token.polygon is not None else None,
            "level": token.level,
            "ocr_provenance": asdict(token.ocr_provenance) if token.ocr_provenance is not None else None,
        }
        for token in tokens
    ]


def deserialize_tokens(payload, *, language):
    tokens = []
    for item in payload:
        raw_provenance = item.get("ocr_provenance")
        provenance = None
        if raw_provenance is not None:
            raw_provenance = dict(raw_provenance)
            raw_provenance["preprocessing"] = tuple(raw_provenance.get("preprocessing", ()))
            provenance = OcrProvenance(**raw_provenance)
        tokens.append(OcrToken(
            text=item["text"], bbox=BBox(*item["bbox"]),
            confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            source=SourceKind(item.get("source", "ocr_page")),
            language=item.get("language") or language,
            rotation=int(item.get("rotation", 0)), provenance=item.get("provenance"),
            polygon=tuple(Point(*point) for point in item["polygon"]) if item.get("polygon") is not None else None,
            level=item.get("level", "line"), ocr_provenance=provenance,
        ))
    return tokens
