# Relatorio - boxes do monitor web e app

Data da avaliacao: 2026-06-11 15:52 BRT

## Resumo executivo

As boxes nao estao aparecendo por dois motivos combinados:

1. No monitor web existe um bug direto: `cameraModeValue(camera)` retorna sempre `"motion"`. Como o polling de boxes filtra cameras em modo `"motion"`, o web nao chama `/monitor/tracks` e nunca atualiza `camera.monitor_boxes`.
2. No backend/runtime, os arquivos de tracks existentes estao antigos. O endpoint `/monitor/tracks` limpa `tracks` quando o payload passa do `max_age_seconds` (1.5s no web, 1.8s no app). Assim, mesmo que o cliente consulte agora, os tracks atuais seriam marcados como `stale` e enviados vazios.

Conclusao: a causa primaria do web e logica de frontend. A causa operacional atual para web/app e falta de tracks frescos, causada por instabilidade de captura/gateway/RTSP da camera em execucao.

## Evidencias principais

### Monitor web nao solicita tracks

Em `templates/monitor_vms_new.html`:

- Linha 3469: `function cameraModeValue(camera)` existe.
- Dentro dela, a funcao retorna sempre `"motion"`.
- Linha 5008: `assignedCameraIdsForBoxes()` filtra com `cameraModeValue(camera) !== "motion"`.
- Linha 6159: `pollTrackBoxes()` depende da lista de `assignedCameraIdsForBoxes()`.

Resultado: como toda camera vira `"motion"`, a lista de cameras para boxes fica vazia e o polling termina antes de chamar `/monitor/tracks`.

Tambem procurei nos logs recentes por `/monitor/tracks` e nao encontrei chamadas. Isso confirma o comportamento: o monitor web atualiza `/monitor/data`, `/monitor/library` e `/monitor/alarms`, mas nao busca as boxes.

### Backend limpa tracks antigos

Em `app/services/track_store.py`:

- Linha 82: `get_tracks(camera_id, max_age_seconds=2.0)`.
- Linha 110: calcula `stale = age_seconds is None or age_seconds > max_age_seconds`.
- Linha 113: se estiver stale, faz `payload["tracks"] = []`.

Em `app/web/web_routes.py`:

- Linha 3404: `_build_monitor_tracks_payload(...)`.
- Linha 3428: busca `track_store.get_tracks(camera_id, max_age_seconds=max_age)`.
- Linha 3446: rota `/monitor/tracks`.
- Linha 3460: rota SSE `/monitor/tracks/stream`.

Ou seja, o endpoint esta desenhado para entregar somente boxes recentes. Payload velho vira resposta vazia.

### Estado real dos tracks

Arquivos encontrados em `data/runtime_state/tracks`:

- `camera_10.json`: atualizado em `2026-06-11T16:03:49` UTC, 2 tracks, frame `1280x720`.
- `camera_11.json`: atualizado em `2026-06-11T16:03:06` UTC, 1 track, frame `1152x1920`.
- `camera_8.json`: atualizado em `2026-05-21T19:18:48` UTC, 0 tracks.

No momento da avaliacao, camera 10/11 ja estavam com mais de 2h de idade. Como o limite usado pelos clientes e 1.5s/1.8s, esses tracks sao descartados pelo endpoint.

### Logs mostram problema operacional da captura

Em `data/logs/error.log`, camera 11 tem repetidos erros:

- `capture_gateway_no_fallback`
- `connect_timeout`
- `camera gateway did not deliver first frame within 60.0s`
- `direct RTSP fallback is disabled`
- `capture_reconnect_exhausted`

Em `data/logs/app.log`, camera 11 aparece com `watchdog_stall_detected`, `stale_for_20s+`, e alternancia para `reconnecting`/`warming_up`.

Isso explica por que os tracks deixam de ser renovados: o runtime perde ou nao recebe frames recentes.

## Situacao por cliente

### Web

O web tem a causa mais clara: nao chega a pedir boxes.

Fluxo esperado:

1. `pollTrackBoxes()`
2. `assignedCameraIdsForBoxes()`
3. `fetch("/monitor/tracks?...")`
4. `updateCameraBoxes(...)`
5. `renderCameraBoxes(...)`

Fluxo atual:

