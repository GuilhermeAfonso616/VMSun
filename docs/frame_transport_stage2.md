# Frame Transport — Etapa 2

## Objetivo e escopo

A Etapa 2 separa o plano de controle HTTP do plano de dados de frames. A
primeira entrega implementa a Etapa 2A (Camera Gateway → worker) com JPEG
binário em ring buffer mapeado em arquivo. A Etapa 2B (worker → pool) permanece
em HTTP até a validação canário da 2A.

O formato inicial continua sendo JPEG para limitar a mudança ao transporte:
ele elimina Base64, JSON e uma requisição HTTP por frame, mas ainda mantém uma
decodificação JPEG no worker. O cabeçalho reserva identificadores para permitir
payloads BGR24, RGB24 ou NV12 em uma versão futura.

## Topologia

```text
Plano de controle
runtime -- HTTP --> Camera Gateway
  registrar/parar câmera, estado, health e métricas

Plano de dados da Etapa 2A
MediaMTX -- RTSP --> Camera Gateway -- JPEG/mmap --> worker Python

Plano de dados ainda legado da Etapa 2B
worker -- JPEG/Base64/JSON/HTTP local --> pool central
```

O HTTP `/cameras/{id}/frames` continua disponível no modo `http` e como
fallback explícito em `shared_memory_prefer`. Para câmeras selecionadas em
`shared_memory_strict`, esse endpoint retorna
`shared_memory_transport_required` e nunca entrega frames.

## Recurso e compartilhamento entre containers

O arquivo de cada câmera é:

```text
/run/sunorus/frames/camera_{camera_id}_v1.mmap
```

O nome contém somente ID numérico e versão. O Compose usa o volume nomeado
`frame_data`, montado com escrita no `camera-gateway` e somente leitura no
`analitico-runtime`. O container web não recebe o volume.

Um `tmpfs` declarado separadamente em dois containers não seria compartilhado.
Por isso a primeira versão usa volume nomeado: no Linux, o arquivo é aberto com
`MAP_SHARED` e permanece servido pelo page cache. Um deployment Linux pode
montar armazenamento volátil no volume sem mudar o protocolo. Não é utilizado
`ipc: host`.

No desenvolvimento Windows o modo suportado para operação continua sendo
`http`; há uma implementação file-backed apenas para build e testes de
protocolo.

## Endianness e alinhamento

- Endianness: little-endian.
- Cabeçalho global: 128 bytes.
- Cabeçalho por slot: 128 bytes.
- Todos os offsets são explícitos.
- Nenhum leitor depende de padding de `struct` Go ou Python.
- Inteiros de sequência ficam em offsets múltiplos de 8.

## Cabeçalho global v1

| Offset | Bytes | Tipo | Campo |
|---:|---:|---|---|
| 0 | 8 | bytes | magic `SUNFRM01` |
| 8 | 2 | uint16 | protocol_version |
| 10 | 2 | uint16 | header_size = 128 |
| 12 | 4 | uint32 | camera_id |
| 16 | 4 | uint32 | slot_count |
| 20 | 4 | uint32 | slot_capacity |
| 24 | 8 | uint64 | generation_id |
| 32 | 8 | uint64 | latest_frame_id |
| 40 | 4 | uint32 | latest_slot |
| 44 | 4 | uint32 | active (0/1) |
| 48 | 8 | uint64 | created_wall_ns |
| 56 | 8 | uint64 | last_publish_monotonic_ns |
| 64 | 8 | uint64 | frames_written_total |
| 72 | 8 | uint64 | frames_overwritten_total |
| 80 | 8 | uint64 | write_errors_total |
| 88 | 8 | uint64 | payload_too_large_total |
| 96 | 32 | bytes | reservado, zerado |

## Cabeçalho de slot v1

| Offset do slot | Bytes | Tipo | Campo |
|---:|---:|---|---|
| 0 | 8 | uint64 | sequence_begin |
| 8 | 8 | uint64 | sequence_end |
| 16 | 8 | uint64 | generation_id |
| 24 | 8 | uint64 | frame_id |
| 32 | 8 | uint64 | captured_monotonic_ns |
| 40 | 8 | uint64 | published_monotonic_ns |
| 48 | 8 | uint64 | captured_wall_ns |
| 56 | 4 | uint32 | width |
| 60 | 4 | uint32 | height |
| 64 | 2 | uint16 | channels |
| 66 | 2 | uint16 | pixel_format (`1` = BGR24 após decode) |
| 68 | 2 | uint16 | payload_format (`1` = JPEG) |
| 70 | 2 | uint16 | flags (`bit 0` = ready) |
| 72 | 4 | uint32 | payload_size |
| 76 | 4 | uint32 | payload_capacity |
| 80 | 4 | uint32 | CRC32 IEEE do payload |
| 84 | 4 | uint32 | camera_id |
| 88 | 4 | uint32 | slot_index |
| 92 | 36 | bytes | reservado, zerado |
| 128 | capacity | bytes | payload JPEG |

