# Rollback do Avaliador V2 → V1 (§14.1 Fase E)

Este documento descreve como reverter o pipeline de avaliação de V2 para V1 em caso de erro crítico descoberto pós-cutover. O rollback é reversível e não altera gabaritos, extrações Markdown nem resultados históricos.

## Quando usar este procedimento

Acionar o rollback se qualquer uma das condições abaixo for observada após o cutover para V2:

- Métricas V2 produzem valores matematicamente incorretos não identificados durante a homologação.
- O agente externo interpreta campos V2 incorretamente e toma decisões operacionais erradas.
- Regressão no tempo de avaliação que torna o pipeline inviável para o volume de produção.
- Incompatibilidade descoberta entre o contrato do agente e o esquema V2.

## O que NÃO fazer durante o rollback

- **Não reescrever** arquivos de gabarito (`.md` de referência).
- **Não apagar** arquivos JSON V2 já produzidos — eles são evidência de auditoria.
- **Não recomputar OCR** — o rollback opera apenas sobre arquivos de texto/JSON já existentes.
- **Não modificar** o histórico de commits da branch de produção.

## Procedimento passo a passo

### 1. Parar o consumo V2 no agente externo

No código do agente, restaurar o código que lê os campos legados (`grupo1_texto`, `grupo2_estrutura_markdown`, …) e **não** a chave `schema_version`. Fazer deploy desta versão antes de prosseguir.

### 2. Verificar integridade dos gabaritos

```bash
# Calcular SHA-256 dos gabaritos e comparar com os hashes registrados no PR 0
sha256sum corpus/*.md
```

Os hashes devem ser idênticos aos registrados em `docs/evaluation_v2/baseline.md`. Se divergirem, investigar a causa antes de continuar.

### 3. Restaurar compute_metrics.py V1 via git

```bash
# Identificar o commit anterior ao PR 1 (primeiro commit de V2)
git log --oneline scripts/compute_metrics.py

# Fazer checkout do arquivo no commit anterior (substituir <SHA_PR0> pelo SHA real)
git checkout <SHA_PR0> -- scripts/compute_metrics.py scripts/compare_engines.py
```

> **Atenção:** este passo restaura os scripts legados. As correções matemáticas (direção S/D/I, sem corte silencioso) **não estarão mais ativas**. Os resultados V1 voltam ao comportamento pré-correção, o que é esperado para manter comparabilidade histórica.

### 4. Verificar que os testes V1 continuam passando

```bash
python -m pytest tests/ -q --ignore=tests/test_pr1_math_corrections.py \
    --ignore=tests/test_pr2_parser_and_models.py \
    --ignore=tests/test_pr3_block_alignment.py \
    --ignore=tests/test_pr4_structure_metrics.py \
    --ignore=tests/test_pr5_tables_critical_data.py \
    --ignore=tests/test_pr6_integrity_aggregation.py \
    --ignore=tests/test_pr7_homologation.py \
    --ignore=tests/test_pr8_text_metrics.py
```

Todos os testes do pipeline de extração devem continuar passando.

### 5. Preservar artefatos V2 para diagnóstico posterior

```bash
# Mover (não apagar) resultados V2 para pasta de arquivo
mkdir -p output/evaluation_v2_archived
mv output/evaluation_v2/ output/evaluation_v2_archived/$(date +%Y%m%d_%H%M%S)/
```

### 6. Registrar o rollback

Criar um commit documentando o rollback com justificativa:

```
revert(eval): rollback avaliador V2 → V1

Motivo: <descrever o problema encontrado>
Artefatos V2 preservados em output/evaluation_v2_archived/
Gabaritos intocados (SHA verificado: OK)
```

### 7. Reativar V2 corrigido

Após corrigir a causa raiz e passar novamente pelos critérios de aceite do §0.3:

```bash
# Restaurar scripts V2
git checkout HEAD~0 -- scripts/compute_metrics.py scripts/compare_engines.py

# Re-executar suite de homologação
python -m pytest tests/test_pr7_homologation.py -v

# Reavaliar documentos do corpus sem OCR
python scripts/regrade_existing.py \
    --hypothesis <caminho_hypothesis.md> \
    --reference  <caminho_referencia.md> \
    --engine <nome_engine> \
    --output-dir output/evaluation_v2
```

## Referências

- Spec §14.1 Fase E — Rollback
- Spec §0.3 — Condições de aceite globais
- [PR 0 baseline](baseline.md) — hashes dos gabaritos e estado inicial
- `scripts/regrade_existing.py` — reavaliação sem OCR
- `src/structured_pdf_text/evaluation/aggregation.py` — `check_comparability`, `evaluate_rank_eligibility`
