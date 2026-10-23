"""Operational and page sections for canonical comparison/error reports.

Only measured, applicable values enter summaries. Corpus labels never come
from engine claims; page diagnostics never substitute global document metrics.
"""
from __future__ import annotations

import html
import json
import statistics
from collections import Counter
from collections.abc import Callable

from .reporting import finite_number, metric_value, word_deletion_rate


def cell(value: object) -> str:
    """Keep arbitrary metadata inside its Markdown cell, without active HTML."""
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if isinstance(value, bool):
        value = "sim" if value else "não"
    value = html.escape(str(value), quote=True)
    for char in "\\|`*_[]":
        value = value.replace(char, f"&#{ord(char)};")
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def unique_run_labels(all_data: list[tuple[str, dict]]) -> list[tuple[str, dict]]:
    """Retain every run when several artifacts have the same engine name."""
    counts = Counter(label for label, _ in all_data)
    result, used = [], set()
    for label, data in all_data:
        candidate = label
        if counts[label] > 1:
            candidate = f"{label} / {data.get('inputs', {}).get('run_id') or 'unknown'}"
        base, index = candidate, 2
        while candidate in used:
            candidate = f"{base} [{index}]"
            index += 1
        used.add(candidate)
        result.append((candidate, data))
    return result


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    return ["| " + " | ".join(cell(v) for v in headers) + " |",
            "|" + "---|" * len(headers),
            *("| " + " | ".join(cell(v) for v in row) + " |" for row in rows), ""]


def _number(value: object, precision: int = 4) -> str | None:
    return f"{value:.{precision}f}" if finite_number(value) else None


def _nonnegative(value: object) -> float | None:
    return value if finite_number(value) and value >= 0 else None


def _memory(memory: dict, key: str) -> str | None:
    value = _nonnegative(memory.get(key))
    return _number(value / (1024 * 1024), 1) if value is not None else None


def _observed_count(pages: list[dict], field: str, expected: set[str]) -> str | None:
    observed = [p.get("operational", {}).get(field) for p in pages]
    observed = [v for v in observed if isinstance(v, str) and v not in {"", "unknown", "unavailable"}]
    if not observed:
        return None
    count = sum(v in expected for v in observed)
    return f"{count} ({len(observed)}/{len(pages)} observadas)"


_PAGE_METRICS: list[tuple[str, Callable[[dict], float | None]]] = [
    ("CER Sequential Normalized ↓", lambda p: metric_value(p, "text.cer_sequential_normalized")),
    ("WER Sequential ↓", lambda p: metric_value(p, "text.wer_sequential")),
    ("Word Deletion Rate ↓", word_deletion_rate),
    ("Heading F1 ↑", lambda p: metric_value(p, "structure.heading_detection_f1")),
    ("Cell CER ↓", lambda p: metric_value(p, "tables.cell_cer")),
    ("Table Content F1 ↑", lambda p: metric_value(p, "tables.table_content_f1")),
    ("Currency Page F1 ↑", lambda p: metric_value(p, "critical_data.currency")),
    ("Identifier Page F1 ↑", lambda p: metric_value(p, "critical_data.identifiers_combined")),
    ("Missing Page Rate ↓", lambda p: metric_value(p, "integrity.missing_page_rate")),
]


def _breakdown(all_data: list[tuple[str, dict]], field: str, title: str) -> list[str]:
    labels: set[str] = set()
    grouped: dict[str, dict[str, list[dict]]] = {}
    for engine, data in all_data:
        grouped[engine] = {}
        for page in data.get("per_page", []):
            tags = page.get(field)
            if isinstance(tags, str):
                tags = [tags]
            if not tags:
                tags = ["sem condição" if field == "conditions" else "sem família"]
            for tag in set(tags):
                labels.add(tag)
                grouped[engine].setdefault(tag, []).append(page)
    if not labels:
        return []
    lines = [f"## Breakdown por {title} do Corpus", "",
             f"Macro-média por página, agrupada por `{field}` do manifesto do corpus. "
             "Cada célula mostra valor (páginas aplicáveis/total do grupo). "
             "N/A e cálculos indisponíveis são excluídos; zeros definidos são incluídos. "
             "Páginas com várias condições participam de cada grupo; grupos não são somáveis.", ""]
    for name, getter in _PAGE_METRICS:
        lines += [f"### {name}", ""]
        rows = []
        for label in sorted(labels):
            row: list[object] = [label]
            for engine, _ in all_data:
                pages = grouped[engine].get(label, [])
                values = [value for page in pages if (value := getter(page)) is not None]
                mean = _number(statistics.mean(values)) if values else "—"
                row.append(f"{mean} ({len(values)}/{len(pages)})")
            rows.append(row)
        lines += _table([title, *(engine for engine, _ in all_data)], rows)
    return lines


