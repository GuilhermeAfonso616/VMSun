#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

python_bin="${PYTHON:-python3}"
quality_temp="$(mktemp -d "${TMPDIR:-/tmp}/analitico-quality.XXXXXX")"
trap 'rm -rf -- "$quality_temp"' EXIT

export TMPDIR="$quality_temp/temp"
export GOCACHE="$quality_temp/gocache"
mkdir -p "$TMPDIR" "$GOCACHE"

echo "==> Python compile"
"$python_bin" -m compileall -q app tests scripts main.py

echo "==> Legacy SQLite schema compatibility"
"$python_bin" scripts/verify_database_compatibility.py --mode legacy

echo "==> Python tests"
"$python_bin" -m pytest tests -q --basetemp "$quality_temp/pytest"

echo "==> Go gateway tests"
(cd gateway && go test ./...)

echo "==> Operator client build"
AVALONIA_TELEMETRY_OPTOUT=1 DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  dotnet build operator-client/src/Analitico.Operator.App/Analitico.Operator.App.csproj -c Release

echo "Quality gate completed successfully."
