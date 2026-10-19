# Auditoria do avaliador com gabarito — 2026-10-19

**Resultado:** a consolidação está incompleta. Existem erros reproduzíveis que podem alterar a comparação de extratores. O avaliador ainda precisa de correções antes de sustentar a escolha do OCR default.

**Atualização posterior:** a primeira etapa de correções está registrada em
[CORRECOES_ETAPA_1_2026-10-19.md](CORRECOES_ETAPA_1_2026-10-19.md).
As contagens e reproduções abaixo descrevem o estado original auditado.

## Estado auditado e escopo

- HEAD: `439bd75` (`fix: não pontuar células vazias de tabelas ausentes`).
- A árvore de trabalho já continha dez arquivos modificados na migração RapidOCR para PP-OCRv5 Latin. Essas alterações foram preservadas e incluídas na execução da suíte.
- Documento confrontado integralmente: `/home/victor-wsl/workspace/PLANO_CORRECOES_FINAIS_OCR_DEFAULT_183ebffe.md`, 2.335 linhas.
- Foram examinados o pacote `evaluation`, os scripts de avaliação/reavaliação/comparação, os três runners, os contratos e diagnósticos E2E, os pontos EasyOCR/Paddle/RapidOCR indicados no plano, os testes correspondentes e os formatos dos gabaritos V3/V4.
- Os gabaritos e manifestos foram lidos como dados. Nenhuma extração de corpus PDF para Markdown foi executada. Os testes funcionais RapidOCR usaram a imagem sintética do preflight.
- Esta etapa acrescenta documentação e testes de auditoria. Os defeitos do avaliador identificados abaixo permanecem no estado auditado.

## Validação executada

| Verificação | Resultado |
|---|---|
| `.venv/bin/python -m pytest -q` | Exit 0: 1.736 passaram, 6 skips, 4 xfails |
| Testes independentes de requisitos do avaliador, anexos a esta auditoria | **32 falharam, 7 passaram**, em 39 verificações |
| RapidOCR PP-OCRv5 Latin, imagem sintética, ONNX Runtime | READY; CER 0; acentos, moeda, data, percentual e hífens preservados |
| RapidOCR PP-OCRv5 Latin, imagem sintética, OpenVINO | READY; CER 0; mesmas verificações |
| Verificador dos dois hashes RapidOCR | Passou |
| `bash -n` nos scripts de setup e execução envolvidos | Passou |
| `git diff --check` | Passou; Git informou conversão LF/CRLF nos arquivos PowerShell |

Os 32 testes que falharam são verificações adicionais de requisitos, agrupadas nos achados abaixo; **não representam 32 defeitos independentes**. A suíte existente está verde porque parte desses contratos não é exercitada com assertivas suficientes. O teste de exatidão de células parcialmente perdidas, por exemplo, também aponta uma escolha de contrato a explicitar: uma taxa condicional aos pares encontrados precisa ser acompanhada da cobertura.

As reproduções portáveis estão em [test_evaluator_ground_truth_audit.py](test_evaluator_ground_truth_audit.py). Elas contêm assertivas estritas para o comportamento desejado e falham no estado auditado. A coleta padrão está configurada para `tests/`; esta bateria de auditoria é executada explicitamente:

```bash
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short
```

O resumo estruturado está em [evaluator_ground_truth_results.json](evaluator_ground_truth_results.json).

## Bloqueadores confirmados

### A01 — P0: os runners e o comparador consomem contratos diferentes

`compute_metrics.py` produz `metrics_{engine}_{run_id}.json` com os grupos legados e, em paralelo, tenta gerar `metrics_v3_{engine}_{run_id}.json`. Os três runners encaminham o primeiro arquivo ao comparador. `compare_engines.py` aceita apenas `evaluation-3.0.0` e rejeita o arquivo legado.

**Reprodução:** duas execuções de `compute_metrics.py` sobre o mesmo Markdown sintético geram os dois tipos de arquivo. A comparação dos arquivos que os runners selecionam termina com erro de schema. A comparação dos arquivos `metrics_v3_*`, fornecendo identidades completas, passa.

Além disso, não existe `src/structured_pdf_text/evaluation/evaluator.py`. A avaliação central ainda está dentro de `_run_v2()` em um script operacional, embora o pacote mencione a API `evaluate()` na documentação.

**Correção:** centralizar avaliação e carregamento de referências numa API do pacote; fazer os dois CLIs e todos os runners consumirem seu contrato; manter eventuais exportações legadas como projeções desse resultado. Acrescentar integração `compute → artefato selecionado pelo runner → compare` com Markdown sintético.

