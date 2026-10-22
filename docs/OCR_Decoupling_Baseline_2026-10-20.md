# Baseline para o desacoplamento de OCR — 20/10/2026

Estado: **captura concluída com uma exceção local autorizada; PR-01 a PR-03
integrados após a captura**. O EasyOCR V4 foi cancelado a pedido do usuário em
182/224 páginas; o usuário informou que possui esse baseline em outra máquina.
O log e o comando parcial foram preservados, sem tratá-los como resultado
completo. O EasyOCR V5 foi concluído em `balanced` com política `adaptive`.
O código usado nas capturas permaneceu congelado; o desenvolvimento ocorreu na
cópia isolada `/tmp/pdfextractor-decoupling-pr01-03`.

## Escopo e referência

- Código original: `54885e699190015c0bd4c026a3b24f2b296aed8c`.
- Os três arquivos locais que já existiam fora do Git foram preservados:
  `docs/Plan_Decoupling_OCR_Engines.md`, a revisão de extrações de 20/10 e seu script.
- Diretório da captura: `outputs/baseline-20-10-2026/`, ignorado pelo Git.
  `environment.json` guarda hashes do código, PDFs, dependências e configurações;
  `working-tree.patch` e `untracked-snapshot/` preservam o estado inicial.
- V3: 144 páginas; V4: 224; V5: 42. O arquivo V5 existente tem extensão `.pdf`.
  Os resultados dos três corpora são Markdown, inclusive V4.
- EasyOCR local: V3 e V5 completos; V4 parcial e cancelado conforme instrução
  do usuário. A captura V4 disponível em outra máquina não foi importada para
  este diretório de resultados.
- Nenhum arquivo do extrator nativo foi alterado.

A matriz principal contém as quatro engines solicitadas. `rapidocr/` usa
ONNX Runtime; `rapidocr-openvino/` registra o segundo provider separadamente.
Cada processo usa o runner E2E existente com `--mode balanced --language pt-BR
--save-features`, sem `--allow-partial`. O construtor de configuração é
`scripts.evaluate_e2e._build_config`, que parte do preset `best`, usa a política
OCR `adaptive`, habilita tabelas e junção entre páginas e omite cabeçalhos
repetidos. As escalas existentes são EasyOCR 3, Paddle 2, Tesseract 4 e Rapid 3.
O termo *baseline* deste experimento não significa a política OCR `baseline`.

Os arquivos canônicos têm a forma
`V3/easyocr/Document_AI_V3.md`,
`V4/paddleocr/Corpus_Stress_OCR_Markdown_V4.md` e
`V5/easyocr/Document_AI_V5.md` e `V5/rapidocr/Document_AI_V5.md`. Cada diretório também conserva o Markdown
original do runner, features, diagnósticos, manifesto e log. Os nomes públicos
de diretório `paddleocr` e de engine CLI `paddle` são intencionais.

### EasyOCR V5 e cancelamento do V4

O V5 foi extraído em 21/10/2026 pelo código original, com `--mode balanced`,
42 páginas, `document_status=success`, `benchmark_status=valid` e
`quality_policy=adaptive` no manifesto. A execução levou 1668,5 s. O manifesto
registra três avisos de recuperação de linhas por fallback de texto, sem
páginas degradadas ou falhas OCR. O Markdown canônico está em
`outputs/baseline-20-10-2026/V5/easyocr/Document_AI_V5.md`; o log da primeira
tentativa interrompida foi mantido à parte.

As métricas V5 foram calculadas pela API canônica e comparadas com PaddleOCR,
Tesseract e RapidOCR em `outputs/baseline-20-10-2026/V5/comparison-four-engines.md`.
Como nas avaliações anteriores, `evaluation.status=partial` e
`rank_eligible=false` por `evaluation_resource_limit`; a tabela é diagnóstica,
não um ranking homologado. O V4 EasyOCR local parou em 182/224, não gerou
manifesto nem Markdown final e não participa de métricas ou comparação.

## Ambiente e modelos

Linux/WSL2, Python 3.12.13, 20 CPUs lógicas. As identidades são obtidas da
instância que executou cada documento, antes de seu fechamento.

