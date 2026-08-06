#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/mnt/analitico_ssd/Analitico_Go_V4}"
SSD_MOUNT="${SSD_MOUNT:-/mnt/analitico_ssd}"
SERVICE_USER="${SERVICE_USER:-srv-sunshield}"
SERVICE_GROUP="${SERVICE_GROUP:-srv-sunshield}"
STATE_DIR="${STATE_DIR:-/mnt/analitico_ssd/supervisor}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Execute com sudo: sudo bash scripts/install_analitico_supervisor.sh" >&2
  exit 1
fi

if ! mountpoint -q "$SSD_MOUNT"; then
  echo "ERRO: $SSD_MOUNT nao esta montado. O supervisor nao sera instalado." >&2
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/scripts/analitico_supervisor.py" ]]; then
  echo "ERRO: projeto nao encontrado em $PROJECT_DIR" >&2
  exit 1
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$STATE_DIR"
install -m 0644 \
  "$PROJECT_DIR/deploy/systemd/analitico-supervisor.service" \
  /etc/systemd/system/analitico-supervisor.service
install -m 0644 \
  "$PROJECT_DIR/deploy/systemd/analitico-slo-report.service" \
  /etc/systemd/system/analitico-slo-report.service
install -m 0644 \
  "$PROJECT_DIR/deploy/systemd/analitico-slo-report.timer" \
  /etc/systemd/system/analitico-slo-report.timer
install -d -m 0755 /etc/systemd/system/docker-analitico.service.d
install -m 0644 \
  "$PROJECT_DIR/deploy/systemd/docker-analitico.service.d/10-require-ssd.conf" \
  /etc/systemd/system/docker-analitico.service.d/10-require-ssd.conf

if [[ ! -f /etc/default/analitico-supervisor ]]; then
  install -m 0600 \
    "$PROJECT_DIR/deploy/systemd/analitico-supervisor.env.example" \
    /etc/default/analitico-supervisor
  echo "Criado /etc/default/analitico-supervisor em modo audit."
else
  echo "Preservado /etc/default/analitico-supervisor existente."
fi

DOCKER_ENV_TOKEN=""
if [[ -f "$PROJECT_DIR/.env.docker" ]]; then
  DOCKER_ENV_TOKEN="$(sed -n 's/^SUPERVISOR_API_TOKEN=//p' "$PROJECT_DIR/.env.docker" | tail -n 1)"
fi
if [[ -n "$DOCKER_ENV_TOKEN" ]]; then
  if grep -q '^SUPERVISOR_API_TOKEN=' /etc/default/analitico-supervisor; then
    sed -i "s/^SUPERVISOR_API_TOKEN=.*/SUPERVISOR_API_TOKEN=$DOCKER_ENV_TOKEN/" \
      /etc/default/analitico-supervisor
  else
    printf 'SUPERVISOR_API_TOKEN=%s\n' "$DOCKER_ENV_TOKEN" >> /etc/default/analitico-supervisor
  fi
  echo "Token do supervisor sincronizado a partir de .env.docker."
fi

systemctl daemon-reload
systemctl enable analitico-supervisor.service
systemctl restart analitico-supervisor.service
systemctl enable analitico-slo-report.timer
systemctl restart analitico-slo-report.timer
systemctl --no-pager --full status analitico-supervisor.service
systemctl --no-pager --full status analitico-slo-report.timer