Fontes: `scripts/compute_metrics.py:1858`, `scripts/run_benchmark.sh:177`, `scripts/run_benchmark.ps1:180`, `scripts/run_v3_v4_comparison.sh:40`, `scripts/compare_engines.py:506`, `src/structured_pdf_text/evaluation/__init__.py:22`.

### A02 — P0: V4 não gera o artefato canônico pelo caminho do manifesto

Das 224 páginas do manifesto V4, **222 já têm `## Página N` dentro de `expected_markdown`**. A reconstrução em `compute_metrics.py` adiciona outro marcador. O parser canônico detecta a duplicação e lança `ValueError`.

A exceção é capturada por `except Exception: pass`. O comando retorna sucesso com o arquivo legado e sem `metrics_v3_*`. Uma hipótese com marcador duplicado sofre o mesmo problema.

**Reprodução:** manifesto de uma página com `expected_markdown="## Página 1\n\nTexto."`; `compute_metrics.py` retorna 0 e não produz o JSON canônico.

**Correção:** compartilhar a reconstrução de referência já usada pelo regrade, validar marcadores e tornar falha da avaliação canônica explícita no exit code e no diagnóstico. Uma saída antiga não pode ser reutilizada após falha de uma nova avaliação.

Fontes: `scripts/compute_metrics.py:1874`, `scripts/compute_metrics.py:1904`, `scripts/regrade_existing.py:548`.

### A03 — P0: o recorte de páginas altera o hash, mas não a avaliação

`_run_v2()` calcula `effective_selected_pages` para os metadados e para o hash. O `PageScope` é construído sem receber esse recorte; os nós usados em texto, estrutura, tabelas e dados críticos abrangem o documento completo.

**Reprodução:** selecionar somente página 1, reproduzida perfeitamente, de uma referência com duas páginas resulta em CER **0,714286** e `selected_missing_pages=[2]`. O esperado para esse recorte é CER 0 e nenhuma página ausente.

Também foi confirmado que modificar somente a página 2 mantém o mesmo `reference_effective_sha256` do recorte `[1]`, mas altera `quality`. Assim, a identidade declarada não identifica todo o conteúdo realmente avaliado.

O caminho legado ainda usa a interseção global em `penalised_page_nums` para dados críticos, incluindo páginas fora do recorte.

**Correção:** definir e validar o escopo antes de calcular qualquer métrica; filtrar nós, textos e metadados uma única vez. Páginas selecionadas sem gabarito precisam de tratamento explícito. Acrescentar a invariância: alterar conteúdo fora do recorte não altera nenhuma métrica daquele recorte.

Fontes: `scripts/regrade_existing.py:154`, `scripts/regrade_existing.py:160`, `scripts/regrade_existing.py:166`, `scripts/regrade_existing.py:210`, `scripts/compute_metrics.py:1761`.

### A04 — P0: tabelas e linhas perdidas desaparecem do denominador do Cell CER canônico

O módulo canônico calcula CER somente dentro de tabelas e linhas pareadas. Células de tabelas não pareadas entram em FN da detecção, mas não no numerador/denominador do Cell CER. Linhas não pareadas dentro de uma tabela também ficam fora desse CER.

**Resultados reproduzidos:**

- Tabela inteira perdida: `cell_cer=null`, `not_applicable`; o plano pede CER 1 para conteúdo esperado não vazio.
- Tabela com uma linha removida: `cell_cer=0` e `cell_content_exact_match=1`, embora existam células esperadas ausentes.

**Correção:** acumular deleções de todas as células esperadas sem hipótese e inserções sem correspondente. Para exatidão condicional aos pares encontrados, publicar cobertura e um indicador separado de preservação global; cumprir a regra do plano para tabela inteiramente perdida.

A correção de tabelas no script legado passa nos testes básicos, mas não foi incorporada ao módulo que gera `evaluation-3.0.0`.

Fonte: `src/structured_pdf_text/evaluation/table_metrics.py:386`, `:417`, `:447`, `:465`, `:471`.

### A05 — P0: conteúdo HTML previsto pelo corpus é descartado pelo parser

O contrato do V4 usa HTML para tabelas complexas, sem cabeçalho e com células mescladas. **12 páginas possuem `<table>`**: 53–58 e 157–162.

O parser ignora `html_block`. Uma tabela HTML com `R$ 100,00`, inteiramente ausente da hipótese, não fornece texto, células nem moeda de referência ao avaliador canônico. O CER normalizado dessa referência fica N/A.

