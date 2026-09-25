
# Plano Evoluções e comparativo pdfextractor

1. PaddleOCR PP-OCRv6 medium atual ← baseline 
2. RapidOCR PP-OCRv6 medium + ONNXRuntime 
3. RapidOCR PP-OCRv6 medium + OpenVINO 
4. Tesseract 5 + português 
5. EasyOCR + português

# PDFExtractor — Auditoria arquitetural e plano de evolução para comparação de engines OCR

**Repositório:** `victorperone/pdfextractor`  
**Branch alvo para evolução:** `fix/pdfextractor-correcoes`  
**Baseline integrado à `main`:** commit `3e87b90` (`docs: add Apache 2.0 license`)  
**Data:** 25/09/2026  
**Objetivo:** preparar o PDFExtractor para comparar, de forma reproduzível, justa, offline e orientada a CPU, cinco configurações de OCR sem alterar indevidamente o restante do pipeline.

## Plano de engines

1. **PaddleOCR PP-OCRv6 medium atual** — baseline
2. **RapidOCR PP-OCRv6 medium + ONNX Runtime**
3. **RapidOCR PP-OCRv6 medium + OpenVINO**
4. **Tesseract 5 + português**
5. **EasyOCR + português**

---

## Status de Implementação

| # | Etapa | Entregáveis principais | Status |
|---|---|---|---|
| 0 | **Etapa zero — freeze baseline** | `git sha`, `pip freeze`, golden baseline output do corpus | ⬜ |
| P4-pre | **Pré-Fase 4 — validar ONNX export PP-OCRv6** | `paddle2onnx` exporta det + rec sem erro; RapidOCR carrega o ONNX | ⬜ |
| 1 | **Fase 1 — Contrato** | `contracts.py`, `factory.py`, `registry.py`, fake backend, testes de contrato verdes | ⬜ |
| 2 | **Fase 2 — Migrar Paddle** | `backends/paddle.py`, output idêntico ao baseline (diff zero no corpus) | ⬜ |
| 3 | **Fase 3 — Benchmark RAW** | script de CER/WER, manifesto JSON com hash de modelos e corpus | ⬜ |
| 4 | **Fase 4 — RapidOCR ONNX** | `backends/rapidocr.py` (runtime=onnxruntime), benchmark RAW e E2E | ⬜ |
| 5 | **Fase 5 — RapidOCR OpenVINO** | `backends/rapidocr.py` (runtime=openvino), benchmark | ⬜ |
| 6 | **Fase 6 — Tesseract** | `backends/tesseract.py`, TSV parser, PSM policy, benchmark | ⬜ |
| 7 | **Fase 7 — EasyOCR** | `backends/easyocr.py`, PyTorch CPU, modelos offline, benchmark | ⬜ |
| 8 | **Fase 8 — E2E** | confidence policies calibradas por engine, pipeline completo, TableMagic auditado | ⬜ |

> **Como marcar:** substituir `⬜` por `✅` ao concluir cada etapa.

---

# 1. Resumo executivo

A recomendação central é **não reescrever o PDFExtractor para cada engine**.

A evolução deve introduzir uma fronteira arquitetural única:

```text
PDFExtractor
    |
    +-- Native text / visibility / routing / tables / assembly
    |
    +-- OCR service
            |
            +-- PaddleOCR backend
            +-- RapidOCR ONNX backend
            +-- RapidOCR OpenVINO backend
            +-- Tesseract backend
            +-- EasyOCR backend
```

O restante do PDFExtractor deve consumir um **resultado OCR canônico**, independente da biblioteca que o produziu.

A implementação deve preservar o comportamento atual quando nenhum novo parâmetro for informado:

```text
default engine = paddle
default profile = PP-OCRv6 medium atual
```

Assim, a introdução das novas engines não deve mudar automaticamente:

- decisão de usar texto nativo;
- detecção de visibilidade;
- renderização das páginas;
- ordem de leitura;
- lógica de montagem do documento;
- detecção de tabelas;
- renderização Markdown;
- política de memória;
- política de erro;
- comportamento do baseline PaddleOCR;
- arquivos de saída já existentes.

A comparação deve ser dividida em **duas camadas obrigatórias**:

```text
A. OCR RAW benchmark
   mesma imagem -> cada engine
   mede o motor OCR isoladamente

B. End-to-end benchmark
   mesmo PDF -> PDFExtractor completo
   troca somente o backend OCR
   mede o efeito real na saída final
```

Não misturar as duas conclusões.

Um motor pode apresentar CER/WER melhor no OCR bruto e ainda produzir um documento final pior por diferenças de geometria, confiança, ordenação, recuperação ou integração com tabelas.

---

# 2. Escopo e limitação desta auditoria

Esta especificação consolida:

- a arquitetura previamente auditada do PDFExtractor;
- os componentes centrais já identificados no projeto;
- o conjunto de arquivos alterados pelas correções integradas recentemente;
- o estado de `main` informado no merge concluído em `3e87b90`;
- documentação oficial atual das engines consideradas;
- os riscos já observados em comparação de OCR, qualidade, memória, tabelas, cache e execução offline.

Os componentes centrais conhecidos incluem:

```text
src/structured_pdf_text/
├── api.py
├── cli.py
├── config.py
├── memory.py
├── visibility.py
├── assemble/
│   └── document.py
├── diagnostics/
│   ├── compare.py
│   ├── corpus.py
│   └── report.py
├── ocr/
│   ├── models.py
│   ├── paddle.py
│   ├── quality.py
│   └── recovery.py
├── renderers/
│   └── markdown.py
├── tables/
│   └── detector.py
└── text/
    └── line_detector.py
```

**Limitação importante:** o ambiente usado para elaborar este documento não conseguiu fazer um novo clone ao vivo do GitHub. Portanto, o desenvolvedor deve executar a etapa de verificação do SHA e do código atual descrita na seção 4 antes de aplicar qualquer alteração. Não interpretar uma recomendação deste documento como prova de que o código atual ainda contém determinado problema já citado em auditorias anteriores.

---

# 3. Objetivos arquiteturais

A arquitetura final deve atender simultaneamente aos seguintes requisitos.

## 3.1. Compatibilidade retroativa

Sem nenhuma nova flag:

```text
pdfextractor ...
```

deve continuar usando o PaddleOCR PP-OCRv6 medium atual.

A introdução de engines adicionais não deve alterar o comportamento de produção já homologado.

## 3.2. Uma engine por execução

Toda execução deve resolver explicitamente uma identidade completa:

```text
engine
profile
runtime
model artifacts
language
device
preprocessing profile
quality policy
cache/model root
```

Exemplo:

```text
engine: rapidocr
runtime: onnxruntime
profile: ppocrv6-medium
language: pt
device: cpu
```

## 3.3. Offline real

Internet pode ser utilizada somente em instalação/setup.

Durante a extração:

```text
network = unavailable
```

deve continuar funcionando.

Se um modelo estiver ausente:

```text
FAIL FAST
```

Nunca:

```text
baixar automaticamente e continuar
```

## 3.4. Resultados comparáveis

Todas as engines devem produzir o mesmo contrato interno.

Nenhum módulo fora do adaptador deve precisar conhecer:

```text
PaddleOCR
RapidOCR
ONNX Runtime
OpenVINO
Tesseract
EasyOCR
```

## 3.5. Observabilidade

Toda saída de benchmark precisa identificar:

- engine;
- runtime;
- modelo;
- versão;
- hash dos pesos;
- configuração;
- tempo;
- memória;
- entrada;
- tentativas;
- retries;
- falhas;
- warnings;
- resultado bruto;
- resultado final.

---

# 4. Etapa zero obrigatória antes da refatoração

O desenvolvedor deve congelar o baseline atual.

Executar:

```bash
git switch fix/pdfextractor-correcoes
git status
git rev-parse HEAD
git log --oneline --decorate -10

python --version
python -m pip freeze > environment-baseline.txt
python -m pytest -q
```

Registrar:

```text
git_sha
git_dirty
SO
arquitetura
Python
CPU
núcleos físicos
núcleos lógicos
RAM
PaddlePaddle
PaddleOCR
PaddleX
modelo detector
modelo reconhecedor
modelos auxiliares
threads
cache
```

Executar um corpus pequeno e salvar a saída atual como **golden baseline**.

Essa saída não precisa ser considerada “perfeita”.

Ela serve para responder:

> a refatoração arquitetural alterou o comportamento do Paddle antes de começarmos a adicionar outras engines?

A resposta esperada é:

```text
não
```

---

# 5. O que NÃO deve ser alterado na primeira etapa

Esta seção é tão importante quanto a lista de mudanças.

## 5.1. Native text first

Preservar integralmente:

```text
texto nativo confiável
    -> não executar OCR
```

Não transformar a comparação de engines em uma reescrita da estratégia de extração.

## 5.2. Renderização do PDF

A mesma página deve gerar os mesmos pixels para todas as engines.

Não permitir:

```text
Paddle recebe imagem a 300 DPI
RapidOCR recebe imagem a 250 DPI
Tesseract recebe imagem reescalada
EasyOCR recebe imagem com outro crop
```

no benchmark primário.

O raster deve ser gerado uma única vez e identificado por hash.

## 5.3. Visibility filtering

`visibility.py` não deve receber mudanças específicas para cada engine.

Qualquer mudança nessa área durante o experimento cria uma segunda variável.

## 5.4. Line detection

`text/line_detector.py` deve permanecer independente da engine.

Não otimizar a heurística para resultados de um motor durante o comparativo.

## 5.5. Montagem do documento

`assemble/document.py` não deve possuir:

```python
if engine == "tesseract":
    ...
elif engine == "easyocr":
    ...
```

Se isso se tornar necessário, o contrato canônico do OCR está insuficiente.

## 5.6. Markdown

`renderers/markdown.py` deve receber a mesma representação intermediária para todos os motores.

Não corrigir Markdown exclusivamente para uma engine durante o benchmark.

## 5.7. Tabelas

A engine de OCR não deve silenciosamente modificar:

- detector de tabelas;
- regras de seleção da tabela;
- motor de estrutura;
- serialização;
- merge de células;
- cabeçalhos;
- spans.

Essas alterações devem ser experimentos separados.

## 5.8. Thresholds compartilhados de confiança

**Não utilizar o mesmo valor numérico de confiança para todas as engines.**

Exemplo proibido:

```python
if confidence < 0.70:
    run_tablemagic()
```

aplicado indiscriminadamente a:

