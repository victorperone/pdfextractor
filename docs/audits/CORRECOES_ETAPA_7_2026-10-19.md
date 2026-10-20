# Correções do avaliador — etapa 7

## Escopo

Correção **A13**: completar a observabilidade no caminho canônico usado pelo
comparador e pelo relatório de reavaliação. Base: `9a97a97`. O relatório passa
a apresentar informações que já eram coletadas pela extração e diagnósticos
canônicos por página, necessários para analisar famílias e condições do corpus.

Schema permanece `evaluation-3.0.0`, definições de métricas **3.0.4**.
O protocolo aditivo de diagnóstico é
`evaluator.reporting_policy = canonical-page-diagnostics-v1`.
As fórmulas e os resultados de qualidade do documento não foram substituídos
por médias dos diagnósticos. O novo protocolo integra a identidade de publicação:
artefatos anteriores precisam de novo run ID ou diretório ao serem reavaliados.

Nenhuma extração de corpus PDF para Markdown, commit ou push foi executado.
Foram usados testes sintéticos, subprocessos dos CLIs de Markdown e controles
com os gabaritos existentes de V3 e V5.

## Alterações

### Uma implementação de métricas para documento e página

`evaluation/evaluator.py` compartilha `_populate_quality()` entre os dois escopos.
O novo campo `per_page` contém, para cada página selecionada com gabarito:

- ID, família e condições do manifesto do corpus;
- presença/ausência na hipótese, referência vazia e hipótese vazia, em campos
  distintos;
- todos os grupos canônicos de qualidade, integridade e estado da avaliação;
- resultados operacionais da página, preservados do manifesto de execução.

Cada página tem orçamento independente com os mesmos limites registrados no
avaliador. Seu alinhamento é local; a ordem entre páginas e as entidades
deslocadas continuam avaliadas nas métricas globais. Um limite global pode
deixar a avaliação parcial mesmo com diagnósticos locais disponíveis. O caso
inverso não autoriza apresentar um diagnóstico indisponível como zero erros.
Os contadores globais de métricas aplicáveis não somam novamente os diagnósticos.

Uma página ausente, inclusive página 1, não vira uma página emitida vazia.
Páginas fora do escopo não entram nos diagnósticos nem nos agrupamentos.
Limites de recursos, valores N/A e estados FP/FN são preservados com contadores,
avisos e escopo `page` nas métricas escalares.

### Metadados e proveniência

`evaluation/reporting.py` valida IDs, duplicações, aliases `page`/`page_num` e
tipos de `family`/`conditions`. Condições declaradas como string viram um único
rótulo; listas são deduplicadas e ordenadas. Campos inexistentes permanecem sem
rótulo. A validação também cobre manifestos usados apenas como metadados junto
com `--reference`, incluindo o V3.

Família/condição da engine não substituem os rótulos do corpus. Resultados de
execução e de página são copiados para que mutações posteriores do objeto de
entrada não alterem o snapshot retornado.

Sem estado operacional informado, `benchmark_status` agora é `null`: avaliar
Markdown não comprova uma extração válida. Estados explicitamente informados
continuam preservados. O estado do cálculo de métricas segue em `evaluation`.

O escopo da extração medida é preservado em `operational.run_selected_pages`.
O recorte usado para avaliar permanece em `inputs.selected_pages` e
`operational.selected_pages`. Reduzir o recorte de avaliação não divide o tempo
total por um número menor de páginas nem altera contadores declarados do run.
Sem escopo de extração conhecido, s/página fica indisponível.

### Relatórios canônicos

`evaluation/report_renderer.py` é usado pelo comparador canônico e pelo
relatório de erros de `regrade_existing.py`. Além dos grupos de qualidade já
existentes, apresenta:

1. Omissão por palavras, calculada a partir dos contadores D/N do WER;
   precisão/recall de moeda e identificadores, usando TP/FP/FN canônicos.
   Denominador zero não recebe um valor inventado.
2. Engine/run ID de `inputs`, estados de conteúdo, documento, estabilidade,
   avaliação, elegibilidade e motivos de inelegibilidade.
3. Cálculos indisponíveis com estado e avisos, separando-os de N/A.
4. Páginas selecionadas, ausentes, fora do escopo, emitidas vazias e falhas de
   conteúdo. A taxa de falha de conteúdo significa referência visível não
   vazia sem texto visível na hipótese, incluindo ausência; não equivale à
   taxa de exceções do backend.