1. `cameraModeValue(camera)` retorna `"motion"` para todas.
2. `assignedCameraIdsForBoxes()` remove todas.
3. `pollTrackBoxes()` sai sem fazer fetch.
4. `.vms-box-layer` fica vazio.

### App desktop

O app desktop esta melhor encaminhado que o web:

- `MainWindowViewModel.TrackLoopAsync()` abre SSE em `/monitor/tracks/stream`.
- Se SSE cai, faz fallback para `GetMonitorTracksAsync()`.
- `CameraTileViewModel.RebuildTrackBoxes()` desenha as boxes no overlay local.
- `TrackOverlayView` renderiza as caixas em Avalonia.

Mas o app tambem depende de tracks frescos. Se `/monitor/tracks` retorna `stale=true` ou `tracks=[]`, ele limpa ou nao reconstrui as boxes. Portanto, com o runtime parado/stale, o app tambem nao tem box para desenhar.

## Observacoes adicionais

- `app/web/web_routes.py` compila corretamente com `.venv\Scripts\python.exe -m py_compile`.
- `app/runtime/worker_base.py` e `app/services/track_store.py` tambem compilam.
- O banco ativo parece ser `data/analytics.db`, nao `analytics.db` na raiz.
- No banco ativo, a camera 11 esta em `warming_up`; varias outras estao `disabled` ou `stopped`.
- Ha eventos recentes para camera 11, mas evento salvo nao garante box ao vivo; box ao vivo exige track fresco no `track_store`.

## Causa raiz provavel

1. Bug de frontend web: `cameraModeValue()` hardcoded como `"motion"` impede polling das boxes.
2. Runtime sem publicacao continua de tracks: capture/gateway da camera 11 esta falhando por timeout, e os tracks existentes ficam stale.
3. Como protecao, o backend descarta tracks antigos; isso evita desenhar boxes fantasmas, mas deixa web/app sem overlay quando o runtime nao esta saudavel.

## Acoes recomendadas

1. Corrigir `cameraModeValue(camera)` para ler o modo real da camera, por exemplo de `camera.worker_mode`, `camera.source_stream_kind`, `camera.is_motion_mode` ou campo equivalente ja serializado.
2. Remover ou revisar o filtro `cameraModeValue(camera) !== "motion"` em `assignedCameraIdsForBoxes()` se a intencao for mostrar boxes de IA tambem no modo motion/teste.
3. Validar `/monitor/tracks?camera_ids=11&max_age_seconds=3` depois da correcao. O esperado e receber `tracks` nao vazio enquanto o runtime estiver processando frames.
4. Resolver a saude da captura/gateway da camera 11: gateway nao entrega primeiro frame em 60s e fallback RTSP direto esta desabilitado.
5. Considerar mostrar no HUD um estado explicito: `boxes stale`, `sem tracks recentes` ou `poll desativado`, para nao parecer falha visual silenciosa.

## Diagnostico final

As boxes nao estao aparecendo no web principalmente porque o JavaScript nunca busca `/monitor/tracks`. No app, a logica de busca existe, mas depende do mesmo endpoint; como o runtime/capture esta sem tracks frescos, o endpoint retorna vazio. A correcao deve atacar primeiro o bug do `cameraModeValue()` no web e, em paralelo, estabilizar a captura da camera 11/gateway para manter `track_store` atualizado.

## Atualizacao - correcao aplicada

Foi aplicada uma correcao em `templates/monitor_vms_new.html`:

- `cameraModeValue(camera)` deixou de retornar sempre `"motion"` e agora usa `worker_mode`/estado da camera.
- `assignedCameraIdsForBoxes()` deixou de bloquear o polling por modo hardcoded e passa a considerar cameras com imagem visivel.
- Foi adicionado `tests/test_monitor_boxes_polling.py` para proteger essa regressao.

Com isso, o monitor web volta a montar a lista de camera IDs e consultar `/monitor/tracks`. Ainda e necessario que o runtime publique tracks frescos; se a camera/gateway continuar em timeout, o endpoint continuara retornando `tracks=[]` por stale, que e o comportamento esperado de seguranca.

## Atualizacao 2 - app desktop corrigido

Depois da primeira correcao, o monitor web passou a exibir as boxes, mas o app desktop continuou sem mostrar. A imagem enviada em 2026-06-11 mostrou esse estado: web com boxes azuis sobre a camera e app sem overlay.

O executavel correto usado pelo operador e:

`D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`

Nos logs do app, o toggle aparecia como ativo:

- `Boxes alterado: enabled=True; serverSupport=True; playback=boxed`

Mesmo assim, o player continuava abrindo RTSP nativo, por exemplo:

- `rtsp://localhost:8554/cam_11`
- `rtsp://localhost:8554/cam_10`

Isso indicou que o app dizia estar em modo boxed, mas `PlaybackUrl` ainda priorizava `MediaRtspUrl`. Como o video e renderizado por LibVLC em superficie nativa, o overlay Avalonia local pode ficar atras do video e nao aparecer. No web isso nao acontece porque as boxes sao desenhadas no DOM sobre o frame.

Correcao aplicada no app:

- `CameraTileViewModel.PlaybackUrl` agora usa `BoxedStreamUrl` quando `BoxesEnabled` esta ativo.
- `UsesBoxedPlayback` agora depende de `BoxesEnabled` e da existencia de `BoxedStreamUrl`, sem exigir `Camera.IsRunning`.
- `ShowTrackOverlay` fica ativo apenas quando o app nao esta usando playback boxed.
- `CameraSlotViewModel.CanRequestTrackBoxes` agora so pede tracks para overlay local quando `ShowTrackOverlay` esta ativo.

Resultado esperado: com `Boxes: ON`, o app usa o stream boxed do servidor (`/cameras/{id}/stream/boxed`) e recebe as boxes ja desenhadas no video, evitando a limitacao do overlay local sobre LibVLC.

O executavel Debug foi recompilado com sucesso apos encerrar o processo que estava travando o arquivo:

- Processo encerrado: `Analitico.Operator.App`, PID `26776`.
- Build: `dotnet build operator-client\src\Analitico.Operator.App\Analitico.Operator.App.csproj -c Debug`
- Resultado: `Compilacao com exito`, 0 erros.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:13:00`.

Validacao executada:

- `.venv\Scripts\python.exe -m unittest tests.test_monitor_boxes_polling tests.analytics_v2.test_track_store_visual_gate`
- Resultado: `Ran 2 tests ... OK`.

Diagnostico atualizado: o web falhava por bloqueio de polling no JavaScript; o app falhava porque, apesar do toggle indicar `playback=boxed`, a URL real ainda priorizava RTSP nativo e o overlay local ficava invisivel sobre o LibVLC. Ambas as pendencias foram corrigidas no codigo e o executavel Debug correto foi recompilado.

## Atualizacao 3 - correcao do app revisada para desenho local

O operador apontou corretamente que o app nao deve pegar a imagem pronta com boxes ja renderizadas pelo servidor. A solucao anterior, baseada em `BoxedStreamUrl`, foi revisada.

Novo comportamento do app:

- `Boxes: ON`: o app usa o stream raw HTTP/MJPEG limpo (`/cameras/{id}/stream/raw`) dentro de um controle Avalonia (`MjpegVideoView`) e desenha as boxes por cima com `TrackOverlayView`.
- `Boxes: OFF`: o app pode continuar usando o player nativo LibVLC/RTSP.
- `BoxedStreamUrl` nao e mais priorizado para exibir boxes no app.

Arquivos alterados nesta revisao:

- `operator-client/src/Analitico.Operator.App/Controls/MjpegVideoView.cs`: novo controle para renderizar MJPEG/raw dentro do Avalonia.
- `operator-client/src/Analitico.Operator.App/MainWindow.axaml`: adiciona `MjpegVideoView` abaixo do `TrackOverlayView` e mantem `NativeDragVideoView` apenas para playback nativo.
- `operator-client/src/Analitico.Operator.App/ViewModels/CameraTileViewModel.cs`: `UsesBoxedPlayback` passa a ser `false`; `ShowTrackOverlay` fica ativo com `BoxesEnabled`; `ClientOverlayStreamUrl` aponta para raw/processed HTTP sem boxes.
- `operator-client/src/Analitico.Operator.App/ViewModels/CameraSlotViewModel.cs`: quando usa overlay local, nao inicia LibVLC; marca o slot como ativo e deixa o controle Avalonia consumir o stream raw.
- `tests/test_monitor_boxes_polling.py`: adicionada protecao para impedir regressao para playback boxed.

Validacao:

- Build Debug: `dotnet build operator-client\src\Analitico.Operator.App\Analitico.Operator.App.csproj -c Debug`
- Resultado: `Compilacao com exito`, 0 erros.
- Testes: `.venv\Scripts\python.exe -m unittest tests.test_monitor_boxes_polling tests.analytics_v2.test_track_store_visual_gate`
- Resultado: `Ran 3 tests ... OK`.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:19:51`.
- App reaberto pelo executavel correto; log mostra build `11/06 16:19`.

