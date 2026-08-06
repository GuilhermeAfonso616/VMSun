#!/usr/bin/env bash
set -euo pipefail

# Run on the Linux host after an observation window. The Docker socket may be
# selected through DOCKER_HOST, which keeps it compatible with the isolated stack.
HOURS="${1:-12}"
EXPECTED_WORKERS="${2:-21}"
RUNTIME_CONTAINER="${RUNTIME_CONTAINER:-server-analiticos-runtime}"
WEB_CONTAINER="${WEB_CONTAINER:-server-analiticos}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! [[ "$HOURS" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! [[ "$EXPECTED_WORKERS" =~ ^[0-9]+$ ]]; then
  echo "Uso: $0 [horas=12] [workers_esperados=21]" >&2
  exit 2
fi

for container in "$RUNTIME_CONTAINER" "$WEB_CONTAINER"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    echo "Container nao encontrado: $container" >&2
    echo "Verifique DOCKER_HOST, RUNTIME_CONTAINER e WEB_CONTAINER." >&2
    exit 1
  fi
done

for report_script in generate_stability_report.py generate_runtime_event_report.py; do
  if [[ ! -f "$SCRIPT_DIR/$report_script" ]]; then
    echo "Gerador ausente no projeto: $SCRIPT_DIR/$report_script" >&2
    exit 1
  fi
  docker cp "$SCRIPT_DIR/$report_script" "$RUNTIME_CONTAINER:/app/scripts/$report_script"
done

stamp="$(date +%Y%m%d_%H%M%S)"
base_dir="/data/reports/ia_stability/ia_window_${stamp}"
stability_dir="${base_dir}/stability"
runtime_dir="${base_dir}/runtime"

echo "== Avaliacao de estabilidade IA =="
echo "Janela: ${HOURS}h | workers esperados: ${EXPECTED_WORKERS}"
echo "Runtime: ${RUNTIME_CONTAINER} | Web: ${WEB_CONTAINER}"

docker exec "$RUNTIME_CONTAINER" mkdir -p "$stability_dir" "$runtime_dir"

docker exec -w /app "$RUNTIME_CONTAINER" \
  python3 -B /app/scripts/generate_stability_report.py \
  --hours "$HOURS" \
  --output-dir "$stability_dir"

docker exec -w /app "$RUNTIME_CONTAINER" \
  python3 -B /app/scripts/generate_runtime_event_report.py \
  --hours "$HOURS" \
  --output-dir "$runtime_dir"

echo
echo "== Estado atual do stack =="
docker exec -i "$WEB_CONTAINER" python3 - "$EXPECTED_WORKERS" <<'PY'
import json
import sys
import urllib.request

expected = int(sys.argv[1])
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/diagnostics/data", timeout=5) as response:
        payload = json.load(response)
    summary = payload.get("summary") or {}
    print(f"Cameras cadastradas: {summary.get('camera_total', '?')}")
    print(f"Cameras rodando: {summary.get('running_count', '?')}")
    print(f"Workers ativos: {summary.get('worker_count', '?')} / esperado {expected}")
    print(f"Captura gateway: {summary.get('capture_source_gateway_count', '?')}")
    print(f"Stalls atuais: {summary.get('stalling_cameras', '?')}")
    print(f"Restarts acumulados: {summary.get('restart_total', '?')}")

    inactive = [
        camera
        for camera in payload.get("cameras") or []
        if not camera.get("is_running")
    ]
    if inactive:
        print("Cameras fora/sem worker agora:")
        for camera in inactive:
            health = camera.get("health_status") or camera.get("health") or "-"
            print(f"- {camera.get('id')} | {camera.get('name')} | {health}")
    elif summary.get("worker_count") != expected:
        print("Atencao: a contagem atual diverge do alvo, mas nao ha camera marcada como parada.")
except Exception as exc:
    print(f"Nao foi possivel consultar diagnostics/data: {exc}")
PY

echo
echo "== Resumo de estabilidade e causa provavel =="
docker exec "$RUNTIME_CONTAINER" sh -lc "cat '$stability_dir'/stability_summary_*.md"

echo
echo "== Pool, inferencia e processo =="
docker exec "$RUNTIME_CONTAINER" sh -lc "cat '$runtime_dir'/summary.txt"

echo
echo "Relatorios completos:"
echo "- $stability_dir"
echo "- $runtime_dir"
