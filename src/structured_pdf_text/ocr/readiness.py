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

from structured_pdf_text.config import ExtractorConfig, OcrQualityPolicy, effective_ocr_quality_policy


class ReadinessStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
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

        missing = [name for name in ("paddle", "paddleocr") if importlib.util.find_spec(name) is None]
        if missing:
            return ReadinessResult(ReadinessStatus.MISSING, "package_missing", {"packages": missing})

        try:
            validate_local_ocr_models(
                language=config.paddle_model_profile,
                cache_home=cache_home or config.ocr_cache_home,
            )
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
        configured_cache = cache_home or config.ocr_cache_home
        cache = (
            Path(configured_cache).expanduser() / "rapidocr"
            if configured_cache
            else Path.home() / ".cache" / "pdfextractor" / "rapidocr"
        )
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
        configured_cache = (
            os.environ.get("EASYOCR_MODULE_PATH")
            or (
                str(Path(cache_home or config.ocr_cache_home).expanduser() / "easyocr")
                if cache_home or config.ocr_cache_home else None
            )
        )
        cache = Path(configured_cache or Path.home() / ".cache" / "pdfextractor" / "easyocr").expanduser()
        craft_ok = (cache / "craft_mlt_25k.pth").is_file()
        from structured_pdf_text.ocr.languages import easyocr_recognition_model
        recog_name = easyocr_recognition_model(config.language, os.environ.get("EASYOCR_RECOG_NETWORK"))
        recog_ok = (cache / f"{recog_name}.pth").is_file()
        dbnet_name: str | None = None
        dbnet_ok = False
        try:
            import easyocr.config as easy_config  # type: ignore
            model = easy_config.detection_models.get("dbnet18", {})
            dbnet_name = model.get("filename")
            dbnet_ok = bool(dbnet_name and (cache / dbnet_name).is_file())
        except Exception:
            pass
        details = {
            "cache": str(cache), "language": config.language,
            "models": {
                "craft": "available" if craft_ok else "missing",
                "dbnet18": "available" if dbnet_ok else "missing",
                "recognizer": "available" if recog_ok else "missing",
            },
        }
        if not craft_ok or not recog_ok:
            details["missing"] = [
                name for name, available in (("craft_mlt_25k.pth", craft_ok), (f"{recog_name}.pth", recog_ok))
                if not available
            ]
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "model_missing", details)
        requires_dbnet = config.max_quality or effective_ocr_quality_policy(config) == OcrQualityPolicy.EXHAUSTIVE
        if requires_dbnet and not dbnet_ok:
            details["dbnet_model"] = dbnet_name
            return ReadinessResult(ReadinessStatus.DEGRADED, "dbnet18_missing", details)
        return ReadinessResult(ReadinessStatus.READY, details=details)

    return ReadinessResult(ReadinessStatus.UNKNOWN, "unsupported_engine", {"engine": engine})


_SMOKE_LINES: list[str] = [
    "Ação, órgão, informações, você, avô e põe.",
    "segunda-feira e anti-inflamatório",
    "R$ 1.234,56 03/10/2026 12,5%",
    "CPF 123.456.789-09 CNPJ 12.345.678/0001-90",
]
_SMOKE_EXPECTED: str = " ".join(_SMOKE_LINES)

# Maximum CER allowed before probe_deep downgrades to INCOMPLETE.
# Calibrated conservatively: even a badly-configured model should get well below
# this on clean rendered text.  A CER above this indicates wrong model, wrong
# language pack, or a misconfigured recognizer.
_SMOKE_MAX_CER: float = 0.40

def _load_smoke_font(size: int = 42):
    """Load a deterministic Unicode-capable font for the OCR readiness image."""
    from PIL import ImageFont

    candidates: list[Path] = []

    configured = os.environ.get("PDFEXTRACTOR_SMOKE_FONT")
    if configured:
        candidates.append(Path(configured).expanduser())

    if os.name == "nt":
        windows_dir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        fonts_dir = windows_dir / "Fonts"
        candidates.extend([
            fonts_dir / "arial.ttf",
            fonts_dir / "segoeui.ttf",
        ])
    else:
        candidates.extend([
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        ])

    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)

    # Pillow commonly resolves DejaVuSans.ttf even when its absolute
    # location differs from the paths above.
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default(size=size)