def render_run_sections(all_data: list[tuple[str, dict]]) -> list[str]:
    """Four report areas: quality supplements, recall, stability, operation."""
    lines = ["## Contagens de texto e dados críticos", "",
             "Omissão = D/N das palavras no alinhamento sequencial. Precisão e recall "
             "de entidades usam os contadores TP/FP/FN do resultado canônico; "
             "denominador zero permanece indisponível.", ""]
    rows = []
    for engine, data in all_data:
        rows.append([engine, "Word Deletion Rate", _number(word_deletion_rate(data))])
        for category in ("currency", "identifiers_combined"):
            metric = data.get("quality", {}).get("critical_data", {}).get(category, {})
            counts = metric.get("counts", {})
            tp, fp, fn = (counts.get(key) for key in ("tp", "fp", "fn"))
            precision = recall = None
            if (metric_value(data, f"critical_data.{category}") is not None
                    and all(_nonnegative(v) is not None for v in (tp, fp, fn))):
                precision = tp / (tp + fp) if tp + fp else None
                recall = tp / (tp + fn) if tp + fn else None
            rows += [[engine, f"{category} Precision", _number(precision)],
                     [engine, f"{category} Recall", _number(recall)]]
    lines += _table(["Configuração / run", "Métrica", "Valor"], rows)

    lines += ["## Metadados dos Runs", "",
              "Os identificadores vêm de `inputs`. Content/stability são resultados "
              "da extração; Evaluation Status descreve o cálculo das métricas.", ""]
    rows = []
    for engine, data in all_data:
        inputs, op, ev = (data.get(key, {}) for key in ("inputs", "operational", "evaluation"))
        rows.append([engine, inputs.get("engine"), inputs.get("run_id"), data.get("schema_version"),
                     op.get("benchmark_status"), op.get("document_status"), op.get("stability_status"),
                     ev.get("status"), ev.get("rank_eligible"), ev.get("applicable_metric_count"),
                     ev.get("unavailable_metric_count"), ev.get("rank_ineligibility_reasons")])
    lines += _table(["Configuração / run", "Engine", "Run ID", "Schema", "Content status",
                     "Document status", "Stability", "Evaluation Status", "Rank Eligible",
                     "Métricas aplicáveis", "Métricas indisponíveis", "Motivos de inelegibilidade"], rows)
    lines += ["### Cálculos indisponíveis no documento", "",
              "Estados diferentes de N/A são listados com seus avisos. Esses valores "
              "não entram nas médias nem recebem destaque como melhor resultado.", ""]
    rows = []
    for engine, data in all_data:
        groups = {**data.get("quality", {}), "integrity": data.get("integrity", {})}
        for group, metrics in groups.items():
            for key, metric in metrics.items():
                if (isinstance(metric, dict) and "value" in metric
                        and metric.get("status") != "not_applicable"
                        and metric_value(data, f"{group}.{key}") is None):
                    rows.append([engine, f"{group}.{key}", metric.get("status"), metric.get("warnings")])
    lines += _table(["Configuração / run", "Métrica", "Status", "Warnings"], rows)

    lines += ["## Recall e páginas difíceis", "",
              "Contagens de conteúdo usam a projeção visível canônica. Páginas ausentes "
              "são contadas separadamente de páginas emitidas vazias. Falha de conteúdo "
              "significa referência não vazia sem texto visível na hipótese, inclusive ausência.", ""]
    rows = []
    for engine, data in all_data:
        inputs, pages = data.get("inputs", {}), data.get("per_page", [])
        total = len(pages)
        content_failures = sum(not p["reference_empty"] and p["hypothesis_empty"] for p in pages)
        rows.append([engine, inputs.get("selected_pages"), inputs.get("selected_missing_pages"),
                     inputs.get("extra_pages_out_of_scope"), total if pages else None,
                     sum(not p["selected_but_missing"] and p["hypothesis_empty"] for p in pages) if pages else None,
                     content_failures if pages else None,
                     _number(content_failures / total) if total else None])
    lines += _table(["Configuração / run", "Páginas selecionadas", "Ausentes", "Fora do escopo",
                     "Páginas diagnosticadas", "Emitidas vazias", "Falhas de conteúdo", "Content Failure Rate"], rows)
    lines += ["### Piores páginas por CER normalizado", "",
              "Até 20 páginas por run; CER decrescente, desempate pelo número da página. "
              "Métricas indisponíveis não recebem posição neste ranking.", ""]
    rows = []
    for engine, data in all_data:
        pages = data.get("per_page", [])
        ranked = [(value, p) for p in pages
                  if (value := metric_value(p, "text.cer_sequential_normalized")) is not None]
        for cer, page in sorted(ranked, key=lambda item: (-item[0], item[1]["page"]))[:20]:
            rows.append([engine, page["page"], page.get("family"), page.get("conditions"),
                         page.get("selected_but_missing"), _number(cer),
                         _number(metric_value(page, "text.wer_sequential")),
                         page.get("evaluation", {}).get("status")])
    lines += _table(["Configuração / run", "Página", "Família", "Condições", "Ausente",
                     "CER", "WER", "Evaluation Status"], rows)
    for engine, data in all_data:
        excluded = [{"page": p["page"], "status": p.get("quality", {}).get("text", {}).get(
            "cer_sequential_normalized", {}).get("status", "unavailable")}
            for p in data.get("per_page", []) if metric_value(p, "text.cer_sequential_normalized") is None]
        if excluded:
            lines += [f"Páginas sem CER disponível ({cell(engine)}): {cell(excluded)}", ""]
    lines += _breakdown(all_data, "family", "Família")
    lines += _breakdown(all_data, "conditions", "Condição")

    lines += ["## Estabilidade e recovery", "",
              "Contadores e listas de run são os declarados no manifesto. Contagens por "
              "estado mostram quantas páginas têm esse campo observado. Ausência de dados "
              "não significa zero falhas. Recovery Count e Fallback Rate podem ter chamadas "
              "OCR como denominador; não são taxas de páginas recuperadas. Os totais do run "
              "não são recalculados ao reduzir o escopo da avaliação; contagens observadas "
              "por página usam somente o escopo avaliado.", ""]
    rows = []
    for engine, data in all_data:
        op, pages = data.get("operational", {}), data.get("per_page", [])
        rows.append([engine, op.get("status"), op.get("recovery_count"), _number(op.get("fallback_rate")),
                     op.get("recovered_pages"), op.get("degraded_pages"), op.get("failed_ocr_pages"),
                     _observed_count(pages, "ocr_outcome", {"failure", "failed", "error", "runtime_error"}),
                     _observed_count(pages, "ocr_outcome", {"timeout"}),
                     _observed_count(pages, "content_status", {"partial"}),
                     _observed_count(pages, "stability_status", {"recovered"}),
                     op.get("error"), op.get("error_details"), op.get("document_warnings")])
    lines += _table(["Configuração / run", "Run status", "Recovery Count", "Fallback Rate",
                     "Recovered pages declaradas", "Degraded pages declaradas", "Failed OCR pages declaradas",
                     "Hard failures em ocr_outcome", "Timeouts em ocr_outcome", "Partial pages observadas",
                     "Recovered pages observadas", "Runtime error", "Detalhes do erro", "Warnings"], rows)
    lines += ["### Resultados operacionais por página", ""]
    rows = []
    for engine, data in all_data:
        for page in data.get("per_page", []):
            op = page.get("operational", {})
            rows.append([engine, page["page"], op.get("content_status"), op.get("benchmark_page_status"),
                         op.get("stability_status"), op.get("ocr_outcome"), op.get("easyocr_calls"),
                         op.get("easyocr_fallback_count"), _number(op.get("easyocr_fallback_rate")),
                         op.get("easyocr_fallback_reasons"), op.get("partial_reasons"),
                         op.get("complexity_reasons"), op.get("warnings"), op.get("error")])
    lines += _table(["Configuração / run", "Página", "Content status", "Benchmark page status", "Stability",
                     "OCR outcome", "OCR calls", "Fallback count", "Fallback rate", "Fallback reasons",
                     "Partial reasons", "Complexity reasons", "Warnings", "Runtime error"], rows)

    lines += ["## Operacional — observacional", "",
              "Tempo e RAM não alteram métricas de qualidade. s/página divide elapsed "
              "pelo número de páginas selecionadas na extração medida, incluindo ausentes. "
              "Sem escopo de extração registrado, s/página fica indisponível. "
              "Memória em MiB; "
              "RSS do processo pai e pico amostrado da árvore são medidas distintas.", ""]
    rows = []
    for engine, data in all_data:
        op = data.get("operational", {})
        timings, memory = op.get("timings") or {}, op.get("memory") or {}
        elapsed, n = _nonnegative(op.get("elapsed_s")), len(op.get("run_selected_pages") or [])
        rows.append([engine, _number(timings.get("render_s")), _number(timings.get("ocr_s")),
                     _number(timings.get("assembly_s")), _number(elapsed),
                     _number(elapsed / n) if elapsed is not None and n else None,
                     _memory(memory, "peak_rss_bytes"), _memory(memory, "sampled_process_tree_peak_rss_bytes"),
                     op.get("setup_complexity"), op.get("runtime_environment"), op.get("run_selected_pages")])
    lines += _table(["Configuração / run", "Render s", "OCR s", "Assembly s", "Elapsed s", "s/página do run",
                     "Parent Peak RSS MiB", "Sampled Process Tree Peak RSS MiB", "Setup complexity", "Runtime environment",
                     "Páginas do run medido"], rows)
    lines += ["### Perfis efetivos das configurações", "",
              "Identidade completa registrada no manifesto; diferenças de modelo, provider "
              "e pré-processamento pertencem ao perfil de implantação.", ""]
    lines += _table(["Configuração / run", "Perfil registrado", "Protocolo de benchmark", "Memória registrada"],
                    [[engine, data.get("operational", {}).get("engine_identity"),
                      data.get("operational", {}).get("benchmark_protocol_id"),
                      data.get("operational", {}).get("memory")] for engine, data in all_data])
    if any(not data.get("per_page") for _, data in all_data):
        lines += ["Detalhamento por página indisponível em pelo menos um run. "
                  "Reavalie esse artefato para obter os breakdowns canônicos.", ""]
    return lines


def render_error_report(result: dict, *, run_id: str, engine: str,
                        ref_path: object, hyp_path: object) -> str:
    """Canonical per-run error report; every value comes from the result."""
    evaluation, inputs, integrity = (result.get(key, {}) for key in ("evaluation", "inputs", "integrity"))
    lines = [
        f"# Evaluation Error Report (schema {result.get('schema_version')})",
        "",
        f"**Run ID:** `{run_id}`  ",
        f"**Engine:** `{engine}`  ",
        f"**Hypothesis:** `{hyp_path}`  ",
        f"**Reference:** `{ref_path or 'via manifesto'}`  ",
        f"**Created:** `{result.get('evaluator', {}).get('created_at_utc', 'N/A')}`  ",
        "",
        "## Evaluation Status",
        "",
        f"- Status: **{evaluation.get('status')}**",
        f"- Rank eligible: **{evaluation.get('rank_eligible')}**",
        f"- Applicable metrics: {evaluation.get('applicable_metric_count')}",
        f"- Unavailable metrics: {evaluation.get('unavailable_metric_count')}",
    ]
    if evaluation.get("rank_ineligibility_reasons"):
        lines += ["", "### Ineligibility Reasons", ""]
        lines += [f"- `{reason}`" for reason in evaluation["rank_ineligibility_reasons"]]

    validity = integrity.get("markdown_validity", {})
    if validity:
        lines += [
            "", "## Markdown Validity", "",
            f"- Status: **{validity.get('validity_status', 'unknown')}**",
            f"- Parse errors: {validity.get('parse_errors', 0)}",
            f"- Unclosed fences: {validity.get('unclosed_fences', 0)}",
            f"- Duplicate page markers: {validity.get('duplicate_page_markers', 0)}",
        ]
        lines += [f"- ⚠ {warning}" for warning in validity.get("warnings", [])]

    missing = inputs.get("selected_missing_pages", [])
    extra = inputs.get("extra_pages_out_of_scope", [])
    if missing or extra:
        lines += ["", "## Page Scope Issues", ""]
        if missing:
            lines.append(f"- **Missing pages** (FN for all metrics): {missing}")
        if extra:
            lines.append(f"- **Extra pages** (out of scope): {extra}")

    lines += ["", "## Quality Metrics Summary", ""]
    titles = {"text": "Text", "structure": "Structure", "reading_order": "Reading Order",
              "tables": "Tables", "critical_data": "Critical Data"}
    for group, title in titles.items():
        metrics = result.get("quality", {}).get(group, {})
        if not metrics:
            continue
        lines += [f"### {title}", ""]
        for key, metric in metrics.items():
            if not isinstance(metric, dict) or "status" not in metric:
                continue
            value = metric.get("value")
            if finite_number(value):
                lines.append(f"- `{key}`: {value:.4f} ({metric['status']}, {metric.get('direction', '')})")
            else:
                lines.append(f"- `{key}`: N/A ({metric['status']})")
        lines.append("")

    if integrity.get("duplicate_excess_rate"):
        lines += ["## Integrity", ""]
        for label, key in (("Duplicate excess rate", "duplicate_excess_rate"),
                           ("Content coverage recall", "page_content_coverage_recall")):
            value = integrity.get(key, {}).get("value")
            lines.append(f"- {label}: {_number(value) or 'N/A'}")
        lines.append("")

    lines += ["", *render_run_sections([(engine, result)])]
    return "\n".join(lines)
