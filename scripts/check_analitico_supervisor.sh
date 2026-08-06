#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${ANALITICO_SUPERVISOR_STATE_DIR:-/mnt/analitico_ssd/supervisor}"

echo "===== systemd ====="
systemctl --no-pager --full status analitico-supervisor.service || true
systemctl --no-pager --full status analitico-slo-report.timer || true
echo
echo "===== status atual ====="
if [[ -f "$STATE_DIR/status.json" ]]; then
  python3 -m json.tool "$STATE_DIR/status.json"
else
  echo "status.json ainda nao existe"
fi
echo
echo "===== ultimos eventos ====="
tail -n 30 "$STATE_DIR/events.jsonl" 2>/dev/null || true
echo
echo "===== API do runtime ====="
python3 - <<'PY'
import json
import os
import urllib.request

url = os.getenv("ANALITICO_SUPERVISOR_RUNTIME_URL", "http://127.0.0.1:8001")
token = os.getenv("SUPERVISOR_API_TOKEN", "")
request = urllib.request.Request(url.rstrip("/") + "/internal/supervisor/snapshot")
if token:
    request.add_header("X-Analitico-Supervisor-Token", token)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
print(json.dumps(payload.get("summary", {}), indent=2, ensure_ascii=False))
gateway = payload.get("gateway") or {}
print(json.dumps({
    "runtime_tuning": payload.get("runtime_tuning") or {},
    "gateway_health": gateway.get("health") or {},
    "gateway_orphan_camera_ids": gateway.get("orphan_camera_ids") or [],
}, indent=2, ensure_ascii=False))
PY

echo
echo "===== SLO mais recente ====="
SLO_PATH="$STATE_DIR/slo/latest.md"
if [[ -f "$SLO_PATH" ]]; then
  cat "$SLO_PATH"
else
  echo "Relatorio ainda nao gerado. Execute: sudo systemctl start analitico-slo-report.service"
fi
