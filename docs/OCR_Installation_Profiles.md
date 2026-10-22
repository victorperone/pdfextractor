# Instalação isolada das engines OCR

Use Python 3.12 e um ambiente por configuração de execução. As versões atuais
declaram distribuições OpenCV diferentes: EasyOCR exige `opencv-python-headless`,
RapidOCR exige `opencv-python` e PaddleX com extra OCR exige
`opencv-contrib-python`. Instalar todas no mesmo ambiente pode satisfazer os
metadados do pip, mas sobrepõe o módulo `cv2`. A
[orientação oficial do OpenCV](https://pypi.org/project/opencv-python/#installation-and-usage)
requer escolher somente uma dessas distribuições.

| Configuração | Ambiente padrão | Distribuição OpenCV |
|---|---|---|
| `easyocr` | `.ocr-envs/easyocr` | `opencv-python-headless` |
| `paddle` | `.ocr-envs/paddle` | `opencv-contrib-python` |
| `rapidocr-onnxruntime` | `.ocr-envs/rapidocr-onnxruntime` | `opencv-python` |
| `rapidocr-openvino` | `.ocr-envs/rapidocr-openvino` | `opencv-python` |
| `tesseract` | `.ocr-envs/tesseract` | `opencv-python-headless`, para tabelas visuais |

Isso isola a instalação dos programas; o contrato e a seleção da engine na API
continuam iguais. Uma aplicação importa a biblioteca no ambiente da engine que
vai executar. O runner comparativo lança cada extração com seu próprio Python.
Não há roteamento automático ou mistura de engines durante a extração.

Para revisar comandos sem criar diretórios, baixar pacotes ou carregar runtimes:

```bash
python scripts/setup_ocr_environments.py --configuration easyocr paddle rapidocr-onnxruntime rapidocr-openvino tesseract --plan
```

Para instalar somente uma configuração:

```bash
python scripts/setup_ocr_environments.py --configuration rapidocr-onnxruntime
```

O comando cria um virtualenv sem pacotes de sistema, instala o extra e os pins
da plataforma, executa `pip check`, verifica que existe exatamente a distribuição
OpenCV esperada e importa o runtime/provider em um processo separado. Não
instala modelos por padrão. `--provision-models --cache-home <raiz>` habilita
explicitamente os downloads e o preflight estático. EasyOCR usa `<raiz>/easyocr`,
RapidOCR usa `<raiz>/rapidocr` e Paddle usa `<raiz>/paddlex/official_models`.
O parâmetro `--cache-home` passado diretamente ao preflight Paddle é
`<raiz>/paddlex`, conforme a API de cache dessa engine.

Tesseract 5.x e `por.traineddata` são dependências externas: instale-os e adicione
o executável ao `PATH`. O instalador Python não instala o executável. Os wrappers
`setup_ocr_benchmark.sh` e `.ps1` preparam as cinco configurações e seus modelos;
o Bash reconhece também o prefixo local `.ocr-runtime` existente.

Ambientes existentes são recusados antes de qualquer instalação do lote. Para
repetir uma validação, escolha outro `--env-root`; preserve os relatórios
anteriores. A `.venv` de desenvolvimento permanece separada. `--no-index
--find-links <diretório-de-wheels>` permite instalação com wheels previamente
provisionados, incluindo dependências de build e wheels CPU do PyTorch. Essa
opção não pode ser combinada com downloads de modelos.

Cada ambiente guarda `setup.log`, `setup-report.json`, `package-install.json`
(URLs/hashes e dependências resolvidas), `install-check.json` e, no EasyOCR,
`torch-install.json`. O relatório registra falha/interrupção e hashes dos scripts
e constraints antes/depois. Imports não inicializam inferência nem comprovam
qualidade, cancelamento ou ausência de vazamentos. `quality_accepted` e
`release_accepted` permanecem falsos.

Para comparar as engines no Linux/WSL, use um Python com o pacote core disponível
para validar o corpus/calcular métricas e selecione os ambientes de extração:

```bash
scripts/run_benchmark.sh --python .venv/bin/python --environment-root .ocr-envs --configurations easyocr,tesseract --run-suffix isolated-smoke
```

No PowerShell:

```powershell
.\scripts\run_benchmark.ps1 -PythonBin .\.venv\Scripts\python.exe -EnvironmentRoot .ocr-envs -Configuration "easyocr,tesseract" -RunSuffix isolated-smoke
```

Sem a opção de ambientes, `--python`/`-PythonBin` continua selecionando o mesmo
intérprete para todas as configurações. Se um ambiente solicitado estiver
ausente, o runner falha antes da extração, sem fallback para outro Python.
Verificação de modelos RapidOCR ocorre somente quando um provider RapidOCR foi
solicitado; outra engine não depende dos seus pesos ou variáveis de ambiente.

Para inspecionar um ambiente já instalado, usando seu Python:

```bash
.ocr-envs/rapidocr-onnxruntime/bin/python scripts/check_ocr_install.py --configuration rapidocr-onnxruntime --imports --report install-check-new.json
```

O checker não substitui relatórios existentes. Sem `--configuration`, verifica
o ambiente compartilhado histórico e, se consistente, separa os imports de cada
configuração em subprocessos. Erros de metadados impedem os imports; um único
wheel OpenCV não basta para aprovar a instalação.

O workflow manual `OCR isolated installation` oferece Linux e Windows por
configuração e publica os relatórios mesmo em falha. Configurar esse workflow
não é evidência de execução Windows: só resultados observados completam o gate.
Consulte o [estado de implementação](OCR_Decoupling_Implementation_Status.md)
para as instalações e os smokes efetivamente executados.

## GPU no Linux/WSL2

GPU é uma opção explícita da API (`ExtractorConfig(use_gpu=True)`) e da CLI
(`--gpu` em `extract`, `inspect`, `overlay`, `report` e `compare`). O padrão
continua CPU, inclusive quando o ambiente tem um wheel GPU instalado. `native`
e `fast` continuam sem inicializar runtimes OCR. A seleção de dispositivo é
local à engine; pedir GPU não troca engine, provider, modelo ou política OCR.
PaddleOCR (`pt`/`pt-v6-medium`), RapidOCR/ONNX e RapidOCR/OpenVINO usam
Det/Rec PP-OCRv6 **medium por padrão**. Small exige seleção explícita.

| Configuração | Ambiente padrão com `--gpu` | Runtime |
|---|---|---|
| `easyocr` | `.ocr-envs/gpu/easyocr` | PyTorch 2.14.0 + CUDA 13.0; quantização CPU desativada em CUDA |
| `paddle` | `.ocr-envs/gpu/paddle` | `paddlepaddle-gpu` 3.3.1, CUDA 12.6; modelos em `gpu:0` |
| `rapidocr-onnxruntime` | `.ocr-envs/gpu/rapidocr-onnxruntime` | `onnxruntime-gpu[cuda,cudnn]` 1.30.0, CUDA EP no detector e reconhecedor |
| `rapidocr-openvino` | `.ocr-envs/gpu/rapidocr-openvino` | CPU; a implementação OpenVINO do RapidOCR 3.9.2 fixa `device_name="CPU"` |
| `tesseract` | `.ocr-envs/gpu/tesseract` | CPU |

O perfil OpenVINO fixado não oferece aceleração na NVIDIA desta máquina. Para
RapidOCR com CUDA, selecione explicitamente `--ocr-provider onnxruntime`.
Não há fallback de provider para transformar OpenVINO em ONNX.

No WSL2, confirme `nvidia-smi` e `/dev/dxg`. O driver é o instalado no Windows;
não instale um driver Linux NVIDIA dentro da distribuição. As bibliotecas CUDA
e cuDNN de cada runtime ficam no virtualenv correspondente. O CUDA Toolkit do
sistema não precisa ter a mesma versão dos wheels. Consulte a
[orientação NVIDIA para WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
e as [dependências CUDA/cuDNN do ONNX Runtime](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

Para revisar e instalar os ambientes, com Python 3.12:

```bash
.venv/bin/python scripts/setup_ocr_environments.py --gpu \
  --configuration easyocr paddle rapidocr-onnxruntime rapidocr-openvino tesseract --plan

.venv/bin/python scripts/setup_ocr_environments.py --gpu \
  --configuration easyocr paddle rapidocr-onnxruntime rapidocr-openvino tesseract
```

Esses perfis usam `constraints-ocr-gpu-linux-py312.txt` e os índices oficiais
[PyTorch CUDA 13.0](https://download.pytorch.org/whl/cu130/torch/) e
[Paddle CUDA 12.6](https://www.paddlepaddle.org.cn/packages/stable/cu126/).
O lock `uv.lock` continua representando os extras CPU existentes. Paddle GPU e
ORT GPU são instalados com o pacote core e suas dependências explícitas, pois
os extras CPU instalariam um segundo dono de `paddle` ou `onnxruntime`.
O checker rejeita essas colisões e verifica CUDA no processo da engine.
Tesseract/OpenVINO mantêm as constraints CPU. O provisionamento GPU está
definido para Linux/WSL2; Windows nativo exige homologação separada.

Instalar/importar não comprova que a inferência usou GPU. Verifique cada engine
com uma página sintética, sem executar os documentos do corpus:

```bash
.ocr-envs/gpu/easyocr/bin/python scripts/smoke_ocr_gpu.py \
  --engine easyocr --cache-home .ocr-model-cache --output-dir outputs/gpu-smoke-easyocr

.ocr-envs/gpu/paddle/bin/python scripts/smoke_ocr_gpu.py \
  --engine paddle --cache-home .ocr-model-cache/paddle-v6-e2e --output-dir outputs/gpu-smoke-paddle

.ocr-envs/gpu/rapidocr-onnxruntime/bin/python scripts/smoke_ocr_gpu.py \
  --engine rapidocr --cache-home .ocr-model-cache --output-dir outputs/gpu-smoke-rapidocr
```

Os exemplos usam os caches já existentes nesta máquina; substitua pelos caminhos
provisionados em outra instalação. O smoke não baixa modelos e recusa diretórios
de saída existentes. Ele executa o pipeline autorizado em um PDF de imagem e
exige dispositivo efetivo CUDA, tokens OCR reais e documento com sucesso. Também
inspeciona os parâmetros do detector/reconhecedor EasyOCR, as configurações dos
predictors Paddle ou eventos CUDA nos perfis ORT de detecção/reconhecimento.
Uma sessão com CUDA disponível, mas sem nós executados em CUDA, é reprovada.
Checks de conteúdo sintético ficam separados do aceite de execução GPU; o smoke
não aprova qualidade do corpus ou release.

Para extrair depois da validação, por exemplo:

```bash
.ocr-envs/gpu/easyocr/bin/python -m structured_pdf_text.cli extract documento.pdf \
  --gpu --mode balanced --ocr-engine easyocr --ocr-quality-policy adaptive \
  --cache-home .ocr-model-cache --output markdown --output-file outputs/documento-gpu.md
```

Nos runners de benchmark, combine `--environment-root .ocr-envs/gpu --gpu`
(Bash) ou `-EnvironmentRoot .ocr-envs/gpu -Gpu` (PowerShell). Execute uma engine
por vez para evitar disputa de VRAM; todos os modelos de uma engine usam a
primeira GPU. O runtime efetivo e a razão de fallback aparecem na identidade e
nos manifestos. A verificação ocorre antes da inferência: disponibilidade CUDA,
construção dos modelos e providers efetivos no detector/reconhecedor. Se CUDA
estiver indisponível ou faltar uma biblioteca/configuração GPU, há aviso e
execução em CPU. Isso inclui dependências carregadas apenas na primeira
inferência: a mesma operação é repetida uma única vez em CPU. Modelo, idioma,
provider e escopo autorizado são preservados. O ONNX Runtime não recebe
permissão para ocultar falhas com seu retry automático; o backend controla e
registra a transição para CPU. Erros de modelo, cancelamento e exaustão de
memória não são confundidos com falta de configuração GPU. Para exigir GPU na
homologação, use o smoke estrito, que reprova qualquer fallback CPU.

Registre novas execuções GPU em outro namespace: CPU/GPU podem produzir textos
e confidências diferentes, inclusive pela mudança de quantização do EasyOCR.
O comparador mantém `use_gpu=True` significativo e não aprova uma comparação
CPU/GPU como equivalência de refatoração. Preserve os baselines CPU existentes.