```text
PaddleOCR
RapidOCR
Tesseract
EasyOCR
```

As confidências não são calibradas entre motores.

---

# 6. Arquitetura proposta

## 6.1. Nova fronteira OCR

Criar algo equivalente a:

```text
src/structured_pdf_text/ocr/
├── contracts.py
├── registry.py
├── factory.py
├── models.py
├── quality.py
├── recovery.py
└── backends/
    ├── __init__.py
    ├── paddle.py
    ├── rapidocr.py
    ├── tesseract.py
    └── easyocr.py
```

A nomenclatura pode ser adaptada ao padrão real do projeto.

O conceito não deve ser alterado:

```text
contrato
    ↓
factory
    ↓
backend
```

---

# 7. Contrato mínimo de backend

Proposta conceitual:

```python
from typing import Protocol

class OCRBackend(Protocol):
    @property
    def identity(self) -> "OCRBackendIdentity":
        ...

    @property
    def capabilities(self) -> "OCRCapabilities":
        ...

    def recognize(self, request: "OCRRequest") -> "OCRResult":
        ...

    def healthcheck(self) -> "OCRHealth":
        ...

    def close(self) -> None:
        ...
```

O backend deve receber uma imagem já resolvida pelo PDFExtractor.

Não deve abrir PDF.

Não deve decidir se a página precisa de OCR.

Não deve montar Markdown.

Não deve decidir se uma tabela deve ser processada.

---

# 8. Identidade completa da engine

Criar estrutura semelhante a:

```python
@dataclass(frozen=True)
class OCRBackendIdentity:
    engine: str
    runtime: str
    profile: str
    language: str
    device: str

    detector_name: str | None
    recognizer_name: str | None
    orientation_model_name: str | None

    package_versions: dict[str, str]
    artifact_hashes: dict[str, str]
```

Exemplos:

```text
engine=paddle
runtime=paddle_static
profile=ppocrv6-medium
```

```text
engine=rapidocr
runtime=onnxruntime
profile=ppocrv6-medium
```

```text
engine=rapidocr
runtime=openvino
profile=ppocrv6-medium
```

```text
engine=tesseract
runtime=tesseract-cli
profile=por-best
```

```text
engine=easyocr
runtime=torch-cpu
profile=latin-g2-pt
```

---

# 9. Capabilities

As engines não oferecem exatamente as mesmas funções.

O sistema deve declarar capacidades, em vez de fingir equivalência.

Exemplo:

```python
@dataclass(frozen=True)
class OCRCapabilities:
    detection: bool
    recognition: bool
    line_orientation: bool
    page_orientation: bool
    quadrilateral_boxes: bool
    word_boxes: bool
    per_token_confidence: bool
    batch_recognition: bool
```

Não criar código baseado apenas no nome da engine.

Utilizar capacidades.

---

# 10. Entrada OCR canônica

Proposta:

```python
@dataclass(frozen=True)
class OCRRequest:
    image: np.ndarray
    image_sha256: str

    document_id: str
    page_index: int
    region_id: str | None

    input_kind: str
    dpi: int | None

    language: str

    coordinate_system: str
    transform_to_page: tuple[float, ...] | None
```

`input_kind` deve distinguir, no mínimo:

```text
page
region
line
```

Isso será importante especialmente para Tesseract.

---

# 11. Saída OCR canônica

## 11.1. Token

```python
@dataclass(frozen=True)
class OCRToken:
    text: str

    polygon_px: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]

    bbox_px: tuple[float, float, float, float]

    confidence_native: float | None
    confidence_scale: str | None

    level: str
    source_engine: str
```

`confidence_scale` pode ser:

```text
0..1
0..100
engine-specific
unknown
```

## 11.2. Resultado

```python
@dataclass(frozen=True)
class OCRResult:
    status: str

    tokens: tuple[OCRToken, ...]

    text: str

    engine_identity: OCRBackendIdentity

    elapsed_total_s: float
    elapsed_detection_s: float | None
    elapsed_recognition_s: float | None

    warnings: tuple[str, ...]

    raw_result_ref: str | None
```

Status sugeridos:

```text
ok
no_text
partial
model_missing
timeout
runtime_error
budget_blocked
invalid_input
```

Não representar todos esses estados como:

```python
tokens = []
```

---

# 12. Confiança: mudança arquitetural obrigatória

## 12.1. Preservar o score nativo

Sempre guardar:

```text
confidence_native
confidence_scale
```

Não modificar silenciosamente.

## 12.2. Não comparar confiança entre engines

Não produzir gráfico como:

```text
Paddle confiança média = 0.91
Tesseract confiança média = 0.87
logo Paddle é melhor
```

Isso é metodologicamente inválido.

## 12.3. Não reutilizar automaticamente thresholds do Paddle

Se o projeto possui:

```text
strong_mean_confidence
strong_lower_quartile
low_confidence_threshold
```

a política atual não deve ser aplicada a Tesseract e EasyOCR sem calibração.

## 12.4. Benchmark RAW

No benchmark bruto:

```text
não rejeitar texto com base em threshold de confiança do PDFExtractor
```

Preservar o máximo possível da saída da engine.

## 12.5. Benchmark E2E

Antes do E2E definitivo, usar um conjunto de **calibração**, diferente do conjunto de avaliação.

Calibrar por backend:

```text
confidence -> probabilidade real de erro
```

ou substituir decisões críticas por indicadores independentes da engine.

---

# 13. Factory

Adicionar um único ponto de criação.

Exemplo:

```python
def build_ocr_backend(config: OCRConfig) -> OCRBackend:
    match config.engine:
        case "paddle":
            return PaddleOCRBackend(config)

        case "rapidocr-onnx":
            return RapidOCRBackend(config, runtime="onnxruntime")

        case "rapidocr-openvino":
            return RapidOCRBackend(config, runtime="openvino")

        case "tesseract":
            return TesseractOCRBackend(config)

        case "easyocr":
            return EasyOCRBackend(config)

        case _:
            raise UnsupportedOCREngine(...)
```

`api.py` deve depender apenas deste factory/contrato.

---

# 14. Alterações sugeridas por arquivo

## `ocr/paddle.py`

### Alterar

Transformar a implementação atual em implementação explícita de `OCRBackend`.

Mapear a saída Paddle para `OCRResult`.

Preservar:

- modelo atual;
- parâmetros atuais;
- offline;
- recovery;
- comportamento do baseline;
- tratamento de erros.

### Não alterar

Não “melhorar” simultaneamente os parâmetros do Paddle durante a refatoração.

Primeiro:

```text
old Paddle adapter == new Paddle backend
```

Depois experimentar HPI ou novos parâmetros em perfil separado.

---

## `ocr/models.py`

### Alterar

Evoluir de catálogo centrado em Paddle para registro de perfis.

Exemplo:

```text
ppocrv6-medium
tesseract5-por-best
easyocr-pt-latin-g2
```

Separar:

```text
modelo lógico
```

de:

```text
runtime
```

Assim:

```text
PP-OCRv6 medium
```

pode ser executado por:

```text
Paddle
RapidOCR/ONNX
RapidOCR/OpenVINO
```

### Não alterar

Não remover os perfis Paddle existentes até a migração estar concluída.

---

## `config.py`

### Adicionar

```text
OCREngine
OCRRuntime
OCRBackendConfig
OCRModelProfile
```

Configuração sugerida:

```python
@dataclass(frozen=True)
class OCRConfig:
    engine: str = "paddle"
    runtime: str = "paddle_static"
    profile: str = "ppocrv6-medium"
    language: str = "pt"
    device: str = "cpu"
    model_root: Path | None = None
    cpu_threads: int | None = None
```

Adicionar configurações específicas em subestruturas.

Não criar uma classe com dezenas de opções misturadas entre bibliotecas.

---

## `api.py`

### Alterar

Remover conhecimento direto de classes concretas do Paddle fora do ponto de composição.

Receber:

```text
OCRBackend
```

ou criar pelo factory.

### Preservar

- native-first;
- rotas existentes;
- partial states;
- políticas de erro;
- saída pública.

---

## `cli.py`

Adicionar:

```text
--ocr-engine
--ocr-runtime
--ocr-profile
```

Exemplo:

```bash
pdfextractor extract input.pdf \
    --ocr-engine paddle \
    --ocr-profile ppocrv6-medium
```

```bash
pdfextractor extract input.pdf \
    --ocr-engine rapidocr \
    --ocr-runtime onnxruntime \
    --ocr-profile ppocrv6-medium
```

Manter retrocompatibilidade:

```text
ausência das flags -> Paddle atual
```

### Setup

A CLI deve oferecer setup/status por engine.

Conceitualmente:

```bash
pdfextractor models setup --ocr-engine rapidocr --ocr-runtime onnxruntime
pdfextractor models status --ocr-engine rapidocr --ocr-runtime onnxruntime
```

Os nomes reais devem seguir o estilo atual da CLI.

---

## `memory.py`

### Preservar

Utilizar um único sistema de medição.

### Melhorar para benchmark

Medir:

```text
process RSS
process tree RSS
peak RSS
```

Isto é importante porque:

```text
Tesseract
```

pode ser executado como subprocesso.

Se medir apenas o processo Python, o benchmark poderá subestimar o uso do Tesseract.

---

## `ocr/recovery.py`

### Preservar

A política de escalas/regiões deve continuar engine agnostic.

### Alterar somente se necessário

Receber `OCRBackend` em vez de adaptador Paddle concreto.

Não adicionar caminhos específicos por engine nessa primeira fase.

---

## `ocr/quality.py`

### Alteração necessária

Separar:

```text
features observáveis
```

de:

```text
thresholds calibrados por engine
```

Exemplo:

```text
mean_confidence
lower_quartile
text_density
coverage
character_count
duplicate_ratio
```

podem ser calculados.

Porém a interpretação:

```text
mean_confidence >= 0.90
```

não pode ser universal.

---

## `diagnostics/corpus.py`

Manter como telemetria operacional.

Não transformar automaticamente esse relatório em medição de precisão.

Sem ground truth:

```text
não existe CER/WER real
```

---

## `diagnostics/compare.py`

Evoluir para comparar execuções por manifesto, não somente nomes de arquivos.

Deve validar que os braços diferem apenas nas variáveis autorizadas.

Exemplo:

```text
A:
engine=paddle
runtime=paddle_static

B:
engine=rapidocr
runtime=onnxruntime
```

Todo o restante deve ser comprovadamente igual.

---

## `diagnostics/report.py`

Adicionar schema versionado.

Exemplo:

