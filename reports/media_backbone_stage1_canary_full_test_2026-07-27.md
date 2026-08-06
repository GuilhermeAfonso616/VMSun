# Etapa 1 do Media Backbone — teste canário completo

## 1. Identificação

- Branch: `checkpoint/ptz-3d-monitor-20260727`
- Commit inicial: `a7db332` — `Salva checkpoint do PTZ 3D no monitor`
- Checkpoint final: criado ao concluir esta tarefa; consultar `git log -1 --oneline`
- Execução: 2026-07-27, aproximadamente 12:55–14:34 (America/Sao_Paulo)
- Serviços: `analitico`, `analitico-runtime`, `camera-gateway`, `webrtc-gateway` (MediaMTX), PostgreSQL e pool central de inferência com GPU
- Compose: `docker-compose.yml` e `docker-compose.gpu.yml`

O working tree já continha alterações da Etapa 1 e do checkpoint. Elas não foram descartadas.

## 2. Câmera selecionada

### Seleção inicial

- ID: 37
- Nome: NVR - Canal 6 sub
- Codec: H.265/HEVC
- Resolução: 704×576
- FPS anunciado: 15
- Host sanitizado: `177.137.223.61`
- Porta RTSP: 554
- Path MediaMTX: `cam_37`
- Estado antes do teste: Gateway `running`, worker inativo, path pronto, um leitor técnico do Gateway, sem sessão WebRTC e sem alarme ativo no início

A câmera 37 foi mantida inicialmente porque continuava sendo uma substream de baixo impacto, acessível, não crítica e sem operador conectado.

### Troca durante o teste

Durante a primeira execução completa do worker, surgiu um alarme real `person_entered` associado à câmera 37. O alarme não foi criado artificialmente, não foi alterado e não foi encerrado pelo teste. Antes do ensaio disruptivo, a câmera 37 foi devolvida ao estado sem worker e substituída.

### Câmera final do ensaio crítico

- ID: 36
- Nome: NVR - Canal 5 sub
- Codec: H.265/HEVC
- Resolução: 704×576
- FPS anunciado: 15
- Host sanitizado: `177.137.223.61`
- Porta RTSP: 554
- Path MediaMTX: `cam_36`
- Estado antes da falha: Gateway `running`, worker `running_motion_test`, inferência ativa, um leitor RTSP interno
- Sessões WebRTC/operador: 0
- Alarmes ativos: 0
- Worker ativo antes do teste: sim
- Motivo: substream de menor impacto, não crítica, RTSP acessível, path disponível, sem operador, sem alarme e sem auto-start

Outras candidatas avaliadas: IDs 32–35, todas streams principais H.265 1920×1080. Foram preteridas pelo maior custo. A câmera 29 estava parada manualmente e sem path pronto.

O host e a porta foram obtidos com `urllib.parse.urlparse`. Nenhum ID, IP ou segredo foi persistido em código, Compose ou arquivo de exemplo.

## 3. Topologia validada

```text
NVR/câmera
  → MediaMTX (cam_36)
  → Camera Gateway
  → Worker da câmera
  → Pool central de inferência
  → métricas, detecções e pipeline de eventos
```

O worker chegou a `running_motion_test`, recebeu frames pelo Gateway, atribuiu uma câmera ao pool central e produziu inferências. Em uma amostra estável na câmera 36, o pool registrou 23 trabalhos enviados e 23 concluídos, fila 0 e falhas 0.

Não foi produzido alarme artificial. A cena pôde resultar em detecções vazias válidas e o pipeline de eventos permaneceu operacional.

## 4. Resultados por teste

