# Server Analitico VMS

Servidor de analiticos de camera com backend Python/FastAPI, interface web estilo VMS, gateway Go para captura RTSP, gateway WebRTC (MediaMTX, beta), cliente desktop operador em .NET e deploy via Docker Compose.

## Estrutura

- `main.py`: ponto de entrada executavel, sem regras de inicializacao.
- `app/application.py`: fabrica FastAPI e composicao das rotas.
- `app/bootstrap.py`: lifecycle de banco e servicos de background.
- `app/api`: dependencias HTTP e routers REST separados por dominio.
- `app/web`: presenters e rotas das telas do VMS, streams, diagnosticos e operacao.
- `app/runtime`: pipeline de captura, inferencia, eventos, workers e saida.
- `app/analytics_v2`: regras, tracking, scoring, cena e eventos (pipeline atual).
- `app/analytics`: motor de regras legado (deteccao, perfis de camera, motion gate).
- `app/camera`: clientes RTSP/ONVIF e captura via gateway.
- `app/video_sources`: modelos e providers de fontes de video (NVR, HikCentral etc.).
- `app/workers`: workers de video em processo separado.
- `app/services`: servicos operacionais (descoberta de NVR, saude, metricas, feedback, backup, OneDrive etc.).
- `app/db`: modelos e helpers de persistencia (SQLite/Postgres).
- `app/core`: config, logging, seguranca, timezone e utilitarios transversais.
- `app/internal`: rotas internas/admin.
- `app/static`: assets estaticos do VMS (css, js, branding).
- `gateway`: servico Go que centraliza captura RTSP via ffmpeg, com circuit breaker.
- `webrtc-gateway`: config do MediaMTX para o beta RTSP -> WebRTC (`/monitor/webrtc`).
- `operator-client`: cliente desktop (.NET/Avalonia) para operar mosaicos fora do navegador via LibVLC.
- `scripts`: automacao de setup, deploy, datasets, treino e avaliacao da IA.
- `docs`: notas de arquitetura, supervisor e testes especificos.
- `deploy`: unidades systemd para producao.
- `configs`: configs de fine-tuning das IAs (YAML).
- `tests`: testes unitarios, de integracao HTTP e do pipeline analitico.
- `models`: modelos locais. Esta pasta nao entra no Git (exceto pesos versionados explicitamente no `.gitignore`).
- `datasets`: amostras/feedback local. Evite versionar datasets grandes.

## Maquina de edicao

Esta maquina pode ser usada para editar, revisar e enviar via Git sem precisar ter GPU ou cameras conectadas.

```powershell
.\scripts\setup-edit-env.ps1
.\scripts\check-project.ps1
```

Se quiser instalar so o minimo para trabalhar nos scripts e pular dependencias pesadas:

```powershell
.\scripts\setup-edit-env.ps1 -SkipRuntimeDeps
.\scripts\check-project.ps1 -SkipTests -SkipGo -SkipDotnet
```

## Qualidade, homologacao e release

O gate local completo valida Python, compatibilidade do schema SQLite, gateway
Go e build do cliente operador:

```powershell
.\scripts\check-project.ps1
```

Documentos operacionais:

- `docs/QUALITY_GATES.md`: CI, cobertura e definicao de pronto;
- `docs/HOMOLOGATION.md`: ambiente e roteiro de homologacao;
- `docs/FEATURE_FLAGS.md`: rollout gradual e governanca de flags;
- `docs/RELEASE_RUNBOOK.md`: backup, atualizacao e rollback.
- `docs/SECURITY.md`: primeiro acesso, sessoes, senhas e chave de credenciais.
- `docs/NOTIFICATIONS.md`: webhooks, assinatura, retentativas e fila persistente.
- `docs/INCIDENTS.md`: atribuição, SLA, escalonamento e histórico operacional.

## Docker local

Crie o arquivo de ambiente local:

```powershell
Copy-Item .env.docker.example .env.docker
```

Coloque o modelo principal em:

```text
models/best.pt
```

### Gerenciador geral para Windows e Linux

O fluxo recomendado de instalacao e atualizacao usa um unico gerenciador
Python, sem IP ou perfil de GPU fixo:

```bash
# Linux
python3 scripts/manage_analitico.py install
python3 scripts/manage_analitico.py update
```

```powershell
# Windows
python scripts/manage_analitico.py install
python scripts/manage_analitico.py update
```

Comandos disponiveis: `install`, `update`, `up`, `restart`, `stop`, `status` e
`profile`. O gerenciador preserva `.env.docker`, detecta o IPv4 LAN para o
WebRTC, escolhe CPU/NVIDIA e aguarda os servicos ficarem prontos.

Suba com deteccao automatica (NVIDIA quando disponivel; CPU nos demais hosts):

```bash
./scripts/compose-auto.sh up -d --build
```

Esse comando faz parte da instalacao/subida padrao no Linux. Ele tambem detecta
o IPv4 LAN do servidor e configura o ICE do WebRTC automaticamente; nao grave
um IP fixo no projeto. O atualizador inteligente reaplica essa configuracao e
recria o MediaMTX quando houver mudanca relevante:

```bash
./scripts/smart_docker_update.sh
```

Confirme o perfil escolhido sem iniciar containers:

```bash
./scripts/compose-auto.sh --print-profile
```

Para forcar CPU durante um diagnostico:

```bash
ANALITICO_ACCELERATOR=cpu ./scripts/compose-auto.sh up -d --build
```

Para exigir NVIDIA e falhar claramente se o runtime nao estiver pronto:

```bash
ANALITICO_ACCELERATOR=nvidia ./scripts/compose-auto.sh up -d --build
```

Os composes `docker-compose.yml` (CPU) e `docker-compose.gpu.yml` (NVIDIA) sao escolhidos automaticamente pelo `compose-auto.sh`; `Dockerfile` e `Dockerfile.gpu` seguem a mesma logica.

URLs principais:

- Monitor: http://localhost:8000/monitor
- Monitor WebRTC (beta): http://localhost:8000/monitor/webrtc
- API health: http://localhost:8000/api/health
- Gateway health: http://localhost:8090/healthz

Detalhes do gateway WebRTC (portas, `MTX_WEBRTCADDITIONALHOSTS`, acesso pela LAN) estao em `webrtc-gateway/README.md`. O cliente desktop (`operator-client`) tem requisitos e instalador proprios em `operator-client/README.md`.

## Estabilidade e capacidade

Para coletar telemetria ponta a ponta sem controlar cameras:

```powershell
py -B scripts\run_stability_campaign.py observe --duration-minutes 15
```

Preset somente leitura de sete dias:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_stability_7d.ps1
```

O procedimento completo de baseline, rampa, soak, gates e restauracao segura esta
em [`docs/STABILITY_CAMPAIGN.md`](docs/STABILITY_CAMPAIGN.md).

## Fluxo com Git

Nesta maquina:

```powershell
git status
git add .
git commit -m "descricao objetiva da mudanca"
git push
```

Na maquina de treinamento/avaliacao ou producao:

```bash
git pull --ff-only
./scripts/compose-auto.sh up -d --build
```

Arquivos locais que nao devem ir para o Git:

- `.env`, `.env.docker`
- `data/`, `logs/`, `runtime_state/`
- `event_snapshots/`, `debug_frames/`
- `models/`
- bancos SQLite e arquivos grandes de dataset