def _simple_cer(hypothesis: str, reference: str) -> float:
    """Character Error Rate via Levenshtein edit distance (character-level).

    Does not require any external library.  Operates on Unicode codepoints.
    Returns a value in [0, ∞) where 0 = perfect match and 1 = all chars wrong.
    Values > 1 are possible when the hypothesis is much longer than the reference.
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    h = list(hypothesis)
    r = list(reference)
    # Wagner-Fischer DP
    prev = list(range(len(r) + 1))
    for ch in h:
        curr = [prev[0] + 1]
        for j, cr in enumerate(r):
            curr.append(min(prev[j] + (0 if ch == cr else 1),
                            curr[j] + 1,
                            prev[j + 1] + 1))
        prev = curr
    return prev[len(r)] / len(r)

def _smoke_precision_checks(recognised_text: str) -> dict[str, bool]:
    """Validate critical pt-BR features in the deep-smoke OCR result."""
    recognised_norm = " ".join(recognised_text.split())
    recognised_lower = recognised_norm.lower()

    return {
        "currency_exact": "r$ 1.234,56" in recognised_lower,
        "date_exact": "03/10/2026" in recognised_norm,
        "percentage_exact": "12,5%" in recognised_norm,

        "accented_portuguese": all(
            word in recognised_lower
            for word in (
                "ação",
                "órgão",
                "informações",
                "você",
                "avô",
            )
        ),

        "hyphen_preserved": all(
            word in recognised_lower
            for word in (
                "segunda-feira",
                "anti-inflamatório",
            )
        ),
    }

def probe_deep(config: ExtractorConfig) -> ReadinessResult:
    """Load the selected backend, run a Portuguese smoke image, and validate CER.

    Unlike :func:`probe_static`, this probe instantiates the OCR backend and
    runs inference on a synthetic image containing:
    - Real Portuguese words with diacritics
    - Hyphenated Portuguese words
    - Currency (R$ 1.234,56), date (03/10/2026), percentage (12,5%)
    - CPF and CNPJ with their canonical punctuation

    The recognised text is compared against the expected ground truth using
    character-level CER. A CER above ``_SMOKE_MAX_CER`` downgrades the result
    to ``INCOMPLETE / deep_smoke_high_cer``.
    """
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

        image = Image.new("RGB", (1800, 320), "white")
        draw = ImageDraw.Draw(image)
        # The default Pillow bitmap font is too small for OCR detectors at
        # this image scale. Use a large built-in font so deep readiness checks
        # exercise the backend instead of reporting a false no-text failure.
        font = _load_smoke_font(42)
        for i, line_text in enumerate(_SMOKE_LINES):
            draw.text((20, 12 + i * 66), line_text, fill="black", font=font)
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
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "deep_smoke_no_text",
                                   {"status": result.status, "warnings": result.warnings})

        # §43: validate recognised text quality, not just token presence.
        recognised = " ".join(t.text for t in result.tokens)
        # Normalise whitespace for CER comparison
        recognised_norm = " ".join(recognised.split())
        expected_norm = " ".join(_SMOKE_EXPECTED.split())
        cer = _simple_cer(recognised_norm.lower(), expected_norm.lower())
        recognised_lower = recognised_norm.lower()

        precision_checks = _smoke_precision_checks(recognised_norm)

        identity_repr = (backend.identity.__dict__ if hasattr(backend.identity, "__dict__")
                         else str(backend.identity))
        base_details: dict[str, Any] = {
            "status": result.status,
            "token_count": len(result.tokens),
            "smoke_cer": round(cer, 4),
            "smoke_max_cer": _SMOKE_MAX_CER,
            "precision_checks": precision_checks,
            "identity": identity_repr,
        }

        if cer > _SMOKE_MAX_CER:
            base_details["recognised_text"] = recognised_norm[:200]
            base_details["expected_text"] = expected_norm[:200]
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "deep_smoke_high_cer", base_details)

        failed_checks = [name for name, passed in precision_checks.items() if not passed]
        if failed_checks:
            base_details["failed_precision_checks"] = failed_checks
            base_details["recognised_text"] = recognised_norm[:200]
            return ReadinessResult(ReadinessStatus.INCOMPLETE, "deep_smoke_precision_failed", base_details)

        return ReadinessResult(ReadinessStatus.READY, details=base_details)
    except Exception as exc:
        return ReadinessResult(ReadinessStatus.UNKNOWN, "deep_smoke_failed",
                               {"type": type(exc).__name__, "message": str(exc)})
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass
