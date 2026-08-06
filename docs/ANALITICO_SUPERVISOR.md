# Supervisor externo do Analitico

## Objetivo

O supervisor roda no host Linux, fora dos containers. Ele observa runtime,
gateway e workers por uma API interna somente em `127.0.0.1`. A recuperacao
continua sendo executada pelo runtime, por meio do `WorkerLifecycleManager`,
que serializa start/stop/restart por camera e usa uma geracao como fencing
token. Assim, um processo antigo nao pode remover ou sobrescrever o atual.

## Cinco fases implementadas

1. **Identidade e posse:** cada worker recebe `generation`, PID e horario de
   inicio. Publicacoes tardias de uma geracao antiga deixam de alterar o estado.
2. **Ciclo de vida atomico:** todas as criacoes e remocoes passam por um unico
   gerenciador com lock por camera e verificacao de parada real.
3. **Contrato interno:** o runtime oferece snapshot e reconcile autenticaveis
   em `/internal/supervisor/*`, expostos apenas no loopback do host.
4. **Supervisor independente:** o processo externo possui modos `audit` e
   `recover`, cooldown, orcamento por camera e circuit breaker para falha ampla.
5. **Operacao e observabilidade:** status atomico JSON, eventos JSONL, metricas
   Prometheus e unidades systemd dependentes da montagem do SSD.

## Protecoes adicionais

- Compara `.env.docker` com os limites efetivos do gateway e runtime. Qualquer
  divergencia abre o circuito e aparece como `configuration_drift`.
- Lista as fontes registradas no camera-gateway e identifica fontes orfas. Em
  `audit`, apenas registra `gateway_would_cleanup`; em `recover`, remove no
  maximo oito fontes por ciclo apos tres confirmacoes consecutivas.
- Aplica backoff exponencial e quarentena de 15 minutos a cameras que exigem
  recuperacao repetida. O estado sobrevive a reinicios do supervisor.
- Na primeira abertura de cada circuito, grava um pacote `.tar.gz` com snapshot,
  recursos, GPU, disco e logs recentes em `supervisor/incidents`. Somente os 20
  pacotes mais recentes sao mantidos por padrao.
- Pode executar um canario de inferencia real a cada cinco minutos. Ele fica
  desabilitado por padrao durante a validacao inicial. Quando habilitado, passa
  pela pool serializada e nao consome uma vaga de camera.
- Aceita um webhook HTTP opcional para alertas. Sem URL configurada, nenhuma
  notificacao externa e enviada.
- O envio SMTP tambem esta disponivel, mas fica desativado enquanto `SMTP_HOST`
  ou `SMTP_TO` estiver vazio. Webhook e e-mail possuem cooldown independente.
- Um timer systemd atualiza de hora em hora o SLO de 24 horas, 7 dias e 30 dias
  em `/mnt/analitico_ssd/supervisor/slo/latest.md`.

## Preparacao

Primeiro defina o token do runtime. O instalador copia esse valor para o
arquivo protegido do `systemd`:

```bash
TOKEN="$(openssl rand -hex 32)"
grep -q '^SUPERVISOR_API_TOKEN=' .env.docker \
  && sudo sed -i "s/^SUPERVISOR_API_TOKEN=.*/SUPERVISOR_API_TOKEN=$TOKEN/" .env.docker \
  || echo "SUPERVISOR_API_TOKEN=$TOKEN" | sudo tee -a .env.docker
```

Instale o serviço ainda em modo `audit`:

```bash
sudo bash scripts/install_analitico_supervisor.sh
```

O Compose publica o runtime apenas em `127.0.0.1:8001`. Recrie o runtime para
aplicar porta, token e codigo e depois reinicie o supervisor:

```bash
export DOCKER_HOST=unix:///mnt/analitico_ssd/docker-analitico/docker.sock
docker compose --env-file .env.docker \
  -f docker-compose.yml -f docker-compose.gpu.yml \
  up -d --build --force-recreate --no-deps analitico-runtime
sudo systemctl restart analitico-supervisor
```

## Instalacao segura

O instalador falha se o SSD nao estiver montado, preserva configuracao
existente e inicia em `audit`. Verifique:

```bash
sudo bash scripts/check_analitico_supervisor.sh
sudo journalctl -u analitico-supervisor -f
sudo systemctl start analitico-slo-report.service
```

## Promocao audit para recover

Mantenha `audit` por pelo menos 24 a 48 horas. Revise `camera_would_reconcile`,
`circuit_open` e falsos positivos no arquivo
`/mnt/analitico_ssd/supervisor/events.jsonl`. Depois:

```bash
sudo sed -i \
  's/^ANALITICO_SUPERVISOR_MODE=.*/ANALITICO_SUPERVISOR_MODE=recover/' \
  /etc/default/analitico-supervisor
sudo systemctl restart analitico-supervisor
```

O reinicio automatico do container permanece desligado. Ative-o somente apos
validar a reconciliacao individual; ele exige seis falhas consecutivas e tem
orcamento independente de dois reinicios por hora.

## Arquivos de diagnostico

- `status.json`: ultimo ciclo e totais do supervisor.
- `events.jsonl`: decisoes, bloqueios, circuit breaker e recuperacoes.
- `analitico_supervisor.prom`: metricas no formato textfile do Prometheus.
- `recovery_state.json`: cooldown, backoff e quarentena persistentes.
- `incidents/*.tar.gz`: pacote automatico da primeira abertura de circuito.
- `slo/latest.md`: disponibilidade operacional e IA em 24h, 7d e 30d.
- logs do runtime: eventos de geracao, troca e erro de worker.
