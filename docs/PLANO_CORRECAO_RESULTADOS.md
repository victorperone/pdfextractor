# Resultado da execução do plano de correção

## Ponto de partida e validação

- Branch: `fix/pdfextractor-correcoes`.
- Commit inicial: `cc36103527374579ca03b4e7163b72e09644b9a0`.
- Árvore de trabalho inicial limpa.
- Ambiente disponível: WSL2/Linux x86_64; `python3` é Python 3.10.12. O projeto declara Python >=3.12.
- Baseline: `python3 -m pytest -q` executou a suíte e encontrou duas falhas preexistentes: `overlay` nativo exigia pesos OCR ausentes e um teste ainda esperava PP-OCRv5 como modelo padrão.
- Resultado após as mudanças: `python3 -m pytest -q` aprovado. Também foram executados testes focados e uma chamada subprocesso do módulo CLI para overlay nativo e overlay OCR sem perfil.
- Python 3.12 está disponível, mas não tem `pytest` instalado; `python3.12 -m pytest -q` não pôde executar. O ambiente também não tem o entry point `pdftext` instalado, então a validação da CLI foi feita por `python -m structured_pdf_text.cli` sobre o código do workspace.
- Não há pesos OCR no cache local: não foi executada inferência real. A API de métricas Windows foi validada com mock; não há Windows nativo disponível para teste de plataforma.

## Itens aprovados nesta rodada

| Item | Resultado |
| --- | --- |
| 1 — `setup-models` / `models-status` | Ambos já estavam registrados e aparecem na ajuda. Não foram duplicados. Ajuda, perfil desconhecido e retorno de prontidão foram cobertos superficialmente, sem inferência ou validação profunda dos pesos. |
| 2 — `pt-v6-medium` | Adicionado como alias do mesmo objeto de perfil `pt`; ambos usam os pesos PP-OCRv6 medium. Perfil desconhecido falha sem substituição. |
| 3 — PP-OCRv6 padrão | `pt` usa detecção e reconhecimento PP-OCRv6 medium. `pt-v5` continua explícito; não há fallback para v5. Metadados agora registram o perfil e os nomes dos modelos usados. |
| 4 — documentação e ajuda | README e ajuda foram alinhados aos perfis, seleção explícita de OCR, `overlay`, resolução, timeouts e códigos de saída. Exemplos publicados foram revisados contra o parser. |
| 8 — limite de pixels | O cálculo usa dimensões inteiras arredondadas por `ceil`, igual ao PDFium. A escala pedida é preservada quando a área cabe, inclusive na fronteira; só é reduzida acima do limite. Reduções são registradas em `render_limit_reductions`. |
| 10 — `overlay` | O padrão é nativo e não valida nem inicializa OCR. `balanced` e `ocr` exigem perfil explícito; perfil com modo nativo é rejeitado. Cache customizado é aceito. Os demais comandos de execução OCR também distinguem perfil informado de padrão omitido. |
| 12 — timeouts | O timeout cooperativo de documento e o status `timed_out` foram removidos. O inventário não encontrou outros prazos de execução controlados pelo projeto. Chamadas bloqueadas por dependências podem aguardar indefinidamente; métricas de duração e progresso permanecem. |
| 13 — estado e exit code | Avisos permanecem no relatório sem degradar automaticamente o resultado. Falha de página, OCR necessário, recuperação regional necessária e detecção necessária de tabela são marcados como parciais/falha. Sucesso retorna `0`; parcial/falha operacional retorna não zero; erro de uso retorna `2`. Saídas parciais continuam gravadas quando possível. |
| 14 — referência | O relatório preserva `requested_reference`; `effective_reference` só é preenchida quando a referência pedida concluiu com sucesso e há base comparável. Nenhum outro adaptador é escolhido como substituto. |
| 15 — comparação válida | Uma comparação exige referência disponível e pelo menos dois adaptadores válidos. Falhas de adaptadores restantes marcam a comparação como parcial; sem referência ou sem mínimo comparável, falha. Salvar JSON diagnóstico não altera o estado nem o exit code. |
| 19 — memória | Coleta centralizada em bytes, com fonte, disponibilidade e escopo. Linux/WSL usam `VmRSS`/`VmHWM`; Windows usa `GetProcessMemoryInfo`. Pico é o high-water mark do processo desde seu início; processos filhos não são somados. Métricas indisponíveis e máximos agregados indisponíveis são `null`, não zero. |

## Itens adiados, sem alteração de algoritmo

- Item 5: fora do escopo desta rodada.
- Itens 6 e 7: validação profunda de integridade/inferência dos modelos continua adiada; `models-status` permanece uma verificação superficial.
- Item 9: nenhuma alteração na ativação/configuração ou no algoritmo de detecção de tabelas. A suspeita sobre `enable_tables=False` continua para triagem futura, com cenários nativo/OCR/híbrido e contagem de chamadas do detector.
- Item 11: nenhuma alteração de rotação, geometria ou junção de tabelas. Reproduzir títulos e continuidade em páginas 0°, 90°, 180° e 270° antes de propor mudança.
- Itens 16 e 17: nenhuma alteração na grade/renderização de células mescladas ou na heurística de cabeçalho. Investigar tabelas com `rowspan`/`colspan`, sem cabeçalho e cabeçalho repetido.
- Item 18: nenhuma alteração em confiança OCR ou composição do texto. Investigar primeiro quando `content_blocks` fica ausente e se o fallback alternativo é alcançável sem duplicar linhas OCR.

## Evidência executada e limites

- Suíte completa no ambiente com `pytest`: aprovada.
- CLI via módulo: `overlay` nativo terminou com `0` e criou o PNG sem modelos; `overlay --mode balanced` sem perfil terminou com `2` antes de abrir o PDF.
- Teste automatizado cobre o caso bloqueante de todos os adaptadores falharem: o JSON diagnóstico é emitido e a CLI retorna `1`.
- Métricas Windows foram exercitadas por mock e métricas Linux/WSL no processo atual. A execução real em Windows nativo e a execução OCR com pesos instalados permanecem pendentes por indisponibilidade do ambiente.
