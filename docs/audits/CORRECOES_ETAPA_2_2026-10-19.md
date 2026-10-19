# Correções do avaliador — etapa 2

## Escopo

Esta etapa corrige A08, A09 e A10 da auditoria e elimina a matriz quadrática de
Levenshtein de A06. A escalabilidade do alinhamento global de corpus completos
continua pendente: agora há limites explícitos, sem notas aproximadas ou
truncamento silencioso. Não foi executada extração de PDF.

## Alterações

1. **Distância exata:** novo `evaluation/edit_distance.py`, compartilhado pelo
   avaliador e pelo diagnóstico legado de `compute_metrics.py`. Usa duas faixas
   de DP com contadores S/D/I, ampliando a largura até comprovar a distância
   ótima. Preserva os desempates do traceback original e a direção ref → hyp.
   Igualdade e entradas vazias têm atalhos exatos.
2. **Memória e trabalho:** reserva os estados de cada faixa antes de alocá-la.
   A memória depende da largura da faixa e do menor comprimento, sem matriz
   N×M. O pior caso de tempo ainda é quadrático; exceder o orçamento declarado
   interrompe o cálculo com `resource_limit`, valor nulo e motivo explícito.
3. **CER alinhado:** usa somente unidades terminais sem sobreposição. Quando
   recebe uma AST com ancestrais, calcula alinhamento textual próprio. Valida
   que cada endpoint aparece exatamente uma vez. Pares incompletos ou
   duplicados recebem `invalid_input`. O orçamento de distância é compartilhado
   pelos blocos que contribuem para a métrica; não publica um subtotal como nota.
4. **Markdown bruto:** o avaliador passa os corpos originais das páginas ao
   CER, preservando marcação, comentários, espaços e linhas em branco internas.
   O parser também retém uma fonte por página para os consumidores de nós e
   para `CanonicalDocument.raw_markdown`. NFC/LF continuam sendo normalizações
   do contrato, sem remoção de sintaxe.
5. **Projeção visível:** concatena trechos inline contíguos sem inventar espaços.
   O texto extraído dos tokens recebe normalização Unicode/layout, sem uma
   segunda interpretação de Markdown. Código, escapes e pontuação literal são
   preservados. A divisão de páginas do parser deixa de remover indentação do
   primeiro bloco.
6. **Alinhamento estrutural:** elimina a matriz de escores duplicada e verifica
   o orçamento antes do produto cartesiano. Escores iguais têm atalho exato;
   comparações textuais muito grandes têm guarda de trabalho. Ao atingir limite,
   métricas estruturais dependentes ficam nulas, sem declarar todos os blocos
   como ausentes nem publicar um resumo falso de zero matches.
7. **Identidade e elegibilidade:** `metric_definitions_version=3.0.1`, políticas
   de projeção/distância e limites registrados no resultado. Limite de recurso
   torna a avaliação `partial` e inelegível para ranking. O status operacional
   da extração não é alterado por uma limitação do avaliador.
8. **Workers:** a exceção de recurso é serializável e atravessa workers `spawn`.
   O CLI apresenta erro explícito caso os diagnósticos legados atinjam limite.

## Limites padrão

| Recurso | Limite |
|---|---|
| Distância de cada métrica sequencial | 8.000.000 estados DP, somando as faixas tentadas |
| Distâncias dos blocos do CER alinhado | 8.000.000 estados DP compartilhados pela métrica |
| Produto ref×hyp por alinhamento | 250.000 pares candidatos |
| Produto dos comprimentos para uma similaridade de bloco não idêntico | 50.000.000 |

Os dois primeiros limites configuráveis ficam em
`EvaluationRequest.max_text_edit_cells`; o número de candidatos em
`max_alignment_pairs`. A documentação dos limites não representa garantia de
avaliação completa do V4: corpus grandes e hipóteses com muitos erros podem
atingi-los. O alinhamento global ainda precisa de estratégia escalável antes
da liberação de um comparativo completo.

## Testes

- **46 novas regressões passaram**, incluindo 3.969 pares binários exaustivos,
  400 pares de tokens aleatórios com seed fixa e comparação de S/D/I contra
  um oráculo independente que usa a matriz e o traceback originais.
- Teste de 640.001 caracteres com uma substituição: contagem exata `(1,0,0)`
  dentro de 2.000.000 estados; igualdade de 700.000 caracteres com orçamento
  de um estado, sem construir matriz.
- Teste de workspace: pico abaixo de 1 MB para strings de 10.001 caracteres
  com uma substituição, monitorado por `tracemalloc`.
- Casos reais de parser para inline contíguo, código literal, indentação,
  comentários, tabelas, listas e blockquotes; contadores de referência e
  numeradores verificados, além dos valores finais.
- Falhas de recurso, acúmulo do orçamento entre blocos, validação de pares e
  propagação por worker `spawn`, sem conversão em score parcial.
- Bateria explícita de auditoria: **21 passaram, 18 falharam**. As quatro
  reproduções de texto antes reprovadas agora passam.
- Suíte completa final: **1.840 passaram, 6 skips e 4 xfails; zero falhas** (1.850 casos).

Comandos de validação:

```bash
.venv/bin/python -m pytest tests/test_evaluation_text_corrections.py -q --tb=short
.venv/bin/python -m pytest -q --junitxml=/tmp/pdfextractor_stage2.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage2.xml
git diff --check
```

O resumo estruturado está em
[evaluator_stage2_results.json](evaluator_stage2_results.json). Resultados antigos
precisam ser reavaliados para incorporar as correções; o CLI rejeita a mistura
de versões de definição diferentes na comparação.

O teste de worker passou isoladamente, mas a primeira suíte completa revelou
interferência de mocks globais de logging instalados por outros testes. Seu
processo pai agora é isolado em subprocesso, mantendo o teste real de `spawn`.

## O que ainda falta corrigir

1. **A04/A05 — tabelas e HTML:** células/linhas/tabelas perdidas precisam entrar
   nos denominadores; representar tabelas HTML e sua topologia.
2. **A06 — escalabilidade:** tornar o alinhamento global viável para corpus
   grandes, preservando a definição das métricas e os limites explícitos.
3. **A11/A12 — agregação/comparabilidade:** incluir zeros válidos nas médias;
   uma validação fail-closed compartilhada por API e CLI.
4. **A14/A16 — estrutura:** fronteiras de parágrafos e ordinais de listas.
5. **A15/A17 — parsing/ordem:** completar ordem observada no parser público;
   distinguir marcadores de página em código e validar cercas corretamente.
6. **A13 — relatórios:** publicar recuperação, estabilidade, family/conditions,
   tempos, memória e perfis no relatório canônico.
7. **A18/A19 — contrato/proveniência:** versão real do parser, schema v3
   validado e proteção contra colisão de resultados com inputs diferentes.
8. **A20 — legado:** eliminar parsers/cálculos divergentes, incluindo linhas
   vazias, pipes escapados e CER de headings.
9. **Validação final:** repetir a suíte e a bateria de auditoria depois das
   correções; testes de plataforma e extrações de corpus permanecem pendentes.
   Qualquer extração depende de autorização futura do usuário.

## Mensagem de commit sugerida

```text
fix: corrigir métricas de texto e limitar memória do avaliador

- compartilhar Levenshtein exato com contadores S/D/I e memória limitada
- usar Markdown original e preservar texto literal do parser
- alinhar unidades textuais sem sobreposição de ancestrais
- declarar limites de recurso sem produzir notas parciais
- adicionar 46 regressões de exatidão, memória e workers
```
