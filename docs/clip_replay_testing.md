# Clip Replay Testing

Fluxo para transformar clips exportados do OneDrive em cameras RTSP fake e
rodar testes repetiveis contra o pipeline.

## 1. Gerar manifesto

Somente casos revisados:

```powershell
py -3 scripts\build_clip_replay_manifest.py `
  --source-dir "D:\IA_Rebuild\Analitico VMS Clips" `
  --output-dir data\test_replay `
  --rtsp-base-url rtsp://localhost:8554
```

Todos os clips, incluindo nao revisados:

```powershell
py -3 scripts\build_clip_replay_manifest.py `
  --source-dir "D:\IA_Rebuild\Analitico VMS Clips" `
  --output-dir data\test_replay_all `
  --rtsp-base-url rtsp://localhost:8554 `
  --include-unreviewed
```

Saidas:

- `clip_replay_manifest.json`
- `clip_replay_manifest.csv`

## 2. Publicar streams RTSP fake

Publicar 4 clips em loop:

```powershell
py -3 scripts\publish_clip_replay_rtsp.py `
  --manifest data\test_replay\clip_replay_manifest.json `
  --max-streams 4
```

Publicar somente falsos positivos conhecidos:

```powershell
py -3 scripts\publish_clip_replay_rtsp.py `
  --manifest data\test_replay\clip_replay_manifest.json `
  --expectation should_not_alarm `
  --max-streams 4
```

O script imprime URLs como:

```text
rtsp://localhost:8554/replay_event_11
```

Cadastre essas URLs como cameras de teste no sistema.

## 2.1. Publicar uma camera fake sequencial com pausa

Para rodar um unico stream RTSP intercalando verdadeiro/falso positivo e
colocando 30 segundos de imagem parada neutra entre clips:

```powershell
py -3 scripts\publish_clip_replay_sequence_rtsp.py `
  --manifest data\test_replay\clip_replay_manifest.json `
  --rtsp-url rtsp://localhost:8554/replay_sequence_01 `
  --pause-seconds 30 `
  --mode mixed
```

Cadastre no sistema:

```text
rtsp://localhost:8554/replay_sequence_01
```

Modos uteis:

```powershell
# So verdadeiros positivos revisados
py -3 scripts\publish_clip_replay_sequence_rtsp.py --mode should_alarm

# So falsos positivos revisados
py -3 scripts\publish_clip_replay_sequence_rtsp.py --mode should_not_alarm

# Todos os revisados, em ordem aleatoria
py -3 scripts\publish_clip_replay_sequence_rtsp.py --mode reviewed --shuffle
```

Por padrao a pausa e uma imagem preta/neutra, nao o snapshot do evento. Isso
evita que uma pessoa congelada no snapshot gere novo alerta durante a pausa.

## 3. Interpretacao

- `expectation=should_not_alarm`: clip revisado como falso positivo. O ideal e nao gerar alarme ativo.
- `expectation=should_alarm`: clip revisado como verdadeiro positivo.
- `expectation=unknown`: clip ainda nao revisado; use para explorar e depois classificar.

Na pasta atual, os clips revisados com MP4 sao falsos positivos. Os verdadeiros
positivos revisados existem nos JSONs, mas nao possuem MP4 pareado nessa pasta.
