# Correções do avaliador — etapa 8 e validação final

## Escopo e contrato

Continuação sobre `9a97a97`, junto da etapa 7 ainda na árvore de trabalho.
O pedido mais recente autoriza continuar sem pausas e executar os PDFs V3,
V4 e V5 uma vez cada, salvando em `output/loop/001/V<versão>/`.
Nenhum commit ou push foi executado.

Schema `evaluation-3.0.0`, definições **3.0.5**. Resultados anteriores exigem
reavaliação em outro namespace antes de comparação. O aumento de versão
identifica alterações de fórmulas/estados e política de alinhamento; não se
presume equivalência com 3.0.4.

## A18 — validação formal e invariantes

- JSON Schema Draft 2020-12 empacotado em `evaluation/schema_v3.json`, com
  cópia documentada em `docs/evaluation-v3.schema.json`; nenhuma resolução
  remota de referências é necessária.
- Registro explícito dos 69 escalares, com definição, direção, normalização,
  escopo, estados, valores e intervalos; CER/WER podem superar 1.
- `validate_result()` verifica números finitos em toda a árvore JSON,
  contadores inteiros, S/D/I, comprimentos, distâncias, F1, precisão/recall,
  razões de cobertura/ordem, escopo e presença dos diagnósticos por página.
- Contagens de métricas aplicáveis/indisponíveis, inelegibilidade por recursos,
  validade do Markdown e flags de páginas são verificadas entre campos.
- A API valida antes de retornar; o comparador valida antes de gerar relatório.
  `--skip-validation` permanece uma opção diagnóstica explícita.
- Dependência `jsonschema>=4.18,<5` e inclusão do schema na distribuição wheel.
  O build foi inspecionado para confirmar o recurso e a dependência.

A validação estrita também revelou e permitiu corrigir produtores inconsistentes:

1. Cobertura de tokens com denominador zero agora é `null` /
   `undefined_denominator`, preservando os contadores.
2. Direção dos CERs de títulos/listas permanece `lower_is_better`, inclusive
   quando a métrica não é aplicável ou não pode ser calculada.
3. Direção da cobertura de integridade permanece `higher_is_better`.
4. Cobertura e duplicação usam a projeção canônica de folhas: código literal
   participa e ancestrais de listas não duplicam texto.
5. Títulos extras somam inserções no CER, inclusive quando outros títulos
   foram pareados; ausência de caracteres de referência deixa a taxa indefinida.
6. Hipótese vazia continua produzindo os diagnósticos obrigatórios de validade
   e conteúdo de borda. Argumento bruto omitido continua distinguível de vazio.

## A20 — compatibilidade legada

O parser de tabelas legado agora é uma projeção da AST canônica. Preserva
linhas/células vazias, pipes escapados e tabelas HTML; texto com pipes sem GFM
válido e tabelas dentro de código não viram tabelas reais.

O CER de títulos legado usa o alinhador e a distância canônicos. Erros de
acentuação e de texto deixam de desaparecer por exigir igualdade para parear.
As exportações legadas identificam `metric_contract=legacy-diagnostics-only`.
Outros diagnósticos históricos continuam presentes por compatibilidade;
não substituem o resultado canônico usado por runners e comparador.

As 39 reproduções da auditoria original passam sem enfraquecer suas assertivas.

## A06 — exatidão com limites e atalhos comprovados

- CERs de títulos e itens de lista usam a distância exata limitada, com
  orçamento compartilhado por métrica e estado `resource_limit` explícito.
- Alinhamento de sequências normalizadas idênticas, com mesmos tipos/páginas,
  evita a matriz quadrática e produz exatamente os mesmos pares/metadados do
  algoritmo guloso. Política `content-match-v2-exact-identity`.
- Tabelas/linhas idênticas evitam matrizes redundantes; igualdade de conteúdo
  não esconde diferenças de dimensões, topologia, ordinais ou Markdown bruto.
- Contagem estrita de pares concordantes usa Fenwick em O(n log n), mantendo
  empates como não concordantes. Oráculo exaustivo cobre sequências com empates;
  controles de 100.000 posições usam contagem inteira exata.
- Casos gerais diferentes continuam sujeitos aos limites registrados; não há
  truncamento, aproximação silenciosa nem substituição de CER global por média.

Controles completos sem extração de PDF: gabarito contra ele próprio em V3
(144 páginas), V4 (224) e V5 (42). Todas as métricas numéricas são perfeitas,
sem esgotamento de recursos. No V4, o manifesto inclui explicitamente as
páginas em branco 215/216 que o Markdown de referência omite.

## Extração de características em uma passagem

`evaluate_e2e.py --save-features` acrescenta JSON estruturado e relatório
diagnóstico ao Markdown e manifesto existentes. São derivados do mesmo
`StructuredDocument`, com uma única chamada a `extract()`.
`--retain-native-evidence` preserva também caracteres e objetos PDF nativos.
Artefatos existentes são rejeitados antes da extração; publicação usa criação
atômica sem substituição. O teste do runner confirma uma chamada e nenhuma
segunda chamada ao reutilizar o namespace.

O projeto não tinha um modo denominado “extração de características”. A
execução solicitada usa `balanced` com as opções de exportação acima,
RapidOCR/OpenVINO CPU e o par Latin PP-OCRv5 já validado para português:

- recognizer SHA-256: `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a`;
- dicionário SHA-256: `3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff`.

O perfil desta execução fica registrado nos manifestos. A escolha não altera
o OCR default global. Preflight profundo em imagem sintética passou nos dois
providers (ONNX Runtime/OpenVINO), sem extrair qualquer PDF adicional.

## Evidências de testes

| Bateria | Resultado |
|---|---|
| Suíte padrão | 2.491 passaram, 6 skips, 4 xfails; nenhuma falha inesperada |
| Bateria dirigida final | 256 passaram |
| Auditoria original, incluída na dirigida | 39/39 passaram |
| Novos testes de relatório | 75 passaram |
| Schema, estados e invariantes | 85 passaram |
| Otimizações exatas, adaptadores e controles completos | 53 passaram |
| Exportação de características em uma chamada | 4 passaram |
| Preflight RapidOCR profundo em imagem, ONNX/OpenVINO | 2 passaram |
| Wheel com schema e dependência declarada | Verificado |
| `git diff --check` | Passou |

Os testes de `tests/` da bateria dirigida já pertencem à suíte padrão e não
devem ser somados novamente. A auditoria e o preflight são execuções adicionais,
fora da coleta padrão. Os resultados
estruturados ficam em [evaluator_stage8_results.json](evaluator_stage8_results.json),
e os JUnit locais em `output/loop/001/validation/`.

Comandos principais:

```bash
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage8_final.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py tests/test_evaluation_reporting_corrections.py tests/test_evaluation_schema_corrections.py tests/test_evaluation_exact_optimizations.py tests/test_evaluate_e2e_features.py -q --tb=short --junitxml=/tmp/pdfextractor_stage8_final_targeted.xml
.venv/bin/python -m pip wheel --no-deps --no-build-isolation . --wheel-dir /tmp/pdfextractor_stage8_wheel
git diff --check
```

## Execução real concluída

Os três PDFs foram extraídos **uma vez cada**, em `balanced`, com exportação
de características/evidências. Total: **410 páginas**, sem páginas selecionadas
ausentes ou extras fora do escopo na avaliação. Cada execução possui Markdown,
JSON estruturado, diagnóstico, manifesto, métricas canônicas, relatório e logs.

| Documento | Páginas | Extração (benchmark) | Tempo de extração | Avaliação global | Avaliações por página ok/parciais |
|---|---:|---|---:|---|---|
| V3 | 144 | valid | 47,775 s | partial | 144 / 0 |
| V4 | 224 | valid | 291,621 s | partial | 148 / 76 |
| V5 | 42 | partial | 19,781 s | partial | 42 / 0 |

O V5 saiu com código 2 e todos os artefatos salvos: páginas 21/31 têm
`ocr_outcome=degraded`, com `no_usable_tokens` nas evidências das regiões de
figura. Não há páginas com falha completa de OCR. `document_status=success`
não foi confundido com `benchmark_status=valid`: a degradação permanece visível.
As páginas 17/26 registram recuperação de duas linhas por fallback de texto.

As três avaliações globais retornaram métricas/relatórios e código 0, com
`evaluation.status=partial`, `rank_eligible=false` e
`evaluation_resource_limit`. Isso significa que a avaliação publicou os
cálculos disponíveis e os limites encontrados; não que todas as métricas
globais puderam ser calculadas. No V4, 150 páginas têm CER normalizado numérico,
72 atingiram o limite desse cálculo e duas são controles vazios N/A.
O F1 global de tabelas foi 0,6522: 45 TP, 0 FP e 48 FN, para 93 tabelas esperadas
e 45 observadas. A perda não desapareceu dos contadores.

O resumo local está em `output/loop/001/README.md`; a verificação está em
`output/loop/001/verification.json`, incluindo SHA-256 e tamanho dos artefatos.
Foram relidos os JSONs estruturados e métricas dos três documentos, conferindo
sequência/quantidade de páginas, evidências nativas, identidade/modelos, escopo,
finitude e contrato canônico. Os PDFs não foram extraídos novamente.

O resumo contém também agregados **por página**, calculados de S+D+I, incluindo
ausências explícitas 0/0. Uma página com cálculo incompleto impede o micro
completo; macros usam somente valores aplicáveis e mostram cobertura. Esses
agregados não substituem o CER sequencial global indisponível nem removem a
inelegibilidade do documento.

A avaliação do V4 foi substancialmente mais lenta que sua extração. Uma
amostra de métricas de tabela levou aproximadamente 18,5 s; a avaliação da
página 20, incluindo o pipeline de documento recortado e o diagnóstico local,
levou 18,1 s. Uma amostragem adicional foi limitada deliberadamente a 25 s
(exit 124 do `timeout`), somente sobre Markdown salvo. Os stacks apontam custo
da distância exata em páginas densas. Essa amostragem diagnóstica não integra
as contagens de testes aprovados. Os logs/scripts foram preservados em
`output/loop/001/validation/`.

## Limites da conclusão

Os controles de identidade verificam o avaliador, não a qualidade de OCR.
As três extrações usam um perfil; não constituem um comparativo novo entre
todos os engines nem validam Windows nativo ou instalação limpa. Avaliações
parciais por orçamento continuam inelegíveis para ranking completo. Os
diagnósticos por página disponíveis permitem inspecionar esses resultados
sem apresentá-los como uma avaliação global concluída.

A próxima melhoria de escalabilidade deve tratar hipóteses extensamente
diferentes e o custo da distância exata por página, preservando a política
canônica de desempate S/D/I. O controle de identidade completo não demonstra
esse desempenho. Os artefatos desta rodada preservam esses limites e não
sustentam uma escolha automática do OCR default.

## Mensagem para commit

[MENSAGEM_COMMIT_AVALIADOR_FINAL_2026-10-19.md](MENSAGEM_COMMIT_AVALIADOR_FINAL_2026-10-19.md)