| Teste | Resultado | Evidência principal |
|---|---|---|
| 1 — Fluxo completo com worker e IA | **APROVADO** | Gateway `running`; worker operacional; frames e inferências recentes; pool central sem fila/falhas; circuit breaker fechado |
| 2 — Preview, snapshot e MJPEG | **APROVADO APÓS CORREÇÃO** | Snapshot e MJPEG ativos; com worker parado, MJPEG manteve leitor em `cam_36` enquanto o Gateway ficou `stopped_manual`; snapshot a frio retornou imagem 640×360 pelo preview do backbone |
| 3 — Múltiplos consumidores | **APROVADO** | Leitores `cam_36`: 1 → 4 → 1; conexões diretas vistas no Camera Gateway: 4 → 4 → 4; os quatro pertenciam às câmeras fora do canário |
| 4 — Ausência de fallback direto | **APROVADO** | Durante a queda, conexões diretas permaneceram em 4, sem incremento; Gateway passou por `queued`/`warming_up`; worker degradou/reconectou; circuit breaker não abriu |
| 5 — Recuperação automática | **APROVADO** | MediaMTX, path, Gateway, frames e inferência voltaram sem intervenção manual |
| 6 — Reconciliação de path | **APROVADO APÓS CORREÇÃO** | `cam_36` removido foi recriado no ciclo seguinte, com source exatamente igual ao cadastro; órfão `cam_100037` removido; path personalizado preservado |
| 7 — Source on demand | **APROVADO** | Sem consumidores, path permaneceu configurado com `sourceOnDemand=true`, ficou `ready=false` e leitores 0; novo `ffprobe` reabriu HEVC 704×576 |
| 8 — Segurança dos logs | **APROVADO APÓS CORREÇÃO** | Em 12 minutos de logs dos quatro containers: 0 senhas, 0 URLs completas, 0 padrões RTSP com userinfo e 0 nomes de usuário RTSP |

Não foi aberta uma interface gráfica de operador real. Os consumidores adicionais foram três processos `ffmpeg` temporários, todos apontados exclusivamente para `rtsp://127.0.0.1:8554/cam_36`.

## 5. Métricas

| Métrica | Antes | Durante | Depois |
|---|---:|---:|---:|
| Codec / resolução / FPS da origem | HEVC / 704×576 / 15 | iguais | iguais |
| FPS do loop analítico observado | aproximadamente 1,95 | manteve inferência; sem regressão grave observada | worker parado no rollback |
| FPS processado observado | 0,51–0,94 | inferência ativa | N/A |
| Idade de frame no Gateway | 319 ms (primeira câmera) | cresceu durante a indisponibilidade | 439 ms em `cam_36`; 271 ms em `cam_37` |
| Leitores MediaMTX | 1 | 4 no teste múltiplo; indisponível durante a queda | 1 após fechar consumidores; 0 no teste on-demand |
| Fila de inferência | 0 | 0 | 0 antes de parar o worker |
| Inferências | 23/23 em amostra final | interrompidas na queda | retomadas automaticamente; falhas 0 |
| Latência do pool | 63,98–154 ms em amostras | sem crescimento contínuo de fila | retomada normal |
| Reconexões Gateway `cam_36` | 0 | 2 durante falha/retorno | 2 acumuladas; circuit breaker fechado |
| CPU Camera Gateway | 59–62% nas amostras iniciais | sem saturação crítica registrada | 78,85% em amostra pontual pós-rollback |
| RAM Camera Gateway | 928 MiB–1,03 GiB | estável | 985,7 MiB |
| CPU runtime | 0,18% sem worker; 5,46% com worker | variável durante warm-up | 10,82% em amostra pontual pós-rollback |
| RAM runtime | 856 MiB sem worker; 1,249 GiB com worker | estável | 832,9 MiB |
| VRAM | 3.423 MiB antes | usada pelo pool central | 3.940/12.288 MiB, GPU 3%, 51 °C |

Os contadores de frames descartados do Gateway são cumulativos desde o início do processo e não foram usados isoladamente para afirmar regressão.

## 6. Sessões de origem

Foi comprovado **um upstream para múltiplos consumidores**.

Método:

1. Worker ativo, um leitor `rtspSession` em `cam_36`;
2. Três consumidores `ffmpeg` temporários abertos no MediaMTX;
3. Leitores subiram de 1 para 4;
4. As conexões TCP do Camera Gateway ao host RTSP original permaneceram em 4, correspondentes às quatro câmeras fora do canário;
5. Ao fechar os consumidores, os leitores voltaram a 1;
6. Gateway e inferência continuaram operacionais, com fila 0 e falhas 0.

Não houve acesso à telemetria interna do NVR. A unicidade foi comprovada pela API do MediaMTX e pela tabela TCP do container Camera Gateway.

## 7. Falha e recuperação

O `webrtc-gateway` foi parado isoladamente.

Durante a falha:

- Gateway da câmera 36 perdeu o path e passou por estados `queued` e `warming_up`;
- Worker passou por `degraded`, `reconnecting` e `warming_up`;
- Circuit breaker permaneceu fechado;
- Número de conexões diretas do Camera Gateway ao host original permaneceu exatamente em 4;
- Não ocorreu conexão direta adicional para a câmera canário;
- Após a correção de snapshot, uma nova repetição com o MediaMTX parado retornou HTTP 503 com erro explícito `media_path_registration_failed`.

Na primeira medição, antes das otimizações de recuperação:

| Marco | Tempo |
|---|---:|
| MediaMTX aceitando conexão | 0,57 s |
| Primeiro frame observado no runtime | 0,57 s |
| Path `cam_36` pronto | 6,55 s |
| Gateway `running` | 29,35 s |
| Primeira nova inferência | 56,48 s |

Foram identificadas três esperas acumuladas:

1. Backoff do Camera Gateway de até 30 s, compartilhado com origens diretas;
2. Circuit breaker por câmera contabilizando a indisponibilidade do backbone local;
3. Cache de registro por até 300 s e probe inicial do runtime configurado para 60 s.

Depois das correções, uma nova queda real do MediaMTX apresentou:

| Marco estável | Tempo após a parada |
|---|---:|
| API MediaMTX novamente ativa | 0,51 s |
| Path `cam_36` recriado pela tentativa do worker | 5,08 s |
| Origem H.265 disponível no path | 9,98 s |
| Gateway `running` com frame recente | 12,33 s |
| Nova inferência estável | 12,51 s |

O worker manteve o mesmo PID (`80`) e o circuit breaker permaneceu fechado. A retomada estável da inferência caiu de 56,48 s para 12,51 s, redução aproximada de 78%.

Foram observados pelo menos dois ciclos posteriores do reconciliador. Somente o container runtime executou o reconciliador; o container web registrou zero ciclos, evitando duplicação.

## 8. Segurança

Foi feita busca em memória com as credenciais reais, sem imprimi-las, nos logs recentes de:

- `server-analiticos`
- `server-analiticos-runtime`
- `camera-gateway`
- `webrtc-gateway`

Resultado:

- Senha RTSP: 0 ocorrências;
- URL RTSP completa: 0 ocorrências;
- URL RTSP com userinfo: 0 ocorrências;
- Nome de usuário RTSP dentro de URL: 0 ocorrências;
- Relatório anterior: 0 padrões com userinfo.

O relatório contém somente ID, nome, host sanitizado, porta e path.

## 9. Testes automatizados

```text
python -m compileall -q app tests
Resultado: APROVADO

python -m pytest -q
Resultado: 750 passed in 57.44s

cd gateway
go test ./...
Resultado: ok

go vet ./...
Resultado: APROVADO

docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
Resultado canário: APROVADO
Resultado rollback: APROVADO
```

Os testes cobriram backbone, reconciliador, Camera Gateway client, Media Service, preview/snapshot/MJPEG, WebRTC Gateway client, expiração de frame, segurança de URL, métricas do worker e estágio de captura.

A colisão de coleta entre os dois arquivos `test_hik_sdk_worker.py` foi eliminada renomeando o teste de script para `tests/scripts/test_hik_sdk_worker_script.py`. A coleta e a suíte globais passaram integralmente.

## 10. Correções realizadas

1. **Publicação de métricas**
   - Causa: `MetricsStore.set_metrics()` chamava `utc_now_naive()` sem importar a função.
   - Correção: import explícito e teste de persistência/timestamp.

2. **Userinfo em logs**
   - Causa: o mascaramento removia a senha, mas preservava o usuário RTSP.
   - Correção: representação exclusiva para logs sem userinfo; aplicada ao worker, captura e RTSP capture.

3. **Preview remoto com worker parado**
   - Causa: web em modo runtime remoto devolvia apenas placeholder e não abria o preview no backbone.
   - Correção: fallback de mídia para a URL já resolvida pelo modo estrito.

4. **Bloqueio indevido do MediaMTX pelo `RTSPCapture`**
   - Causa: a proteção de RTSP direto bloqueava também o host/porta configurados do backbone.
   - Correção: exceção estrita somente para o endpoint MediaMTX configurado; origens diretas continuam bloqueadas.

5. **Snapshot antigo durante falha**
   - Causa: o endpoint interno entregava frame persistido sem validar idade.
   - Correção: frame expirado retorna 404 interno; snapshot sem backbone retorna 503 explícito.

6. **Path ausente não recriado**
   - Causa: cache de registro de 300 s escondia a remoção feita diretamente no MediaMTX.
   - Correção: o reconciliador invalida o cache somente para paths esperados que não aparecem na listagem real.

7. **Recuperação lenta do Gateway**
   - Causa: origens MediaMTX usavam o mesmo backoff de 5–30 s das câmeras remotas e a queda do backbone alimentava o circuit breaker individual.
   - Correção: perfil dedicado de retry de 1–5 s para o host interno permitido e falhas do backbone não contam para o circuit breaker da câmera.

8. **Probe do runtime aguardando 60 segundos**
   - Causa: o Compose sobrescrevia o padrão de 20 s com 60 s.
   - Correção: timeout operacional reduzido para 15 s; o timeout de aquisição H.265 do FFmpeg permanece em 60 s.

9. **Cache sobrevivendo ao restart do MediaMTX**
   - Causa: o processo Python mantinha sucesso de registro em memória embora os paths dinâmicos do MediaMTX tivessem sido perdidos.
   - Correção: antes de reutilizar o cache, o cliente confirma a existência do path pela API; quando ausente, invalida e recria imediatamente.

10. **Colisão dos testes Hik SDK**
    - Causa: dois módulos com o mesmo nome-base em diretórios diferentes causavam `import file mismatch`.
    - Correção: o teste de entrada do script foi renomeado para nome único.

11. **URL RTSP no status público da descoberta**
    - Causa: o snapshot público do job incluía a URL de cada candidata.
    - Correção: o status assíncrono expõe somente metadados operacionais; a URL permanece apenas no estado interno protegido do job.

Cada correção recebeu teste automatizado e os testes relacionados foram reexecutados.

## 11. Riscos remanescentes

- Não houve teste com uma interface gráfica de operador real; foram usados consumidores técnicos.
- Não há métrica nativa do NVR para contar sessões, portanto a prova usou conexões TCP do Gateway e leitores MediaMTX.
- O ensaio de campo foi feito em H.265; não houve canário H.264.
- A câmera 37 gerou um alarme real durante o canário; o teste não alterou seu ciclo operacional.
- As correções passaram no canário, mas ainda não tiveram período prolongado de soak com várias origens/NVRs.

## 12. Conclusão

**Classificação: aprovada para rollout parcial.**

Os critérios críticos de fluxo completo, múltiplos consumidores, ausência de fallback direto, falha, recuperação, reconciliação, source-on-demand e segurança foram comprovados no ambiente real. Não se recomenda rollout global ainda, devido à ausência de soak prolongado, operador gráfico real, telemetria nativa do NVR, câmera H.264 e coleta global íntegra.

## Rollback executado

O ambiente foi restaurado para:

```env
CAMERA_GATEWAY_SOURCE_MODE=mediamtx_prefer
CAMERA_GATEWAY_MEDIAMTX_CAMERA_IDS=
MEDIA_BACKBONE_RECONCILE_ENABLED=true
MEDIA_BACKBONE_RECONCILE_INTERVAL_SECONDS=30
MEDIA_BACKBONE_REMOVE_ORPHAN_PATHS=true
GATEWAY_SOURCE_POLICY=any
GATEWAY_ALLOWED_RTSP_HOSTS=webrtc-gateway
```

Estado final:

- Zero workers ativos;
- Câmeras 36 e 37 sem worker;
- Seis fontes do Camera Gateway consumindo o MediaMTX;
- Zero conexões diretas do Camera Gateway ao host original;
- MediaMTX, Gateway, web, runtime e PostgreSQL ativos;
- `docker compose config --quiet` aprovado.

Comando equivalente para reaplicar o rollback no PowerShell:

```powershell
$env:CAMERA_GATEWAY_SOURCE_MODE='mediamtx_prefer'
$env:CAMERA_GATEWAY_MEDIAMTX_CAMERA_IDS=''
$env:MEDIA_BACKBONE_RECONCILE_ENABLED='true'
$env:MEDIA_BACKBONE_RECONCILE_INTERVAL_SECONDS='30'
$env:MEDIA_BACKBONE_REMOVE_ORPHAN_PATHS='true'
$env:GATEWAY_SOURCE_POLICY='any'
$env:GATEWAY_ALLOWED_RTSP_HOSTS='webrtc-gateway'
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --no-deps --force-recreate analitico-runtime analitico
```
