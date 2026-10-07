# Análise de execução e desempenho — 07/10/2026

**Atualização após a avaliação integral:** este documento preserva os ensaios e correções anteriores. A comparação final está em [Revisao_06-10-2026.md](Revisao_06-10-2026.md#resultado-integral-e-recursos): baseline concluiu em 16 min 42 s, adaptive em 1 h 59 min 47 s; exhaustive foi morto por OOM na página 96 após 3 h 39 min 53 s, sem Markdown final. Os ganhos dos ensaios isolados abaixo não representam ganho medido no documento completo. Foram documentadas novas pendências de qualidade, orientação, recursos e desempenho; GPU continua condicionada à resolução delas.

## Caso informado

```bash
python -m structured_pdf_text.cli extract corpus/V3/Document_AI_V3.pdf \
  --mode balanced \
  --ocr-quality-policy exhaustive \
  --output markdown \
  -o output/06-10-2026/Document_AI_V3.md
```

O PDF tem **144 páginas**. Esse comando seleciona EasyOCR por padrão, renderização OCR na escala **3**, inferência CPU e política exaustiva. Não habilita o perfil `--max-quality`, tabelas, tiles ou OCR de células. Entretanto, a recuperação de linhas fracas e a releitura de rodapés também usam a política exaustiva.

O tempo não corresponde a uma chamada de OCR por página. Cada imagem pode gerar cerca de vinte reconhecimentos, e cada recorte em outra escala repete esse conjunto. Isso explica um custo muito maior que o modo adaptativo. Não foi identificado um laço infinito nessa sequência.

## Correções efetuadas nesta rodada

| Caminho | Problema confirmado | Correção |
|---|---|---|
| `ocr/backends/easyocr.py` — workers | No CPU, upstream cria DataLoaders por caixa, inclusive nas tentativas de contraste; o padrão de quatro workers inicia processos repetidamente para carregar um único recorte | Padrão automático igual a zero; threads de inferência continuam independentes; overrides explícitos preservados |
| Mesmo arquivo — candidatos | Beamsearch, wordbeamsearch e ajuste de contraste repetiam a detecção com os mesmos pixels e parâmetros | Cache limitado a uma chamada exaustiva; os reconhecimentos continuam independentes; imagens e parâmetros diferentes não compartilham resultados; cópias preservam a geometria |
| Mesmo arquivo — Readers alternativos | A reconstrução exigia `lang_list` e outros atributos que o Reader real não guarda; `no_quantize` e DBNet18 eram descartados | Snapshot dos parâmetros reais de construção; mesmas línguas, rede, cache e quantização; downloads desabilitados nas variantes |
| Mesmo arquivo — ciclo de vida | Readers alternativos eram reconstruídos a cada tentativa quando seus atributos estavam disponíveis | Cache por instância, incluindo construções indisponíveis; liberado no `close`; revalidação forçada invalida o Reader DBNet18 |
| Mesmo arquivo — canvas DBNet18 | A regra de CRAFT usava o lado maior, mas DBNet18 redimensiona pelo lado menor | Cálculo específico por detector, também no fallback; probe com canvas 96 em vez do padrão 2560 |
| Mesmo arquivo — tratamento de falhas | Variantes opcionais e fallbacks absorviam falta de memória e podiam continuar com evidência incompleta | Falhas fatais e de recursos são propagadas; erros opcionais comuns continuam com a degradação existente |
| `cli.py` — setup de modelos | O probe recebia Reader sem os parâmetros necessários; a rede de reconhecimento configurada no ambiente não era provisionada pelo setup | Snapshot também no setup e uso da mesma rede nas construções de CRAFT/DBNet18 |
| `ocr/languages.py`, `ocr/readiness.py` e backend | Inglês e a opção `standard` podiam ser verificados contra o arquivo de reconhecimento errado | Resolução compartilhada de `english_g2`/`latin_g2`, preservando redes explicitamente configuradas |
| `api.py` — diagnósticos | O tempo de releitura de rodapés não aparecia separado | Inclusão de `ocr_footnote_refinement_ms` |
| Backend EasyOCR — contadores regionais | O ensaio executou 183 chamadas, mas os refinamentos informavam zero passes/batches porque não existiam os contadores esperados pela orquestração | Contadores por requisição de imagens/variantes, independentes do acumulado por página; os batches internos de reconhecimento por caixa não são incluídos |

O probe antigo de DBNet18 podia ampliar uma imagem de **96 × 320** para **2560 × 8544** pixels. O probe corrigido preserva **96 × 320**, suficientes para verificar construção e inferência. A primeira utilização de DBNet18 ainda pode compilar extensões nativas do EasyOCR; isso pertence à preparação do runtime e deve ser medido separadamente.

O cache mantém os Readers alternativos em memória até o fechamento do extrator. Esse aumento de memória residente é uma troca deliberada para evitar carregar pesos e reconstruir redes em cada recorte.

## Medições controladas

Ambiente: EasyOCR **1.7.2**, Torch **2.14.0+cpu**, torchvision **0.29.0+cpu**; PaddlePaddle **3.3.1** e PaddleOCR **3.7.0** também instalados. Não houve medição de GPU.

| Amostra | Antes | Depois | Conferência de saída |
|---|---:|---:|---|
| Imagem sintética com dez linhas; duas threads; comparação workers 4/0 | 3,168 s | 2,498 s; repetição 2,497 s | Mesmo texto nas três execuções |
| Página 72 do PDF; escala 1; duas threads; mesmos 18 candidatos | 86,412 s | 55,898 s; repetição 55,929 s | Todos os textos, confidências arredondadas a seis casas e coordenadas iguais |

Na segunda amostra houve **35,3% menos tempo**, com detecção reduzida de **18 para 15** execuções e **três reutilizações**, sem remover reconhecimentos. A comparação isolou workers/cache e manteve a configuração anterior das variantes: `no_quantize` e DBNet18 ainda não participavam, por falta dos parâmetros de construção. A correção posterior que torna essas variantes operacionais amplia o conjunto quando o runtime é funcional. Portanto, **35,3% não é uma previsão para o documento inteiro nem para o conjunto ampliado**.

### Pipeline na configuração do comando informado

Foi executada a **página 72 inteira**, com `mode="balanced"`, `ocr_quality_policy="exhaustive"`, escala padrão **3**, threads automáticas (**10 threads efetivas**) e seleção limitada por `page_indices=(71,)`. CRAFT, `no_quantize` e **DBNet18 funcional** participaram. O processo terminou com **exit code 0**, documento em status **success**, sem avisos de degradação, e 526 caracteres de leitura. A contagem de caracteres não equivale a uma avaliação de precisão contra o corpus.

| Medida | Valor |
|---|---:|
| Duração total local | **573,842 s — 9 min 34 s** |
| OCR inicial | **198,130 s** |
| Recuperação regional | **372,776 s** |
| Avaliação de rodapés, sem releitura necessária | 0,001 s |
| Chamadas EasyOCR na página | **183** |
| Detecções efetivamente executadas nas variantes | **154** |
| Reutilizações de detecção | **29** |
| Regiões fracas avaliadas | 3 |
| Escalas avaliadas nos refinamentos | 2 + 3 + 3 |
| Pico de RSS do processo | **12.570.939.392 bytes — 11,71 GiB** |

A primeira passagem executou vinte variantes. As oito imagens de recuperação produziram mais 163 chamadas, incluindo variantes condicionais de deskew. Dois refinamentos foram aceitos; o grupo maior não atingiu o ganho de qualidade exigido. **A recuperação regional consumiu aproximadamente 65% do tempo total.** Assim, otimizar somente o primeiro OCR não resolve o custo dominante deste caso.

O ensaio confirma que o modo exaustivo continua caro na CPU após eliminar trabalho repetido. Não há comparação antes/depois do pipeline completo nesta configuração; não atribuir a ele o ganho de 35,3% da amostra em escala 1. Houve outras verificações locais durante partes do ensaio, de modo que este tempo caracteriza o caso executado e não um benchmark isolado de hardware.

O sistema disponibiliza cerca de 15,5 GiB de RAM e 4 GiB de swap. Após os ensaios, cerca de 1,37 GiB de swap estava ocupado; os contadores do kernel também indicavam atividade de swap desde o boot. Esses dados não atribuem essa atividade a uma chamada específica. Na medição completa, registrar CPU, RSS e entrada/saída de swap ao longo do tempo para separar custo de inferência de paginação. O pico observado mostra que considerar apenas os bytes RGB do raster subestima a memória dos tensores intermediários.

As evidências foram preservadas em [pagina_72_pipeline.json](output/revisao_2026-10-07/pagina_72_pipeline.json) e [comparacao_18_candidatos.json](output/revisao_2026-10-07/comparacao_18_candidatos.json). O primeiro JSON registra o ensaio antes da correção final dos contadores regionais: seus zeros em `ocr_targeted_refinement_passes/batches` são a inconsistência encontrada, e **não** ausência de trabalho. `easyocr_calls`, tempos e número de tentativas registram o trabalho efetivo. A correção desses contadores foi validada com o backend e o refinador nos testes, sem alterar o reconhecimento.

Uma reprodução da configuração por página pode ser feita pela API:

```python
from structured_pdf_text.api import PdfTextExtractor
from structured_pdf_text.config import ExtractorConfig

config = ExtractorConfig(
    mode="balanced", ocr_quality_policy="exhaustive", page_indices=(71,),
)
with PdfTextExtractor(config) as extractor:
    document = extractor.extract("corpus/V3/Document_AI_V3.pdf")
    facts = document.pages[0].diagnostics.facts
    print(document.diagnostics.status.value)
    print(facts["easyocr_calls"], facts["timings_ms"])
    print(facts["ocr_targeted_refinement_passes"])
```

## Conferência do código e limites

A varredura estática analisou **114 arquivos Python** de `src` e `scripts`, incluindo referências a funções. Os helpers privados sem chamadas no fluxo principal foram conferidos: são wrappers de compatibilidade, helpers de testes ou integrações, como `_split_columns`, `_can_continue`, `_emit_prose_block` e `_apply_deskew`. A falta de uma chamada interna não foi tomada como prova de defeito, nem esses wrappers foram removidos.

As dezesseis combinações de apropriação de tabelas passaram, com uma ocorrência por valor, sem fallback duplicado e com separação das palavras residuais. As pendências corrigidas foram removidas de [Revisao_06-10-2026.md](Revisao_06-10-2026.md); R54 continua adiado.

A suíte completa passou: **783 testes**, **39 avisos**, **32,66 s**, com saída de processo zero:

```bash
.venv/bin/python -m pytest -o addopts='' -q
```

Os novos testes cobrem equivalência dos candidatos, isolamento do cache, parâmetros de detecção, reutilização e fechamento dos modelos, Readers com a interface real do upstream, probes, setup, línguas e propagação de falta de memória. Um teste anterior foi ajustado para contar reconhecimentos ao comparar políticas: detecções compartilhadas deixaram de representar a quantidade de candidatos.

A medição completa das 144 páginas e a avaliação de precisão do corpus ainda precisam ser executadas para confirmar o tempo total após as correções. Os ganhos locais não comprovam que as 7–10 horas foram eliminadas. A implementação de GPU deve respeitar essa etapa de validação.
