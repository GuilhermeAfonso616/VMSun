#!/usr/bin/env bash
set -euo pipefail

DEFAULT_REMOTE_BRANCH="origin/codex/split-web-runtime"

cd "$(dirname "$0")/.."
COMPOSE=("$PWD/scripts/compose-auto.sh")

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERRO: existem mudancas locais rastreadas. Resolva antes de atualizar."
  git status --short
  exit 1
fi

UPSTREAM="${1:-}"
if [ -z "$UPSTREAM" ]; then
  UPSTREAM="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
fi
if [ -z "$UPSTREAM" ]; then
  UPSTREAM="$DEFAULT_REMOTE_BRANCH"
fi

REMOTE="${UPSTREAM%%/*}"
BRANCH="${UPSTREAM#*/}"

echo "Atualizando referencias de $REMOTE/$BRANCH..."
git fetch "$REMOTE" "$BRANCH"

TARGET="$REMOTE/$BRANCH"
CHANGED="$(git diff --name-only HEAD.."$TARGET")"

if [ -z "$CHANGED" ]; then
  echo "Nada para atualizar. Containers atuais preservados."
  "${COMPOSE[@]}" ps
  exit 0
fi

echo "Arquivos que vao atualizar:"
echo "$CHANGED"
echo

needs_full_build=false
needs_analitico_build=false
needs_gateway_build=false
needs_recreate=false
needs_webrtc_recreate=false

if echo "$CHANGED" | grep -Eq '(^Dockerfile|^Dockerfile\.gpu|^requirements|^pyproject\.toml|^poetry\.lock|^docker-compose.*\.yml$)'; then
  needs_full_build=true
elif echo "$CHANGED" | grep -Eq '(^app/|^templates/|^main\.py|^scripts/)'; then
  needs_analitico_build=true
fi

if echo "$CHANGED" | grep -Eq '^gateway/'; then
  needs_gateway_build=true
fi

if echo "$CHANGED" | grep -Eq '(^scripts/compose-auto\.sh$|^webrtc-gateway/mediamtx\.yml$|^docker-compose.*\.yml$)'; then
  needs_webrtc_recreate=true
fi

if echo "$CHANGED" | grep -Eq '(^\.env|^configs/|^wsdl/|^models/)'; then
  needs_recreate=true
fi

echo "Aplicando git pull --ff-only..."
git pull --ff-only

if [ "$needs_full_build" = true ]; then
  echo "Modo escolhido: build completo por mudanca de Docker/dependencias/compose."
  "${COMPOSE[@]}" build analitico camera-gateway
  "${COMPOSE[@]}" up -d
elif [ "$needs_gateway_build" = true ] && [ "$needs_analitico_build" = true ]; then
  echo "Modo escolhido: build do analitico e camera-gateway."
  "${COMPOSE[@]}" build analitico camera-gateway
  "${COMPOSE[@]}" up -d
elif [ "$needs_gateway_build" = true ]; then
  echo "Modo escolhido: build apenas do camera-gateway."
  "${COMPOSE[@]}" build camera-gateway
  "${COMPOSE[@]}" up -d --no-deps camera-gateway
elif [ "$needs_analitico_build" = true ]; then
  echo "Modo escolhido: build rapido apenas do analitico."
  "${COMPOSE[@]}" build analitico
  "${COMPOSE[@]}" up -d --no-deps analitico analitico-runtime
elif [ "$needs_recreate" = true ]; then
  echo "Modo escolhido: sem build, apenas recriar containers para configs."
  "${COMPOSE[@]}" up -d --no-build --force-recreate analitico analitico-runtime
else
  echo "Modo escolhido: sem build, apenas garantir containers ativos."
  "${COMPOSE[@]}" up -d --no-build
fi

if [ "$needs_webrtc_recreate" = true ]; then
  echo "Aplicando configuracao automatica de rede WebRTC da instalacao."
  "${COMPOSE[@]}" up -d --no-deps --force-recreate webrtc-gateway
fi

echo
echo "Status final:"
"${COMPOSE[@]}" ps