| Configuração | Composição capturada |
| --- | --- |
| EasyOCR | 1.7.2, Torch 2.14.0+cpu, CRAFT e Latin G2; DBNet18 ausente |
| PaddleOCR | 3.7.0, PaddlePaddle 3.3.1, PaddleX 3.7.2; perfil atual PP-OCRv6 medium |
| Tesseract | 5.5.3, `por.traineddata` com SHA-256 `c4932b937207a9514b7514d518b931a99938c02a28a5a5a553f8599ed58b7deb` |
| RapidOCR ONNX | 3.9.2, ONNX Runtime 1.30.0; Det v6 small, Rec Latin v5, Cls v2 |
| RapidOCR OpenVINO | 3.9.2, OpenVINO 2024.4.0; mesma composição de modelos |

O par RapidOCR previsto pelo código atual estava ausente e foi provisionado
antes das extrações. Não houve migração de modelo:

- `latin_PP-OCRv5_rec_mobile.onnx`:
  `b20bd37c168a570f583afbc8cd7925603890efbcdc000a59e22c269d160b5f5a`.
- `ppocrv5_latin_dict.txt`:
  `3c0a8a79b612653c25f765271714f71281e4e955962c153e272b7b8c1d2b13ff`.

Os logs RapidOCR identificam os caminhos Det/Rec/Cls carregados pelo processo.
`effective-models.json` complementa o manifesto original com esses caminhos e
hashes, sem sobrescrevê-lo. O classificador é carregado pela biblioteca, mas
`with_angle_cls=False` desativa sua aplicação. `model-files.json` inventaria
pesos, dicionários e configurações, distinguindo inventário de evidência de uso.

O smoke real passou para EasyOCR, Tesseract e ambos os providers RapidOCR.
Paddle inicializou e reconheceu texto, mas o probe de precisão ficou
`incomplete`: perdeu caracteres iniciais, inclusive em `Ação`,
`segunda-feira` e `R$`. Não houve ajuste de limiares para aprová-lo.

`pip check` registra duas dependências de distribuição ausentes:
`opencv-python-headless` pedido por EasyOCR e `opencv-python` pedido por
RapidOCR. O ambiente possui `opencv-contrib-python` e os imports/inferências
funcionaram. Esse conflito permanece uma pendência de homologação; instalar
outra variante OpenCV durante a captura alteraria o ambiente de referência.

## Controles de caracterização

- Suíte original: **2491 passed, 6 skipped, 4 xfailed**, sem falhas.
  Os logs e o inventário de resultados ficam na raiz da captura.
- Matriz reduzida: 36 combinações de modo `native|fast|balanced`, engine e
  política `baseline|adaptive|exhaustive` na página nativa V5/1. Todas tiveram
  zero inicializações OCR; `native` e `fast` não renderizaram raster.
- OCR forçado: PDF sintético com valores e ID, Tesseract real nas três
  políticas; todas solicitaram OCR e concluíram o documento.
- CLI: 72 configurações capturadas no construtor, com preflight simulado e sem
  extração. A CLI atual ignora `--no-ocr-quality-variants` em `--best` e
  `--max-quality`, mantendo respectivamente `adaptive` e `exhaustive`.
  `controls/cli-config-matrix.json` conserva todas as flags efetivas.
- Imports em processos isolados: pacote, configuração, contratos, registry,
  factory e CLI não carregaram frameworks OCR. O probe estático EasyOCR
  carregou `easyocr` e Torch ao importar `easyocr.config`; os demais probes
  não carregaram runtimes. A matriz com o mesmo PATH das extrações está em
  `controls/import-matrix-runtime-path-v2.json`. A primeira matriz usou o PATH
  ambiente, sem o wrapper Tesseract, e foi preservada com essa limitação.
- Falha controlada: engine injetada retorna tokens na página 1 e falha na 2.
  O resultado preserva o conteúdo anterior e registra `partial_success`,
  `benchmark_status=partial`, `page_ocr_unavailable` e fechamento da engine.
  A captura válida usa o sufixo `controlled-ocr-failure-v2`; a tentativa inicial,
  que também falhou por um erro do fake, foi preservada e não é referência.
- Repetições V5 nas páginas 1, 3, 19, 21, 24, 30 e 31: as cinco configurações
  apresentaram igualdade exata de conteúdo, estrutura, geometria, decisões e
  Markdown entre dois processos independentes. Os relatórios definitivos usam
  `repeats/<configuração>/comparison-config-markdown-v5.json`. As primeiras
  comparações incluíram custos de montagem e carga de CPU observada; foram
  preservadas, junto das comparações corrigidas. Threads, modelos e demais
  settings continuam exigindo igualdade. Os manifestos registram páginas
  degradadas; repetibilidade não significa qualidade aprovada.

