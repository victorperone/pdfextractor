# Revisão da entrega OCR — 22/10/2026

## Comportamento implementado

- CPU é o padrão de `ExtractorConfig` e de todos os comandos da CLI,
  inclusive quando o Python selecionado possui runtimes CUDA.
- `use_gpu=True` na API ou `--gpu` na CLI solicita GPU. A preferência percorre
  settings, factory, presets, ferramentas e o worker Paddle.
- EasyOCR consulta CUDA no PyTorch; Paddle verifica compilação CUDA e presença
  de dispositivo; RapidOCR/ONNX carrega bibliotecas CUDA/cuDNN e verifica os
  providers efetivos do detector e reconhecedor após construir as sessões.
- Ausência de GPU ou de biblioteca/configuração CUDA produz warning e execução
  em CPU. Dependência GPU carregada somente durante inferência também permite
  uma tentativa em CPU, registrada na identidade. Uma sessão RapidOCR com CUDA
  disponível em apenas um estágio é reconstruída integralmente em CPU.
- A recuperação conserva engine, provider, idioma, pesos e autorização de
  pixels. Não instala pacotes nem baixa modelos durante a extração. A base CPU
  da engine precisa estar instalada. Erros de modelo, cancelamento e OOM
  preservam suas classificações; não se tornam sucesso por falta de CUDA.
- Paddle (`pt` e alias `pt-v6-medium`), RapidOCR/ONNX e RapidOCR/OpenVINO usam
  **PP-OCRv6 medium em Det/Rec por padrão**. Small permanece explícito.
- EasyOCR desativa quantização dinâmica CPU ao selecionar CUDA e restaura a
  configuração CPU ao reconstruir o Reader para fallback.
- Tesseract permanece CPU. O provider OpenVINO do RapidOCR 3.9.2 também fica
  em CPU; esta implementação fixa `device_name="CPU"` e não acelera a RTX 3060.
- O extrator em `src/structured_pdf_text/native/` não foi alterado.

## Ambientes desta máquina

O provisionamento foi feito em ambientes separados, sem substituir o ambiente
CPU histórico. Não reutilize `.ocr-envs/gpu` das primeiras tentativas: os
ambientes desta validação estão em **`.ocr-envs/gpu-ready-20261022`**.

| Perfil | Python relativo ao repositório | Runtime instalado |
| --- | --- | --- |
| EasyOCR | `.ocr-envs/gpu-ready-20261022/easyocr/bin/python` | EasyOCR 1.7.2, PyTorch 2.14.0+cu130, torchvision 0.29.0+cu130 |
| PaddleOCR | `.ocr-envs/gpu-ready-20261022/paddle/bin/python` | Paddle GPU 3.3.1 / CUDA 12.6, PaddleOCR 3.7.0, PaddleX 3.7.2 |
| RapidOCR/ONNX | `.ocr-envs/gpu-ready-20261022/rapidocr-onnxruntime/bin/python` | RapidOCR 3.9.2, ONNX Runtime GPU 1.30.0 / CUDA 13.0 |
| RapidOCR/OpenVINO | `.ocr-envs/gpu-ready-20261022/rapidocr-openvino/bin/python` | RapidOCR 3.9.2, OpenVINO 2026.4.1, CPU |
| Tesseract | `.ocr-envs/gpu-ready-20261022/tesseract/bin/python` | Tesseract 5.5.3, CPU |

Hardware observado: WSL2, NVIDIA GeForce RTX 3060, 12.288 MiB de VRAM,
Windows driver 610.88. Os runtimes CUDA/cuDNN estão nos respectivos virtualenvs;
não foi instalado driver Linux NVIDIA no WSL. CPU e GPU não compartilham as
distribuições que possuem os namespaces `paddle`/`onnxruntime`, e cada ambiente
possui apenas a distribuição OpenCV esperada.

O Python de `.venv` continua com runtimes CPU. Passar `--gpu` nesse ambiente
solicita CUDA e permite fallback; para usar CUDA preparada, execute o Python
da engine na tabela acima.

Exemplo EasyOCR no caminho balanced/adaptive:

```bash
.ocr-envs/gpu-ready-20261022/easyocr/bin/python -m structured_pdf_text.cli extract documento.pdf \
  --gpu --mode balanced --ocr-engine easyocr --ocr-quality-policy adaptive \
  --cache-home .ocr-model-cache --output markdown --output-file outputs/documento-gpu.md
```

Para CPU no mesmo ambiente, omita `--gpu`. Para Paddle, selecione seu Python,
`--ocr-engine paddle` e `--cache-home .ocr-model-cache/paddle-v6-e2e`. Para
RapidOCR/ONNX, selecione seu Python e
`--ocr-engine rapidocr --ocr-provider onnxruntime --cache-home .ocr-model-cache`.
Nenhum desses comandos precisa de uma flag de tamanho para usar medium.

Para os comandos Tesseract desta instalação local:

```bash
export PATH="$PWD/.ocr-runtime/bin:$PATH"
export TESSDATA_PREFIX="$PWD/.ocr-runtime/share/tessdata"
```

## Evidências e limites

O namespace desta tarefa é `outputs/validation-ocr-gpu-20261022/`. As tentativas
interrompidas e a instalação com cache offline incompleto foram preservadas;
o relatório final substitui seu estado operacional sem reescrever aqueles
arquivos. O índice consolidado é
`outputs/validation-ocr-gpu-20261022/validation-summary-final.json`: estado
**ready**, cinco instalações aprovadas em `pip check`/OpenCV/imports, 14
casos de runtime aprovados, fonte estável durante a validação e nativo intacto. Paddle tem `setup-report-recovery.json` e
`install-check-recovery.json`; a primeira tentativa permanece em
`setup-report.json`/`setup.log`.

