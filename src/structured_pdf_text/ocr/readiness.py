"""Static and deep readiness probes for OCR deployments.

Static probes inspect installed packages, executables, model paths and model
hashes without constructing an inference runtime. Deep probes instantiate the
selected backend and run a small image through it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from structured_pdf_text.config import ExtractorConfig


class ReadinessStatus(str, Enum):
    READY = "ready"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    CORRUPT = "corrupt"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    status: ReadinessStatus
    reason_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def probe_static(config: ExtractorConfig, *, cache_home: str | Path | None = None) -> ReadinessResult:
    """Inspect readiness without loading an OCR model or inference runtime."""
    engine = config.ocr_engine
    if engine == "paddle":
        from structured_pdf_text.ocr.paddle import validate_local_ocr_models
        from structured_pdf_text.errors import PaddleOcrUnavailable

        try:
            validate_local_ocr_models(language=config.paddle_model_profile, cache_home=cache_home)
            return ReadinessResult(ReadinessStatus.READY, details={"profile": config.paddle_model_profile})
        except PaddleOcrUnavailable as exc:
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "model_missing", {"message": str(exc)})
        except ValueError as exc:
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "invalid_model_profile", {"message": str(exc)})
        except Exception as exc:
            return ReadinessResult(ReadinessStatus.UNKNOWN, "paddle_probe_error", {"message": str(exc)})

    if engine == "tesseract":
        executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
        if not executable:
            return ReadinessResult(ReadinessStatus.MISSING, "executable_missing")
        lang = os.environ.get("TESSERACT_LANG") or (
            "por" if config.language == "pt-BR" else "eng"
        )
        flags: list[str] = []
        if os.environ.get("TESSERACT_TESSDATA_DIR"):
            flags = ["--tessdata-dir", os.environ["TESSERACT_TESSDATA_DIR"]]
        try:
            result = subprocess.run(
                [executable, *flags, "--list-langs"], capture_output=True,
                text=True, encoding="utf-8", timeout=10,
            )
            if result.returncode:
                return ReadinessResult(ReadinessStatus.UNKNOWN, "language_probe_failed", {"stderr": result.stderr})
            available = {line.strip() for line in (result.stdout + result.stderr).splitlines()}
            required = set(lang.split("+"))
            missing = sorted(required - available)
            if missing:
                return ReadinessResult(ReadinessStatus.INCOMPLETE, "language_data_missing", {"missing": missing})
            return ReadinessResult(ReadinessStatus.READY, details={"executable": executable, "language": lang})
        except subprocess.TimeoutExpired:
            return ReadinessResult(ReadinessStatus.UNKNOWN, "executable_timeout")
        except OSError as exc:
            return ReadinessResult(ReadinessStatus.UNKNOWN, "executable_error", {"message": str(exc)})

    if engine == "rapidocr":
        if importlib.util.find_spec("rapidocr") is None:
            return ReadinessResult(ReadinessStatus.MISSING, "package_missing", {"package": "rapidocr"})
        provider_module = "onnxruntime" if (config.ocr_provider or "onnxruntime") == "onnxruntime" else "openvino"
        if importlib.util.find_spec(provider_module) is None:
            return ReadinessResult(ReadinessStatus.MISSING, "provider_missing", {"provider": provider_module})
        det = os.environ.get("RAPIDOCR_DET_MODEL")
        cache = Path.home() / ".cache" / "pdfextractor" / "rapidocr"
        configured_rec = os.environ.get("RAPIDOCR_REC_MODEL")
        configured_keys = os.environ.get("RAPIDOCR_REC_KEYS")
        if configured_rec or configured_keys:
            rec, keys = configured_rec, configured_keys
        else:
            rec = str(cache / "latin_PP-OCRv3_rec_mobile.onnx")
            keys = str(cache / "latin_dict.txt")
        if bool(rec) != bool(keys):
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "model_configuration_incomplete")
        if rec and keys:
            configured_paths = [rec, keys] + ([det] if det else [])
            missing_files = [item for item in configured_paths if not Path(item).is_file()]
            if missing_files:
                return ReadinessResult(ReadinessStatus.INCOMPLETE, "model_file_missing", {"paths": missing_files})
            from structured_pdf_text.ocr.backends.rapidocr import portuguese_dictionary_profile
            profile, missing_chars = portuguese_dictionary_profile(keys)
            if config.language == "pt-BR" and profile != "latin/pt-compatible":
                return ReadinessResult(
                    ReadinessStatus.INCOMPLETE, "pt_br_dictionary_incomplete",
                    {"missing_characters": "".join(missing_chars)},
                )
        elif config.language == "pt-BR":
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "pt_br_model_not_configured")
        return ReadinessResult(ReadinessStatus.READY, details={"provider": provider_module})

    if engine == "easyocr":
        if importlib.util.find_spec("easyocr") is None:
            return ReadinessResult(ReadinessStatus.MISSING, "package_missing", {"package": "easyocr"})
        cache = Path(os.environ.get("EASYOCR_MODULE_PATH") or Path.home() / ".cache" / "pdfextractor" / "easyocr").expanduser()
        expected = ("craft_mlt_25k.pth", "latin_g2.pth")
        missing = [name for name in expected if not (cache / name).is_file()]
        if missing:
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "model_missing", {"cache": str(cache), "missing": missing})
        return ReadinessResult(ReadinessStatus.READY, details={"cache": str(cache), "language": config.language})

    return ReadinessResult(ReadinessStatus.UNKNOWN, "unsupported_engine", {"engine": engine})


def probe_deep(config: ExtractorConfig) -> ReadinessResult:
    """Load the selected backend and run a tiny generated Portuguese sample."""
    static = probe_static(config)
    if static.status != ReadinessStatus.READY:
        return static
    backend = None
    try:
        from PIL import Image, ImageDraw, ImageFont
        from structured_pdf_text.ocr.factory import build_ocr_backend
        from structured_pdf_text.ocr.contracts import OCRRequest
        import hashlib
        import io

        image = Image.new("RGB", (1500, 300), "white")
        draw = ImageDraw.Draw(image)
        # The default Pillow bitmap font is too small for OCR detectors at
        # this image scale. Use a large built-in font so deep readiness checks
        # exercise the backend instead of reporting a false no-text failure.
        font = ImageFont.load_default(size=42)
        draw.text((20, 12), "ã õ á é í ó ú ç ê ô", fill="black", font=font)
        draw.text((20, 78), "R$ 1.234,56 03/10/2026 12,5%", fill="black", font=font)
        draw.text((20, 144), "CPF 123.456.789-09 CNPJ 12.345.678/0001-90", fill="black", font=font)
        draw.text((20, 210), "palavra com hífen", fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payload = buffer.getvalue()
        backend = build_ocr_backend(config)
        result = backend.recognize(OCRRequest(
            image=image,
            image_sha256=hashlib.sha256(payload).hexdigest(),
            document_id="pt-br-readiness-smoke",
            page_index=0,
            input_kind="page",
            language=config.language,
        ))
        if result.status not in {"ok", "recovered"} or not result.tokens:
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "deep_smoke_no_text", {"status": result.status, "warnings": result.warnings})
        return ReadinessResult(ReadinessStatus.READY, details={"status": result.status, "token_count": len(result.tokens), "identity": backend.identity.__dict__ if hasattr(backend.identity, "__dict__") else str(backend.identity)})
    except Exception as exc:
        return ReadinessResult(ReadinessStatus.UNKNOWN, "deep_smoke_failed", {"type": type(exc).__name__, "message": str(exc)})
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass
