# Correções do avaliador — etapa 1

## Escopo concluído

Esta etapa corrige a entrada e a integração do avaliador: A01, A02, A03 e A07
da [auditoria original](AVALIADOR_GABARITO_2026-10-19.md). Também preserva
os metadados operacionais de A13 e a ordem observada das páginas no caminho
canônico de A15. Esses dois achados ainda têm trabalho pendente.

- Criada a API `EvaluationRequest` / `evaluate()` no pacote `evaluation`.
  `evaluate_document()` fornece a forma de objeto para os relatórios;
  `_run_v2()` permanece como wrapper de compatibilidade.
- Os dois CLIs usam o mesmo carregamento de gabarito, divisão de páginas,
  interpretação do manifesto de execução e avaliador canônico.
- O recorte é validado antes das métricas. Texto, estrutura, tabelas, dados
  críticos e integridade recebem somente as páginas selecionadas com gabarito.
  Páginas selecionadas ausentes da hipótese continuam no denominador. Páginas
  extras ficam em `inputs.extra_pages_out_of_scope`.
- Alterações em conteúdo fora do recorte não mudam `quality`, `integrity`
  nem o hash efetivo. Dados críticos do diagnóstico legado também passaram
  a respeitar o recorte.
- Escopo vazio, IDs inválidos e seleção sem gabarito são rejeitados.
- Manifesto usado como gabarito exige `expected_markdown` textual em cada
  página. String vazia explícita é aceita. Páginas duplicadas, IDs conflitantes,
  campos nulos e marcadores incompatíveis são rejeitados.
- `expected_markdown` com marcador próprio é reconstruído sem duplicação.
  A carga das 224 páginas do manifesto V4 foi exercitada por teste, sem avaliar
  seu texto completo. O manifesto V3 sem gabarito exige referência separada.
- Markdown sem marcadores é tratado como página 1 nos dois CLIs.
- Falha canônica retorna erro explícito antes de escrever resultados.
  Uma tentativa inválida não sobrescreve um artefato anterior; os runners
  propagam o erro e não encaminham esse artefato ao comparador.
- Os três runners selecionam `metrics_v3_*` produzido por `compute_metrics.py`.
  O Bash também define corretamente o caminho da hipótese por backend.
- O regrade preserva recovery, estabilidade, protocolo, identidade, tempos
  e memória do manifesto operacional. Sua escrita JSON/relatório é atômica.
- O parsing por página do avaliador central mantém a ordem observada da
  hipótese. O gabarito segue a ordem canônica dos IDs, consistente com seu
  hash efetivo. A inversão física de páginas passou a reduzir reading order nesse
  caminho; o parser público independente ainda será revisto em etapa posterior.
- Corrigida falha adicional encontrada pela integração: a exibição sem
  `--quiet` aceita métricas N/A. A sugestão de comando aponta ao JSON canônico.

## Validação

| Verificação | Resultado |
|---|---|
| Suíte padrão completa | **1.794 passaram, 6 skips, 4 xfails; zero falhas** — 1.804 casos |
| Novas regressões em `tests/test_evaluation_input_contract.py` | **58 passaram** |
| Bateria explícita de auditoria | **17 passaram, 22 falharam**; falhas restantes documentadas abaixo |
| Sintaxe Bash dos dois runners alterados | Passou |
| `git diff --check` | Passou; avisos de conversão LF/CRLF em PowerShell |

Os testes novos incluem API, CLIs, igualdade das métricas com manifesto ou
Markdown, invariância do recorte, páginas ausentes, dados inválidos, preservação
operacional e falhas canônicas simuladas. A integração executa o loop Bash com
extração e preflight simulados, e os CLIs reais de métricas e comparação sobre
Markdown sintético. Também verifica propagação de falhas de cálculo e comparação.
PowerShell recebeu verificação do caminho selecionado, sem execução nativa Windows.

Na bateria de auditoria, a variante que envia um arquivo legado ao comparador
foi corrigida para exigir sua rejeição, como pede o contrato v3. A seleção real
do runner agora é validada pela integração; aceitar schema legado não é solução
para A01. Portanto, a diferença entre as contagens original e atual inclui essa
correção da expectativa do teste, além das correções de produção.

Comandos executados:

```bash
.venv/bin/python -m pytest tests/test_evaluation_input_contract.py -q --tb=short
.venv/bin/python -m pytest -q --junitxml=/tmp/pdfextractor_stage1.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage1.xml
bash -n scripts/run_benchmark.sh scripts/run_v3_v4_comparison.sh
git diff --check
```

## Pendências para as próximas etapas

O avaliador ainda não está liberado como referência definitiva. As 22 falhas
restantes da bateria explícita abrangem tabelas/células ausentes, conteúdo HTML,
Markdown bruto e projeção visível, sobreposição no alinhamento, fronteiras de
parágrafos, ordinais de listas, marcadores em código e cercas, macroagregação,
comparabilidade pública, relatório operacional, identidade real do parser,
colisões de resultados e parsers legados. O custo quadrático de memória do
Levenshtein permanece pendente; não foi executado sobre o corpus completo.

A próxima etapa proposta é corrigir o cálculo de texto e seu consumo de memória
(A06/A08–A10), antes de avançar para tabelas e HTML. As alterações RapidOCR
PP-OCRv5 existentes antes desta etapa foram preservadas. Nenhuma extração de
PDF para Markdown, commit ou push foi executado.

## Mensagem de commit sugerida

```text
fix: unificar entradas e escopo do avaliador canônico

- centralizar avaliação e validação de gabaritos no pacote evaluation
- aplicar seleção de páginas a todas as métricas e preservar metadados de execução
- corrigir artefatos dos runners e tornar falhas canônicas explícitas
- adicionar 58 regressões de API, CLI e integração sem extração
```