5. Até 20 piores páginas por CER normalizado, com desempate pelo número da
   página. Páginas sem CER disponível são listadas com seu estado, fora desse
   ranking.
6. Breakdown por família/condição: macro-média por página e cobertura
   `aplicáveis/total`. Zeros definidos participam, N/A e indisponibilidade não.
   Páginas com várias condições participam de cada grupo; os grupos não são
   somáveis.
7. Recovery/fallback declarados, listas de páginas recuperadas/degradadas/com
   falha, observações de falha/timeout/partial/recovery com cobertura, erros,
   avisos e motivos operacionais por página.
8. Render/OCR/assembly/elapsed, s/página da extração registrada, RSS do pai,
   pico amostrado da árvore em MiB, complexidade e ambiente quando registrados.
9. Identidade completa de implantação, protocolo e memória registrados.

Contagens de hard failure e timeout no relatório referem-se explicitamente a
`ocr_outcome`; incluem `failed`/`runtime_error`, além de `failure`/`error`.
`unknown` não é uma observação de sucesso. Essas contagens não reconstroem um
histórico de exceções e retries que o manifesto não contenha. Motivos, avisos,
falhas declaradas e estados são exibidos separadamente.

O caminho canônico só destaca/computa médias com escalares finitos e status
numérico aplicável. Um payload com valor numérico e estado `resource_limit`
não pode vencer uma comparação nem entrar num agrupamento.

Execuções do mesmo engine mantêm colunas distintas pelo run ID, com sufixo
quando necessário, sem colapsar valores num dicionário. Texto arbitrário de
metadados é escapado para não introduzir HTML ativo ou quebrar células Markdown.
Artefatos canônicos antigos sem `per_page` continuam legíveis, com a ausência
do detalhamento explicitamente indicada. Exportações legadas de compute ainda
são diagnósticas; sua consolidação é a pendência A20.

## Validação

Os resultados estruturados e as contagens efetivas dos JUnit estão em
[evaluator_stage7_results.json](evaluator_stage7_results.json).

Comandos usados:

```bash
.venv/bin/python -m pytest tests/test_evaluation_reporting_corrections.py -q --tb=short --junitxml=/tmp/pdfextractor_stage7_reporting.xml
.venv/bin/python -m pytest tests/test_evaluation_reporting_corrections.py tests/test_evaluation_artifact_corrections.py tests/test_compare_engines.py tests/test_evaluation_input_contract.py tests/test_evaluation_unified_phase4.py tests/test_pr7_homologation.py -q --tb=short --junitxml=/tmp/pdfextractor_stage7_targeted.xml
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage7.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage7.xml
git diff --check
```

Os controles locais dos gabaritos usam páginas 1/2/3 do V3 e 1/12/23 do V5,
incluindo marcadores adjacentes a tabelas HTML. Verificam identidades de texto,
rótulos reais, escopo e presença dos agrupamentos. Não medem a qualidade de
extração de nenhuma engine. Evidências JUnit locais:

- `outputs/loop/0001/V3/validacao_relatorio_etapa7.xml`;
- `outputs/loop/0001/V5/validacao_relatorio_etapa7.xml`.

A bateria independente permanece sem alterar as assertivas originais. A13
passa; as três falhas restantes são:

- `test_legacy_parser_keeps_empty_structural_table_row`;
- `test_legacy_parser_keeps_escaped_pipe_inside_one_cell`;
- `test_legacy_heading_cer_sees_text_error`.

## Pendências

1. **A18:** schema JSON v3 formal e invariantes entre estados, valores e contadores.
2. **A20:** consolidar/corrigir métricas e parser legados de tabelas e títulos.
3. **A06 e validação final:** escalabilidade do alinhamento, execução real de
   corpus e gates de plataforma, incluindo Windows.

Diagnósticos por página acrescentam computação e volume de saída. Esta etapa
não conclui a validação de desempenho nem autoriza considerar uma avaliação
parcial como um resultado completo para escolha do OCR default.

## Mensagem sugerida para commit

```text
fix: completar relatório canônico e diagnósticos por página

- compartilhar métricas entre escopos de documento e página
- preservar estados, recovery, perfis, tempo e memória nos relatórios
- publicar breakdowns com cobertura e separar escopo medido do avaliado
- corrigir IDs e manter colunas distintas para runs do mesmo engine
- adicionar regressões e controles com gabaritos V3 e V5
```