Diagnostico final revisado: o web desenha as boxes sobre o video no navegador; o app agora tambem deve desenhar as boxes localmente sobre o stream limpo, sem depender da imagem boxed pronta do servidor.

## Atualizacao 4 - fonte correta para overlay local

A revisao anterior colocou o app em overlay local, mas usou `/cameras/{id}/stream/raw` como fonte preferencial. No teste real com a camera 10, esse endpoint retornou o placeholder `CONECTANDO PREVIEW`, igual ao print enviado pelo operador.

Foi validado em 2026-06-11 16:25 BRT:

- `/cameras/10/stream/raw`: retorna placeholder `CONECTANDO PREVIEW`.
- `/monitor/gateway/cameras/10/stream/live`: retorna imagem limpa real da camera/padrao colorido.

Correcao aplicada:

- O app agora le `monitor_stream_url` no modelo `OperatorCamera`.
- O bootstrap do servidor passa a publicar `monitor_stream_url`.
- Mesmo que o servidor ainda nao publique esse campo, o app deriva automaticamente `monitor/gateway/cameras/{id}/stream/live`.
- `ClientOverlayStreamUrl` agora prioriza `MonitorStreamUrl`, depois `ProcessedStreamUrl`, depois `RawStreamUrl`.

Validacao:

- Build Debug do app: sucesso, 0 erros.
- `app/api/routes.py`: `py_compile` OK.
- Testes: `Ran 3 tests ... OK`.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:25:16`.
- App reaberto; log mostra build `11/06 16:25`.

Diagnostico final da fonte: para desenhar boxes localmente, o app nao deve usar `/stream/boxed` e tambem nao deve depender de `/stream/raw` quando ele esta sem frame. A fonte correta para o monitor operacional e o stream limpo do gateway (`/monitor/gateway/cameras/{id}/stream/live`) com `TrackOverlayView` desenhando as boxes por cima.

## Atualizacao 5 - rollback do MJPEG e overlay nativo

O operador validou que o caminho com MJPEG/Avalonia ficou lento e visualmente diferente do web. Essa abordagem foi removida do caminho operacional.

Revisao aplicada:

- Removido o uso de `MjpegVideoView` no mosaico.
- O app voltou a usar `NativeDragVideoView`/LibVLC com RTSP como motor de video principal.
- `Boxes: ON` nao troca mais a URL do video para gateway/raw/boxed.
- Foi criado `NativeTrackOverlayView`, um overlay nativo leve para desenhar boxes por cima da superficie nativa do LibVLC.
- O log do toggle agora deve indicar `playback=rtsp+native-overlay`.

Arquivos principais:

- `operator-client/src/Analitico.Operator.App/Controls/NativeTrackOverlayView.cs`
- `operator-client/src/Analitico.Operator.App/MainWindow.axaml`
- `operator-client/src/Analitico.Operator.App/ViewModels/CameraTileViewModel.cs`
- `operator-client/src/Analitico.Operator.App/ViewModels/CameraSlotViewModel.cs`
- `operator-client/src/Analitico.Operator.App/ViewModels/MainWindowViewModel.cs`

Validacao:

- Build Debug do app: sucesso, 0 erros.
- `app/api/routes.py`: `py_compile` OK.
- Testes: `Ran 3 tests ... OK`.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:36:34`.
- App reaberto; log mostra build `11/06 16:36`.

Diagnostico final desta revisao: o caminho MJPEG foi descartado por desempenho. O app agora fica novamente no player rapido RTSP/LibVLC e tenta desenhar as boxes em overlay nativo por cima dele.

## Atualizacao 6 - correcao da proporcao

