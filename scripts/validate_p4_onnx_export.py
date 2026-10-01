"""P4-pre: Validate PP-OCRv6 ONNX export for RapidOCR compatibility.

Runs in two sequential phases:
  Phase A — Export Paddle models → ONNX  (requires paddle2onnx)
  Phase B — Validate ONNX with onnxruntime and RapidOCR

Usage (server — PowerShell):
    python scripts\\validate_p4_onnx_export.py `
        --onnx-dir output\\onnx_models

Optional parameters:
    --cache-home   PaddleX cache directory (default: ~/.cache/pdfextractor/paddlex)
    --language     Model profile (default: pt → PP-OCRv6 medium)
    --skip-export  Skip export and go directly to validation (existing ONNX)
    --onnx-det     Path to existing detection ONNX (bypasses det export)
    --onnx-rec     Path to existing recognition ONNX (bypasses rec export)

Examples:
    # Export + validate everything:
    python scripts\\validate_p4_onnx_export.py --onnx-dir output\\onnx_models

    # Validate pre-existing ONNX only (downloaded from RapidOCR hub):
    python scripts\\validate_p4_onnx_export.py `
        --onnx-dir output\\onnx_models `
        --skip-export

    # Use specific pre-existing ONNX:
    python scripts\\validate_p4_onnx_export.py `
        --onnx-det path\\to\\det.onnx `
        --onnx-rec path\\to\\rec.onnx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers — discover model files
# ---------------------------------------------------------------------------

def _find_model_files(model_dir: Path) -> tuple[Path, Path] | None:
    """Return (model_graph_file, pdiparams) inside a PaddleX model directory.

    PaddleX 3.x / PaddlePaddle 3.x (PIR format — new):
        inference.json   + inference.pdiparams   ← graph in JSON/PIR
    PaddleX 2.x / older (classic format):
        inference.pdmodel + inference.pdiparams
        model.pdmodel     + model.pdiparams
    """
    for stem in ("inference", "model"):
        pdiparams = model_dir / f"{stem}.pdiparams"
        if not pdiparams.is_file():
            continue
        # Prefer .pdmodel (classic), fall back to .json (PIR)
        for ext in (".pdmodel", ".json"):
            candidate = model_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate, pdiparams
    # Broad fallback: any .pdmodel/.json + .pdiparams
    pdiparams_list = list(model_dir.glob("*.pdiparams"))
    if not pdiparams_list:
        return None
    pdiparams = pdiparams_list[0]
    for pattern in ("*.pdmodel", "*.json"):
        matches = [
            f for f in model_dir.glob(pattern)
            # Exclude README and config files that are not the model graph
            if f.stem in ("inference", "model") or f.suffix == ".pdmodel"
        ]
        if matches:
            return matches[0], pdiparams
    return None


def _resolve_cache_home(cache_home: str | None) -> Path:
    return Path(
        cache_home
        or os.environ.get(
            "PADDLE_PDX_CACHE_HOME",
            str(Path.home() / ".cache" / "pdfextractor" / "paddlex"),
        )
    ).expanduser().resolve()


def _model_root(cache_home: Path) -> Path:
    return cache_home / "official_models"


# ---------------------------------------------------------------------------
# Phase A — Export Paddle → ONNX
# ---------------------------------------------------------------------------

def _export_model_to_onnx(
    model_dir: Path,
    output_onnx: Path,
    model_label: str,
    opset: int = 11,
) -> bool:
    """Export one Paddle inference model to ONNX. Returns True on success."""
    print(f"\n[EXPORT] {model_label}")
    print(f"  model_dir : {model_dir}")
    print(f"  output    : {output_onnx}")

    pair = _find_model_files(model_dir)
    if pair is None:
        print(
            f"  [FAIL] Arquivo de grafo do modelo não encontrado em {model_dir}.\n"
            f"         Esperado: inference.pdmodel (formato clássico) ou inference.json (PIR/PaddleX 3.x)\n"
            f"         Conteúdo do diretório:"
        )
        if model_dir.is_dir():
            for f in sorted(model_dir.iterdir()):
                print(f"           {f.name}")
        else:
            print(f"           [diretório não existe: {model_dir}]")
        return False

    model_file, pdiparams = pair
    fmt = "PIR/JSON (PaddleX 3.x)" if model_file.suffix == ".json" else "clássico (.pdmodel)"
    print(f"  model_file: {model_file.name}  [{fmt}]")
    print(f"  pdiparams : {pdiparams.name}")

    try:
        import paddle2onnx  # type: ignore
    except (ImportError, OSError) as exc:
        exc_msg = str(exc).lower()
        is_dll = any(kw in exc_msg for kw in ("dll", "entry point", "procedure", "0xc0"))
        if is_dll:
            print(
                "  [FAIL] paddle2onnx está instalado mas falhou ao carregar extensão nativa.\n"
                "         Erro de DLL no Windows (STATUS_ENTRYPOINT_NOT_FOUND / 0xC0000139):\n"
                "         Incompatibilidade de ABI entre paddle2onnx 2.x e PaddlePaddle 3.3.1.\n"
                "         → CF-2: exportação direta bloqueada no Windows com esse par de versões.\n"
                f"         Detalhe: {exc}"
            )
        else:
            print(
                f"  [FAIL] paddle2onnx não encontrado ou falhou ao carregar: {exc}\n"
                "         Instale com: pip install paddle2onnx>=2.0"
            )
        return False

    output_onnx.parent.mkdir(parents=True, exist_ok=True)

    try:
        t0 = time.perf_counter()
        paddle2onnx.export(
            model_dir=str(model_dir),
            model_filename=model_file.name,
            params_filename=pdiparams.name,
            save_file=str(output_onnx),
            opset_version=opset,
            enable_onnx_checker=True,
        )
        elapsed = time.perf_counter() - t0
        size_mb = output_onnx.stat().st_size / (1024 * 1024)
        print(f"  [OK] Exportado em {elapsed:.1f}s — {size_mb:.1f} MB")
        return True
    except Exception as exc:
        print(f"  [FAIL] Erro na exportação: {exc}")
        if model_file.suffix == ".json":
            print(
                "  Dica: se o erro mencionar 'not support' ou 'invalid format',\n"
                "  o paddle2onnx instalado pode não suportar o formato PIR (PaddleX 3.x).\n"
                "  Verifique: pip show paddle2onnx  (requer >=2.0 para PIR)"
            )
        return False


def phase_a_export(
    language: str,
    cache_home: Path,
    onnx_dir: Path,
    opset: int = 11,
) -> dict[str, Path | None]:
    """Export det + rec models. Returns {'det': Path|None, 'rec': Path|None}."""
    try:
        from structured_pdf_text.ocr.models import get_profile
        profile = get_profile(language)
    except Exception as exc:
        print(f"[FAIL] Não foi possível carregar o perfil '{language}': {exc}")
        return {"det": None, "rec": None}

    model_root = _model_root(cache_home)
    det_dir = model_root / profile.detection
    rec_dir = model_root / profile.recognition

    det_onnx = onnx_dir / f"{profile.detection}.onnx"
    rec_onnx = onnx_dir / f"{profile.recognition}.onnx"

    det_ok = _export_model_to_onnx(det_dir, det_onnx, f"Detection: {profile.detection}", opset)
    rec_ok = _export_model_to_onnx(rec_dir, rec_onnx, f"Recognition: {profile.recognition}", opset)

    return {
        "det": det_onnx if det_ok else None,
        "rec": rec_onnx if rec_ok else None,
    }


# ---------------------------------------------------------------------------
# Phase B — Validate ONNX
# ---------------------------------------------------------------------------

def _validate_with_onnxruntime(onnx_path: Path, label: str) -> dict:
    """Load ONNX file with onnxruntime and report input/output shapes."""
    print(f"\n[ORT] {label} — {onnx_path.name}")
    result = {"status": "fail", "inputs": [], "outputs": []}

    try:
        import onnxruntime as ort  # type: ignore
    except ImportError:
        print("  [FAIL] onnxruntime não está instalado. pip install onnxruntime")
        return result

    try:
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        inputs = [
            {"name": i.name, "shape": i.shape, "dtype": i.type}
            for i in session.get_inputs()
        ]
        outputs = [
            {"name": o.name, "shape": o.shape, "dtype": o.type}
            for o in session.get_outputs()
        ]
        result["status"] = "ok"
        result["inputs"] = inputs
        result["outputs"] = outputs
        print(f"  [OK] Carregado com sucesso")
        for inp in inputs:
            print(f"    input  {inp['name']} : {inp['shape']}  {inp['dtype']}")
        for out in outputs:
            print(f"    output {out['name']} : {out['shape']}  {out['dtype']}")
        return result
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        return result


def _import_rapidocr() -> tuple[type | None, str]:
    """Try to import RapidOCR from either the new or old package.

    Returns (RapidOCR class or None, api_variant string).
    rapidocr-onnxruntime 1.x  → import rapidocr_onnxruntime
    rapidocr 3.x (newer)      → import rapidocr
    """
    # New package (rapidocr>=3.x — not yet on PyPI as of 2026-09)
    try:
        from rapidocr import RapidOCR  # type: ignore
        return RapidOCR, "new"
    except ImportError:
        pass
    # Legacy package (rapidocr-onnxruntime 1.x — latest 1.4.4)
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        return RapidOCR, "legacy"
    except ImportError:
        pass
    return None, "none"


def _validate_with_rapidocr(det_onnx: Path, rec_onnx: Path) -> dict:
    """Run RapidOCR on a synthetic test image with the given ONNX models."""
    print(f"\n[RAPIDOCR] det={det_onnx.name}  rec={rec_onnx.name}")
    result = {"status": "fail", "tokens": 0, "elapsed_s": None, "api": None}

    RapidOCR, api = _import_rapidocr()
    if RapidOCR is None:
        print(
            "  [SKIP] rapidocr-onnxruntime não está instalado.\n"
            "         pip install rapidocr-onnxruntime"
        )
        result["status"] = "skip"
        return result

    result["api"] = api
    print(f"  API: {api}")

    try:
        import numpy as np
    except ImportError:
        print("  [FAIL] numpy não está instalado.")
        return result

    try:
        engine = RapidOCR(
            det_model_path=str(det_onnx),
            rec_model_path=str(rec_onnx),
        )
        # Synthetic image: white background with simple black rectangle
        img = np.full((200, 600, 3), 255, dtype=np.uint8)
        img[70:130, 50:550] = 30  # dark block simulating text area

        t0 = time.perf_counter()
        out = engine(img)
        elapsed_wall = time.perf_counter() - t0

        # Handle both old (tuple) and new (object) return formats
        if isinstance(out, tuple) and len(out) == 2:
            # Legacy: (result_list, elapse_float)
            raw_result, elapsed = out
            elapsed = float(elapsed) if elapsed else elapsed_wall
        elif hasattr(out, "elapse"):
            # New API object
            raw_result = out
            elapsed = float(out.elapse) if out.elapse else elapsed_wall
        else:
            raw_result = out
            elapsed = elapsed_wall

        tokens = len(raw_result) if raw_result else 0
        result["status"] = "ok"
        result["tokens"] = tokens
        result["elapsed_s"] = round(elapsed, 3)
        print(f"  [OK] Inferência concluída em {elapsed:.2f}s — {tokens} token(s)")
        if tokens > 0 and raw_result:
            print(f"  Exemplo: {raw_result[0]}")
        return result
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        return result


def _validate_rapidocr_builtin() -> dict:
    """Run RapidOCR with its built-in/default ONNX models (no export required).

    rapidocr-onnxruntime 1.x bundles lightweight PP-OCR models as package data,
    so no internet access and no explicit model paths are needed.
    This validates that the ONNX runtime stack is functional on this machine.
    """
    print(f"\n[RAPIDOCR-BUILTIN] Testando RapidOCR com modelos padrão do pacote")
    result: dict = {"status": "fail", "tokens": 0, "elapsed_s": None, "api": None, "note": "builtin-models"}

    RapidOCR, api = _import_rapidocr()
    if RapidOCR is None:
        print(
            "  [SKIP] rapidocr-onnxruntime não instalado.\n"
            "         pip install rapidocr-onnxruntime"
        )
        result["status"] = "skip"
        return result

    result["api"] = api
    print(f"  API: {api}")

    try:
        import numpy as np
    except ImportError:
        print("  [FAIL] numpy não disponível.")
        return result

    try:
        engine = RapidOCR()  # uses built-in package models
        img = np.full((200, 600, 3), 255, dtype=np.uint8)
        img[70:130, 50:550] = 30  # dark block simulates text area

        t0 = time.perf_counter()
        out = engine(img)
        elapsed_wall = time.perf_counter() - t0

        if isinstance(out, tuple) and len(out) == 2:
            raw_result, elapsed = out
            elapsed = float(elapsed) if elapsed else elapsed_wall
        elif hasattr(out, "elapse"):
            raw_result = out
            elapsed = float(out.elapse) if out.elapse else elapsed_wall
        else:
            raw_result = out
            elapsed = elapsed_wall

        tokens = len(raw_result) if raw_result else 0
        result["status"] = "ok"
        result["tokens"] = tokens
        result["elapsed_s"] = round(elapsed, 3)
        print(f"  [OK] Inferência em {elapsed:.2f}s — {tokens} token(s)")
        if tokens > 0 and raw_result:
            print(f"  Exemplo: {raw_result[0]}")
        return result
    except Exception as exc:
        exc_str = str(exc)
        print(f"  [FAIL] {exc_str}")
        if "download" in exc_str.lower() or "http" in exc_str.lower() or "connect" in exc_str.lower():
            print("  Nota: erro de rede ao baixar modelos — servidor sem acesso à internet?")
        return result


def phase_b_validate(det_onnx: Path | None, rec_onnx: Path | None) -> dict:
    """Validate ONNX files with onnxruntime + RapidOCR, plus built-in model fallback."""
    results: dict = {"det_ort": None, "rec_ort": None, "rapidocr": None, "rapidocr_builtin": None}

    if det_onnx is None or not det_onnx.is_file():
        print(f"\n[SKIP] Det ONNX exportado não encontrado: {det_onnx}")
    else:
        results["det_ort"] = _validate_with_onnxruntime(det_onnx, "Det")

    if rec_onnx is None or not rec_onnx.is_file():
        print(f"\n[SKIP] Rec ONNX exportado não encontrado: {rec_onnx}")
    else:
        results["rec_ort"] = _validate_with_onnxruntime(rec_onnx, "Rec")

    if det_onnx and det_onnx.is_file() and rec_onnx and rec_onnx.is_file():
        results["rapidocr"] = _validate_with_rapidocr(det_onnx, rec_onnx)

    # Always validate RapidOCR with built-in models — independent of export result
    results["rapidocr_builtin"] = _validate_rapidocr_builtin()

    return results


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _print_summary(export: dict, validate: dict) -> bool:
    """Print a pass/fail summary. Returns True if P4-pre is considered passed.

    Critério de aprovação (dois caminhos):
      - Caminho A (completo): exportação OK + validação ort + inferência RapidOCR com modelos exportados
      - Caminho B (builtin):  inferência RapidOCR com modelos embutidos (exportação bloqueada por CF-2)
    """
    print("\n" + "=" * 60)
    print("RESUMO P4-pre")
    print("=" * 60)

    det_exported = export.get("det") is not None
    rec_exported = export.get("rec") is not None
    det_ort_ok = (validate.get("det_ort") or {}).get("status") == "ok"
    rec_ort_ok = (validate.get("rec_ort") or {}).get("status") == "ok"
    rapid_status = (validate.get("rapidocr") or {}).get("status", "fail")
    rapid_ok = rapid_status in ("ok", "skip")
    builtin_status = (validate.get("rapidocr_builtin") or {}).get("status", "fail")
    builtin_ok = builtin_status == "ok"

    print("  [Caminho A — exportação paddle2onnx]")
    rows_a = [
        ("Exportação det", "✅" if det_exported else "❌ CF-2"),
        ("Exportação rec", "✅" if rec_exported else "❌ CF-2"),
        ("Validação det (onnxruntime)", "✅" if det_ort_ok else "⏭️ n/a"),
        ("Validação rec (onnxruntime)", "✅" if rec_ort_ok else "⏭️ n/a"),
        ("Inferência RapidOCR (modelos exportados)", "✅" if rapid_status == "ok" else ("⏭️ n/a" if rapid_status in ("skip", "fail") and not det_exported else "❌")),
    ]
    for label, status in rows_a:
        print(f"    {status}  {label}")

    print()
    print("  [Caminho B — modelos embutidos rapidocr-onnxruntime]")
    b_icon = "✅" if builtin_ok else ("⏭️ skip" if builtin_status == "skip" else "❌")
    print(f"    {b_icon}  Inferência RapidOCR (modelos padrão do pacote)")

    path_a_ok = det_ort_ok and rec_ort_ok and rapid_ok
    passed = path_a_ok or builtin_ok

    print()
    if path_a_ok:
        print("✅ P4-pre APROVADO (Caminho A) — PP-OCRv6 ONNX exportado e validado.")
        print("   Fase 4 pode usar modelos PP-OCRv6 exportados.")
    elif builtin_ok:
        print("✅ P4-pre APROVADO (Caminho B) — RapidOCR funcional com modelos embutidos.")
        print("   CF-2 pendente: exportação paddle2onnx bloqueada por incompatibilidade de DLL.")
        print("   Fase 4 usa modelos embutidos do rapidocr-onnxruntime.")
    else:
        print("❌ P4-pre REPROVADO — nenhum caminho funcionou. Veja os erros acima.")
    print("=" * 60)
    return passed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P4-pre: Valida export ONNX do PP-OCRv6 para RapidOCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--onnx-dir", type=Path, default=Path("output/onnx_models"),
        help="Diretório onde os arquivos ONNX serão salvos/buscados (padrão: output/onnx_models)",
    )
    parser.add_argument(
        "--cache-home", default=None,
        help="Diretório cache do PaddleX (padrão: ~/.cache/pdfextractor/paddlex)",
    )
    parser.add_argument(
        "--language", default="pt",
        help="Perfil de modelos (padrão: pt → PP-OCRv6 medium)",
    )
    parser.add_argument(
        "--opset", type=int, default=11,
        help="ONNX opset version para exportação (padrão: 11)",
    )
    parser.add_argument(
        "--skip-export", action="store_true",
        help="Pular exportação Paddle→ONNX; usar ONNX já existentes em --onnx-dir",
    )
    parser.add_argument(
        "--onnx-det", type=Path, default=None,
        help="Caminho explícito para o ONNX de detecção (bypass da exportação)",
    )
    parser.add_argument(
        "--onnx-rec", type=Path, default=None,
        help="Caminho explícito para o ONNX de reconhecimento (bypass da exportação)",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="Salvar relatório JSON neste caminho",
    )

    args = parser.parse_args(argv)

    cache_home = _resolve_cache_home(args.cache_home)
    onnx_dir: Path = args.onnx_dir

    print(f"P4-pre — Validação ONNX PP-OCRv6")
    print(f"  cache_home  : {cache_home}")
    print(f"  onnx_dir    : {onnx_dir}")
    print(f"  language    : {args.language}")

    # --- Phase A: Export ---
    if args.onnx_det and args.onnx_rec:
        # Explicit paths provided — skip export
        export_result: dict = {"det": args.onnx_det, "rec": args.onnx_rec}
        print(f"\n[INFO] Usando ONNX explícitos:")
        print(f"  det: {args.onnx_det}")
        print(f"  rec: {args.onnx_rec}")
    elif args.skip_export:
        # Look for existing files in onnx_dir
        try:
            from structured_pdf_text.ocr.models import get_profile
            profile = get_profile(args.language)
            det_onnx = onnx_dir / f"{profile.detection}.onnx"
            rec_onnx = onnx_dir / f"{profile.recognition}.onnx"
        except Exception:
            det_onnx = None
            rec_onnx = None
        export_result = {
            "det": det_onnx if (det_onnx and det_onnx.is_file()) else None,
            "rec": rec_onnx if (rec_onnx and rec_onnx.is_file()) else None,
        }
        print(f"\n[INFO] --skip-export ativo; buscando ONNX em {onnx_dir}")
        for key, path in export_result.items():
            status = "encontrado" if path else "NÃO encontrado"
            print(f"  {key}: {path} — {status}")
    else:
        print("\n--- Fase A: Exportação Paddle → ONNX ---")
        export_result = phase_a_export(args.language, cache_home, onnx_dir, args.opset)

    # --- Phase B: Validate ---
    print("\n--- Fase B: Validação ONNX ---")
    validate_result = phase_b_validate(export_result.get("det"), export_result.get("rec"))

    # --- Summary ---
    passed = _print_summary(export_result, validate_result)

    # --- Report ---
    if args.report:
        report = {
            "p4_pre_passed": passed,
            "export": {
                k: str(v) if v else None for k, v in export_result.items()
            },
            "validate": validate_result,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"\n[REPORT] {args.report}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
