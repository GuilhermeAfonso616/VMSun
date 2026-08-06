#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACCELERATOR="${ANALITICO_ACCELERATOR:-auto}"
PRINT_PROFILE=false
ENV_FILE="$ROOT_DIR/.env.docker"

env_file_value() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  awk -F= -v wanted="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 ~ "^[[:space:]]*" wanted "[[:space:]]*$" {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      if (($0 ~ /^".*"$/) || ($0 ~ /^\047.*\047$/)) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print
      exit
    }
  ' "$ENV_FILE"
}

if [[ "${1:-}" == "--print-profile" ]]; then
  PRINT_PROFILE=true
  shift
fi

case "${ACCELERATOR,,}" in
  auto|cpu|nvidia) ;;
  *)
    echo "ERRO: ANALITICO_ACCELERATOR deve ser auto, cpu ou nvidia." >&2
    exit 2
    ;;
esac

host_has_nvidia() {
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}

docker_has_nvidia() {
  local runtime_info
  runtime_info="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  [[ "$runtime_info" == *'"nvidia"'* ]]
}

detect_primary_lan_ipv4() {
  local candidate=""
  local route_line=""

  if command -v ip >/dev/null 2>&1; then
    route_line="$(ip -4 route get 1.1.1.1 2>/dev/null | head -n 1 || true)"
    candidate="$(
      awk '{
        for (field = 1; field <= NF; field++) {
          if ($field == "src" && (field + 1) <= NF) {
            print $(field + 1)
            exit
          }
        }
      }' <<<"$route_line"
    )"
  fi

  if [[ -z "$candidate" ]] && command -v hostname >/dev/null 2>&1; then
    for candidate in $(hostname -I 2>/dev/null || true); do
      if [[ "$candidate" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        && [[ "$candidate" != 127.* ]] \
        && [[ "$candidate" != 169.254.* ]]; then
        break
      fi
      candidate=""
    done
  fi

  printf '%s' "$candidate"
}

configure_webrtc_ice_host() {
  local detected=""

  if [[ -z "${MTX_WEBRTCADDITIONALHOSTS:-}" ]]; then
    MTX_WEBRTCADDITIONALHOSTS="$(env_file_value MTX_WEBRTCADDITIONALHOSTS)"
    export MTX_WEBRTCADDITIONALHOSTS
  fi

  if [[ -n "${MTX_WEBRTCADDITIONALHOSTS:-}" ]]; then
    echo "WebRTC ICE: usando override MTX_WEBRTCADDITIONALHOSTS=${MTX_WEBRTCADDITIONALHOSTS}" >&2
    return
  fi

  detected="$(detect_primary_lan_ipv4)"
  if [[ -n "$detected" ]]; then
    export MTX_WEBRTCADDITIONALHOSTS="$detected"
    echo "WebRTC ICE: IPv4 LAN detectado automaticamente: $detected" >&2
    return
  fi

  echo "AVISO: nao foi possivel detectar o IPv4 LAN para o WebRTC ICE." >&2
  echo "Defina MTX_WEBRTCADDITIONALHOSTS apenas se os clientes estiverem em outra maquina." >&2
}

configure_webrtc_ice_host

PROFILE="cpu"
PROFILE_REASON="GPU NVIDIA utilizavel nao detectada"

if [[ "${ACCELERATOR,,}" == "nvidia" ]]; then
  if ! host_has_nvidia; then
    echo "ERRO: perfil NVIDIA solicitado, mas nvidia-smi nao detectou uma GPU utilizavel." >&2
    exit 3
  fi
  if ! docker_has_nvidia; then
    echo "ERRO: perfil NVIDIA solicitado, mas o runtime NVIDIA nao aparece em docker info." >&2
    exit 4
  fi
  PROFILE="nvidia"
  PROFILE_REASON="forcado por ANALITICO_ACCELERATOR=nvidia"
elif [[ "${ACCELERATOR,,}" == "cpu" ]]; then
  PROFILE="cpu"
  PROFILE_REASON="forcado por ANALITICO_ACCELERATOR=cpu"
elif host_has_nvidia && docker_has_nvidia; then
  PROFILE="nvidia"
  PROFILE_REASON="GPU e runtime NVIDIA detectados automaticamente"
elif host_has_nvidia; then
  PROFILE_REASON="GPU detectada, mas runtime NVIDIA indisponivel no Docker"
fi

COMPOSE_FILES=(--env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.yml")
if [[ "$PROFILE" == "nvidia" ]]; then
  COMPOSE_FILES+=(-f "$ROOT_DIR/docker-compose.gpu.yml")
fi

echo "Perfil de aceleracao: $PROFILE ($PROFILE_REASON)" >&2

if [[ "$PRINT_PROFILE" == true ]]; then
  printf 'profile=%s\n' "$PROFILE"
  printf 'detect_device=auto\n'
  printf 'webrtc_additional_hosts=%s\n' "${MTX_WEBRTCADDITIONALHOSTS:-}"
  printf 'compose_files='
  printf '%q ' "${COMPOSE_FILES[@]}"
  printf '\n'
  exit 0
fi

if [[ "$#" -eq 0 ]]; then
  echo "Uso: $0 [--print-profile] <comando docker compose>" >&2
  echo "Exemplo: $0 up -d --build" >&2
  exit 2
fi

cd "$ROOT_DIR"
exec docker compose "${COMPOSE_FILES[@]}" "$@"
