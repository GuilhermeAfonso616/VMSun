# Etapa 4A — canário e A/B do Gateway

Data: 2026-07-29

## Canário

| Campo | Valor |
| --- | --- |
| Câmera | 37 |
| Nome | NVR - Canal 6 sub |
| Codec | H.265/HEVC |
| Resolução de origem | 704x576 |
| FPS de origem | 15 |
| Saída analítica | 960x540, 2 FPS |
| Origem | canal de NVR via MediaMTX |
| Fast path | preservado |
| Cliente | SSE HTTP medido; browser visual indisponível |

Não havia câmera direta nem H.264 real funcional no inventário. O teste H.264
usou um publisher RTSP sintético temporário, sem banco, removido ao final.

## Janelas A/B

Foram coletadas 120 amostras a cada 5 s por perfil, aproximadamente 10 minutos
cada:

- compatibility: 13:51:18Z–14:01:30Z;
- balanced: 14:03:21Z–14:13:24Z;
- low_latency: 14:14:24Z–14:24:26Z.

| Métrica | Compatibility | Balanced | Low-latency |
| --- | ---: | ---: | ---: |
| Time to first frame | 2.056 ms | 1.849 ms | 3.103 ms |
| Decode FPS médio | 1,891 | 2,006 | 2,006 |
| Publish FPS médio | 1,892 | 2,006 | 2,006 |
| CPU Gateway, amostra | 111,02% | não coletada | 156,51% |
| RAM Gateway, amostra | 1,191 GiB | não coletada | 1,38 GiB |
| OSD lag | 6/6 em -1 s | não coletado | 6/6 no mesmo segundo |
| PTS drift médio | 100,311 ms | 256,957 ms | 241,445 ms |
| PTS drift p95 | 336,393 ms | 490,314 ms | 425,996 ms |
| PTS drift máximo | 358,882 ms | 611,045 ms | 598,232 ms |
| Frame local age médio | 235,799 ms | 262,914 ms | 236,031 ms |
| Frame local age p95 | 473,694 ms | 488,383 ms | 477,138 ms |
| Frame local age máximo | 510,339 ms | 612,458 ms | 518,687 ms |
| Frames substituídos, carga normal | 0 | 0 | 0 |
| Frames stale | 0 | 0 | 0 |
| Reconexões/restarts | 0 | 0 | 0 |
| Falhas de abertura | 0 | 0 | 0 |
| Gateway -> cliente média | 254,557 ms | não coletada | 311,617 ms |
| Gateway -> cliente p95 | 413,901 ms | não coletada | 498,219 ms |
| Gateway -> cliente máximo | 545,690 ms | não coletada | 740,341 ms |

CPU/RAM são snapshots do container com sete câmeras e não constituem série
comparável. Não são usados para aprovar o perfil.

## OSD

O primeiro método por correlação de cena foi rejeitado: a câmera estava em
patrulha e o cenário dominava o relógio. O probe foi corrigido para:

1. manter o decoder MediaMTX aquecido em low-latency;
2. alinhar por relógio UTC do mesmo host;
3. rejeitar referência acima de 1 s;
4. exigir leitura visual da montagem;
5. não apresentar alinhamento de captura como idade da origem.

Resultado válido, seis pares por perfil:

```text
compatibility: MediaMTX estava 1 segundo à frente em 6/6
low_latency:   mesmo segundo em 6/6
```

A resolução do OSD é um segundo. Logo, o resultado suporta ganho aproximado de
um segundo, mas não prova média/p95 subsegundo nem timestamp original.
Os snapshots temporários foram removidos após inspeção.

## SSE

Sessenta identidades distintas por perfil:

| Métrica | Compatibility média/p95/máx | Low-latency média/p95/máx |
| --- | ---: | ---: |
| Publicação -> cliente HTTP | 168,334 / 309,252 / 335,536 ms | 218,388 / 407,092 / 477,060 ms |
| Gateway -> cliente HTTP | 254,557 / 413,901 / 545,690 ms | 311,617 / 498,219 / 740,341 ms |
| SSE send -> cliente HTTP | 3,149 / 6,730 / 8,710 ms | 3,838 / 8,939 / 18,181 ms |