## Equivalência, qualidade e defeitos conhecidos

`scripts/compare_ocr_baseline.py` compara o documento estruturado inteiro,
mantendo ordem, células, texto, confiança, geometria, proveniência, decisões e
status. Exclui apenas campos explícitos de tempo/memória e carga de CPU observada nos diagnósticos e
registra seus valores separadamente. Tolerância geométrica é explícita e só
atinge `bbox`/`polygon` em pontos; o padrão é zero. Texto, valores, sinais,
contagens e escopo permanecem exatos. Entradas vazias ou não finitas são erro.

A CLI também exige snapshots da configuração resolvida, verifica identidade
de input/modelo/provider/ambiente e compara os bytes dos dois Markdown. Uma
troca de modelo ou flag não é declarada equivalente por acaso. Os testes
injetam perda de célula, troca de sinal, duplicação, alteração de bbox, status,
escopo, flags e Markdown.

As métricas de qualidade são calculadas pela API canônica existente, schema
3.0.0/definições 3.0.5, com seus limites originais. V3 usa seu Markdown e
manifesto; V5 usa `_REFERENCIA.md` e manifesto. V4 usa o manifesto como
referência, pois seu Markdown de referência difere nas páginas brancas 215/216.
O lock do corpus V4 foi verificado usando `corpus/V4` como raiz efetiva.
Métricas legadas não serão usadas para homologar qualidade ou ordenar engines.
As avaliações globais já concluídas apresentaram `evaluation.status=partial` e
`rank_eligible=false`, por `evaluation_resource_limit` nas métricas globais.
Os limites não foram aumentados; os resultados disponíveis e por página foram
preservados.

As extrações concorrentes servem para referência funcional. Tempo/RSS não
constituem um ranking de desempenho entre engines neste experimento.

Defeitos anteriores foram reproduzidos e preservados em `controls/`:

1. V3 nativo, páginas 128/137/141: sentinelas de conteúdo coberto aparecem no
   Markdown. É bloqueador de liberação para produção, mesmo que a refatoração
   preserve a saída. Nenhuma correção foi feita no extrator nativo.
2. V5 nativo/fast, páginas 3/30: o CNPJ de controle da lista não aparece no
   Markdown. A falha de conservação nativa permanece registrada.

Captura reproduzível, equivalência da refatoração e qualidade operacional são
critérios distintos. Resultados parciais, probes incompletos, referências
conflitantes ou métricas limitadas por recurso não recebem aprovação implícita.

## Reconstrução em um namespace novo

Provisionar as mesmas versões e os modelos inventariados; validar os hashes.
No ambiente desta captura:

```bash
export PATH="$PWD/.venv/bin:$PWD/.ocr-runtime/bin:$PATH"
export EASYOCR_MODULE_PATH="$PWD/.ocr-model-cache/easyocr"
export RAPIDOCR_REC_MODEL="$PWD/.ocr-model-cache/rapidocr/latin_PP-OCRv5_rec_mobile.onnx"
export RAPIDOCR_REC_KEYS="$PWD/.ocr-model-cache/rapidocr/ppocrv5_latin_dict.txt"
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
.venv/bin/python scripts/run_decoupling_baseline.py \
  --output-dir outputs/baseline-recapture
```

`--dry-run` lista as 15 invocações sem criar arquivos nem carregar modelos.
O runner recusa diretório existente, confere inputs antes/depois de cada run,
salva a configuração resolvida e retorna 2 se houver resultado parcial ou 1 se
houver falha. Não altera o avaliador nem as configurações de reconhecimento.

```bash
.venv/bin/python scripts/compare_ocr_baseline.py before-features.json after-features.json \
  --before-manifest before-manifest.json --after-manifest after-manifest.json \
  --before-config before-config.json --after-config after-config.json \
  --output comparison-new.json
```

Os manifests devem apontar para os Markdown preservados. O comparador cria o
relatório exclusivamente em um caminho novo; exit 0 significa equivalência,
1 diferença e 2 input inválido. A identidade do commit pode mudar na refatoração;
PDF, modelo/provider, ambiente e configuração efetiva precisam permanecer iguais.
