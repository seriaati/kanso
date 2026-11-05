#!/usr/bin/env bash
# Starts the demo instance in docker-compose.demo.yml. Usage: ./demo.sh
# AI API keys are added in the demo's Settings -> AI keys; they survive the daily reset.
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -gt 0 ]; then
  echo "usage: $0  (add AI API keys in the demo's Settings -> AI keys)" >&2
  exit 2
fi

compose=(docker compose -f docker-compose.demo.yml)
"${compose[@]}" up --build --detach --wait

address=$("${compose[@]}" port app 8000)
echo
echo "Kanso demo: http://localhost:${address##*:}  (password: demo)"
echo "AI: add your API keys under Settings -> AI keys"
echo "Stop it with: ${compose[*]} down"