O socket SSE não dominou a latência. A diferença entre janelas mostra jitter do
TrackStore/cadência e não comprova regressão causada pelo perfil FFmpeg.
O navegador integrado não estava disponível; renderização real não foi medida.

## Latest-frame e consumidor lento

Em carga normal, o consumidor acompanhou 2 FPS e não houve substituição. No
teste determinístico, 40 frames foram publicados mais rápido que o consumidor:

- mailbox permaneceu com capacidade 1;
- frames pendentes foram substituídos;
- menos de 20 atualizações foram consumidas;
- o último frame entregue foi o 40;
- não houve replay sequencial do backlog.

Assim, backlog pós-parser foi reproduzido e contido. O atraso anterior ao parser
não foi reproduzido.

## Watchdog

Com limiar canário propositalmente baixo (100 ms), o estado entrou em `lagging`
e recuperou em amostras intermediárias. Não houve restart, confirmando que picos
isolados não atravessam o hold. O restart por lag sustentado, cooldown e teto por
hora passaram em teste determinístico, mas a recuperação live após restart
sustentado não foi validada. O watchdog terminou desligado.

## H.264, H.265 e origem

- H.265/NVR 37: abriu nos três perfis, sem corrupção/fallback/restart;
- H.264 sintético 640x360/10 FPS: `low_latency` abriu em 2.993 ms, estabilizou
  em 2,02 FPS, drift observado 212,581 ms, zero fallback/restart;
- câmera direta real: não disponível no inventário; permanece pendente.

## Conclusões

1. O atraso de aproximadamente 10 s não foi reproduzido.
2. Não houve backlog crescente no parser/mailbox.
3. `balanced` e `low_latency` não melhoraram idade local, drift ou
   Gateway->cliente nesta janela.
4. O OSD sugere que `low_latency` removeu aproximadamente um segundo constante
   observado em `compatibility`.
5. A idade original permanece desconhecida e o drift PTS é apenas relativo.
6. O perfil `low_latency` fica **aprovado para teste**, não para canário
   permanente ou rollout.

Validações finais:

```text
go test ./...: passou
go vet ./...: passou
go test -race ./...: bloqueado por ausência de CGO/gcc
pytest direcionado: 34 passaram
pytest completo: 871 passaram, 1 ignorado, 4 falhas preexistentes
Compose CPU+GPU config: passou
```

Classificação:

```text
Investigação do Gateway: backlog não localizado
Perfil low-latency: aprovado para teste
Meta de atraso do frame: não atingida
```

## Configuração de teste

```env
GATEWAY_FFMPEG_LATENCY_PROFILE=compatibility
GATEWAY_FFMPEG_BALANCED_CAMERA_IDS=
GATEWAY_FFMPEG_LOW_LATENCY_CAMERA_IDS=37
GATEWAY_FRAME_LAG_WATCHDOG_ENABLED=false
GATEWAY_MAX_ANALYTIC_FRAME_AGE_MS=0
```

## Estado final/rollback

```env
GATEWAY_FFMPEG_LATENCY_PROFILE=compatibility
GATEWAY_FFMPEG_BALANCED_CAMERA_IDS=
GATEWAY_FFMPEG_LOW_LATENCY_CAMERA_IDS=
GATEWAY_FRAME_LAG_WATCHDOG_ENABLED=false
GATEWAY_FRAME_LAG_WATCHDOG_CAMERA_IDS=
GATEWAY_MAX_ANALYTIC_FRAME_AGE_MS=0
GATEWAY_MAX_ANALYTIC_FRAME_AGE_CAMERA_IDS=
GATEWAY_FFMPEG_PROFILE_FALLBACK_ENABLED=true
GATEWAY_FFMPEG_PROFILE_STRICT=false
```
