# Camera Gateway: perfis de baixa latência

## Objetivo e segurança

O Camera Gateway oferece três perfis FFmpeg e uma mailbox de último frame para
impedir backlog entre o `stdout` MJPEG e a publicação. O padrão permanece
`compatibility`; nenhum canário é ativado por código.

```env
GATEWAY_FFMPEG_LATENCY_PROFILE=compatibility
GATEWAY_FFMPEG_BALANCED_CAMERA_IDS=
GATEWAY_FFMPEG_LOW_LATENCY_CAMERA_IDS=
```

As listas aceitam IDs separados por vírgula e `*`. Não use `*` antes de soak.
`low_latency` tem precedência sobre `balanced`.

## Comando FFmpeg

Parte comum:

```text
ffmpeg -hide_banner -loglevel warning -nostats -stats_period 0.25
  -progress pipe:2 -rtsp_transport tcp <flags-do-perfil>
  -i <URL-SANITIZADA> -map 0:v:0 -an
  -vf fps=<FPS>,scale=<LARGURA>:<ALTURA>
  -q:v <QUALIDADE> -f image2pipe -vcodec mjpeg pipe:1
```

Perfis:

| Perfil | Flags de entrada |
| --- | --- |
| `compatibility` | sem flags adicionais; preserva o comportamento anterior |
| `balanced` | `-fflags +discardcorrupt -analyzeduration 1000000 -probesize 1000000` |
| `low_latency` | `-fflags +nobuffer+discardcorrupt -flags low_delay -max_delay 0 -analyzeduration 500000 -probesize 500000` |

RTSP continua em TCP. `-avioflags direct`, `-use_wallclock_as_timestamps`,
`-framedrop` e UDP não foram adotados: não houve evidência suficiente de ganho
e eles ampliam risco de incompatibilidade ou mudam a semântica temporal.

O log contém somente o comando sanitizado. Credenciais da URL de entrada são
mascaradas.

## Latest-frame-wins

`LatestFrameMailbox` tem capacidade lógica e física de um frame. O produtor:

1. nunca espera o worker;
2. substitui o frame pendente;
3. incrementa os contadores de substituição/descarte;
4. preserva o frame que já foi entregue ao consumidor;
5. fecha de forma idempotente.

O ring HTTP continua existindo para compatibilidade, mas o worker escolhe o
item mais novo. O endpoint de frames publica `stream_generation_id`; uma nova
geração invalida identidade anterior no cliente Python.

## PTS, drift e idade

O Gateway lê `out_time_us` de `-progress pipe:2`. A base é:

```text
drift = wall_elapsed - pts_elapsed
```

Isso detecta progresso abaixo do tempo real, mas não é timestamp original da
câmera. `gateway_frame_source_estimated_age_ms` é uma estimativa relativa e
nunca deve ser apresentada como latência absoluta. Regressões superiores a
250 ms e saltos superiores a 30 s reiniciam a base e incrementam
`gateway_pts_discontinuities_total`.

O limite stale é opt-in:

```env
GATEWAY_MAX_ANALYTIC_FRAME_AGE_MS=500
GATEWAY_MAX_ANALYTIC_FRAME_AGE_CAMERA_IDS=37
```

Ele só atua quando existe estimativa PTS. O padrão `0` desliga o descarte.

## Watchdog

```env
GATEWAY_FRAME_LAG_WATCHDOG_ENABLED=false
GATEWAY_FRAME_LAG_WATCHDOG_CAMERA_IDS=
GATEWAY_FRAME_LAG_RESTART_THRESHOLD_MS=2000
GATEWAY_FRAME_LAG_RESTART_HOLD_SECONDS=5
GATEWAY_FRAME_LAG_RESTART_COOLDOWN_SECONDS=30
GATEWAY_FRAME_LAG_RESTART_MAX_PER_HOUR=3
```

Um pico isolado não reinicia. Lag contínuo precisa atravessar o hold; cooldown e
limite por hora evitam loop. O restart afeta apenas o FFmpeg da câmera, cria nova
geração e não troca de perfil silenciosamente.

## Fallback

```env
GATEWAY_FFMPEG_PROFILE_FALLBACK_ENABLED=true
GATEWAY_FFMPEG_PROFILE_STRICT=false
```
Ordem:

```text
low_latency -> balanced -> compatibility
balanced -> compatibility
```

Fallback gera log, contador e estado operacional. Em `strict=true`, somente o
perfil selecionado é tentado.

## Estado operacional

`GET /cameras/{id}/status` inclui perfil configurado/ativo, PID, geração,
TTFF, FPS, idade local, PTS/drift, tempos de pipe/JPEG/publicação, substituições,
stale, fallback, watchdog e motivo do último restart. URLs completas não são
expostas.

## Canário e rollback

Canário:

```env
GATEWAY_FFMPEG_LATENCY_PROFILE=compatibility
GATEWAY_FFMPEG_LOW_LATENCY_CAMERA_IDS=37
GATEWAY_FRAME_LAG_WATCHDOG_ENABLED=false
GATEWAY_MAX_ANALYTIC_FRAME_AGE_MS=0
```

Rollback:

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
