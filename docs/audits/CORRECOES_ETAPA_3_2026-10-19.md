# Correções do avaliador — etapa 3

## Escopo

Correções A04/A05: preservação global de células, linhas e tabelas; projeção
de conteúdo HTML e representação explícita de células mescladas. Não foi
executada extração de PDF nem OCR de corpus. Os documentos V3/V5 foram usados
somente pelos seus gabaritos Markdown em testes de regressão.

## Contrato das métricas

- **Cell Exact Match:** `exact / (paired + missing + extra)`. Células ausentes
  não recebem crédito, inclusive quando eram vazias. A antiga taxa condicional
  aos pares permanece como `cell_content_exact_match_matched`, com cobertura
  `paired / reference_cells`. Não substituir a taxa global pela condicional.
- **Cell CER:** soma das distâncias dos pares, deleções de todas as células
  esperadas sem hipótese e inserções de todas as células sem referência,
  dividida pela soma dos caracteres de todas as células de referência.
  Inserções em células cujo texto esperado era vazio também entram no numerador.
  A métrica pode exceder 1. Não adiciona um caractere artificial ao denominador.
- **Referência global vazia:** CER nulo, `undefined_denominator` se houver texto
  inserido; `not_applicable` se ambos os textos forem vazios. A perda de uma
  tabela com células vazias continua penalizada nas métricas estruturais.
- **Tabela perdida:** com conteúdo esperado não vazio, Cell CER 1; exatidão,
  alinhamento e preservação de spans 0. Linhas de tabelas não pareadas entram
  em FN/FP da detecção de linhas. A acurácia de largura também inclui tabelas
  ausentes e extras em seu denominador.
- **Alinhamento de células:** compara colunas das âncoras após pareamento por
  conteúdo dentro das linhas; perdas e extras permanecem no denominador.
- **Topologia:** nova `cell_span_accuracy` (MET-T12), que compara rowspan/colspan
  nos pares de âncoras; células não pareadas entram como falhas. Métricas de
  ordem de linhas/colunas e localização de página permanecem distintas.

## HTML

O novo módulo `evaluation/html_tables.py` usa o parser HTML da biblioteca
padrão, sem novas dependências. Emite TABLE → TABLE_ROW → TABLE_CELL, com
`row_index`, `column_index`, `rowspan`, `colspan` e indicação de cabeçalho.
As reservas de slots usam intervalos: um colspan grande não expande o grid
nem duplica o texto. A largura compara posições ocupadas, não o número bruto
de elementos `<td>`.

Também preserva captions, texto antes/depois da tabela, conteúdo inline,
entidades HTML, quebras `<br>`, parágrafos dentro de células e células vazias.
Comentários, script/style/template/head e elementos com `hidden` não fornecem
texto. O parser Markdown conserva ainda a separação de palavras em `<br>`
inline de tabelas GFM. IDs, texto bruto e posições de origem são determinísticos,
incluindo Unicode e CRLF.

### Limites explícitos

- `rowspan=0` ocupa até o fim do grupo de linhas; spans positivos são limitados
  ao grupo existente. A métrica compara esses spans efetivos.
- Tabelas aninhadas, spans sobrepostos/inválidos, conteúdo fora de células ou
  captions e blocos HTML de tabela incompletos geram `ValueError`, em vez de
  uma avaliação que silenciosamente descarta conteúdo.
- Uma tabela precisa estar inteira em um bloco HTML do Markdown. Linhas em
  branco internas podem encerrar esse bloco no CommonMark e são rejeitadas
  explicitamente quando deixarem uma tabela aberta.
- Não há navegador nem interpretação de CSS externo. Este suporte não pretende
  calcular a visibilidade resultante de folhas de estilo arbitrárias.
- O pareamento de tabelas/linhas continua usando o algoritmo determinístico
  por similaridade já existente; não é uma solução global ótima de atribuição.
- A escalabilidade global do alinhamento permanece pendente (A06).

## Recursos e identidade

O Cell CER passa a usar o Levenshtein exato da etapa 2, com orçamento compartilhado
por todas as células dessa métrica. `max_text_edit_cells` configura tanto os
orçamentos textuais quanto esse orçamento independente de tabela, registrado
também como `resource_policy.max_table_edit_cells`. Exaustão produz valor nulo,
`resource_limit`, aviso e avaliação `partial`, inelegível para ranking. Não
publica a distância parcial acumulada como resultado completo.

As definições passam a **3.0.2**; o schema permanece `evaluation-3.0.0`.
Políticas registradas: `html-table-anchors-v1` e `all-cell-anchors-v2`.
Resultados anteriores precisam de reavaliação antes da comparação.

## Validação

- **45 regressões novas passaram**, com verificações exatas de numeradores,
  denominadores, cobertura, FP/FN, estados e fonte original. Incluem 80 casos
  de perda total gerados com seed fixa, inserções que levam CER acima de 1,
  spans grandes sem expansão, controles de orçamento cumulativo e integração
  dos dois CLIs com a API canônica.
- **V3:** as dez tabelas HTML de dez páginas foram reconhecidas; controles de
  identidade e deleção completa passaram.
- **V5:** as duas tabelas HTML de duas páginas passaram pelos mesmos controles.
- Resultados JUnit desses controles, conforme o diretório solicitado:
  `outputs/loop/0001/V3/validacao_gabarito_html_etapa3.xml` e
  `outputs/loop/0001/V5/validacao_gabarito_html_etapa3.xml`.
  Esses controles não são medições da qualidade de um extrator.
- Suíte padrão: **1.885 passaram, 6 skips e 4 xfails; zero falhas** (1.895 casos).
- Auditoria independente: **25 passaram, 14 falharam**, em 39 verificações.
  Passaram as três reproduções de perdas de tabelas/linhas e a de descarte HTML.
  A auditoria permanece fora do `testpaths` padrão e é executada explicitamente.

Comandos:

```bash
.venv/bin/python -m pytest tests/test_evaluation_table_corrections.py -q --tb=short --junitxml=/tmp/pdfextractor_stage3_table.xml
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage3.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage3.xml
.venv/bin/python -m pytest tests/test_evaluation_table_corrections.py -q -k 'available_corpus and V3' --junitxml=outputs/loop/0001/V3/validacao_gabarito_html_etapa3.xml
.venv/bin/python -m pytest tests/test_evaluation_table_corrections.py -q -k 'available_corpus and V5' --junitxml=outputs/loop/0001/V5/validacao_gabarito_html_etapa3.xml
git diff --check
```

## Pendências para as próximas etapas

1. Agregação de zeros válidos e comparabilidade compartilhada entre API/CLI.
2. Fronteiras de parágrafos, ordinais de listas e ordem observada no parser.
3. Marcadores de página dentro de código e validação de cercas Markdown.
4. Relatório canônico com resultados operacionais completos.
5. Schema formal v3, versão real do parser e proteção contra colisão de saídas.
6. Divergências dos parsers e métricas legados.
7. Escalabilidade do alinhamento e validação final do conjunto completo.

O resumo estruturado está em [evaluator_stage3_results.json](evaluator_stage3_results.json).
O avaliador ainda não está liberado como referência definitiva.

## Mensagem de commit sugerida

```text
fix: penalizar perdas de tabelas e preservar gabaritos HTML

- incluir células, linhas e tabelas ausentes nas métricas globais
- contar deleções e inserções no CER de células com orçamento exato
- representar tabelas HTML e spans sem duplicar texto
- adicionar cobertura, exatidão condicional e acurácia de spans
- validar regressões e controles dos gabaritos V3 e V5
```
