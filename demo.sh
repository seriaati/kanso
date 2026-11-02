#!/usr/bin/env bash
# Starts the demo instance in docker-compose.demo.yml. Usage: ./demo.sh [anthropic-api-key]
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -gt 1 ] || [[ "${1:-}" == -* ]]; then
  echo "usage: $0 [anthropic-api-key]" >&2
  exit 2
fi
# Only a key given here turns AI on, never one left in the shell or in .env.
export DEMO_ANTHROPIC_API_KEY="${1:-}"

compose=(docker compose -f docker-compose.demo.yml)
"${compose[@]}" up --build --detach --wait

address=$("${compose[@]}" port app 8000)
echo
echo "kanso demo: http://localhost:${address##*:}  (password: demo)"
echo "AI: $([ -n "$DEMO_ANTHROPIC_API_KEY" ] && echo on || echo off)"
echo "Stop it with: ${compose[*]} down"