```text
benchmark_schema_version
run_id
engine_identity
git_sha
environment
input_hash
metrics
failures
warnings
```

---

## `assemble/document.py`

Não alterar algoritmos durante a integração inicial.

Somente ajustar tipos de entrada se necessário para aceitar o resultado canônico.

---

## `tables/detector.py`

Não criar lógica baseada na engine.

Não alterar thresholds ou regras de TableMagic durante o benchmark RAW.

---

## `text/line_detector.py`

Manter congelado no comparativo inicial.

---

## `visibility.py`

Manter congelado.

---

## `renderers/markdown.py`

Manter congelado.

A qualidade Markdown deve ser uma métrica E2E, não uma variável diferente entre braços.

---

# 15. Dependências

Não adicionar todas as engines ao conjunto base obrigatório.

Usar extras.

Exemplo conceitual:

```toml
[project.optional-dependencies]

ocr-paddle = [
    "paddlepaddle==...",
    "paddleocr==...",
    "paddlex==...",
]

ocr-rapid-onnx = [
    "rapidocr>=3.9.0",
    "onnxruntime==...",
]

ocr-rapid-openvino = [
    "rapidocr>=3.9.0",
    "openvino==...",
]

ocr-easyocr = [
    "easyocr==...",
    "torch==...",
]

benchmark = [
    ...
]
```

Tesseract é preferencialmente uma dependência externa do sistema:

```text
tesseract executable
por.traineddata
```

Não obrigar PyTorch, OpenVINO e ONNX Runtime a usuários que utilizam somente Paddle.

---

# 16. Backend 1 — PaddleOCR PP-OCRv6 medium

## Papel

Baseline.

## Identidade

Registrar explicitamente:

```text
PP-OCRv6_medium_det
PP-OCRv6_medium_rec
runtime Paddle
CPU
threads
```

Além dos módulos auxiliares efetivamente habilitados.

## Resultado esperado do adaptador

Paddle fornece, entre outros elementos:

```text
texto reconhecido
score
polígonos de detecção
bounding boxes
```

O adaptador deve converter tudo para `OCRResult`.

## Requisito crítico

O novo backend Paddle deve produzir saída equivalente ao código anterior antes de iniciar a comparação das demais engines.

## HPI

`enable_hpi=True` deve ser tratado como **perfil experimental separado**.

Não usar HPI no baseline principal se o objetivo for comparar posteriormente Paddle contra RapidOCR/ONNX e RapidOCR/OpenVINO, pois o HPI pode escolher backends de alta performance e contaminar a interpretação.

Sugestão:

```text
paddle-static-baseline
paddle-hpi-secondary
```

---

# 17. Backend 2 — RapidOCR PP-OCRv6 medium + ONNX Runtime

## Requisito de versão

A documentação atual do RapidOCR informa suporte ao PP-OCRv6 em ONNX Runtime a partir da série 3.9.

Fixar uma versão validada no projeto.

## Configuração lógica

```text
engine = rapidocr
runtime = onnxruntime
ocr_version = PP-OCRv6
model_type = medium
language = pt
CPU
```

## Offline

Não depender do mecanismo de download automático.

Fornecer:

```text
model_root_dir
ou model paths explícitos
font_path explícito quando necessário
```

O `font_path` merece atenção: a documentação informa que certos caminhos podem baixar fonte automaticamente quando não fornecida.

## Resultado nativo do RapidOCR

A documentação atual expõe:

```text
boxes
txts
scores
word_results
elapse_list
elapse
```

`boxes` possui formato quadrilateral por linha.

Isso torna o RapidOCR relativamente natural para adaptação ao contrato atual.

## Resultado esperado

Esperar forte compatibilidade estrutural com o Paddle:

```text
quadriláteros
texto por linha
score por linha
```

Porém **não exigir resultados bit a bit idênticos**.

Mesmo usando a mesma família PP-OCRv6, podem existir diferenças em:

- formato do modelo;
- backend numérico;
- preprocessing;
- resize;
- postprocessing;
- thresholds;
- precisão de ponto flutuante;
- ordenação.

Essas diferenças são parte do experimento.

---

# 18. Backend 3 — RapidOCR PP-OCRv6 medium + OpenVINO

A arquitetura deve reutilizar o mesmo `RapidOCRBackend`.

Não criar um segundo adaptador inteiro.

Exemplo:

```python
RapidOCRBackend(runtime="onnxruntime")
RapidOCRBackend(runtime="openvino")
```

Isso é importante para garantir que:

```text
ONNX vs OpenVINO
```

não seja confundido com diferenças no código do adaptador.

## Resultado esperado

Mesmo contrato do RapidOCR ONNX:

```text
boxes
txts
scores
word_results
timing
```

A principal variável deve ser:

```text
runtime
```

## Hipótese a testar

OpenVINO pode apresentar vantagem de desempenho em CPUs suportadas, especialmente hardware Intel.

Isso é uma hipótese, não um resultado garantido.

A qualidade também deve ser medida; não assumir equivalência exata apenas porque o modelo lógico é PP-OCRv6 medium.

---

# 19. Backend 4 — Tesseract 5 + português

## Perfil principal

Para avaliação de qualidade:

```text
Tesseract 5.x
language = por
tessdata_best
LSTM
```

`tessdata_best` é o conjunto oficial orientado a maior precisão, com custo de desempenho superior.

Opcionalmente, depois do benchmark principal:

```text
tessdata_fast
```

pode ser medido como perfil de velocidade.

Não misturar `best` e `fast` no mesmo resultado “Tesseract”.

## Integração recomendada

Utilizar o executável do Tesseract como processo externo e solicitar TSV.

Exemplo conceitual:

```bash
tesseract input.png stdout -l por --psm 3 tsv
```

O adaptador deve parsear:

```text
level
page_num
block_num
par_num
line_num
word_num
left
top
width
height
conf
text
```

## Vantagem arquitetural do TSV

Ele preserva:

```text
bloco
parágrafo
linha
palavra
bounding box
confiança
```

## Diferença geométrica

Tesseract fornece naturalmente caixas retangulares axis-aligned no TSV.

O contrato deve converter:

```text
left/top/width/height
```

para polígono de quatro pontos.

Não inventar rotação inexistente.

## Confiança

No TSV, confiança de palavras normalmente está na escala:

```text
0..100
```

e níveis não aplicáveis podem usar `-1`.

Preservar o valor nativo.

## PSM

Não escolher um único `--psm` aleatoriamente para todos os tipos de input.

Definir política reproduzível por `input_kind`.

Sugestão inicial:

```text
page   -> PSM 3
region -> PSM 6
line   -> PSM 7
```

Essa política deve ser congelada e registrada.

Realizar depois uma etapa de tuning em conjunto de calibração.

## DPI

A documentação oficial destaca que Tesseract normalmente se beneficia de resolução adequada e cita pelo menos 300 DPI como referência útil.

Não re-renderizar apenas para Tesseract no benchmark RAW.

Se for testado um perfil Tesseract otimizado a 300 DPI, isso deve ser outro experimento, separado do teste com raster comum.

---

# 20. Backend 5 — EasyOCR + português

## Inicialização

```python
easyocr.Reader(
    ["pt"],
    gpu=False,
    model_storage_directory=...,
    download_enabled=False,
)
```

A documentação oficial suporta:

```text
pt
gpu=False
download_enabled=False
```

O português utiliza a família de reconhecimento Latin.

A configuração atual do projeto EasyOCR mapeia idiomas latinos ao modelo `latin_g2`.

## Offline

Durante setup:

```text
baixar modelos
validar MD5/hash
armazenar localmente
```

Durante runtime:

```text
download_enabled=False
```

Se modelo estiver faltando:

```text
falhar
```

## Resultado

`readtext(detail=1)` retorna conceitualmente:

```text
bbox
text
confidence
```

A documentação também permite separar:

```text
detect()
recognize()
```

## Detector

O EasyOCR utiliza CRAFT para detecção em sua configuração padrão.

Portanto o benchmark de engine completa compara:

```text
CRAFT + recognizer EasyOCR
```

contra:

```text
PP-OCRv6 det + rec
```

Isso é aceitável no benchmark de substituição de produto.

Não chamar esse resultado de “comparação apenas dos reconhecedores”.

## CPU

`gpu=False` deve ser explícito.

PyTorch aumenta:

- footprint;
- startup;
- RAM;
- complexidade da instalação.

Isso deve ser medido, não usado como motivo para rejeitar antecipadamente.

---

# 21. Setup offline unificado

Criar conceito único:

```text
ModelStore
```

Exemplo:

```text
~/.cache/pdfextractor/
└── ocr/
    ├── paddle/
    │   └── ppocrv6-medium/
    ├── rapidocr/
    │   ├── onnxruntime/
    │   │   └── ppocrv6-medium/
    │   └── openvino/
    │       └── ppocrv6-medium/
    ├── tesseract/
    │   └── tessdata/
    │       └── por.traineddata
    └── easyocr/
        ├── detector/
        └── recognition/
```

Para cada artefato registrar:

```text
logical_name
source
version
sha256
size
path
license
```

---

# 22. Contrato de readiness

Não considerar modelo pronto apenas porque o diretório existe.

Status sugeridos:

```text
missing
incomplete
corrupt
ready
incompatible
unknown
```

Cada engine deve implementar:

```text
lightweight validation
```

e opcionalmente:

```text
smoke test real
```

O smoke test deve:

1. iniciar engine;
2. usar uma imagem minúscula local;
3. executar inferência;
4. validar schema;
5. fechar engine;
6. não usar internet.

---

# 23. Teste formal de ausência de rede

A execução offline precisa ser comprovada.

Não apenas documentada.

Executar teste em ambiente sem rede:

```text
Docker --network none
```

ou sandbox equivalente.

Verificar para as cinco engines:

```text
engine init
model load
OCR
shutdown
```

Resultado esperado:

```text
PASS
```

com todos os modelos previamente provisionados.

---

# 24. Design do benchmark

## Camada A — OCR RAW

Objetivo:

> medir a engine OCR, minimizando influência do PDFExtractor.

Fluxo:

```text
PDF
  ↓
renderização congelada
  ↓
PNG/RGB com hash
  ↓
mesmos pixels
  ├── Paddle
  ├── Rapid ONNX
  ├── Rapid OpenVINO
  ├── Tesseract
  └── EasyOCR
  ↓
OCRResult canônico
  ↓
métricas contra ground truth
```

### Desativar do comparativo RAW

