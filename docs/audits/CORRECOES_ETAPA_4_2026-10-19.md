# Correções do avaliador — etapa 4

## Escopo e resultado

Correções A11/A12: incluir zeros válidos nas estatísticas e compartilhar uma
única validação de comparabilidade entre a API pública e o CLI.
As definições passam a **3.0.3**; o schema permanece `evaluation-3.0.0`.
Não foi executada extração de PDF, OCR de corpus, commit ou push.

## Agregação

- Valores numéricos finitos dos estados `ok`, `false_negative_only` e
  `false_positive_only` entram em macro, mediana, extremos, pior caso e
  cobertura. Assim, agregar F1 1 e F1 0 retorna macro **0,5**, em vez de 1.
- N/A e estados de indisponibilidade continuam excluídos das estatísticas
  escalares. Booleanos, strings e NaN/Infinity não são escores numéricos válidos.
- O resumo de aplicabilidade do documento usa a mesma regra. Zeros válidos
  contam como métricas aplicáveis e não bloqueiam ranking por falta de métricas.
  Objetos de diagnóstico sem `value`/`status` não entram nessa contagem.
- A micro exige os dois nomes de contadores e calcula a razão das somas.
  Contadores ausentes, negativos ou de tipo inválido geram `ValueError`, sem
  substituição silenciosa por zero. Não mistura definições, direções, unidades
  ou perfis de normalização diferentes.
- Páginas com referência vazia e inserções mantêm o numerador completo, mesmo
  que sua taxa local seja `undefined_denominator`. Ausências corretas N/A
  contribuem com 0/0 e não diluem a taxa global.
- Uma página cuja avaliação está indisponível/incompleta torna a micro global
  nula. A macro pode continuar disponível sobre o subconjunto aplicável, com
  cobertura explícita; esse subconjunto não é publicado como micro completa.
- Inteiros são somados exatamente antes da divisão. A mediana de valores
  finitos muito grandes usa média que evita overflow intermediário.

Política registrada no resultado: `aggregation_policy=finite-numeric-outcomes-v1`.

## Comparabilidade compartilhada

O novo módulo `evaluation/comparability.py` define a chave e a validação.
`aggregation.check_comparability()` e os wrappers canônicos de
`scripts/compare_engines.py` delegam à mesma implementação.

### Identidades obrigatórias e iguais

1. Schema canônico `evaluation-3.0.0`.
2. `reference_effective_sha256`, `pdf_sha256` e `manifest_sha256`: SHA-256
   hexadecimal de 64 caracteres; maiúsculas/minúsculas não alteram o digest.
3. Protocolo reconhecido `reference-effective-v1`.
4. `selected_pages`: array não vazio de inteiros positivos únicos. A ordem
   da lista não altera o escopo. Duplicatas, booleanos, floats e strings são
   rejeitados; não são convertidos silenciosamente em páginas inteiras.
5. Modo de extração e identificador do protocolo de benchmark.
6. Versão das definições e políticas de normalização, parser, alinhamento,
   entidades, projeção textual, distância, HTML, tabelas, agregação e
   comparabilidade.
7. Limites de recurso declarados como inteiros positivos, incluindo os
   orçamentos textuais, de tabelas e de alinhamento.

### Regras de falha explícita

- Omitir o mesmo campo nos dois resultados não comprova equivalência.
- Hash bruto legado não substitui hash efetivo. Schemas antigos precisam de
  reavaliação, mesmo quando os dois arquivos têm o mesmo schema antigo.
- Proveniência é validada também para um único run. Seções malformadas geram
  diagnóstico de incomparabilidade, sem crash da API.
- O parâmetro público `strict=False` é mantido por compatibilidade de chamada;
  não enfraquece os requisitos. O bypass diagnóstico continua explícito no
  CLI (`--skip-validation`), com aviso de comparação heterogênea no relatório.
- Arquivos solicitados ausentes, globs sem correspondência, JSON ilegível ou
  JSON cujo topo não é objeto causam falha do CLI. Nenhum deles é removido
  silenciosamente do conjunto. Falha de validação preserva o relatório anterior.
