# Correções do avaliador — etapa 5

## Escopo

Correções **A14, A15, A16 e A17** e da identidade do parser em **A18**.
Definições **3.0.4**, schema `evaluation-3.0.0`.
Nenhuma extração de PDF, OCR de corpus, commit ou push foi executado.

## Parágrafos

- `paragraph_detection_f1` exige um par de blocos do alinhador e a preservação
  de ambos os extremos do intervalo de tokens do parágrafo. Páginas distintas
  não compartilham correspondências. Dividir/deslocar/juntar parágrafos deixa
  de receber detecção perfeita apenas por haver igual quantidade de blocos.
- `paragraph_boundary_f1`, precision e recall medem separadamente as divisões
  internas. Início/fim de página são excluídos; nenhuma divisão nos dois lados
  resulta em N/A. A falta de um parágrafo único continua penalizada na detecção.
- Tokens são palavras do texto visível do parser, separadas por whitespace,
  preservando caixa, acentos e pontuação. Não se somam ancestrais. A sequência
  de parágrafos é avaliada na ordem observada, independentemente por página.
- A correspondência usa o caminho exato de Levenshtein; desempate determinístico
  diagonal, deleção e inserção. Substituições podem preservar as divisões;
  erros de texto continuam medidos pelos CER/WER próprios.
- O traceback usa um byte por estado e quatro bytes por linha. Bandas crescem
  até certificar o caminho ótimo. Todas as tentativas/páginas compartilham um
  orçamento `max_text_edit_cells`, independente dos demais grupos. Esgotar o
  orçamento torna os seis resultados nulos com `resource_limit`, avaliação
  parcial e ranking bloqueado, sem publicar um resultado aproximado como exato.
- Política registrada: `paragraph_policy=page-token-spans-exact-gaps-v1`.
- O T25 existente agora exige **F1 de detecção 0 e F1 de fronteiras 0** para
  `Long paragraph one / Short` versus `Long / paragraph one Short`.
- IDs de definições estruturais foram corrigidos para o catálogo do módulo:
  headings S01–S03, parágrafos S04, grupos/itens/hierarquia S05–S07, ordem de
  leitura S08, confusão S09, ordem de blocos S10 e fronteiras S11. O CER de
  headings e a detecção de parágrafos não compartilham mais o mesmo ID.

## Listas

- A AST conserva `list_start` no contêiner ordenado e `ordered_ordinal` em cada
  item. O texto visível continua sem os marcadores de lista.
- Compara-se o ordinal **renderizado pelo CommonMark**: início mais índice do
  item, incluindo início em zero, delimitadores `.`/`)` e reinícios aninhados.
  Repetir `1.` ou trocar um marcador posterior para `99.` não altera o ordinal
  renderizado. Diferenças literais continuam disponíveis no CER Markdown bruto.
- `1.,2.` versus `5.,6.` resulta em precisão ordinal **0**, em vez de N/A.
  O universo é o dos itens de referência ordenados que possuem correspondência
  com outro item; itens perdidos também são penalizados pela detecção de itens.
- Há compatibilidade com nós fabricados antigos que incluíam o número no texto.

## Páginas, HTML, cercas e fonte

- `markdown_syntax.py` compartilha a gramática de marcadores entre parser,
  carregador de referência/manifesto, validade e diagnóstico de bordas.
  Marcadores são cabeçalhos na coluna zero, fora de código, elementos HTML,
  citações e listas. Não se cria página com texto de exemplo dentro de código.
- O parser público preserva a ordem física das páginas. O avaliador central
  continua ordenando somente a referência por ID para a identidade efetiva;
  a hipótese conserva a ordem observada.
- Há uma regra explícita de protocolo para o delimitador imediatamente depois
  de um elemento HTML concluído, mesmo sem linha em branco. O V5 depende disso:
  os marcadores **12 e 23** seguem `</table>` imediatamente. A tokenização
  CommonMark isolada os absorveria e reduziria indevidamente 42 páginas a 40.
  O contexto HTML conserva marcadores dentro de elementos/comentários; texto
  literal ou uma cerca posterior ao HTML não expõe marcadores internos.
- Política: `page_syntax_policy=top-level-page-markers-outside-code-html-v1`.
  NFC/LF e a serialização/hash de páginas mantêm `reference-effective-v1`;
  corrigir a interpretação dos delimitadores altera os inputs antes do hash,
  não o algoritmo de serialização de um mapa de páginas já determinado.
