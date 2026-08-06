# Etapa 2 — implementação do transporte local de frames

Data: 2026-07-27 (America/Sao_Paulo)  
Branch: `checkpoint/ptz-3d-monitor-20260727`  
Commit inicial: `11932ac`  
Commit final: não criado nesta tarefa

## 1. Fluxo antigo encontrado

O fluxo real Gateway → worker era:

1. `gateway/capture.go` executava FFmpeg com saída `image2pipe/mjpeg`;
2. `gateway/gateway.go:recordFrame` mantinha `latestJPEG` e um ring em memória
   do processo Go;
3. `gateway/handlers.go:framesSince` atendia
   `/cameras/{id}/frames`, convertendo cada JPEG para Base64 dentro de JSON;
4. `app/camera/gateway_frames_capture.py` fazia polling HTTP, parseava JSON,
   decodificava Base64 e executava `cv2.imdecode`;
5. o worker mantinha somente o frame mais recente por meio do mailbox de
   `worker_capture_stage.py`/`frame_pipeline.py`;
6. `worker_frame_processor.py` e `preprocess.py` executavam resize, motion gate,
   ROI, tracking e criação da entrada de inferência;
7. `inference_detection.py` codificava novamente o frame/crop como JPEG,
   convertia para Base64 e enviava JSON/HTTP a `/internal/inference/track`;
8. `app/internal/routes.py` desfazia Base64, executava `cv2.imdecode` e
   submetia o trabalho ao `InferencePool`.

Portanto havia JPEG/Base64/JSON/HTTP no trecho Gateway → worker e uma segunda
codificação JPEG/Base64/JSON/HTTP no trecho worker → pool.

Os workers Python são processos filhos criados com `multiprocessing` no modo
`spawn`. O Gateway e o runtime são containers distintos.

## 2. Fluxo novo

A Etapa 2A implementada usa:

```text
MediaMTX → Gateway Go → JPEG binário em mmap → worker Python → pipeline existente
```

O HTTP continua sendo o plano de controle. Para câmeras migradas não há Base64,
JSON ou requisição HTTP por frame no caminho Gateway → worker.

A Etapa 2B foi implementada em seguida, após a 2A ter sido validada em canário,
respeitando a ordem definida para a tarefa. Ela usa:

```text
worker Python → JPEG binário em Unix Domain Socket → pool central
```

Para câmeras selecionadas não há Base64 nem JSON no payload: o JPEG trafega como
bytes após um cabeçalho binário de tamanho fixo. O JSON permanece apenas na
resposta de metadados (tracks e runtime), que é pequena e não contém imagem.

O padrão de ambos os trechos continua `http`; a migração é por configuração.

## 3. Protocolo e consistência

- arquivo por câmera: `camera_{id}_v1.mmap`;
- magic: `SUNFRM01`;
- versão: 1;
- little-endian e offsets explícitos, sem padding implícito;
- cabeçalho global de 128 bytes;
- quatro slots por padrão, cada um com cabeçalho de 128 bytes;
- capacidade padrão de 2 MiB por slot;
- payload JPEG binário;
- `generation_id` novo quando o buffer é recriado;
- seqlock: sequência ímpar durante escrita e par após commit;
- validação de câmera, versão, dimensões, tamanho, índice e CRC32;
- leitura latest-only, com contagem de frames ignorados;
- arquivo `0660`, sem URL, host ou credenciais no nome/metadados.

O layout completo está documentado em `docs/frame_transport_stage2.md`.

### 3.1 Protocolo da Etapa 2B (worker → pool)

- socket Unix em `/run/sunorus/inference.sock`, dentro do volume compartilhado;
- magic de requisição `SUNINF01`, de resposta `SUNIRSP1`;
- versão 1, little-endian, offsets explícitos (`struct` sem padding implícito);
- cabeçalho de requisição de 80 bytes, de resposta de 32 bytes;
- payload JPEG binário logo após o cabeçalho, sem Base64 e sem JSON;
- CRC32 do corpo da resposta validado antes do parse;
- limites de 32 MiB por requisição e 4 MiB por resposta;
- estados de retorno: `OK`, `INVALID`, `BACKPRESSURE`, `ERROR`;
- backpressure propagado como erro específico, não como falha de transporte;
- identidade validada na resposta: `camera_id`, `job_id` e `generation_id`
  precisam coincidir com os enviados, o que impede resultado cruzado entre
  câmeras e aplicação de job de geração antiga.

## 4. Seleção, fallback e rollback

Modos do trecho Gateway → worker:

- `http`: comportamento legado;
- `shared_memory_prefer`: mmap com fallback HTTP explícito, log e métrica;
- `shared_memory_strict`: mmap obrigatório, endpoint HTTP de frames bloqueado;
- `FRAME_TRANSPORT_CAMERA_IDS`: lista canário ou `*`.

Modos do trecho worker → pool:

- `http`: comportamento legado;
- `binary_prefer`: socket binário com fallback HTTP explícito, log limitado a um
  registro a cada 5 s e contador `inference_transport_fallback_total`;
- `binary_strict`: socket obrigatório; a indisponibilidade gera erro explícito e
  o fallback HTTP é bloqueado, com log de `inference_transport_strict`;
- `INFERENCE_TRANSPORT_CAMERA_IDS`: lista canário ou `*`.

Em `binary_strict` a falha ao abrir o servidor de socket no boot propaga e
impede a subida silenciosa em modo degradado; nos demais modos ela é registrada
e o runtime segue por HTTP.

A seleção está centralizada no Gateway e no factory Python. O restante do
pipeline recebe a mesma interface de captura.

Rollback:

```env
FRAME_TRANSPORT_MODE=http
FRAME_TRANSPORT_CAMERA_IDS=
INFERENCE_TRANSPORT_MODE=http
```

Depois é necessário recriar `camera-gateway` e `analitico-runtime`.

## 5. Containers

Foi criado o volume nomeado `frame_data`, montado:

- leitura/escrita no `camera-gateway`;
- somente leitura no `analitico-runtime`;
- não montado no container web.

Dois `tmpfs` independentes não foram usados, pois não compartilhariam o mesmo
filesystem entre containers. `ipc: host` também não foi usado.

Linux usa `mmap(MAP_SHARED)`. No desenvolvimento Windows, o modo HTTP continua
disponível; o backend Windows do Gateway existe para build/teste, mas o rollout
operacional do mmap é Linux.

## 6. Observabilidade

Foram adicionadas métricas de readiness, geração, slots, capacidade, escritas,
sobrescritas, erros, payload excessivo, frame mais recente, idade, leituras,
frames ignorados/corrompidos, mudanças de geração, custo de cópia/validação,
espera por novo frame, fallback e requisições ao endpoint HTTP legado.

O tempo `shared_buffer_read_latency_ms` mede somente cópia e validação. A espera
pela próxima publicação é separada em `shared_buffer_wait_ms`.

Para o trecho worker → pool foram expostas em `worker_metrics_publisher`:
`inference_transport_mode`, `inference_jobs_submitted_total`,
`inference_payload_bytes_total`, `inference_transport_latency_ms`,
`inference_transport_fallback_total` e `inference_transport_errors_total`.
O modo aparece como `http`, `binary_local` ou `http_fallback`, o que torna o
fallback visível no estado e não apenas no log.

Não há log por frame. Criação, conexão, fallback, recuperação e erros
incompatíveis são registrados de forma limitada.

## 7. Arquivos da Etapa 2 alterados

- `.env.example`
- `.env.docker.example`
- `docker-compose.yml`
- `docs/frame_transport_stage2.md`
- `gateway/frame_transport.go`
- `gateway/frame_transport_test.go`
- `gateway/mapped_region_unix.go`
- `gateway/mapped_region_windows.go`
- `gateway/monotonic_linux.go`
- `gateway/monotonic_other.go`
- `gateway/monotonic_windows.go`
- `gateway/gateway.go`
- `gateway/handlers.go`
- `gateway/stats.go`
- `gateway/types.go`
- `app/core/config.py`
- `app/camera/frame_transport.py`
- `app/camera/shared_frame_reader.py`
- `app/runtime/capture.py`
- `app/runtime/worker_metrics_publisher.py`
- `app/runtime/worker_metrics_reporter.py`
- `app/services/camera_gateway_client.py`
- `app/services/camera_operation_service.py`
- `tests/camera/test_shared_frame_reader.py`

Etapa 2B:

- `app/runtime/inference_transport.py`
- `app/runtime/inference_detection.py`
- `app/bootstrap.py`
- `app/runtime/worker_metrics_publisher.py`
- `docker-compose.yml`
- `tests/runtime/test_inference_transport.py`

Alterações já existentes em arquivos de incidentes, frontend, IA3 e scripts de
análise não foram modificadas nem incluídas neste escopo.

## 8. Testes

- `python -m compileall -q app tests`: aprovado;
- testes Python direcionados: 22 aprovados na revalidação final (2A + 2B);
- `go test ./...`: aprovado;
- `go vet ./...`: aprovado;
- Compose base + GPU `config --quiet`: aprovado;
- suíte Python completa: 760 aprovados, 1 falhou.

Os testes da 2B em `tests/runtime/test_inference_transport.py` cobrem seleção por
modo e canário, endianness explícita do cabeçalho, preservação de `job_id`,
contadores de payload, rejeição de resposta com identidade divergente e
comportamento do fallback.

A colisão entre os dois antigos módulos `test_hik_sdk_worker.py` não ocorreu; o
teste de script está nomeado `test_hik_sdk_worker_script.py`.

A única falha global foi
`test_close_requires_assignee_and_classification`, também reproduzida
isoladamente. Ela decorre de alterações preexistentes e fora do escopo em
`incident_service.py`, que atribuem automaticamente o ator antes de fechar o
incidente.

`go test -race ./...` não foi executado porque o toolchain local está com CGO
desabilitado; o comando retornou `-race requires cgo`.

## 9. Limitações e continuidade

- a Etapa 2B remove Base64/JSON/HTTP do payload, mas mantém a codificação JPEG:
  BGR24/NV12 ficam para uma etapa posterior;
- a 2B usa socket binário, não mmap; o payload ainda é copiado uma vez pelo
  socket, o que é aceitável nesta versão mas não é zero-copy;
- o canário da 2B durou uma janela curta (13 jobs) e serve como prova de
  funcionamento, não como medição estatística;
- não foi feito soak de duas horas nem de 24–72 horas;
- o teste real de quatro câmeras não foi executado porque as demais candidatas
  tinham workers e/ou sessões de operador ativas;
- frame maior que slot e isolamento entre câmeras foram validados por teste
  automatizado, não por injeção disruptiva na câmera real;
- a comparação A/B curta compartilhou GPU/CPU com outros workers e deve ser
  tratada como indicativa;
- o transporte ainda usa JPEG; BGR/NV12 ficam reservados para outra etapa.

Recomendação: manter ambos os modos em `http` como padrão, ampliar o canário da
2A após soak e repetir o canário da 2B em janela mais longa antes de considerar
rollout parcial.

## 10. Classificação

**Etapa 2A: aprovada para canário.**

**Etapa 2B: implementada e aprovada para canário.** O transporte legado
permanece disponível e é o padrão.

Não há evidência suficiente para rollout parcial ou global em nenhum dos dois
trechos: falta soak e falta A/B com carga controlada.