- A verificação de schema não agrupa por nome da engine: nomes repetidos não
  escondem um arquivo de schema incompatível.

### Resultados que não alteram comparabilidade

Páginas perdidas/extras, status parcial, recuperação, fallback, tempos, RAM,
qualidade, elegibilidade de ranking, identidade da engine, hipótese, timestamps
e commit de implementação não definem identidade de entrada. O hash bruto do
arquivo de referência também não substitui o conteúdo efetivamente avaliado.
Essas informações continuam preservadas como proveniência/resultados.

Política registrada: `comparability_policy=canonical-inputs-and-policies-v1`.
Resultados anteriores devem ser reavaliados para obter a identidade completa.
Markdown sem proveniência ainda pode ser avaliado; a comparação homogênea
exige as identidades acima. Esta etapa não fabrica hashes de PDF/manifesto
nem preenche identificadores ausentes com valores sintéticos em produção.

## Testes

- **180 novas regressões passaram**, incluindo matriz de omissão e diferença
  para todos os campos obrigatórios, tipos malformados, protocolo desconhecido,
  limites de recurso, equivalência API/CLI e preservação do relatório anterior.
- Distribuições confrontadas com oráculo independente de frações em **200
  casos aleatórios com seed fixa**. Controles adicionais usam dados críticos
  reais do parser, inserções sem denominador local e contadores inteiros grandes.
- **396 testes direcionados passaram**, incluindo os contratos anteriores de
  integração e homologação. Fixtures positivas antigas foram migradas para
  identidades canônicas completas; a rejeição de legado tem controle explícito.
- Suíte padrão: **2.065 passaram, 6 skips e 4 xfails; zero falhas**, em 2.075 casos.
- Auditoria independente: **29 passaram, 10 falharam**, em 39 verificações.
  As quatro reproduções A11/A12 antes reprovadas agora passam.
- `git diff --check`: passou.

Comandos principais:

```bash
.venv/bin/python -m pytest tests/test_evaluation_comparison_corrections.py -q --tb=short --junitxml=/tmp/pdfextractor_stage4_comparison.xml
.venv/bin/python -m pytest tests/test_evaluation_comparison_corrections.py tests/test_compare_engines.py tests/test_pr6_integrity_aggregation.py tests/test_pr7_homologation.py tests/test_evaluation_unified_phase2.py tests/test_evaluation_input_contract.py -q --tb=short --junitxml=/tmp/pdfextractor_stage4_targeted.xml
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage4.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage4.xml
git diff --check
```

Os testes desta etapa usam Markdown sintético e arquivos temporários; não foi
necessária execução de documento real. O resumo estruturado está em
[evaluator_stage4_results.json](evaluator_stage4_results.json).

## Pendências

1. **A14/A16:** fronteiras de parágrafos e ordinais de listas.
2. **A15/A17:** ordem observada no parser público, marcadores de página dentro
   de código e validação de cercas Markdown.
3. **A13:** relatório canônico com resultados operacionais e identificadores
   completos, além dos recortes por família/condição.
4. **A18/A19:** versão real do parser, schema formal v3 e proteção contra
   colisões de resultados com entradas diferentes.
5. **A20:** divergências de parsers/métricas legados.
6. **A06 e validação final:** escalabilidade de alinhamento e testes finais
   de corpus/plataformas. O avaliador ainda não está liberado como referência
   definitiva nem como base final de escolha de OCR default.

## Mensagem de commit sugerida

```text
fix: incluir zeros válidos e unificar comparabilidade do avaliador

- preservar resultados FP/FN-only nas estatísticas e cobertura
- calcular micro somente com contadores completos e válidos
- compartilhar validação fail-closed entre API e CLI
- exigir proveniência, políticas e limites de recurso compatíveis
- rejeitar arquivos ausentes ou inválidos sem reduzir a comparação
- adicionar 180 regressões de agregação e comparabilidade
```