**Correção:** representar o conteúdo visível das tabelas HTML no modelo canônico e definir a topologia de `rowspan`/`colspan`. Recursos ainda não suportados devem ser declarados explicitamente com sua cobertura; o conteúdo não pode desaparecer silenciosamente da referência.

Fontes: `src/structured_pdf_text/evaluation/markdown_parser.py:166`, contrato `reference_contract.tables` do manifesto V4.

### A06 — P0: o cálculo global de Levenshtein é inviável para o corpus completo

`text_metrics._lev_ops()` aloca uma matriz Python `(len(ref)+1) × (len(hyp)+1)`. Não há atalho para igualdade nem um orçamento de recursos. A função é chamada sobre o texto do documento completo para várias métricas.

A leitura dos gabaritos, sem executar o cálculo de distâncias, mostrou:

| Gabarito | Caracteres visíveis projetados | Armazenamento mínimo das referências da matriz, ref=hyp |
|---|---:|---:|
| V3 | 32.841 | 8,04 GiB |
| V4 | 631.033 | 2.966,85 GiB, aproximadamente 2,90 TiB |

Esse limite inferior considera apenas ponteiros de 8 bytes. Objetos de inteiros e listas aumentam o consumo. **É uma estimativa estrutural de alocação, não uma medição de OOM**; a matriz grande não foi criada nesta auditoria.

**Correção:** calcular métricas por página com microagregação de S/D/I ou adotar um algoritmo exato com memória limitada e política de escopo equivalente. Recursos insuficientes precisam produzir estado explícito; truncamento silencioso alteraria o resultado. Revisar também a matriz global de candidatos do alinhador.

Fontes: `src/structured_pdf_text/evaluation/text_metrics.py:75`, `:90`, `scripts/regrade_existing.py:210`.

### A07 — P0: ausência de gabarito é convertida em referência vazia

Os carregadores usam `entry.get("expected_markdown", "")`. Isso confunde campo inexistente com página intencionalmente vazia.

O manifesto V3 contém metadados de 144 páginas e **nenhuma** entrada `expected_markdown`; seu gabarito está em `corpus/V3/Document_AI_V3.md`. Usar apenas esse manifesto é aceito como se as 144 referências fossem vazias.

**Reprodução:** regrade de manifesto contendo apenas `{"pages":[{"page":1,"family":"metadata_only"}]}` retorna sucesso e produz métricas.

**Correção:** exigir referência explícita quando faltar `expected_markdown`; aceitar controles vazios somente quando declarados; validar páginas duplicadas, valores nulos, IDs e cobertura da referência.

Fontes: `scripts/regrade_existing.py:108`, `scripts/compute_metrics.py:1255`.

## Outros erros e lacunas confirmados

