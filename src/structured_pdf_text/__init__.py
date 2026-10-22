"""Compatible public exports, resolved only when requested."""
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import PdfTextExtractor
    from .config import (
        ExtractionMode,
        ExtractorConfig,
        OcrQualityPolicy,
        OcrQualityThresholds,
        SecurityLimits,
        best_extraction_config,
        max_quality_extraction_config,
    )
    from .errors import (
        ExtractionError,
        ExtractionCancelled,
        FatalExtractionError,
        PaddleOcrUnavailable,
        RequiredRuntimeUnavailableError,
        ResourceExhaustedExtractionError,
    )
    from .ocr.pipeline.cancellation import CancellationToken
    from .document import (
        NativeCharacter,
        NativeObjectEvidence,
        NativePageEvidence,
        StructuredDocument,
        StructuredPage,
    )

__all__ = ['ExtractionMode',
 'ExtractorConfig',
 'OcrQualityPolicy',
 'OcrQualityThresholds',
 'PdfTextExtractor',
 'SecurityLimits',
 'best_extraction_config',
 'max_quality_extraction_config',
 'ExtractionError',
 'ExtractionCancelled',
 'CancellationToken',
 'FatalExtractionError',
 'PaddleOcrUnavailable',
 'RequiredRuntimeUnavailableError',
 'ResourceExhaustedExtractionError',
 'NativeCharacter',
 'NativeObjectEvidence',
 'NativePageEvidence',
 'StructuredDocument',
 'StructuredPage']

_EXPORTS = {'PdfTextExtractor': ('.api', 'PdfTextExtractor'),
 'ExtractionMode': ('.config', 'ExtractionMode'),
 'ExtractorConfig': ('.config', 'ExtractorConfig'),
 'OcrQualityPolicy': ('.config', 'OcrQualityPolicy'),
 'OcrQualityThresholds': ('.config', 'OcrQualityThresholds'),
 'SecurityLimits': ('.config', 'SecurityLimits'),
 'best_extraction_config': ('.config', 'best_extraction_config'),
 'max_quality_extraction_config': ('.config', 'max_quality_extraction_config'),
 'ExtractionError': ('.errors', 'ExtractionError'),
 'ExtractionCancelled': ('.errors', 'ExtractionCancelled'),
 'CancellationToken': ('.ocr.pipeline.cancellation', 'CancellationToken'),
 'FatalExtractionError': ('.errors', 'FatalExtractionError'),
 'PaddleOcrUnavailable': ('.errors', 'PaddleOcrUnavailable'),
 'RequiredRuntimeUnavailableError': ('.errors', 'RequiredRuntimeUnavailableError'),
 'ResourceExhaustedExtractionError': ('.errors', 'ResourceExhaustedExtractionError'),
 'NativeCharacter': ('.document', 'NativeCharacter'),
 'NativeObjectEvidence': ('.document', 'NativeObjectEvidence'),
 'NativePageEvidence': ('.document', 'NativePageEvidence'),
 'StructuredDocument': ('.document', 'StructuredDocument'),
 'StructuredPage': ('.document', 'StructuredPage')}


def __getattr__(name: str):
    try:
        module, symbol = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module, __name__), symbol)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
