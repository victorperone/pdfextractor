# Correções do avaliador — etapa 6

## Escopo

Correção **A19**: impedir que outro conteúdo substitua silenciosamente métricas,
relatórios ou delta de uma execução anterior. A escolha desta etapa antes de
novas extrações protege os resultados que serão usados na comparação.

Base: `ca10c76`. Definições permanecem **3.0.4** e schema `evaluation-3.0.0`:
esta etapa altera publicação, sem mudar as fórmulas de qualidade. O protocolo
novo de publicação é `evaluation-artifacts-v1`.

Nenhuma extração de PDF, OCR de corpus, commit ou push foi executado. A falha de
sobrescrita é independente do conteúdo extraído e foi exercitada com Markdown
e subprocessos reais dos dois CLIs. A autorização para usar V3/V5 em extrações
reais, quando necessárias, continua registrada; elas não foram necessárias aqui.

## Contrato de identidade

`evaluation/artifacts.py` concentra a política usada por compute e regrade.

- A identidade inclui hashes dos snapshots efetivamente avaliados de hipótese
  e referência, `inputs`, schema, políticas/limites do avaliador e todos os
  metadados do run, incluindo páginas, perfil e resultados operacionais.
- Timestamps e commit de implementação não alteram a identidade. Os caminhos
  dos arquivos também não: mover uma cópia com os mesmos bytes não cria uma
  execução diferente. Metadados são serializados de forma determinística.
- Esta identidade é **mais estrita que comparabilidade de benchmark**. A mesma
  hipótese, todos os inputs e os mesmos metadados precisam concordar para
  reutilizar uma publicação. Runs comparáveis com outputs diferentes continuam
  comparáveis, mas exigem IDs/diretórios de publicação distintos.
- Alterar hipótese, referência, manifesto, PDF, escopo, modo, perfil, recovery,
  protocolo ou política de recurso causa erro explícito. Todos os arquivos
  anteriores permanecem com os mesmos bytes e mtimes.
- Repetir inputs idênticos retorna os arquivos publicados originalmente, sem
  regravar métricas, relatório ou journal. Para produzir uma nova avaliação,
  inclusive após mudança do código, usar outro ID/diretório.
- Se inputs/políticas forem iguais mas o resultado canônico calculado mudar,
  a publicação falha para investigação de determinismo/versionamento. Não se
  permite anexar um relatório novo calculado sobre resultados diferentes das
  métricas já publicadas. Só timestamp e commit são excluídos dessa verificação.
- IDs aceitam letras ASCII, dígitos, `.`, `_` e `-`, começando por letra/dígito.
  Paths, strings vazias e caracteres especiais de nomes Windows são rejeitados
  antes de criar os outputs. Omissão de `--run-id` mantém os defaults anteriores.

## Publicação e concorrência

- Cada namespace tem um journal oculto `.evaluation-<sha256>.json`, com
  identidade, papéis e SHA-256 exato dos arquivos. O journal é necessário para
  verificar/reutilizar o conjunto e deve acompanhar os artefatos.
- Arquivos antigos sem journal, modificados fora do publicador, ausentes,
  registros inválidos e symlinks não são substituídos ou adotados silenciosamente.
  O erro orienta usar outro `run-id` ou diretório quando há colisão.
- Um lock exclusivo coordena escritores do mesmo namespace, incluindo aliases
  de hífen/underscore e combinações engine/ID que geram o mesmo nome.
- `atomic_create_text()` publica um hard link de um temporário completo e
  sincronizado. O nome final só aparece com conteúdo completo e a criação
  falha se outro escritor o ocupar, inclusive numa corrida entre namespaces
  diferentes. O `os.replace()` dos demais usos de `atomic_write_text()` não
  foi alterado.
- Todos os conflitos conhecidos são verificados antes de escrever o conjunto.
  Métricas, erro e delta são preparados antes da publicação. O journal é gravado
  por último. Falhas normais de I/O removem somente arquivos novos desta
  tentativa e liberam o lock; evidências anteriores são preservadas.
- O conjunto não é uma transação única do filesystem: consumidores podem ver
  arquivos novos antes do journal. Morte do processo pode deixar lock/arquivos
  incompletamente registrados, que bloqueiam reutilização para revisão. Não há
  remoção automática de lock antigo nem reparo que arrisque substituir evidência.
- O filesystem precisa oferecer hard links no diretório de saída. Se não
  oferecer, a operação falha explicitamente, sem fallback que sobrescreva.
  Esta etapa foi executada no ambiente Linux/WSL; o gate Windows permanece
  pendente na validação final de plataformas.

## Convivência compute/regrade