| ID | Prioridade | Evidência e efeito | Correção proposta |
|---|---|---|---|
| A08 | P1 | `**importante**` versus `importante` recebe `cer_markdown_raw=0`. A reconstrução por folhas perde marcação e separadores. | Medir o corpo Markdown original, preservando a origem e excluindo somente os delimitadores de página definidos no contrato. |
| A09 | P1 | `a**b**c` versus `abc` recebe CER visível 0,4; o extrator de tokens insere espaços entre trechos contíguos. Em código, `**literal**` versus `literal` recebe CER visível 0 porque a normalização interpreta de novo o texto literal. | Extrair o texto respeitando a semântica dos tokens; normalizar Unicode/espaçamento sem reanalisar texto já projetado. |
| A10 | P1 | No CER alinhado, uma tabela com 15 caracteres nas células contribui com 50 caracteres de referência, somando ancestrais e descendentes. | Alinhar unidades textuais sem sobreposição para métricas de texto; usar o universo estrutural separadamente. |
| A11 | P1 | `aggregate_metric()` exclui zeros com `false_negative_only` e `false_positive_only`. Agregar F1 1 e F1 0 retorna macro 1, em vez de 0,5. | Incluir resultados numéricos definidos nesses estados; excluir N/A e indisponibilidade conforme contrato explícito. |
| A12 | P1 | A função pública `check_comparability()` aceita PDFs diferentes e hashes efetivos com protocolo ausente em ambos. O CLI já rejeita os PDFs distintos. | Uma única validação fail-closed para API e CLI, com protocolo reconhecido, identidades de entrada e versão de avaliador. |
| A13 | P1 | Regrade recebe manifesto `partial/recovered` e grava `benchmark_status="valid"`. O renderer canônico omite recovery, estabilidade, family/conditions, tempo, RAM e perfis; lê engine/run-id de locais diferentes de onde são gravados. | Preservar o manifesto operacional nos dois CLIs e publicar as áreas de relatório exigidas pelo plano no caminho efetivamente utilizado. |
| A14 | P1 | `Long paragraph one / Short` versus `Long / paragraph one Short` recebe paragraph F1 1. Não existe métrica canônica de fronteiras que cumpra T25. | Definir fronteiras sobre tokens alinhados e medir suas posições; manter detecção de parágrafos como métrica distinta. |
| A15 | P1 | Inverter a ordem física de páginas 1 e 2, mantendo seus números e conteúdos, recebe reading order 1. O parser ordena as páginas antes de atribuir `order_index`. | Preservar ordem observada e publicar localização/ordem de página sem confundi-las com conteúdo ordenado por ID. |
| A16 | P1 | Lista `1., 2.` versus `5., 6.` deixa `ordered_list_ordinal_accuracy=null`. O regex procura ordinais no texto visível, que já os perdeu. | Registrar ordinal/start na AST e comparar os atributos apropriados. |
| A17 | P1 | `## Página 2` dentro de código cria uma página real. Uma cerca válida de quatro crases contendo uma linha de três crases é classificada como inválida. | Reconhecer delimitadores de página e cercas com estado de parsing; tratar duplicações como diagnóstico estruturado. |
| A18 | P1 | Parser instalado é `markdown-it-py 4.2.0`, mas a identidade publicada é fixa `markdown-it-py-3.0.0+gfm`. O único JSON Schema versionado exige `evaluation-v2.0`. | Registrar a versão efetiva; publicar e validar schema v3 de resultados reais, com checks de finitude, estados e definições. |
| A19 | P1 | Reutilizar engine/run-id com outra hipótese sobrescreve o resultado anterior e retorna 0. O teste T49 repete os mesmos inputs e não exercita essa colisão. | Rejeitar colisão incompatível ou exigir opção explícita de substituição; validar os hashes antes de considerar reexecução idempotente. |
| A20 | P1 | O parser legado descarta linhas de tabela inteiramente vazias e divide `\|` escapado em coluna extra. O heading CER legado só calcula textos já idênticos, retornando zero ou N/A para erros. | Retirar o cálculo independente legado; derivar diagnósticos e compatibilidade da avaliação central. |

Fontes principais dos itens A08–A20: `evaluation/text_metrics.py`, `evaluation/markdown_parser.py`, `evaluation/normalization.py`, `evaluation/structure_metrics.py`, `evaluation/aggregation.py`, `evaluation/integrity_metrics.py`, `scripts/regrade_existing.py`, `scripts/compare_engines.py`, `scripts/compute_metrics.py` e `docs/evaluation-v2.schema.json`.

## Cobertura real dos testes anunciados

- **T25:** o teste denominado `different_boundary_reduces_f1` verifica somente o status. O caso reproduzido continua com F1 1.
- **T35:** há comparação dos dois CLIs com referência Markdown simples; falta equivalência usando o manifesto com marcadores embutidos do V4 e validação do recorte sobre os valores.
- **T41:** os testes não asseguram uma penalidade para inversão física de páginas; o parser elimina essa informação.
- **T49:** repetir os mesmos inputs não verifica proteção contra sobrescrita de outro resultado.
- **T50:** checar chaves e ausência de NaN/Infinity não substitui validação contra um JSON Schema v3 nem invariantes entre estados, valores e contadores.
- **Teste do último commit de células vazias:** a linha `| | |` é descartada pelo parser legado. A reprodução mostrou `cell_ref_count=2`, quando o cabeçalho e a linha vazia deveriam totalizar 4 células. O teste não exercita todas as células que seu nome sugere.
- Vários cenários usam nós AST fabricados. São úteis para métricas isoladas, mas precisam de pares equivalentes que passem pelo parser real e pelos CLIs.

## Confronto de todos os itens do plano anexado