- native text;
- TableMagic;
- montagem Markdown;
- merge de documento;
- fallback entre engines;
- threshold do PDFExtractor;
- seleção por confiança;
- retries específicos não equivalentes.

---

# 25. Camada B — End-to-end

Objetivo:

> medir o que o usuário realmente recebe.

Fluxo:

```text
PDF
  ↓
native-first
  ↓
visibility
  ↓
routing
  ↓
OCR backend selecionado
  ↓
recovery
  ↓
tabelas
  ↓
assembly
  ↓
Markdown / structured result
```

Alterar somente:

```text
OCR backend
```

Qualquer outra diferença deve aparecer no manifesto.

---

# 26. Camada C — Diagnóstico recognition-only opcional

Se houver diferença grande entre motores, executar um terceiro experimento opcional.

Usar **as mesmas caixas de texto de ground truth ou de um detector congelado** e comparar somente reconhecimento.

Isso responde:

```text
o problema está na detecção?
```

ou:

```text
o problema está no reconhecimento?
```

Não é obrigatório para a primeira rodada, mas é altamente útil para explicar divergências.

---

# 27. Corpus recomendado

O corpus deve ser congelado antes de analisar os resultados.

Separar por classes.

## Controle

```text
PDF 100% nativo
```

Objetivo:

```text
nenhuma engine OCR deve ser chamada
```

Se os resultados variarem entre engines, existe acoplamento indevido.

## OCR limpo

```text
scan 300 DPI
preto/branco
texto impresso
português
```

## Baixa resolução

```text
150–200 DPI
```

## Ruído

```text
compressão
scanner antigo
fundo cinza
manchas
```

## Inclinação

```text
pequeno skew
90°
180°
```

## Texto pequeno

```text
rodapés
notas
fontes pequenas
```

## Layout

```text
uma coluna
múltiplas colunas
cabeçalho/rodapé
listas
```

## Conteúdo crítico

```text
datas
valores monetários
percentuais
CPF
CNPJ
processos
identificadores
zeros à esquerda
```

## Tabelas

Separar tabelas do benchmark OCR bruto.

No E2E:

```text
com bordas
sem bordas
células vazias
multilinha
spans
```

Não incluir gráficos/fluxogramas se não fazem parte dos requisitos do produto.

---

# 28. Ground truth

Sem ground truth manual:

```text
não existe comparação de acurácia confiável
```

A transcrição deve ser:

- revisada manualmente;
- versionada;
- identificada por documento/página/região;
- normalizada por política documentada.

Manter:

```text
ground_truth_raw
ground_truth_normalized
```

---

# 29. Normalização

Aplicar no máximo normalizações explicitamente definidas.

Recomendação:

```text
Unicode NFC
line ending padronizado
política fixa de espaços
```

Não remover na métrica estrita:

```text
acentos
pontuação
R$
%
/
-
.
,
zeros à esquerda
```

Um CPF errado por um caractere não deve virar “correto” após normalização excessiva.

---

# 30. Métricas obrigatórias de acurácia

## CER

Character Error Rate.

Publicar:

```text
CER strict
CER normalized
```

## WER

Word Error Rate.

Publicar:

```text
WER strict
WER normalized
```

## Omissão

Texto esperado ausente.

## Inserção

Texto inexistente acrescentado.

## Duplicação

Texto correto repetido indevidamente.

## Campos críticos

Exatidão exata de:

```text
datas
moeda
CPF/CNPJ
códigos
números
percentuais
```

## Acentuação

Medir erro de diacríticos separadamente se relevante.

---

# 31. Métricas de detecção

Para engines completas:

```text
precision
recall
F1
IoU
```

Parear caixas de ground truth com caixas detectadas.

Não medir apenas:

```text
quantidade de caixas encontradas
```

---

# 32. Ordem de leitura

Criar métrica separada.

Texto correto em ordem errada pode inutilizar documento.

Medir:

```text
line order
block order
column order
```

Não esconder erro de ordem usando apenas CER global.

---

# 33. Métricas de desempenho

Registrar separadamente:

```text
cold startup
model load
first inference
warm inference
page latency
document latency
throughput
```

Não misturar:

```text
tempo de baixar modelo
```

com:

```text
tempo de OCR
```

---

# 34. Memória

Medir:

```text
RSS inicial
RSS após carregar modelo
RSS de pico
RSS final
process tree RSS
```

Especialmente para:

```text
Tesseract subprocess
EasyOCR/PyTorch
OpenVINO
```

---

# 35. CPU

Registrar:

```text
número de threads
CPU física/lógica
afinidade se usada
oversubscription
```

Executar todas as engines sob política conhecida.

Não permitir, por exemplo:

```text
Paddle 4 threads
OpenVINO 16
Tesseract ilimitado
```

sem registrar que isso faz parte do perfil.

Existem dois benchmarks válidos:

```text
same-resource benchmark
```

e:

```text
best-tuned-per-engine benchmark
```

Eles respondem perguntas diferentes e não devem ser misturados.

---

# 36. Repetição e warm-up

Para desempenho:

```text
1 ou mais warm-ups
5 ou mais repetições medidas
```

conforme custo do corpus.

Publicar:

```text
median
p50
p95
min
max
```

Evitar conclusão baseada em uma única execução.

---

# 37. TableMagic: regra crítica

No benchmark RAW:

```text
TableMagic = OFF
```

No benchmark E2E, TableMagic pode permanecer no pipeline, porém deve ser auditável.

Registrar por página/tabela:

```text
tablemagic_invoked
tablemagic_selected
trigger_reason
ocr_engine
ocr_confidence_native
quality_policy
```

## Problema do threshold de 70%

Se existe ou existiu lógica equivalente a:

```text
TableMagic quando OCR confidence < 70%
```

ela não pode ser aplicada da mesma forma às cinco engines.

Exemplo:

```text
Paddle score 0.70
Tesseract score 70
EasyOCR score 0.70
```

não representam necessariamente a mesma probabilidade de erro.

## Opções válidas

### Opção A — Benchmark E2E com gatilho congelado independente de confiança

Preferível para primeira comparação.

### Opção B — Política calibrada por engine

Usar conjunto de calibração separado.

### Opção C — Forçar TableMagic igual em todos os braços

Útil em benchmark específico de tabelas.

---

# 38. OCR interno de TableMagic

Se a pipeline de tabelas executar OCR próprio, ela pode mascarar o backend selecionado.

O relatório deve distinguir:

```text
general_ocr_engine
table_ocr_engine
```

Se TableMagic utiliza PaddleOCR internamente enquanto o documento geral usa Tesseract:

```text
não rotular o resultado como "Tesseract puro"
```

Rotular corretamente:

```text
Tesseract general OCR + Paddle table OCR
```

Para comparação estrita de engines:

```text
desabilitar OCR interno da tabela
```

quando a API suportar e injetar o OCR externo de forma documentada;

ou:

```text
separar a avaliação de tabelas
```

caso isso não seja possível.

---

# 39. Manifesto de execução

Cada run deve criar um arquivo semelhante a:

```json
{
  "schema_version": "1.0",
  "run_id": "20260925-paddle-001",

  "git_sha": "...",
  "git_dirty": false,

  "engine": "paddle",
  "runtime": "paddle_static",
  "profile": "ppocrv6-medium",
  "language": "pt",
  "device": "cpu",

  "model_artifacts": {
    "detector": {
      "name": "PP-OCRv6_medium_det",
      "sha256": "..."
    },
    "recognizer": {
      "name": "PP-OCRv6_medium_rec",
      "sha256": "..."
    }
  },

  "input": {
    "corpus_version": "...",
    "manifest_sha256": "..."
  },

  "settings": {
    "dpi": 300,
    "cpu_threads": 8
  }
}
```

---

# 40. Teste automático de paridade entre braços

Antes de executar benchmark:

```python
assert_same_except(
    run_a,
    run_b,
    allowed_differences={
        "engine",
        "runtime",
        "model_artifacts",
        "engine_specific_settings",
    },
)
```

Se diferirem:

```text
DPI
document list
rendering
quality policy
TableMagic
output schema
threads
timeout
```

o benchmark deve falhar ou exigir justificativa explícita.

---

# 41. Artefatos RAW

Guardar opcionalmente, para corpus autorizado:

```text
runs/
└── RUN_ID/
    ├── manifest.json
    ├── metrics.json
    ├── engine_raw/
    ├── canonical_ocr/
    ├── final_output/
    └── logs/
```

Não publicar conteúdo sensível por padrão.

---

# 42. Comparação Paddle vs RapidOCR ONNX

Objetivo principal:

```text
mesma família PP-OCRv6 medium
runtime diferente
```

Controlar:

```text
mesmo corpus
mesmos pixels
mesmo language
mesmos thresholds de benchmark raw
mesma política de geometria
```

Resultados a examinar:

```text
CER/WER
boxes
omissões
duplicações
latência
startup
RSS
robustez offline
```

A análise deve apontar onde os resultados divergem:

```text
detecção
reconhecimento
postprocessing
```

---

# 43. Comparação RapidOCR ONNX vs OpenVINO

Este é o comparativo mais controlado.

Idealmente alterar apenas:

```text
runtime
```

Não duplicar lógica do adaptador.

Resultados esperados:

```text
qualidade semelhante, mas não presumida idêntica
desempenho potencialmente diferente
memória diferente
startup diferente
```

A vantagem de OpenVINO é dependente do hardware.

Não extrapolar benchmark Intel para outras CPUs.

---

# 44. Comparação com Tesseract

Tesseract é uma arquitetura diferente.

Diferenças esperadas de representação:

```text
word hierarchy
axis-aligned boxes
page segmentation modes
confidence 0..100
```

O adaptador deve converter schema, não comportamento.

Não forçar Tesseract a imitar internamente a arquitetura PP-OCR.

---

# 45. Comparação com EasyOCR

EasyOCR também muda:

```text
detector
recognizer
runtime
dependências
```

Logo o resultado mede uma substituição completa de OCR.

Isso é útil para produto.

Não apresentar como benchmark isolado de “mesmo reconhecedor”.

---

# 46. Hipóteses a testar por engine

Estas não são conclusões antecipadas.

## PaddleOCR PP-OCRv6 medium

Hipótese:

```text
melhor integração com o estado atual do projeto
boa qualidade geral
maior dependência do ecossistema Paddle
```

## RapidOCR + ONNX Runtime

Hipótese:

```text
qualidade próxima ao PP-OCRv6 executado pelo Paddle
runtime mais simples/leve em CPU
boa portabilidade
```

## RapidOCR + OpenVINO

Hipótese:

```text
qualidade próxima ao RapidOCR/ONNX
possível ganho de CPU em hardware favorável
```

## Tesseract 5 por best

Hipótese:

```text
forte em scans limpos de texto impresso
baixo acoplamento
offline simples
menor footprint
mais sensível a segmentação e qualidade de imagem
```

## EasyOCR

Hipótese:

```text
boa detecção geral
suporte português
CPU funcional
maior custo de PyTorch em memória/startup
```

Todas devem ser validadas no corpus do projeto.

---

# 47. Critérios que NÃO podem definir vencedor

Não decidir apenas por:

```text
confidence média
número de caracteres
número de boxes
tempo de uma única página
benchmark do fornecedor
tamanho do modelo
um documento de demonstração
```

---

# 48. Critérios de decisão

Construir tabela de decisão depois de obter resultados.

Exemplo:

| Dimensão | Métrica |
|---|---|
| Acurácia | CER strict |
| Acurácia | WER strict |
| Campos críticos | exact match |
| Detecção | recall/F1 |
| Robustez | falhas por página |
| Offline | execução sem rede |
| Desempenho | p50/p95 |
| Memória | peak RSS |
| Startup | cold start |
| Footprint | disco |
| Integração | regressões E2E |
| Operação | setup/readiness |

Não atribuir pesos antes de o proprietário do produto definir prioridades.

---

# 49. Testes unitários do contrato

Criar `FakeOCRBackend`.

Casos:

```text
texto normal
nenhum texto
erro
timeout
partial
caixas rotacionadas
caixas inválidas
score ausente
score 0
Unicode
acentos
multilinha
```

O restante do PDFExtractor deve funcionar sem biblioteca OCR real.

---

# 50. Testes de contrato por engine

Todas as engines devem passar a mesma suíte.

Exemplo:

```python
@pytest.mark.parametrize("backend", BACKENDS)
def test_backend_contract(backend):
    result = backend.recognize(FIXTURE)
    validate_ocr_result(result)
```

Validar:

```text
finite coordinates
nonnegative boxes
text is str
no NaN confidence
status valid
identity present
timing nonnegative
```

---

# 51. Testes de geometria

Fixtures contendo:

```text
horizontal
rotated
crop
offset
scaled region
```

Verificar transformação:

```text
engine coordinates
    ->
canonical image coordinates
    ->
page coordinates
```

Não aceitar divergências silenciosas.

---

# 52. Testes de offline

Para cada backend:

```text
model installed
network disabled
init
OCR
close
```

Adicionar à suíte de integração.

---

# 53. Testes de modelo ausente

Esperado:

```text
erro antes de processar página
```

Mensagem deve indicar:

```text
engine
artifact
path
comando de setup
```

Nunca tentar internet em runtime.

---

# 54. Teste de baseline

Antes de implementar RapidOCR:

```text
old Paddle
vs
new PaddleBackend
```

Sobre corpus congelado.

Esperado:

```text
mesmo texto
mesma geometria dentro de tolerância
mesmas rotas
mesmo Markdown
```

Se não ocorrer:

```text
parar
investigar
```

Não continuar adicionando engines sobre um baseline já alterado.

---

# 55. CI

Dividir testes.

## PR rápida

```text
unit
contract fake
schema
config
CLI
```

## Runner com modelos

```text
Paddle smoke
Rapid ONNX smoke
Rapid OpenVINO smoke
Tesseract smoke
EasyOCR smoke
offline
```

## Benchmark periódico/manual

```text
corpus completo
métricas de qualidade
performance
```

Não baixar modelos automaticamente em CI sem etapa explícita.

---

# 56. Branches sugeridas

Implementar em etapas pequenas.

```text
refactor/ocr-backend-contract
```

Depois:

```text
feat/rapidocr-onnx
feat/rapidocr-openvino
feat/tesseract-ocr
feat/easyocr
feat/ocr-benchmark
```

Ou equivalente segundo a convenção do projeto.

Não implementar os cinco backends em um único commit gigante.

---

# 57. Ordem recomendada de implementação

## Fase 1 — Contrato

- criar tipos canônicos;
- factory;
- registry;
- fake backend;
- testes.

## Fase 2 — Migrar Paddle

- encapsular implementação atual;
- validar golden baseline;
- nenhuma mudança de qualidade.

## Fase 3 — Benchmark RAW

- corpus;
- ground truth;
- CER/WER;
- manifesto;
- performance;
- offline.

## Fase 4 — RapidOCR ONNX

- setup;
- adapter;
- tests;
- benchmark.

## Fase 5 — RapidOCR OpenVINO

- reutilizar adapter;
- alterar runtime;
- benchmark.

## Fase 6 — Tesseract

- setup;
- TSV parser;
- PSM policy;
- benchmark.

## Fase 7 — EasyOCR

- setup;
- PyTorch CPU;
- download disabled;
- benchmark.

## Fase 8 — E2E

- calibrar confidence policies;
- habilitar pipeline completo;
- auditar TableMagic;
- comparar saída final.

---

# 58. Schema de relatório sugerido

```json
{
  "run": {},
  "environment": {},
  "engine": {},
  "models": {},
  "corpus": {},

  "accuracy": {
    "cer_strict": null,
    "cer_normalized": null,
    "wer_strict": null,
    "wer_normalized": null,
    "critical_field_exact": null
  },

  "detection": {
    "precision": null,
    "recall": null,
    "f1": null
  },

  "performance": {
    "cold_start_s": null,
    "page_p50_s": null,
    "page_p95_s": null,
    "peak_rss_bytes": null
  },

  "reliability": {
    "pages_total": 0,
    "pages_ok": 0,
    "pages_partial": 0,
    "pages_failed": 0
  },

  "offline": {
    "network_disabled": true,
    "success": true
  }
}
```

---

# 59. Relatório final

Gerar dois rankings descritivos por métricas, sem esconder tradeoffs:

```text
accuracy table
performance table
resource table
reliability table
```

Não produzir somente:

```text
winner = X
```

O resultado útil é algo como:

```text
Engine A:
  menor CER
  maior RAM

Engine B:
  CER próximo
  menor latência

Engine C:
  melhor footprint
  pior em scans degradados
```

A escolha final depende do requisito do produto.

---

# 60. Regras específicas de fair comparison

## Regra 1

Mesmo corpus.

## Regra 2

Mesmos pixels no RAW.

## Regra 3

Mesma política de normalização.

## Regra 4

Falhas permanecem no denominador.

## Regra 5

Sem fallback silencioso entre engines.

## Regra 6

Sem downloads durante execução.

## Regra 7

Versões e hashes registrados.

## Regra 8

Confiança não é acurácia.

## Regra 9

Paddle/RapidOCR usando PP-OCRv6 não são presumidos idênticos.

## Regra 10

TableMagic não pode esconder qual OCR produziu o texto.

---

# 61. Definição de sucesso da arquitetura

A refatoração está pronta quando este pseudocódigo for possível:

```python
config = OCRConfig(
    engine="rapidocr",
    runtime="onnxruntime",
    profile="ppocrv6-medium",
    language="pt",
    device="cpu",
)

backend = build_ocr_backend(config)

result = backend.recognize(request)
```

e o restante do PDFExtractor não sabe qual biblioteca foi chamada.

---

# 62. Definição de sucesso do comparativo

O comparativo está pronto quando for possível executar:

```text
run 1 = Paddle PP-OCRv6 medium
run 2 = RapidOCR PP-OCRv6 medium ONNX
run 3 = RapidOCR PP-OCRv6 medium OpenVINO
run 4 = Tesseract 5 por
run 5 = EasyOCR pt
```

sobre o mesmo corpus e gerar automaticamente:

```text
manifest
raw outputs
CER/WER
detection metrics
critical field accuracy
latency
RSS
failures
offline proof
E2E metrics
```

---

# 63. Checklist para o desenvolvedor

## Antes

- [ ] registrar SHA;
- [ ] `pytest` verde;
- [ ] salvar baseline Paddle;
- [ ] congelar corpus;
- [ ] definir ground truth.

## Arquitetura

- [ ] `OCRBackend`;
- [ ] `OCRRequest`;
- [ ] `OCRResult`;
- [ ] `OCRToken`;
- [ ] `OCRBackendIdentity`;
- [ ] `OCRCapabilities`;
- [ ] factory;
- [ ] registry.

## Paddle

- [ ] migrado para backend;
- [ ] saída equivalente;
- [ ] default preservado;
- [ ] offline preservado.

## Rapid ONNX

- [ ] PP-OCRv6 medium;
- [ ] português;
- [ ] modelos locais;
- [ ] fonte local;
- [ ] sem download;
- [ ] benchmark.

## Rapid OpenVINO

- [ ] mesmo adapter;
- [ ] runtime OpenVINO;
- [ ] mesmas configurações;
- [ ] benchmark.

## Tesseract

- [ ] Tesseract 5;
- [ ] `por.traineddata`;
- [ ] `tessdata_best`;
- [ ] TSV;
- [ ] PSM documentado;
- [ ] offline;
- [ ] process tree RSS.

## EasyOCR

- [ ] `["pt"]`;
- [ ] `gpu=False`;
- [ ] `download_enabled=False`;
- [ ] model directory explícito;
- [ ] offline;
- [ ] benchmark.

## Métricas

- [ ] CER;
- [ ] WER;
- [ ] strict;
- [ ] normalized;
- [ ] critical fields;
- [ ] omissions;
- [ ] insertions;
- [ ] duplicates;
- [ ] reading order;
- [ ] detection F1;
- [ ] latency;
- [ ] peak RSS;
- [ ] failures.

## E2E

- [ ] native pages idênticas;
- [ ] TableMagic auditável;
- [ ] confidence policies calibradas;
- [ ] Markdown comparável;
- [ ] nenhuma falha excluída.

---

# 64. Itens que devem bloquear o benchmark

Não publicar comparação se qualquer item abaixo ocorrer:

```text
modelos baixados durante a execução
corpus diferente entre braços
DPI diferente sem declarar
falhas excluídas do denominador
confidence tratada como ground truth
TableMagic executando OCR oculto
versão/modelo não identificado
Paddle baseline alterado junto com a refatoração
resultado RAW indisponível
ground truth inexistente para alegação de qualidade
```

---

# 65. Recomendação final de desenho

A evolução recomendada é:

```text
                   ┌──────────────────────────┐
                   │       PDFExtractor       │
                   └────────────┬─────────────┘
                                │
                    Native / visual routing
                                │
                         needs OCR?
                                │
                                ▼
                   ┌──────────────────────────┐
                   │       OCRBackend         │
                   │      canonical API       │
                   └────────────┬─────────────┘
                                │
          ┌─────────────────────┼─────────────────────────┐
          │                     │                         │
          ▼                     ▼                         ▼
       Paddle                RapidOCR                 Tesseract
   PP-OCRv6 medium      PP-OCRv6 medium                 5/por
                         │          │
                         ▼          ▼
                       ONNX      OpenVINO

                                │
                                ▼
                             EasyOCR
                              pt/CPU

Todos
  │
  ▼
OCRResult canônico
  │
  ├── RAW benchmark
  │
  └── pipeline normal
          │
          ▼
      recovery
          │
      tables
          │
      assembly
          │
      Markdown
```

A primeira entrega deve terminar **antes de adicionar RapidOCR**:

```text
Paddle antigo
     ==
Paddle atrás de OCRBackend
```

Somente depois dessa equivalência estar demonstrada, implementar:

```text
RapidOCR + ONNX Runtime
```

Essa ordem reduz drasticamente o risco de atribuir a uma engine nova uma regressão causada pela própria refatoração.

---

# 66. Referências oficiais consultadas

## PaddleOCR

- PaddleOCR — documentação geral e PP-OCRv6  
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/index.en.md

- PP-OCRv6  
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md

- General OCR Pipeline  
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md

- Table Recognition Pipeline V2  
  https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/table_recognition_v2.en.md

## RapidOCR

- Model list  
  https://rapidai.github.io/RapidOCRDocs/main/en/model_list/

- Parameters  
  https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/parameters/

- Usage/output schema  
  https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/

- Installation  
  https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/

## Tesseract

- Tesseract command line and TSV  
  https://github.com/tesseract-ocr/tessdoc/blob/main/Command-Line-Usage.md

- Improving quality  
  https://github.com/tesseract-ocr/tessdoc/blob/main/ImproveQuality.md

- `tessdata_best`  
  https://github.com/tesseract-ocr/tessdata_best

## EasyOCR

- API documentation  
  https://jaided.ai/easyocr/documentation/

- Supported languages  
  https://jaided.ai/easyocr/

- Model Hub / offline models  
  https://jaided.ai/easyocr/modelhub/

---

# 67. Resultado esperado da primeira implementação

Antes de qualquer benchmark entre motores, o desenvolvedor deve conseguir demonstrar:

```text
1. Paddle atual encapsulado no contrato novo.
2. Resultado funcionalmente equivalente ao baseline.
3. CLI anterior continua funcionando.
4. Native-first continua igual.
5. TableMagic continua igual.
6. Markdown continua igual.
7. Model setup/status continua funcionando.
8. Execução continua totalmente offline.
9. Testes existentes continuam verdes.
10. Novos testes de contrato estão verdes.
```

Esse será o ponto seguro para começar a implementação do **RapidOCR PP-OCRv6 medium + ONNX Runtime**, primeira engine realmente nova do plano.

---

# 68. Auditoria do repositório — gaps e riscos identificados

Esta seção registra lacunas identificadas após leitura completa do repositório em 25/09/2026, branch `fix/pdfextractor-correcoes`. Os itens abaixo complementam o plano e devem ser resolvidos antes ou durante as fases indicadas.

---

## 68.1. Conflito com `engine.py` protegido → resolver via `contracts.py`

O repositório já possui `src/structured_pdf_text/ocr/engine.py` com o Protocol `OcrEngine`:

```python
class OcrEngine(Protocol):
    def recognize_page(self, page_image, page_index, page_bbox=None, *, quality_variants=None) -> list[OcrToken]: ...
    def recognize_region(self, page_image, page_index, region_bbox) -> list[OcrToken]: ...
```

Este arquivo é **protegido** e não deve ser alterado.

O plano propõe um `OCRBackend` mais rico com `identity`, `capabilities`, `healthcheck`, `close`. Esse contrato mais rico **não pode ir em `engine.py`**.

**Resolução obrigatória para a Fase 1:**

- `contracts.py` define o novo Protocol `OCRBackend` com os métodos adicionais.
- Os backends em `backends/` implementam ambos os protocolos **estruturalmente** (Python structural typing — sem herança explícita).
- `recovery.py` (também protegido) continua recebendo qualquer objeto que satisfaça `OcrEngine`. Os novos backends satisfazem automaticamente porque implementam `recognize_page` e `recognize_region`.

Resultado: `engine.py` e `recovery.py` permanecem intocados em todas as fases.

---

## 68.2. Pré-requisito da Fase 4 — exportar PP-OCRv6 para ONNX

RapidOCR suporta oficialmente PP-OCRv4 ONNX. Para PP-OCRv6, é necessário:

1. Exportar os modelos PaddlePaddle (`.pdmodel` + `.pdiparams`) para ONNX usando `paddle2onnx`.
2. Verificar que a arquitetura interna do PP-OCRv6 exporta sem erro (há mudanças na rede em relação à v4/v5).
3. Confirmar que o RapidOCR consegue carregar e executar os modelos exportados.

**Esta validação deve ser feita como a etapa "P4-pre" na tabela de status**, antes de iniciar o desenvolvimento do `backends/rapidocr.py`.

Comandos de referência:

```bash
# exportar modelo de detecção
paddle2onnx \
    --model_dir ~/.cache/pdfextractor/paddlex/official_models/PP-OCRv6_medium_det \
    --model_filename inference.pdmodel \
    --params_filename inference.pdiparams \
    --opset_version 11 \
    --save_file pp_ocrv6_det.onnx

# exportar modelo de reconhecimento
paddle2onnx \
    --model_dir ~/.cache/pdfextractor/paddlex/official_models/PP-OCRv6_medium_rec \
    --model_filename inference.pdmodel \
    --params_filename inference.pdiparams \
    --opset_version 11 \
    --save_file pp_ocrv6_rec.onnx
```

Validação mínima com RapidOCR antes de avançar:

```python
from rapidocr_onnxruntime import RapidOCR
engine = RapidOCR(det_model_path="pp_ocrv6_det.onnx", rec_model_path="pp_ocrv6_rec.onnx")
result, _ = engine(img)
assert result is not None
```

---

## 68.3. Token mapper por engine — mapeamento para `OcrToken`

O tipo `OcrToken` (definido em `document.py`) é o contrato de saída canônico que todos os backends devem produzir. Cada backend precisa de um mapper responsável pela conversão do formato nativo:

| Engine | Formato nativo | Mapeamento para `OcrToken` |
|---|---|---|
| Paddle | já produz `OcrToken` via `PaddleOcrEngine` | wrapping direto — preservar coordenadas e confidence |
| RapidOCR | `[[bbox_4pts, text, confidence]]` | bbox → `BBox`, confidence como float |
| Tesseract (TSV) | linhas TSV: `level conf left top width height text` | filtrar `level==5` (word), calcular `BBox` de pixel → doc coords |
| EasyOCR | `[[bbox_4pts, text, confidence]]` | similar ao RapidOCR |

**Atenção sobre coordenadas:** todos os backends recebem uma imagem rasterizada na escala do PDFExtractor (`ocr_render_scale=2.0` por padrão). O `BBox` retornado em `OcrToken` deve estar em **coordenadas de página** (não de pixel), usando a mesma transformação que o `PaddleOcrEngine` atual aplica. O adapter de cada backend deve realizar essa transformação explicitamente.

---

## 68.4. EasyOCR e Windows Server 2025 — restrições de ambiente

O ambiente de produção é **Windows Server 2025, CPU-only**. Duas restrições relevantes:

1. **Conflito PaddlePaddle + PyTorch no mesmo venv:** EasyOCR usa PyTorch. PaddlePaddle e PyTorch podem coexistir, mas a combinação deve ser testada antes de incluir EasyOCR no mesmo ambiente de benchmark. A alternativa é usar um venv separado para a Fase 7.

2. **Download de modelos EasyOCR:** por padrão, EasyOCR tenta baixar modelos no primeiro uso. No ambiente offline do servidor, os modelos (`.pth`) devem ser colocados manualmente em `~/.EasyOCR/model/` antes da execução. O `setup-models` da CLI deve incluir EasyOCR na Fase 7.

---

## 68.5. Tesseract como subprocesso — medição de memória

Tesseract via `pytesseract` ou subprocess direto não aparece no RSS do processo Python. O benchmark RAW deve capturar a memória da árvore de processos completa:

```python
import psutil

proc = psutil.Process()
# inclui o subprocesso Tesseract
all_procs = [proc] + proc.children(recursive=True)
peak_rss = sum(p.memory_info().rss for p in all_procs) / (1024 * 1024)
```

Sem isso, o benchmark subestimará sistematicamente o custo de memória do Tesseract em relação às outras engines.

---

## 68.6. `config.py` — extensão retrocompatível

O `ExtractorConfig` atual tem `language: str = "pt"`. Os novos campos devem ser **adicionados** sem remover os existentes:

```python
# adições sugeridas em ExtractorConfig
ocr_engine: str = "paddle"          # "paddle" | "rapidocr-onnx" | "rapidocr-openvino" | "tesseract" | "easyocr"
ocr_runtime: str = "paddle_static"  # apenas informativo para paddle; "onnxruntime" | "openvino" para rapidocr
```

O factory lê `ocr_engine`; quando `engine == "paddle"`, usa `language` como antes. Ausência dos novos campos (via `dataclasses.field(default=...)`) garante que todo código existente continua funcionando sem alteração.






## PDFExtractor — Auditoria técnica e plano corretivo para comparar PP-OCRv5 e PP-OCRv6 com PP-TableMagic

## 14. Resultados do Comparativo PP-TableMagic — tm-v5 × tm-v6 (Seção 2.2)

### 14.1. Metadados da execução

| Campo | Valor |
|---|---|
| Script | `scripts/eval_v6/compare_tablemagic.py` |
| SHA do commit | `ee80004bda3176523dba420964b1d8633c7084ef` |
| Plataforma | Windows Server 2025 (AMD64) |
| paddleocr | 3.7.0 |
| paddlepaddle | 3.3.1 |
| paddlex | 3.7.2 |
| pypdfium2 | 5.11.0 |
| Pillow | 11.1.0 |
| PDF de referência | `Document_AI_V2.pdf` |
| Páginas processadas | 42 (todas) |
| Status geral | ✅ 84 inferências, 0 erros |

**Perfis comparados:**