O operador identificou que, apos voltar para RTSP/LibVLC com overlay nativo, a proporcao da camera vertical ficou incorreta. A causa foi desalinhamento de geometria:

- As boxes eram calculadas com base no frame de tracking (`source_frame_width/source_frame_height`), por exemplo `1152x1920`.
- O LibVLC estava preenchendo o slot sem receber explicitamente esse mesmo aspect ratio.
- Resultado: o video parecia esticado/encaixado diferente do overlay.

Correcao aplicada:

- `CameraTileViewModel` agora expoe `VideoAspectRatio` a partir do ultimo payload de tracks.
- `CameraSlotViewModel` aplica `MediaPlayer.AspectRatio` e `MediaPlayer.Scale = 0` quando o player inicia e quando chegam tracks.
- Assim, o LibVLC e o overlay passam a usar a mesma proporcao do frame de tracking.

Validacao:

- Build Debug do app: sucesso, 0 erros.
- Testes: `Ran 3 tests ... OK`.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:40:20`.
- App reaberto; log mostra build `11/06 16:40`.

## Atualizacao 7 - fundo preto do overlay nativo

Apos a correcao de proporcao, o operador reportou falha total: a camada de boxes aparecia, mas o video ficava preto. A causa foi o `NativeTrackOverlayView`: a janela nativa criada para desenhar as boxes nao estava transparente de fato, entao cobria a superficie do LibVLC.

Correcao aplicada:

- `NativeTrackOverlayView` agora usa `WS_EX_LAYERED`.
- Foi aplicado color key preto via `SetLayeredWindowAttributes`, deixando o fundo da janela transparente.
- A janela tambem retorna `HTTRANSPARENT` em `WM_NCHITTEST`, para nao capturar cliques/drag.

Validacao:

- Build Debug do app: sucesso, 0 erros.
- Testes: `Ran 3 tests ... OK`.
- Executavel atualizado: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`
- Timestamp do executavel: `11/06/2026 16:43:54`.
- App reaberto; log mostra build `11/06 16:43`.

## Atualizacao 8 - descarte definitivo do overlay nativo

O teste isolado no `Analitico.Operator.DragTest` confirmou que uma janela nativa transparente sobre o `LibVLCSharp.Avalonia.VideoView` nao e confiavel: a camada cobre o video, deixa apenas as boxes visiveis e sofre com proporcao/z-order.

O caminho nativo foi descartado. O teste com callbacks do LibVLC comprovou que video e boxes podem ser desenhados na mesma superficie Avalonia. Os logs registraram frames recebidos e desenhados continuamente para `cam_10` e `cam_11`.

## Atualizacao 9 - canvas otimizado e coordenadas originais

Implementacao concluida em 2026-06-15:

- `CallbackTrackVideoView` passou a desenhar video e boxes na mesma superficie.
- Removida a alocacao de um novo `byte[]` para cada frame; o buffer gerenciado agora e reutilizado.
- Atualizacao visual limitada a 20 FPS, descartando frames excedentes antes da copia pesada.
- Boxes armazenadas nas coordenadas originais do frame e escaladas dentro do canvas no momento do desenho.
- Resize de slot nao reconstrói mais todas as boxes.
- Snapshot das boxes e atualizado somente quando muda a revisao dos tracks, evitando `ToArray()` a cada frame.
- Mudancas de URL/resolucao usam debounce de 300 ms, evitando reinicios intermediarios do player.
- O ultimo frame permanece visivel durante troca de resolucao ate chegar o primeiro frame novo.
- `track_id <= 0` nao e mais usado como chave global, eliminando a colisao repetida `track:-1`.
- Removidos `NativeTrackOverlayView` e `TrackOverlayView`, implementacoes antigas sem uso.

Validacao:

- Build Debug: sucesso, 0 erros e 0 avisos.
- Executavel: `D:\Analitico\operator-client\src\Analitico.Operator.App\bin\Debug\net8.0\Analitico.Operator.App.exe`.
- Build registrada em log: `15/06/2026 12:25`.
- A validacao anterior com os servicos ativos comprovou callback e desenho continuo nas duas cameras.
- Validacao de carga com 4/9 cameras permanece pendente porque API `8000`, RTSP `8554` e Docker estavam desligados em 2026-06-15.
