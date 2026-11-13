#!/usr/bin/env python3
"""Executa OCR Tesseract + métricas canônicas para V3..V10 em WSL.

Uso: python -u scripts/run_tesseract_batch_wsl.py 001
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CORPORA = [
    ("V3", "corpus/V3/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf"),
    ("V4", "corpus/V4/Corpus_Stress_OCR_Markdown_V4.pdf"),
    ("V5", "corpus/V5/Document_AI_V5.pdf"),
    ("V6", "corpus/V6/Document_AI_V6.pdf"),
    ("V7", "corpus/V7/Document_AI_V7.pdf"),
    ("V8", "corpus/V8/Document_AI_V8.pdf"),
    ("V9", "corpus/V9/Document_AI_V9.pdf"),
    ("V10", "corpus/V10/Document_AI_V10.pdf"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def input_hashes(job: dict) -> dict[str, str]:
    """SHA-256 dos arquivos de entrada que a rodada usa para um corpus (T3-P15)."""
    paths = {"pdf": job["pdf"], "reference": job.get("reference"), "manifest": job.get("manifest")}
    return {name: sha256(path) for name, path in paths.items() if path is not None}


def changed_inputs(job: dict) -> list[str]:
    """Entradas alteradas ou removidas desde a pré-verificação.

    O protocolo exige PDF, referência e manifesto idênticos entre extração e
    métricas (``corpus_policy``). Na RUN 003 o corpus V10 foi substituído
    durante a rodada; a extração e o avaliador leram arquivos diferentes dos
    validados na pré-verificação.
    """
    expected = job.get("input_hashes", {})
    changed = []
    for name, digest in expected.items():
        path = {"pdf": job["pdf"], "reference": job.get("reference"), "manifest": job.get("manifest")}[name]
        try:
            current = sha256(path)
        except OSError:
            current = None
        if current != digest:
            changed.append(name)
    return changed


def preflight() -> list[dict]:
    """Inspeciona os dois possíveis gabaritos e escolhe a melhor cobertura válida.

    Aceita escopo parcial explícito (>= 2 páginas corretamente marcadas),
    mas não trata Markdown inteiro sem delimitação como uma referência multi-página.
    """
    import pypdfium2
    from structured_pdf_text.evaluation.reference_hash import split_pages
    from structured_pdf_text.evaluation.reference_inputs import manifest_pages

    errors = []
    jobs = []
    for version, relative in CORPORA:
        pdf = REPO / relative
        base = pdf.with_suffix("")
        ref = base.with_name(base.name + "_REFERENCIA.md")
        # Fallback: um corpus pode marcar páginas no _REFERENCIA.md com
        # comentários HTML (não reconhecidos por split_pages) e trazer o
        # manifesto sem expected_markdown; a versão paginada com "## Página NNN"
        # é então o gabarito utilizável. (O V10 revisado de 2026-11-12 traz
        # expected_markdown no manifesto, que passa a ser o gabarito escolhido.)
        ref_paged = base.with_name(base.name + "_REFERENCIA_PAGINADA.md")
        manifest = base.with_name(base.name + "_MANIFESTO.json")
        if not pdf.is_file():
            errors.append(f"{version}: PDF inexistente: {pdf}")
            continue
        doc = pypdfium2.PdfDocument(str(pdf))
        try:
            pdf_pages = len(doc)
        finally:
            doc.close()

        candidates = []
        diagnostics = []
        if ref.is_file():
            try:
                pages = split_pages(ref.read_text(encoding="utf-8"))
                ids = set(pages)
                bad = sorted(i for i in ids if i < 1 or i > pdf_pages)
                diagnostics.append(f"referência Markdown: {len(ids)}/{pdf_pages} páginas, fora do PDF={bad[:8]}")
                if not bad and (pdf_pages == 1 or len(ids) > 1):
                    candidates.append((len(ids), 1, "reference", ids, ref))
                elif pdf_pages > 1 and len(ids) == 1:
                    diagnostics.append("referência Markdown parece não paginada; não será usada")
            except Exception as exc:
                diagnostics.append(f"referência Markdown inválida: {exc}")
        else:
            diagnostics.append("referência Markdown ausente")

        if manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                pages = manifest_pages(payload)
                ids = set(pages)
                bad = sorted(i for i in ids if i < 1 or i > pdf_pages)
                diagnostics.append(f"manifesto com expected_markdown: {len(ids)}/{pdf_pages} páginas, fora do PDF={bad[:8]}")
                if not bad:
                    candidates.append((len(ids), 0, "manifesto", ids, None))
            except Exception as exc:
                diagnostics.append(f"manifesto sem gabarito utilizável: {exc}")
        else:
            diagnostics.append("manifesto ausente")

        # Prioridade -1: só vence quando nenhum gabarito primário cobre tantas
        # páginas, preservando a escolha histórica de V3..V9.
        if ref_paged.is_file():
            try:
                pages = split_pages(ref_paged.read_text(encoding="utf-8"))
                ids = set(pages)
                bad = sorted(i for i in ids if i < 1 or i > pdf_pages)
                diagnostics.append(f"referência paginada: {len(ids)}/{pdf_pages} páginas, fora do PDF={bad[:8]}")
                if not bad and (pdf_pages == 1 or len(ids) > 1):
                    candidates.append((len(ids), -1, "reference_paginada", ids, ref_paged))
            except Exception as exc:
                diagnostics.append(f"referência paginada inválida: {exc}")

        if not candidates:
            errors.append(f"{version}: nenhum gabarito paginado válido ({pdf_pages} páginas no PDF). " +
                          "; ".join(diagnostics))
            continue
        count, _, source, ids, reference_path = max(candidates, key=lambda item: (item[0], item[1]))
        missing = sorted(set(range(1, pdf_pages + 1)) - ids)
        jobs.append({
            "version": version, "pdf": pdf,
            "reference": reference_path,
            "manifest": manifest if manifest.is_file() else None,
            "source": source, "reference_page_ids": sorted(ids),
            "missing_reference_pages": missing,
            "pages": pdf_pages,
            "details": "; ".join(diagnostics),
        })
        jobs[-1]["input_hashes"] = input_hashes(jobs[-1])
    if errors:
        # Exibe também os corpora utilizáveis; nenhuma extração é iniciada.
        details = [
            f"{j['version']}: gabarito={j['source']}, cobertura={len(j['reference_page_ids'])}/{j['pages']}"
            for j in jobs
        ]
        raise RuntimeError("Pré-verificação falhou:\n  " + "\n  ".join(errors) +
                           ("\nDisponíveis:\n  " + "\n  ".join(details) if details else ""))
    return jobs


def build_config():
    """Config canônica de benchmark, igual a ``evaluate_e2e._build_config``.

    Parte de ``best_extraction_config`` (tabelas, merge entre páginas e
    supressão de cabeçalho/rodapé ativos). Construir ``ExtractorConfig``
    diretamente herdaria ``enable_tables=False`` e
    ``preserve_headers_footers=True`` (T3-P00 da rodada 001).
    """
    from dataclasses import replace
    from structured_pdf_text.config import ExtractionMode, best_extraction_config

    base = best_extraction_config(language="pt-BR", preserve_headers=False)
    return replace(
        base, mode=ExtractionMode.BALANCED, ocr_quality_policy="adaptive",
        ocr_engine="tesseract", num_threads=8,
    )


def tesseract_thread_limit(num_threads: int) -> int | None:
    """``OMP_THREAD_LIMIT`` the Tesseract backend sets for ``num_threads``.

    Mirrors ``ocr/backends/tesseract.py::_run_tesseract_tsv``: -1 leaves the
    environment untouched (None), 0 uses every CPU, otherwise the value itself.
    """
    if num_threads == -1:
        return None
    return max(1, os.cpu_count() or 1) if num_threads == 0 else num_threads


def round_settings(config) -> dict:
    """Round parameters recorded in ``run_summary.json`` and run manifests.

    Built from the effective config (T3-P30): rounds 001–005 recorded a
    hard-coded ``threads: 6`` even after ``build_config()`` moved to 8.
    """
    return {
        "mode": "balanced", "language": "pt-BR", "ocr_quality_policy": "adaptive",
        "ocr_engine": "tesseract", "threads": config.num_threads,
        "tesseract_omp_thread_limit": tesseract_thread_limit(config.num_threads),
        "output": "markdown_paginated", "supplemental_audit": True,
    }


def extract_one(version: str, pdf: Path, folder: Path, expected_pages: int) -> int:
    from structured_pdf_text.api import PdfTextExtractor
    from structured_pdf_text.renderers.markdown import render_markdown
    from structured_pdf_text.evaluation.reference_hash import split_pages
    from structured_pdf_text.atomic_io import atomic_create_text
    from structured_pdf_text.benchmark_protocol import protocol_manifest
    from evaluate_e2e import _classify_page_status, _classify_document_status, _diagnostic_reasons

    config = build_config()

    def progress(current: int, total: int) -> None:
        print(f"Extracting page {current}/{total}...", flush=True)

    started = time.perf_counter()
    with PdfTextExtractor(config) as extractor:
        document = extractor.extract(pdf, progress_callback=progress)
    elapsed = time.perf_counter() - started
    status = document.diagnostics.status.value
    paginated = render_markdown(document, diagnostic=True)
    found = split_pages(paginated)
    if sorted(found) != list(range(1, expected_pages + 1)):
        raise RuntimeError(f"Paginação inválida no {version}: {len(found)} páginas / esperado {expected_pages}")
    paged_output = folder / f"{version}_extracted_paginated.md"
    atomic_create_text(paged_output, paginated)

    degraded_pages, failed_pages, recovered_pages = [], [], []
    page_entries = []
    for page in document.pages:
        facts = dict(getattr(page.diagnostics, "facts", {}) or {})
        partial_reasons, complexity_reasons = _diagnostic_reasons(page.diagnostics)
        ocr_tokens_added = int(getattr(page.diagnostics, "ocr_tokens_added", 0))
        has_content = bool(page.reading_text.strip())
        ocr_outcome = facts.get("ocr_outcome")
        if ocr_outcome is None:
            ocr_outcome = ("success" if ocr_tokens_added else "not_requested" if has_content else "unknown")
        page_status, stability = _classify_page_status(str(ocr_outcome), facts, has_content, ocr_tokens_added)
        number = page.page_index + 1
        if page_status == "invalid":
            failed_pages.append(number)
        elif stability == "degraded":
            degraded_pages.append(number)
        elif stability == "recovered":
            recovered_pages.append(number)
        page_entries.append({
            "page": number,
            "ocr_outcome": ocr_outcome,
            "content_status": ("invalid" if page_status == "invalid" else "empty" if not has_content
                               else "partial" if page_status == "degraded" else "valid"),
            "stability_status": stability,
            "partial_reasons": partial_reasons,
            "complexity_reasons": complexity_reasons,
            "warnings": list(getattr(page.diagnostics, "warnings", [])),
        })
    benchmark_status = _classify_document_status(status, failed_pages, degraded_pages)
    metadata = {
        "schema_version": "2.0",
        "run_id": f"{folder.name}_{version}",
        "engine": "tesseract",
        "mode": "balanced",
        "language": "pt-BR",
        **protocol_manifest(language="pt-BR", mode="balanced", quality_policy="adaptive"),
        "threads": config.num_threads,
        "tesseract_omp_thread_limit": tesseract_thread_limit(config.num_threads),
        "extractor_flags": {
            "enable_tables": config.enable_tables,
            "merge_cross_page_tables": config.merge_cross_page_tables,
            "preserve_headers_footers": config.preserve_headers_footers,
            "ocr_quality_variants": config.ocr_quality_variants,
            "effective_ocr_quality_policy": config.effective_ocr_quality_policy().value,
        },
        "pdf_path": str(pdf.relative_to(REPO)),
        "pdf_sha256": sha256(pdf),
        "selected_pages": [p.page_index + 1 for p in document.pages],
        "page_count": len(document.pages),
        "document_status": status,
        "benchmark_status": benchmark_status,
        "degraded_pages": degraded_pages,
        "failed_ocr_pages": failed_pages,
        "recovered_pages": recovered_pages,
        "document_warnings": list(document.diagnostics.warnings),
        "elapsed_s": round(elapsed, 3),
        "extracted_markdown": str(paged_output),
        "pages": page_entries,
        "runtime_environment": {"platform": platform.platform(), "python": platform.python_version(),
                                "requested_threads": config.num_threads, "cpu_count": os.cpu_count()},
    }
    run_manifest = folder / f"{version}_run_manifest.json"
    atomic_create_text(run_manifest, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(f"Extração: {status} | benchmark: {benchmark_status} | {len(found)} páginas | {elapsed:.2f}s", flush=True)
    print(f"Paginado: {paged_output}", flush=True)
    return 0


def run() -> int:
    parser = argparse.ArgumentParser(description="Tesseract V3 a V10: extrai e calcula métricas sequencialmente")
    parser.add_argument("rodada", help="Identificador da rodada, ex.: 001, 002, 003")
    parser.add_argument("--check-only", action="store_true",
                        help="Verificar PDFs, gabaritos e número de páginas sem executar nada")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{3,}", args.rodada):
        parser.error("A rodada deve conter pelo menos 3 dígitos, por exemplo 001")
    folder = REPO / "output" / "tesseract" / args.rodada
    if folder.exists() and not args.check_only:
        print(f"ERRO: rodada já existe, nenhum arquivo será sobrescrito: {folder}", file=sys.stderr)
        return 2
    try:
        jobs = preflight()
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    if args.check_only:
        print("PRÉ-VERIFICAÇÃO APROVADA. Nenhuma extração foi iniciada.")
        for job in jobs:
            origin = job["source"]
            coverage = f"{len(job['reference_page_ids'])}/{job['pages']}"
            print(f"{job['version']}: gabarito={origin}, cobertura={coverage}")
            if job["missing_reference_pages"]:
                print(f"  ATENÇÃO: páginas sem gabarito: {job['missing_reference_pages']}; métricas somente no escopo coberto")
        return 0

    folder.mkdir(parents=True, exist_ok=False)
    summary_path = folder / "run_summary.json"
    summary = {"rodada": args.rodada, "started_utc": utc_now(),
               "settings": round_settings(build_config()), "results": []}

    with (folder / "run.log").open("x", encoding="utf-8", buffering=1) as log:
        def say(message: str = "") -> None:
            print(message, flush=True)
            print(message, file=log, flush=True)

        def call(command: list[str]) -> tuple[int, float]:
            t0 = time.perf_counter()
            with subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                code = proc.wait()
            return code, time.perf_counter() - t0

        try:
            for idx, job in enumerate(jobs, 1):
                version = job["version"]
                pdf = job["pdf"]
                label = pdf.name
                item = {"version": version, "pdf": str(pdf.relative_to(REPO)),
                        "pages": job["pages"], "started_utc": utc_now()}
                item["input_hashes"] = job["input_hashes"]
                summary["results"].append(item)
                changed = changed_inputs(job)
                if changed:
                    item["result"] = "corpus_changed"
                    say(f"ERRO no {version}: entradas alteradas desde a pré-verificação ({', '.join(changed)}); "
                        "extração e métricas não executadas.")
                    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    continue
                say("\n" + "=" * 72)
                say(f"[{idx}/{len(jobs)}] Tesseract: {label} -> Extract")
                say("=" * 72)
                extract_cmd = ["/usr/bin/time", "-v", sys.executable, "-u", str(Path(__file__).resolve()),
                               "--extract-one", version, str(pdf), str(folder), str(job["pages"])]
                rc, sec = call(extract_cmd)
                item["extract_elapsed_s"] = round(sec, 3)
                item["extract_exit_code"] = rc
                if rc:
                    item["result"] = "extract_error"
                    say(f"ERRO no {version}: extração falhou (rc={rc}); métricas não executadas.")
                    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    continue

                run_manifest = folder / f"{version}_run_manifest.json"
                meta = json.loads(run_manifest.read_text(encoding="utf-8"))
                item["extraction_status"] = meta["document_status"]
                item["benchmark_status"] = meta["benchmark_status"]
                changed = changed_inputs(job)
                if meta.get("pdf_sha256") != job["input_hashes"]["pdf"] and "pdf" not in changed:
                    changed.append("pdf")
                if changed:
                    item["result"] = "corpus_changed"
                    say(f"ERRO no {version}: entradas alteradas durante a rodada ({', '.join(changed)}); "
                        "métricas não executadas.")
                    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    continue
                say("\n" + "=" * 72)
                say(f"[{idx}/{len(jobs)}] Tesseract: {label} -> Metrics")
                say("=" * 72)
                run_id = f"{args.rodada}_{version}"
                item["ground_truth_source"] = job["source"]
                item["reference_pages"] = len(job["reference_page_ids"])
                item["reference_missing_pages"] = job["missing_reference_pages"]
                metric_run_manifest = run_manifest
                if job["missing_reference_pages"]:
                    # Documento inteiro foi extraído, mas a avaliação só pode
                    # pontuar páginas com gabarito. Preserva o escopo original.
                    scoped_meta = dict(meta)
                    scoped_meta["extraction_selected_pages"] = meta["selected_pages"]
                    scoped_meta["selected_pages"] = job["reference_page_ids"]
                    scoped_meta["reference_missing_pages"] = job["missing_reference_pages"]
                    metric_run_manifest = folder / f"{version}_metrics_scope.json"
                    metric_run_manifest.write_text(
                        json.dumps(scoped_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                    )
                    say(f"AVISO: gabarito parcial {len(job['reference_page_ids'])}/{job['pages']}; "
                        f"páginas sem referência {job['missing_reference_pages']}. "
                        "Métricas calculadas apenas sobre páginas com gabarito.")
                metric_cmd = ["/usr/bin/time", "-v", sys.executable,
                              str(REPO / "scripts" / "compute_metrics.py"),
                              "--hypothesis", str(folder / f"{version}_extracted_paginated.md"),
                              "--run-manifest", str(metric_run_manifest),
                              "--engine", "tesseract", "--run-id", run_id,
                              "--output-dir", str(folder), "--supplemental-audit"]
                if job["reference"] is not None:
                    metric_cmd += ["--reference", str(job["reference"])]
                if job["manifest"] is not None:
                    metric_cmd += ["--manifesto", str(job["manifest"])]
                rc, sec = call(metric_cmd)
                item["metrics_elapsed_s"] = round(sec, 3)
                item["metrics_exit_code"] = rc
                if rc:
                    item["result"] = "metrics_error"
                    say(f"ERRO no {version}: cálculo de métricas falhou (rc={rc}).")
                else:
                    metric_path = folder / f"metrics_v3_tesseract_{run_id}.json"
                    evaluated = json.loads(metric_path.read_text(encoding="utf-8"))["evaluation"]
                    item["metrics_status"] = evaluated["status"]
                    item["rank_eligible"] = evaluated["rank_eligible"]
                    item["applicable_metrics"] = evaluated["applicable_metric_count"]
                    item["ineligibility_reasons"] = evaluated["rank_ineligibility_reasons"]
                    grade_ok = (meta["benchmark_status"] == "valid"
                                and evaluated["status"] == "ok"
                                and evaluated["rank_eligible"])
                    item["result"] = ("partial_reference" if grade_ok and job["missing_reference_pages"]
                                      else "ok" if grade_ok else "needs_review")
                    say(f"[{version}] Status final: {item['result']}; "
                        f"OCR {item['extract_elapsed_s']}s; métricas {item['metrics_elapsed_s']}s")
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except KeyboardInterrupt:
            say("Interrompido pelo usuário. Resultados já gravados preservados.")
            summary["interrupted"] = True
            summary["ended_utc"] = utc_now()
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 130
        summary["ended_utc"] = utc_now()
        say("\n" + "=" * 72)
        say("RESUMO DA RODADA " + args.rodada)
        say("=" * 72)
        for item in summary["results"]:
            say(f"{item['version']}: {item.get('result', 'incomplete')} | OCR "
                f"{item.get('extract_elapsed_s', '—')}s | metrics {item.get('metrics_elapsed_s', '—')}s "
                f"| {item.get('applicable_metrics', '—')} métricas")
        say(f"Relatório: {summary_path}")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if len(summary["results"]) == len(CORPORA) and all(
        item.get("result") == "ok" for item in summary["results"]
    ) else 1


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--extract-one":
        sys.exit(extract_one(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), int(sys.argv[5])))
    sys.exit(run())
