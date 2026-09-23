"""structured-pdf-text — local, auditable PDF text extraction engine.

Public API entry point.  Import :class:`PdfTextExtractor` and
:func:`best_extraction_config` for typical usage.  All other symbols are
re-exported here for convenience but are also importable from their respective
sub-modules.
"""

from .api import PdfTextExtractor
from .config import (
    ExtractionMode,
    ExtractorConfig,
    OcrQualityPolicy,
    OcrQualityThresholds,
    SecurityLimits,
    best_extraction_config,
)
from .errors import (
    ExtractionError,
    FatalExtractionError,
    PaddleOcrUnavailable,
    RequiredRuntimeUnavailableError,
    ResourceExhaustedExtractionError,
)
from .document import (
    NativeCharacter,
    NativeObjectEvidence,
    NativePageEvidence,
    StructuredDocument,
    StructuredPage,
)

__all__ = [
    "ExtractionMode",
    "ExtractorConfig",
    "OcrQualityPolicy",
    "OcrQualityThresholds",
    "PdfTextExtractor",
    "SecurityLimits",
    "best_extraction_config",
    "ExtractionError",
    "FatalExtractionError",
    "PaddleOcrUnavailable",
    "RequiredRuntimeUnavailableError",
    "ResourceExhaustedExtractionError",
    "NativeCharacter",
    "NativeObjectEvidence",
    "NativePageEvidence",
    "StructuredDocument",
    "StructuredPage",
]
