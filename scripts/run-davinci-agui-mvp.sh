#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "${APP_RUNTIME_MODE:-local_inline}" != "local_inline" ]]; then
  echo "Davinci AG-UI MVP requires APP_RUNTIME_MODE=local_inline" >&2
  exit 2
fi

agent_pid=""
mock_pid=""

cleanup() {
  trap - EXIT INT TERM
  [[ -n "$agent_pid" ]] && kill "$agent_pid" 2>/dev/null || true
  [[ -n "$mock_pid" ]] && kill "$mock_pid" 2>/dev/null || true
  [[ -n "$agent_pid" ]] && wait "$agent_pid" 2>/dev/null || true
  [[ -n "$mock_pid" ]] && wait "$mock_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 &
agent_pid=$!
DAVINCI_AGENT_ORIGIN=http://127.0.0.1:8000 \
  uv run uvicorn demo.davinci_mock.app:app --host 127.0.0.1 --port 4173 &
mock_pid=$!

wait_for_url() {
  local url="$1"
  for _ in $(seq 1 100); do
    if curl --silent --fail "$url" >/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

wait_for_url http://127.0.0.1:8000/api/health
wait_for_url http://127.0.0.1:4173/health
echo "Davinci AG-UI MVP: http://127.0.0.1:4173/dashboard/1024"

wait "$agent_pid" "$mock_pid"