A suíte global registra **2.975 passed, 4 xfailed, zero skips**, sem falhas:
`pytest-final-cpu-fallback.xml`. Os 75 testes de seleção/fallback GPU estão
incluídos nesse total, com casos de ausência de biblioteca na construção e na
inferência, retry limitado, preservação de OOM e defaults medium. A auditoria
independente do avaliador passou nos 39 casos. Gate AST, compilação Python,
`uv lock --check --offline` e `git diff --check` passaram.

O smoke estrito usa uma página sintética pelo pipeline autorizado. Exige
sucesso, tokens OCR e prova de inferência CUDA, além de identidade e hashes.
O teste de documento usa somente a **página 140** de
`corpus/V3/Document_AI_V3.pdf` (o PDF local fica em `corpus/V3/`, não na raiz).
Foi preservado um PDF de uma página e gerada sua versão raster a 150 dpi para
obrigar reconhecimento OCR. Nenhum documento completo foi reextraído nesta
validação. Essa amostra verifica funcionamento, não equivalência/qualidade de
V3/V4/V5.

Resultados observados na página 140 (uma página por execução):

| Configuração | CPU padrão no ambiente CUDA | GPU solicitada | GPU solicitada em ambiente sem CUDA |
| --- | --- | --- | --- |
| EasyOCR | Aprovado, CPU | Aprovado, CUDA | Aprovado, warning + CPU |
| PaddleOCR v6 medium | Aprovado, CPU | Aprovado, CUDA | Aprovado, warning + CPU |
| RapidOCR/ONNX v6 medium | Aprovado, CPU | Aprovado, CUDA | Aprovado, warning + CPU |

RapidOCR/OpenVINO medium também passou nessa página com aviso e CPU quando
GPU foi solicitada. Tesseract passou em CPU. Os 14 casos incluem essas 11
extrações e três smokes estritos GPU em página sintética. Os resultados
completos estão em `validation-final2/report.json` e nos relatórios individuais.

Prova CUDA da página sintética: ambos os modelos EasyOCR em `cuda:0`; cinco
predictors Paddle criados com GPU; ONNX Runtime com 224 eventos CUDA no detector
e 191 no reconhecedor. Dois eventos CPU do reconhecedor são operações auxiliares
do grafo; não foram apresentados como execução exclusiva de todos os nós em GPU.

Além desses casos, `balanced-adaptive-final/report.json` comprova as três
engines em balanced/adaptive, com identidade CUDA e tokens reais. O comando
CLI EasyOCR com esses parâmetros também passou; saída em
`easyocr-cli-balanced-adaptive-final.md`. Não são benchmarks de desempenho nem
certificação de todos os caminhos exhaustive/DBNet e corpus completo.

## Revisão do plano de desacoplamento

O código das etapas PR-01 a PR-14 está integrado: contratos imutáveis,
registry/settings/factories lazy, serviço autorizado de rasters, coordenador,
policies e pipelines locais, integrador com conservação/ownership/linhagem,
worker Paddle com protocolo 5, cancelamento sem deadline de inferência,
Paddle somente v6, RapidOCR v6 medium/small, quinta engine fake e ferramentas
isoladas de instalação, comparação e endurance. O registro detalhado e a
rastreabilidade estão em [OCR_Decoupling_Implementation_Status.md](OCR_Decoupling_Implementation_Status.md).

**O aceite integral do plano ainda não terminou.** Continuam necessários:

1. Equivalência por página/região com os baselines originais e revisão das
   divergências observadas em conteúdo crítico, geometria, figuras e tabelas.
2. Correção/homologação dos defeitos de qualidade registrados: perdas/falsos
   positivos EasyOCR, smoke crítico Paddle e falso positivo RapidOCR small.
   O sucesso de uma página sintética não encerra esses controles anteriores.
3. Matriz completa de corpus e endurance complexo para engines/providers.
4. Instalação, readiness, cancelamento e execução observados em Windows nativo.
   O workflow configurado não comprova uma execução remota.
5. Importação do baseline EasyOCR V4 existente na outra máquina; a captura
   local foi cancelada a pedido em 182/224 páginas.
6. Tratamento dos P0 nativos já conhecidos em uma alteração própria. Eles
   continuam bloqueadores de release; não foi alterado o extrator nativo.

Por isso os relatórios mantêm `quality_accepted=false` e
`release_accepted=false`. Funcionamento CPU/GPU não aprova esses gates.

## Mensagem de commit sugerida

```text
feat(ocr): consolida desacoplamento e adiciona GPU opcional com fallback para CPU

- preserva CPU como padrão e propaga --gpu por CLI, API, settings e workers
- valida CUDA e recupera ausência de bibliotecas/configuração com aviso em CPU
- mantém PP-OCRv6 medium como default de Paddle e RapidOCR/ONNX/OpenVINO
- adiciona instalação isolada, provas de inferência GPU e testes de regressão
- documenta os resultados WSL2 e os gates de homologação ainda pendentes
```

O commit não foi criado automaticamente. Pesos, virtualenvs, PDFs e resultados
locais continuam fora do versionamento; revise os arquivos novos de scripts,
testes, constraints, workflow e documentação junto das alterações rastreadas.