| Perfil | OCR interno | Modelos de estrutura |
|---|---|---|
| `tm-v5` | `PP-OCRv5_server_det` + `latin_PP-OCRv5_mobile_rec` | SLANeXt, RT-DETR-L, PP-LCNet (idênticos entre braços) |
| `tm-v6` | `PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec` | SLANeXt, RT-DETR-L, PP-LCNet (idênticos entre braços) |

Observação sobre o CF-02 desta rodada: o parâmetro `use_ocr_results_with_table_cells` foi omitido do construtor de `TableRecognitionPipelineV2` porque o `paddleocr 3.7.0` rejeita argumentos desconhecidos no construtor. Isso está registrado no manifesto `evaluation.json` como `"not_applied_paddleocr_3.7.0_constructor_rejects_argument"`. O efeito sobre a comparação é simétrico — ambos os braços foram afetados da mesma forma.

### 14.2. Comportamento do pipeline com `use_layout_detection=False`

**Todas as 42 páginas retornaram exatamente 1 "tabela" por página, independentemente do conteúdo real.**

Com `use_layout_detection=False`, a `TableRecognitionPipelineV2` recebe a página inteira como uma única região de tabela, sem etapa prévia de detecção de layout. Isso é intencional no contexto desta avaliação — elimina a variável de detecção de layout para isolar o efeito do OCR interno. Porém, **o campo `table_count_delta` (sempre 0) não tem poder discriminante neste corpus**: páginas de texto puro, gráficos, diagrama e tabelas reais recebem o mesmo tratamento.

Consequência direta: as métricas `tm_v5_tables` e `tm_v6_tables` registradas no `evaluation.json` refletem o número de *regiões processadas pela pipeline de tabela*, não o número de tabelas detectadas no conteúdo do PDF.

### 14.3. Desempenho

**Inicialização do pipeline:**

| Perfil | Init (ms) | Diferença |
|---|---|---|
| tm-v5 | 5.627 ms | referência |
| tm-v6 | 4.591 ms | **−18% (v6 mais rápido)** |

**Inferência por página:**

| Métrica | tm-v5 | tm-v6 | Diferença |
|---|---|---|---|
| Total (42 páginas) | ~814 s | ~666 s | **−18% (v6 mais rápido)** |
| Média por página | ~19,4 s | ~15,9 s | **−18%** |
| Máximo (págs. 19–20, tabela de 270 células) | ~30 s | ~28 s | −7% |

A vantagem de velocidade do v6 no modo TableMagic (~18%) é ligeiramente inferior à registrada no comparativo OCR puro (~28% em modo adaptativo). A sobreposição dos cinco modelos de estrutura de tabela partilhados amortece parte da diferença.

**Páginas mais lentas (19 e 20)** correspondem à tabela de orçamento trimestral com ~270 células. Ambos os braços levaram ~28-30 s para essas páginas.

### 14.4. Qualidade textual dentro das células

Os modelos de estrutura de tabela (SLANeXt, RT-DETR-L, PP-LCNet) são **idênticos entre tm-v5 e tm-v6**. Portanto, a geometria de células e o HTML estrutural são os mesmos — as diferenças observadas nas saídas são exclusivamente de OCR.

**Casos em que tm-v6 produz resultado melhor:**

| Página | tm-v5 | tm-v6 | Observação |
|---|---|---|---|
| 2 | `OcR` | `OCR` | Capitalização corrigida |
| 4 | `portuquês` | `português` | Acento corrigido |
| 4 | `GS2-PT-BR-ÁRvORE-004` | `GS2-PT-BR-ÁRVORE-004` | Caixa alta consistente |
| 7 | `tachade` | `tachado` | Grafia corrigida |
| 18 | `1° semestre` | `1º semestre` | Símbolo ordinal correto |
| 27 | `GS2-SCAN-CoNTRAST-O27` | `GS2-SCAN-CONTRAST-027` | Corrige 'O' → '0' e caixa |
| 28 | `sințético` / `NOIsE-028` | `sintético` / `NOISE-028` | Diacrítico e capitalização corretos |

**Casos em que tm-v5 produz resultado melhor (regressões do v6):**

| Página | tm-v5 | tm-v6 | Observação |
|---|---|---|---|
| 11 | `calcular_total` | `calcular total` | v6 remove underscore em nome de função |
| 13 | `9ª` | `gª` | v6 confunde '9' com 'g' |
| 15 | `TI` | `πI` | v6 confunde 'T' com 'π' |

As regressões do v6 afetam identificadores de código (underscores) e símbolos alfanuméricos em contextos de baixa resolução. Esse padrão é consistente com o observado no comparativo OCR puro (Seção 13.4).

**Observação importante — página 30 (fonte 6,4 pt):**

No comparativo OCR puro (Seção 13.4), o tm-v5 **falhou completamente** na página 30 em modo baseline, invertendo a ordem de leitura. No modo TableMagic, **ambos os braços leram o conteúdo corretamente**. A hipótese é que a decomposição em células pelo SLANeXt força uma ordem de leitura por grade, mitigando o bug de ordem de leitura do v5. Isso é relevante para a decisão de integração: TableMagic pode compensar problemas de layout do OCR puro em páginas de texto denso.

### 14.5. Confiança do modelo (avg_score)

A maioria das páginas apresenta `avg_score` > 0,99 em ambos os braços — alta confiança no texto extraído.

**Páginas com diferença relevante:**

| Página | Conteúdo | tm-v5 avg_score | tm-v6 avg_score | Observação |
|---|---|---|---|---|
| 12 | Fórmulas matemáticas | 0,822 | 0,957 | v6 significativamente mais confiante |
| 32 | Tabela rotacionada 180° | 0,343 | 0,764 | v6 ~2× mais confiante |
| 33 | Tabela rotacionada 270° | 0,311 | 0,721 | v6 ~2× mais confiante |

Para conteúdo rotacionado (págs. 32–33), nenhum dos braços corrige a orientação da tabela (`use_table_orientation_classify=False`), mas o v6 apresenta confiança significativamente maior no texto que consegue extrair. Isso se alinha com os melhores modelos de reconhecimento do PP-OCRv6 para caracteres em orientações não padrão.

### 14.6. Introspeção de modelos carregados

O campo `loaded_model_names_observed` em ambos os braços retornou **apenas os cinco modelos de estrutura de tabela**:

```
['PP-LCNet_x1_0_table_cls', 'RT-DETR-L_wired_table_cell_det',
 'RT-DETR-L_wireless_table_cell_det', 'SLANeXt_wired', 'SLANeXt_wireless']
```

Os modelos OCR internos (`PP-OCRv5_server_det`, `latin_PP-OCRv5_mobile_rec`, `PP-OCRv6_medium_det`, `PP-OCRv6_medium_rec`) **não aparecem** nesta lista. O mecanismo `_loaded_model_names()` acessa `pipeline._model_list` ou equivalente, que no PaddleX 3.7.x expõe apenas os modelos de estrutura de tabela, não os sub-componentes OCR internos.

Isso significa que a verificação de manifesto confirma que os modelos de estrutura corretos foram carregados, mas não comprova via introspeção que o OCR interno correto foi utilizado. A evidência indireta (diferenças de qualidade de texto confirmam comportamento distinto entre os braços) valida que os modelos OCR configurados via `text_detection_model_dir` / `text_recognition_model_dir` foram de fato utilizados.

### 14.7. Volume de artefatos por página — nota para rodadas futuras

A execução atual gerou a seguinte estrutura de saída:

```
output/compare_tablemagic/
  tm-v5/
    page_0001/  input.png + result_000/ (artifacts) + page_result.json
    page_0002/  ...
    ...         (42 diretórios × ~5–10 arquivos cada)
  tm-v6/
    page_0001/  ...
    ...         (42 diretórios × ~5–10 arquivos cada)
  evaluation.json
```

Para 42 páginas × 2 perfis = **84 diretórios por página**, totalizando centenas de arquivos (PNGs de entrada + HTML de resultado + JSONs por item). Isso é aceitável para a rodada de avaliação, mas inviável para uso corrente ou integração em CI.

**Melhoria pendente:** adicionar flag `--no-artifacts` a `compare_tablemagic.py` para omitir a chamada a `_save_result_artifacts()` e salvar apenas `page_result.json` + `evaluation.json`. O `evaluation.json` já contém `pred_html`, `cell_count`, `avg_score` e `elapsed_ms` para toda análise de qualidade; os artefatos visuais só são necessários para inspeção manual pontual.

### 14.8. Conclusões e próximos passos

**Síntese:**

| Dimensão | Resultado |
|---|---|
| Velocidade | tm-v6 ~18% mais rápido na inferência e inicialização |
| Estrutura de tabela | **Idêntica** entre os braços (mesmos 5 modelos compartilhados) |
| Qualidade OCR em células | tm-v6 melhor em capitalização, diacríticos e rotações; tm-v5 melhor em underscores e alguns símbolos alfanuméricos |
| Conteúdo rotacionado | tm-v6 significativamente mais confiante (avg_score 2× maior em págs. 32–33) |
| Página de fonte mínima (pág. 30) | Ambos corretos via TableMagic (v5 falha sem TableMagic) |
| Erros de inferência | Nenhum em ambos os braços (42/42 páginas) |

**PP-OCRv6 apresenta vantagem consistente em TableMagic**, preservando a tendência observada no comparativo OCR puro. O risco de regressão mais relevante é a remoção de underscores em identificadores de código (página 11) — cenário presente em PDFs com blocos de código-fonte.

**Próximas etapas:**

1. **Gate 6 — Critérios de rollback e promoção:** definir os limiares quantitativos (avg_score mínimo, taxa de erro, tempo máximo) que determinam se v6 substitui v5 como perfil padrão. *(pendente)*
2. ✅ **Integração seletiva do TableMagic (`compare_hybrid.py`):** implementado — `scripts/eval_v6/compare_hybrid.py` ativa TableMagic somente quando `avg_confidence < 0.70`, usa `use_layout_detection=True` (Opção B) e não gera artefatos por página. Ver Seção 15.
3. ✅ **Sem artefatos por página:** resolvido por design no `compare_hybrid.py` — saída é apenas `evaluation.json`.
4. **Corpus de produção:** executar `compare_hybrid.py` sobre `corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf` quando o arquivo estiver disponível no servidor. Essa será a rodada definitiva antes da decisão de promoção do v6.

---

## 15. Avaliação Híbrida — OCR com fallback condicional para PP-TableMagic

