"""Token fusion: merges native PDF text with OCR results and resolves spatial conflicts."""

from .conflicts import TokenConflict
from .token_fusion import FusionResult, fuse_native_and_ocr, fuse_native_tokens

__all__ = ["FusionResult", "TokenConflict", "fuse_native_and_ocr", "fuse_native_tokens"]