Os nomes anteriores são mantidos em diretório novo. Se os dois CLIs forem usados
com inputs idênticos no mesmo namespace, o papel de cada arquivo é preservado.

| Papel | Nome inicial | Nome alternativo quando ocupado por outro papel |
|---|---|---|
| Canônico de compute | `metrics_v3_{slug}_{id}.json` | Sem substituição |
| Legado de compute | `metrics_{slug}_{id}.json` | `metrics_legacy_{slug}_{id}.json` |
| Erro legado | `errors_{slug}_{id}.md` | `errors_legacy_{slug}_{id}.md` |
| Canônico de regrade | `metrics_{engine}_{id}.json` | `metrics_v3_{slug}_{id}.json` |
| Erro canônico | `errors_{engine}_{id}.md` | `errors_v3_{slug}_{id}.md` |

Alias de um papel existente conserva os bytes originais, incluindo timestamps
de criação e separadores de linha. O CLI informa os caminhos realmente usados.
Os nomes reservados são verificados mesmo quando não serão produzidos naquela
invocação, impedindo apropriação de restos não registrados do outro caminho.

Um delta pode ser acrescentado à mesma execução, sem regravar os arquivos
anteriores. Seu hash de origem V1 é conservado: trocar o arquivo V1 para produzir
outro delta exige novo namespace. Arquivo V1 ausente, JSON inválido ou topo que
não seja objeto são rejeitados antes de qualquer publicação.

## Testes

- **97 novas regressões passaram**, incluindo matriz de nove alterações de
  entrada nos dois CLIs, idempotência byte a byte/mtime, variações de IDs e
  convivência dos CLIs nas duas ordens, com e sem hífens na engine.
- Concorrência exercitada com subprocessos reais: um lock de outro processo
  bloqueia a escrita, e dois runs simultâneos diferentes não misturam artefatos.
- Falhas injetadas de I/O, journal, filesystem sem hard links e ocupação do
  nome após preflight verificam rollback, limpeza e preservação do vencedor.
- Corrupção/ausência de artefato, journal inválido, versão desconhecida e symlink
  têm controles explícitos. Deltas inválidos e alterações de fonte também.
- T49 agora verifica todos os bytes e mtimes da repetição, além de `quality`.
  O teste de equivalência manifesto/Markdown usa diretórios distintos, pois as
  fontes têm proveniências diferentes mesmo com qualidade/hash efetivo iguais.
- **230 testes direcionados passaram**, incluindo contratos de inputs, fase 4
  e homologação anteriores.
- Suíte padrão: **2.274 passaram, 6 skips e 4 xfails; zero falhas**, em 2.284
  casos. `git diff --check` passou.
- Auditoria independente: **35 passaram e 4 falharam**, em 39 verificações.
  A reprodução original de sobrescrita passou sem alteração de suas assertivas.
- Resultados completos em
  [evaluator_stage6_results.json](evaluator_stage6_results.json).

Comandos principais:

```bash
.venv/bin/python -m pytest tests/test_evaluation_artifact_corrections.py -q --tb=short --junitxml=/tmp/pdfextractor_stage6_artifacts.xml
.venv/bin/python -m pytest -q --tb=short --junitxml=/tmp/pdfextractor_stage6.xml
.venv/bin/python -m pytest docs/audits/test_evaluator_ground_truth_audit.py -q --tb=short --junitxml=/tmp/pdfextractor_audit_stage6.xml
git diff --check
```

## Pendências

1. **A13:** relatório operacional canônico completo, identificadores, perfis,
   tempos/RAM e recortes por família/condição.
2. **A18:** schema formal v3 e invariantes de status/valor/contadores.
3. **A20:** parser/métricas legados de tabelas e CER de headings.
4. **A06 e validação final:** escalabilidade, corpus real e plataformas.

As quatro falhas restantes da auditoria reproduzem relatório operacional,
linhas vazias de tabela, pipes escapados e CER de heading nos caminhos legados.
O avaliador ainda não está liberado como referência definitiva de escolha de
OCR default. A etapa para para a revisão solicitada pelo usuário.

## Mensagem de commit sugerida

```text
fix: impedir sobrescrita e colisões de artefatos de avaliação

- compartilhar identidade e publicação imutável entre compute e regrade
- preservar bytes e timestamps em repetições idênticas
- rejeitar inputs alterados, outputs não registrados e publicações danificadas
- criar arquivos atomicamente sem substituir escritores concorrentes
- preservar papéis canônicos e legados ao compartilhar diretórios
- validar delta antes da publicação e proteger sua identidade de origem
- adicionar 97 regressões e fortalecer o contrato T49
```