| Item | Estado observado |
|---|---|
| P0-01 — RapidOCR Latin/PT | Par PP-OCRv5 pendente validado por hash e imagem sintética nos dois providers em Linux/WSL. Setup e defaults alterados; instalação limpa e execução nativa Windows não foram realizadas nesta auditoria. |
| P0-02 — agregação e tabelas perdidas | Correções básicas presentes no legado; caminho canônico ainda viola a penalidade de células ausentes. A04/A20. |
| P0-03 — comparabilidade baseada em inputs | CLI v3 verifica identidades e mantém resultados ruins comparáveis. API pública e integração dos runners divergem. A01/A03/A12. |
| P0-04 — estrutura de páginas ausentes | Legado inclui referências ausentes e seus testes passam. A seleção canônica e o conteúdo HTML ainda estão incorretos. A03/A05. |
| P0-05 — recovery continua avaliado | E2E separa conteúdo/estabilidade e runners usam `--allow-partial`. Regrade e renderer canônico perdem parte da observabilidade. A13. |
| P0-06 — teste EasyOCR canvas | Corrigido e passando, incluindo magnificação e override. |
| P1-01 — partial reasons | Implementado a partir de `facts["partial_reasons"]`, com complexidade separada. |
| P1-02 — document failure | Classificação E2E trata `failure` explicitamente. A propagação canônica operacional precisa de A13. |
| P1-03 — fallback acumulado | EasyOCR mantém contadores e razões por ciclo de página; testes passam. Publicação no relatório canônico ainda incompleta. |
| P1-04 — family e conditions | Preservados no legado e nos breakdowns legados. A saída canônica não carrega esse detalhamento. A13. |
| P1-05 — micro/macro | Legado distingue os nomes e usa contadores em texto/tabelas. Agregador do pacote exclui zeros F1 válidos. A11. |
| P1-06 — Bash propaga erro do compare | Implementado; atualmente o erro é acionado pela incompatibilidade dos artefatos. A01. |
| P1-07 — Paddle fd 1 e reset de protocolo | Redirecionamento de fd nativo e descarte do worker presentes; testes de protocolo passam neste ambiente. Falta evidência de execução nativa Windows para o gate de plataforma. |
| P1-08 — região sem bbox | Retorna `invalid_input`; teste passa. |
| P2-01 — múltiplas tabelas | Há matching guloso por conteúdo, com forma/ordem no legado. A consolidação ainda precisa evitar a duplicação de algoritmos e cobrir ambiguidades. |
| P2-02 — tempos separados | E2E coleta render/OCR/assembly; renderer canônico não os mostra. A13. |
| P2-03 — RAM | Coleta e renderer legado presentes; renderer canônico incompleto. A13. |
| P2-04 — perfis efetivos | Identidades são coletadas; relatório canônico não publica o detalhamento previsto. A13. |
| Gate 1 — suíte geral | Passou no estado de trabalho auditado. |
| Gate 2 — RapidOCR | Passou no teste sintético com o par PP-OCRv5 e um thread nos dois providers deste ambiente. |
| Gate 3 — métricas sintéticas | **Não passou**: os testes adicionais revelam os achados acima. |
| Gate 4 — smoke difícil de corpus | Não executado nesta auditoria, conforme restrição do usuário sobre extração de PDF. |
| Full V4 e escolha de default | Permanecem pendentes dos bloqueadores e das validações de corpus autorizadas futuramente. |

## Validação do par RapidOCR pendente

- RapidOCR `3.9.2`; ONNX Runtime `1.30.0`; OpenVINO `2024.4.0`; CPU; um thread.
- Recognizer: `latin_PP-OCRv5_rec_mobile.onnx`.
- SHA-256: `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a`.
- Dicionário: `ppocrv5_latin_dict.txt`.
- SHA-256: `3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff`.
- Os dois providers registraram `profile=latin/pt-compatible`, os dois hashes e os caminhos efetivos na identidade.
- A imagem padrão do preflight possui texto acentuado, hífens, moeda, data, percentual, CPF e CNPJ. O CER total foi zero nos dois providers; os checks explícitos de precisão também passaram.
- Os artefatos foram usados a partir de `/tmp` nesta validação. O teste não substitui instalação limpa, validação Windows ou avaliação de corpus difícil.

## Ordem proposta para a próxima etapa de correção

1. Consolidar API, carregadores de gabarito, seleção de páginas e contrato dos artefatos; eliminar exceções silenciosas e corrigir o consumo pelos runners. A01–A03/A07.
2. Tornar o cálculo de texto executável em corpus grande e corrigir projeção literal, Markdown bruto e sobreposição do alinhamento. A06/A08–A10.
3. Corrigir células ausentes e acrescentar suporte visível/estrutural ao HTML previsto pelo corpus. A04/A05/A20.
4. Unificar comparabilidade/agregação, completar fronteiras/ordinais/ordem física e validade gramatical. A11/A12/A14–A17.
5. Preservar observabilidade, publicar schema/proveniência corretos e proteger artefatos. A13/A18/A19.
6. Incorporar as reproduções como regressões permanentes durante cada correção e repetir os gates correspondentes. A bateria deve comparar valores e contadores esperados, incluindo autoavaliação, deleção, inserção, permutação, multiplicidade e independência de conteúdo fora do recorte.

Cada etapa deve produzir um conjunto revisável e parar para a revisão solicitada pelo usuário. A auditoria não executou commit ou push.
