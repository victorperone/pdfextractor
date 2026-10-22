"""Compatible public exports, resolved only when requested."""
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import OcrEngine
    from .pipeline.cancellation import CancellationToken
    from .paddle import PaddleOcrEngine, PaddleOcrUnavailable
    from .render import render_page
    from .reconstruct import dehyphenate_ocr_lines, merge_ocr_bullet_markers, reconstruct_ocr_lines, segment_ocr_paragraphs
    from .recovery import (
        OcrRegionRefiner,
        RegionRefinementAttempt,
        RegionRefinementGoal,
        RegionRefinementRequest,
        RegionRefinementResult,
        recover_ocr_tokens,
    )
    from .critical_data import CriticalDataRefiner, DataType, get_allowlist

__all__ = ['OcrEngine',
 'CancellationToken',
 'PaddleOcrEngine',
 'PaddleOcrUnavailable',
 'OcrRegionRefiner',
 'RegionRefinementAttempt',
 'RegionRefinementGoal',
 'RegionRefinementRequest',
 'RegionRefinementResult',
 'CriticalDataRefiner',
 'DataType',
 'dehyphenate_ocr_lines',
 'get_allowlist',
 'merge_ocr_bullet_markers',
 'reconstruct_ocr_lines',
 'segment_ocr_paragraphs',
 'recover_ocr_tokens',
 'render_page']

_EXPORTS = {'OcrEngine': ('.engine', 'OcrEngine'),
 'CancellationToken': ('.pipeline.cancellation', 'CancellationToken'),
 'PaddleOcrEngine': ('.paddle', 'PaddleOcrEngine'),
 'PaddleOcrUnavailable': ('.paddle', 'PaddleOcrUnavailable'),
 'render_page': ('.render', 'render_page'),
 'dehyphenate_ocr_lines': ('.reconstruct', 'dehyphenate_ocr_lines'),
 'merge_ocr_bullet_markers': ('.reconstruct', 'merge_ocr_bullet_markers'),
 'reconstruct_ocr_lines': ('.reconstruct', 'reconstruct_ocr_lines'),
 'segment_ocr_paragraphs': ('.reconstruct', 'segment_ocr_paragraphs'),
 'OcrRegionRefiner': ('.recovery', 'OcrRegionRefiner'),
 'RegionRefinementAttempt': ('.recovery', 'RegionRefinementAttempt'),
 'RegionRefinementGoal': ('.recovery', 'RegionRefinementGoal'),
 'RegionRefinementRequest': ('.recovery', 'RegionRefinementRequest'),
 'RegionRefinementResult': ('.recovery', 'RegionRefinementResult'),
 'recover_ocr_tokens': ('.recovery', 'recover_ocr_tokens'),
 'CriticalDataRefiner': ('.critical_data', 'CriticalDataRefiner'),
 'DataType': ('.critical_data', 'DataType'),
 'get_allowlist': ('.critical_data', 'get_allowlist')}


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