### 15.1. Design do script `compare_hybrid.py`

**Arquivo:** `scripts/eval_v6/compare_hybrid.py`

**Lógica por página:**

```
para cada página e perfil (v5, v6):
    1. PaddleOCR → avg_confidence
    2. se avg_confidence < 0.70 E texto_detectado > 0:
           TableRecognitionPipelineV2 (use_layout_detection=True)
           se tabelas encontradas  → usar texto das células
           se nenhuma tabela       → manter OCR, registrar 'ocr_tablemagic_no_table'
    3. senão:
           usar resultado OCR diretamente
```

**Modos registrados por página:**

| Modo | Condição |
|---|---|
| `ocr` | `avg_confidence >= 0.70` — OCR direto |
| `tablemagic` | confiança baixa + tabelas detectadas — TableMagic substitui OCR |
| `ocr_tablemagic_no_table` | confiança baixa + sem tabelas — OCR mantido |
| `no_text_detected` | PaddleOCR não detectou nenhum texto |

**Decisões de design:**

- **Threshold 0.70** — valor escolhido após análise dos comparativos anteriores: páginas rotacionadas (págs. 31–33) têm confiança << 0.30; ruído (pág. 28) ~0.55–0.70; páginas normais > 0.90. Threshold de 0.80 ativaria TableMagic em páginas aceitáveis (0.70–0.80), adicionando ~15s desnecessários.
- **`use_layout_detection=True` (Opção B)** — detecta regiões de tabela no layout antes de processar, em vez de tratar a página inteira como uma tabela.
- **Lazy init do TableMagic** — `TableRecognitionPipelineV2` só é inicializado na primeira página que disparar o threshold, economizando ~4–5s de init quando todas as páginas têm alta confiança.
- **Sem artefatos** — imagens são criadas em `tempfile.mkdtemp()` e deletadas após cada página; saída é apenas `output/compare_hybrid/evaluation.json`.
- **Parâmetro `--confidence-threshold`** — configurável via CLI (padrão: 0.70).

### 15.2. Modelo de layout — PP-DocLayout_plus-L

**Modelo escolhido:** `PP-DocLayout_plus-L` — padrão do PaddleX 3.7.2.

**Funcionamento offline:** 100% offline via `layout_detection_model_dir` após download inicial.

**Download:** necessário uma vez por diretório de cache (v5 e v6), na primeira execução com internet. Execuções subsequentes funcionam com `PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True`.

**Alternativas menores** (se houver necessidade de economizar espaço ou velocidade):

| Modelo | Quando usar |
|---|---|
| `PP-DocLayout_plus-L` | ✅ Padrão — melhor precisão, ~200MB |
| `PP-DocLayout-M` | Alternativa média — menos preciso, mais rápido |
| `PP-DocLayout-S` | Alternativa pequena — menor espaço, menor precisão |

**Diretório esperado por cache:**
```
~/.cache/pdfextractor/paddlex/official_models/PP-DocLayout_plus-L/
~/.cache/pdfextractor/paddlex-v6-eval/official_models/PP-DocLayout_plus-L/
```

### 15.3. Pré-requisitos para execução no servidor

1. **PDF do corpus definitivo** disponível em `corpus/Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf`
2. **PP-DocLayout_plus-L** baixado em ambos os caches (v5 e v6)
3. Modelos OCR e de estrutura de tabela já presentes (confirmados na Seção 14)

### 15.4. Comandos de execução (PowerShell — Windows Server)

> O ambiente virtual já deve estar ativo: confirme que o prompt exibe `(.venv)`.

**Passo 0 — Copiar o script do WSL para o workspace Windows (apenas se ainda não estiver presente):**

```powershell
Copy-Item "\\wsl.localhost\Ubuntu\home\victorperone\workspace\pdfextractor\scripts\eval_v6\compare_hybrid.py" -Destination "scripts\eval_v6\compare_hybrid.py"
```

**Passo 1 — Baixar PP-DocLayout_plus-L (apenas uma vez, requer internet):**

```powershell
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --pages 1 --allow-download --output-dir output\compare_hybrid_test
```

**Passo 2 — Verificar inventário offline:**

```powershell
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --v5-cache "$HOME\.cache\pdfextractor\paddlex" --v6-cache "$HOME\.cache\pdfextractor\paddlex-v6-eval" --check-only
```

**Passo 3 — Execução definitiva (100% offline):**

```powershell
$env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
python scripts\eval_v6\compare_hybrid.py corpus\Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf --v5-cache "$HOME\.cache\pdfextractor\paddlex" --v6-cache "$HOME\.cache\pdfextractor\paddlex-v6-eval" --confidence-threshold 0.70 --output-dir output\compare_hybrid
```

### 15.6. Gate 6 — Decisão de promoção (2026-09-24) ✅ CONCLUÍDO

**Decisão:** PP-OCRv6 (`pt-v6-medium`) promovido a perfil padrão de produção.

**Alterações realizadas:**

| Ficheiro | Alteração |
|---|---|
| `src/structured_pdf_text/ocr/models.py` | Chave `"pt"` passa a apontar para PP-OCRv6 (`PP-OCRv6_medium_det` + `PP-OCRv6_medium_rec`). PP-OCRv5 preservado como `"pt-v5"` para rollback. |
| `scripts/eval_v6/compare_hybrid.py` | `DEFAULT_THRESHOLD` atualizado de `0.70` para `0.85` (compensa sobreconfiança do v6 em páginas degradadas). |

**Passo de deployment no servidor:**
```powershell
# Mover modelos v6 para o caminho padrão de produção
Rename-Item "$HOME\.cache\pdfextractor\paddlex" "$HOME\.cache\pdfextractor\paddlex-v5-backup"
Rename-Item "$HOME\.cache\pdfextractor\paddlex-v6-eval" "$HOME\.cache\pdfextractor\paddlex"
```

**Rollback disponível:**
```powershell
# Reverter cache
Rename-Item "$HOME\.cache\pdfextractor\paddlex" "$HOME\.cache\pdfextractor\paddlex-v6-eval"
Rename-Item "$HOME\.cache\pdfextractor\paddlex-v5-backup" "$HOME\.cache\pdfextractor\paddlex"
# Reverter código: alterar chave "pt" em models.py de volta para PP-OCRv5, ou usar --language pt-v5
```

---

### 15.5. Resultados e conclusão

**Corpus:** `Corpus_Integrado_PDF_OCR_TableMagic_V3.pdf` — 144 páginas  
**Data de execução:** 2026-09-24  
**Script:** `scripts/eval_v6/compare_hybrid.py` — pipeline 3-níveis (nativo → OCR → TableMagic)  
**Limiar de confiança:** 0.70 | **min_native_chars:** 50

---

#### Distribuição de páginas por modo de extração

| Modo de extração | v5 (PP-OCRv5) | v6 (PP-OCRv6) |
|---|---|---|
| Texto nativo (`native`) | **98 / 144 (68,1%)** | **98 / 144 (68,1%)** |
| Somente OCR (`ocr_only`) | 42 / 144 (29,2%) | 44 / 144 (30,6%) |
| TableMagic activado com tabelas (`tablemagic`) | **2** | **0** |
| TableMagic activado sem tabelas (`ocr_tablemagic_no_table`) | 2 | 2 |
| Erros | **0** | **0** |

---

#### Desempenho de inicialização e OCR

| Métrica | v5 | v6 | Diferença |
|---|---|---|---|
| Inicialização OCR (`ocr_init_ms`) | 4 514 ms | **1 221 ms** | v6 **3,7× mais rápido** |
| Inicialização TableMagic (`tm_init_ms`) | 4 640 ms | 5 645 ms | v6 ~20% mais lento |
| Velocidade média por página escaneada | ~33–36 s | ~22–25 s | v6 **~30% mais rápido** |

---

#### Análise das páginas críticas

As páginas 96, 97, 99 e 116 são as únicas que acionaram o limiar de confiança em pelo menos um perfil. Todas correspondem a conteúdo com degradação visual severa (rotação, tabelas financeiras invertidas).

| Página | v5 conf. | v5 modo | v6 conf. | v6 modo |
|---|---|---|---|---|
| 96 | 0,291 | `ocr_tablemagic_no_table` | 0,666 | `ocr_tablemagic_no_table` |
| 97 | 0,298 | `ocr_tablemagic_no_table` | 0,683 | `ocr_tablemagic_no_table` |
| 99 | 0,463 | **`tablemagic` (3 tabelas)** | 0,754 | `ocr_only` |
| 116 | 0,457 | **`tablemagic` (1 tabela)** | 0,778 | `ocr_only` |

- **Páginas 96 e 97**: ambos os perfis acionam TableMagic, mas nenhuma tabela estruturada é detectada — o conteúdo está demasiado degradado/rotacionado para recuperação automática.
- **Páginas 99 e 116**: divergência crítica. O v5, com menor confiança (< 0,70), aciona TableMagic e recupera tabelas estruturadas. O v6 retorna confiança acima do limiar (0,754 e 0,778) e permanece em modo OCR — produzindo texto embaralhado com pontuação elevada (sobreconfiança em conteúdo rotacionado).

---

#### Conclusões

1. **Texto nativo idêntico**: ambos os perfis concordam em quais páginas têm camada textual (98/144). O nível 1 do pipeline elimina OCR para 68% do documento.

2. **v6 significativamente mais rápido em OCR**: ~30% de ganho por página e 3,7× na inicialização — vantagem relevante para volumes grandes.

3. **v6 apresenta sobreconfiança em conteúdo degradado/rotacionado**: nas páginas 99 e 116, o v6 atribui scores elevados (> 0,75) a texto ilegível, ignorando o escalamento para TableMagic. O v5 é mais conservador e aciona o fallback correctamente.

4. **Impacto prático do TableMagic**: em 144 páginas de corpus misto, o TableMagic contribuiu em apenas 2 páginas com o v5. A estratégia de fallback funcionou correctamente quando acionada.

5. **Recomendação — limiar diferenciado por perfil**: se o v6 for promovido como perfil padrão, o limiar de confiança deverá ser reduzido para ~0,80–0,85 para compensar a tendência de sobreconfiança, garantindo que páginas como 99 e 116 continuem a activar TableMagic.

6. **Gate 6 (decisão de promoção)**: o v6 é superior em velocidade, mas apresenta risco de qualidade regressiva em páginas com rotação/degradação severa. Antes de promover, é necessário validar o texto produzido para as páginas 99 e 116 em ambos os perfis e definir se o limiar ajustado resolve a divergência.