- Validade de cercas usa tokens e mapas de fonte do parser. Comprimento do
  delimitador, crases/tis, indentação, contêineres e fechamentos automáticos
  são respeitados. Cercas sem fechamento são contadas por bloco, não por
  paridade de strings de três crases. CommonMark permite fechamento automático;
  o diagnóstico do avaliador continua reportando essa ausência como inválida.
- Offsets são posições de caracteres no documento original. CRLF/LF/CR são
  preservados e o fim de fonte não ultrapassa o documento. Separadores Unicode
  como U+2028 não são confundidos com novas linhas do CommonMark.
- Identidade do parser usa a versão instalada (`markdown-it-py-4.2.0+gfm` neste
  ambiente). As novas políticas são obrigatórias e iguais na comparabilidade.
  Artefatos anteriores precisam de reavaliação antes da comparação homogênea.

## Validação

- **108 testes novos passaram** em `test_evaluation_structure_corrections.py`.
- Oráculo independente de matriz completa: **961 pares exaustivos** de
  sequências repetidas e **200 pares aleatórios** com seed 183, confrontando
  todos os vértices do caminho e a filtragem de fronteiras.
- **256 combinações** de todas as divisões de cinco tokens confrontam
  contadores TP/FP/FN de fronteiras e intervalos, sem depender de contagem igual.
- Controle de 20.000 tokens com uma edição certifica o caminho em menos de
  61.000 estados. Orçamento compartilhado, indisponibilidade e bloqueio de
  ranking têm regressões próprias.
- **488 testes direcionados passaram**, incluindo parser, estrutura, fases
  anteriores, inputs e comparabilidade. A matriz de comparabilidade ganhou
  quatro casos para omissão/diferença das duas políticas novas.
- Controles de gabarito: **V3 144 páginas e V5 42 páginas**, com autoavaliação
  estrutural por página, incluindo ordinais reais. Resultados JUnit em
  `outputs/loop/0001/V3/validacao_gabarito_estrutura_etapa5.xml` e
  `outputs/loop/0001/V5/validacao_gabarito_estrutura_etapa5.xml` (arquivos ignorados
  pelo Git). São testes sobre Markdown existente, sem executar os PDFs.
- Auditoria independente: **34 passaram, 5 falharam**, em 39 verificações.
  As cinco reproduções desta etapa agora passam sem alterar suas assertivas.
- Suíte padrão: **2.177 passaram, 6 skips e 4 xfails; zero falhas**, em 2.187
  casos. `git diff --check` passou.
- Resumo estruturado em
  [evaluator_stage5_results.json](evaluator_stage5_results.json).

Comandos principais:

```bash
.venv/bin/python -m pytest tests/test_evaluation_structure_corrections.py -q --tb=short --junitxml=/tmp/pdfextractor_stage5_structure.xml
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage5.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage5.xml
git diff --check
```

## Pendências

1. **A13:** relatório canônico com estados operacionais, recovery, perfis,
   tempo/RAM, identificadores e recortes por família/condição.
2. **A18:** schema formal v3 e invariantes entre status, valores e contadores.
3. **A19:** impedir sobrescrita de artefatos por runs com entradas diferentes.
4. **A20:** convergir tabelas e CER de headings nos caminhos legados.
5. **A06 e validação final:** escalabilidade de alinhamento e revisão final de
   corpus/plataformas. Os controles de identidade não comprovam a qualidade
   de uma extração real nem encerram essa validação.

As cinco falhas restantes da auditoria reproduzem relatório operacional,
colisão de artefatos, linhas vazias/pipe escapado do parser legado e CER legado
de heading. O avaliador ainda não está liberado como referência definitiva
para escolha do OCR default. Esta etapa para para a revisão solicitada.

## Mensagem de commit sugerida

```text
fix: validar fronteiras de parágrafos e preservar estrutura Markdown

- medir intervalos e fronteiras com alinhamento exato de tokens e orçamento
- conservar início e ordinais renderizados de listas na AST
- preservar ordem física e distinguir marcadores de código e HTML
- reconhecer páginas após tabelas HTML completas no gabarito V5
- validar cercas pela gramática e corrigir offsets de fonte
- registrar versão real do parser e políticas nas definições 3.0.4
- adicionar 108 regressões e controles dos gabaritos V3/V5
```
