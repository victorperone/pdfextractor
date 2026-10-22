"""Bounded CPU recovery for an unavailable optional GPU runtime."""
from __future__ import annotations

from functools import wraps

from structured_pdf_text.errors import FatalExtractionError, raise_if_resource_exhausted


def gpu_configuration_failure(exc: BaseException) -> bool:
    """Recognize GPU dependency/configuration failures, preserving fatal errors."""
    raise_if_resource_exhausted(exc, stage="ocr_gpu")
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, FatalExtractionError):
            return False
        message = str(current).lower()
        gpu = any(word in message for word in (
            "cuda", "cudnn", "cublas", "cufft", "cusolver", "cusparse",
            "nvrtc", "nvjitlink", "nvidia", "gpu", "libtorch_cuda",
        ))
        configuration = any(word in message for word in (
            "not found", "cannot", "could not", "failed", "unavailable",
            "not available", "not compiled", "not supported", "unsupported",
            "missing", "no kernel", "invalid device", "driver", "dll", "error",
        ))
        if gpu and configuration:
            return True
        current = current.__cause__ or current.__context__
    return False


def retry_gpu_configuration_on_cpu(method):
    """Retry the same operation once after an engine-local CPU transition."""
    @wraps(method)
    def run(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:
            if not self._fallback_to_cpu(exc):
                raise
            return method(self, *args, **kwargs)
    return run