## Protocolo da Etapa 2B — worker → pool

O segundo trecho não usa mmap: usa um Unix Domain Socket em
`/run/sunorus/inference.sock`, dentro do mesmo volume compartilhado. O payload
JPEG segue como bytes logo após um cabeçalho binário de tamanho fixo, sem
Base64 e sem JSON.

Little-endian e offsets explícitos, pelas mesmas razões da 2A: `struct.Struct`
com prefixo `<` não insere padding implícito.

### Cabeçalho de requisição v1 — 80 bytes

| Offset | Bytes | Tipo | Campo |
|---:|---:|---|---|
| 0 | 8 | bytes | magic `SUNINF01` |
| 8 | 2 | uint16 | protocol_version |
| 10 | 2 | uint16 | header_size = 80 |
| 12 | 4 | uint32 | flags, reservado |
| 16 | 4 | uint32 | camera_id |
| 20 | 8 | uint64 | generation_id |
| 28 | 8 | uint64 | job_id |
| 36 | 8 | uint64 | submitted_monotonic_ns |
| 44 | 4 | int32 | offset_x |
| 48 | 4 | int32 | offset_y |
| 52 | 4 | float32 | scale_x |
| 56 | 4 | float32 | scale_y |
| 60 | 4 | uint32 | width |
| 64 | 4 | uint32 | height |
| 68 | 2 | uint16 | payload_format (`1` = JPEG) |
| 70 | 2 | uint16 | reservado |
| 72 | 4 | uint32 | payload_size |
| 76 | 4 | uint32 | CRC32 IEEE do payload |
| 80 | payload_size | bytes | payload JPEG |

### Cabeçalho de resposta v1 — 32 bytes

| Offset | Bytes | Tipo | Campo |
|---:|---:|---|---|
| 0 | 8 | bytes | magic `SUNIRSP1` |
| 8 | 2 | uint16 | protocol_version |
| 10 | 2 | uint16 | header_size = 32 |
| 12 | 4 | uint32 | status (`0` OK, `1` INVALID, `2` BACKPRESSURE, `3` ERROR) |
| 16 | 8 | uint64 | job_id |
| 24 | 4 | uint32 | body_size |
| 28 | 4 | uint32 | CRC32 IEEE do corpo |
| 32 | body_size | bytes | corpo JSON com tracks e runtime |

O corpo da resposta continua em JSON por ser pequeno e não conter imagem. Ele
carrega `camera_id`, `job_id` e `generation_id`, e o cliente rejeita a resposta
se qualquer um divergir do que enviou — é o que impede resultado cruzado entre
câmeras e aplicação de job de geração antiga.

Limites: 32 MiB por requisição e 4 MiB por resposta. `BACKPRESSURE` é propagado
como erro específico e não conta como falha de transporte, para não disparar
fallback quando o pool apenas está saturado.

## Ciclo de escrita

1. Selecionar `frame_id mod slot_count`;
2. Publicar sequência ímpar em `sequence_begin` e `sequence_end`;
3. Escrever metadados e payload;
4. Escrever CRC32;
5. Publicar sequência par em `sequence_end` e depois `sequence_begin`;
6. Atualizar `latest_frame_id` e `latest_slot`;
7. Nunca aguardar consumidor.

Os stores de sequência e índice são atômicos e alinhados no processo Go.
Payload vazio, JPEG inválido ou maior que a capacidade é rejeitado, contado e
não se torna o último frame.

## Ciclo de leitura

1. Validar arquivo regular, sem symlink, dentro da raiz configurada;
2. Validar magic, versão, câmera, limites e tamanho total;
3. Ler geração, último frame e slot;
4. Rejeitar slot com sequência zero, ímpar ou divergente;
5. Validar geração, câmera, índice, dimensões, formato e tamanho;
6. Copiar somente os bytes válidos;
7. Ler novamente a sequência;
8. Validar CRC32;
9. Decodificar JPEG com OpenCV;
10. Se atrasado, contabilizar IDs pulados e consumir somente o mais recente.

O reader verifica inode e tamanho. Quando o Gateway recria o arquivo por
restart ou mudança de origem, ele reabre o mmap e conta uma mudança de geração.

## Geração e lifecycle

Uma geração aleatória nova é criada ao:

- iniciar o Gateway e registrar a câmera;
- alterar/reiniciar a origem;
- recriar o buffer.

Parar a origem marca `active=0`. O arquivo permanece por padrão para evitar
corrida de reopen. A exclusão de uma câmera usa `remove_buffer=true` no plano
de controle e remove somente o arquivo daquela câmera.

## Sinalização e backpressure

A v1 usa polling leve configurável (`5 ms`) somente enquanto aguarda um frame.
Como o Gateway publica atualmente cerca de 2 FPS, o reader normalmente dorme
no mmap até o próximo frame e não cria fila. O mailbox do worker também
continua sendo latest-only.

Unix Domain Socket/eventfd fica previsto para uma versão posterior se a
medição mostrar CPU relevante no polling. Não há socket exposto na rede.

## Modos e canário

```env
FRAME_TRANSPORT_MODE=http
FRAME_TRANSPORT_CAMERA_IDS=
```

- `http`: fluxo legado integral; a lista é ignorada.
- `shared_memory_prefer`: tenta mmap; fallback HTTP explícito, com log, estado e
  contador.
- `shared_memory_strict`: mmap obrigatório; buffer ausente/inválido degrada o
  transporte e o endpoint HTTP por frame é bloqueado.

`FRAME_TRANSPORT_CAMERA_IDS=36,37` seleciona IDs; `*` seleciona todas. A
seleção é centralizada em `frame_transport_selected()` no Python e
`frameTransportConfig.selected()` no Gateway.

O trecho worker → pool tem modos próprios e independentes:

```env
INFERENCE_TRANSPORT_MODE=http
INFERENCE_TRANSPORT_CAMERA_IDS=
```

- `http`: fluxo legado integral; a lista é ignorada.
- `binary_prefer`: tenta o socket binário; fallback HTTP explícito, com log
  limitado a um registro a cada 5 s, estado `http_fallback` e contador.
- `binary_strict`: socket obrigatório; a indisponibilidade gera erro explícito,
  o fallback HTTP é bloqueado e a falha ao abrir o servidor no boot propaga em
  vez de subir em modo degradado silencioso.

A seleção é centralizada em `inference_transport_selected()`. Os dois trechos
são desacoplados: é possível rodar a 2A em `shared_memory_prefer` mantendo a 2B
em `http`, que é a configuração recomendada durante o soak da 2A.

## Métricas

Gateway:

- `frame_transport_mode`
- `shared_buffer_ready`
- `shared_buffer_generation`
- `shared_buffer_capacity_bytes`
- `shared_buffer_slots`
- `shared_buffer_frames_written_total`
- `shared_buffer_frames_overwritten_total`
- `shared_buffer_write_errors_total`
- `shared_buffer_payload_too_large_total`
- `shared_buffer_last_frame_id`
- `shared_buffer_last_write_age_ms`
- `frame_transport_http_requests_total`

Worker:

- `shared_buffer_frames_read_total`
- `shared_buffer_frames_skipped_total`
- `shared_buffer_corrupt_frames_total`
- `shared_buffer_generation_changes_total`
- `shared_buffer_read_latency_ms`
- `shared_buffer_wait_ms`
- `shared_buffer_frame_age_ms`
- `frame_transport_http_fallback_total`
- `frame_transport_errors_total`

## Compatibilidade e rollback

Rollback não exige banco nem remoção manual:

```env
FRAME_TRANSPORT_MODE=http
FRAME_TRANSPORT_CAMERA_IDS=
INFERENCE_TRANSPORT_MODE=http
```

Depois, recriar somente `camera-gateway` e `analitico-runtime`. Arquivos antigos
ficam inativos e podem ser substituídos com segurança numa ativação futura.

## Limitações da v1

- JPEG ainda é codificado no Gateway e decodificado no worker;
- polling leve em vez de sinalização por socket;
- volume nomeado em vez de tmpfs compartilhado gerenciado pelo Compose;
- a Etapa 2B remove Base64/JSON/HTTP do payload, mas mantém a codificação JPEG e
  usa socket em vez de mmap: o payload é copiado uma vez, não é zero-copy;
- limpeza no startup é conservadora: nenhum arquivo de outra instância é
  removido sem prova de ownership;
- Windows operacional usa fallback HTTP.
