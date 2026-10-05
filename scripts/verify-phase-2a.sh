#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_ID="phase2a-$PPID-$$"
OS_PROJECT="workspace-${RUN_ID}-opensandbox"
PG_PROJECT="workspace-${RUN_ID}-postgres"
SERVER_KEY="${RUN_ID}-local-only"

pick_port() {
  uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

OPENSANDBOX_PORT=$(pick_port)
POSTGRES_PORT=$(pick_port)
export OPENSANDBOX_PORT POSTGRES_PORT

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    OPENSANDBOX_SERVER_API_KEY="$SERVER_KEY" \
      docker compose -p "$OS_PROJECT" -f "$ROOT/deploy/opensandbox/compose.yaml" logs --tail 120 >&2 || true
  fi
  OPENSANDBOX_SERVER_API_KEY="$SERVER_KEY" \
    docker compose -p "$OS_PROJECT" -f "$ROOT/deploy/opensandbox/compose.yaml" down -v >/dev/null 2>&1 || true
  docker compose -p "$PG_PROJECT" -f "$ROOT/compose.integration.yaml" down -v >/dev/null 2>&1 || true
  return "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT"
docker info >/dev/null
uv lock --check
uv run pytest tests/security/test_opensandbox_boundary.py -q
node --test tests/js/*.cjs
RUN_LIVE_OPENSANDBOX_CLAUDE=0 uv run pytest -q

docker compose -p "$PG_PROJECT" -f compose.integration.yaml up -d --wait postgres
OPENSANDBOX_SERVER_API_KEY="$SERVER_KEY" \
  docker compose -p "$OS_PROJECT" -f deploy/opensandbox/compose.yaml up -d

for _ in $(seq 1 100); do
  if curl --fail --silent \
    "http://127.0.0.1:${OPENSANDBOX_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

docker build \
  --pull=false \
  -f deploy/docker/Dockerfile.opensandbox-runner \
  -t "workspace-agent-runner:${RUN_ID}" .
RUNNER_IMAGE=$(docker image inspect "workspace-agent-runner:${RUN_ID}" --format '{{.Id}}')
TEST_POSTGRES_URL="postgresql+asyncpg://workspace:workspace@127.0.0.1:${POSTGRES_PORT}/workspace_test"
export TEST_POSTGRES_URL

uv run pytest tests/integration/test_opensandbox_postgres.py -q
RUN_OPENSANDBOX_DOCKER=1 \
OPENSANDBOX_API_URL="http://127.0.0.1:${OPENSANDBOX_PORT}" \
OPENSANDBOX_API_KEY="$SERVER_KEY" \
OPENSANDBOX_RUNNER_IMAGE="$RUNNER_IMAGE" \
  uv run pytest tests/integration/test_opensandbox_docker.py -q

if [[ "${RUN_LIVE_OPENSANDBOX_CLAUDE:-0}" == "1" ]]; then
  RUN_LIVE_OPENSANDBOX_CLAUDE=1 \
  OPENSANDBOX_API_URL="http://127.0.0.1:${OPENSANDBOX_PORT}" \
  OPENSANDBOX_API_KEY="$SERVER_KEY" \
  OPENSANDBOX_RUNNER_IMAGE="$RUNNER_IMAGE" \
  TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
    uv run pytest tests/live/test_opensandbox_claude.py -q
fi

echo "Phase 2A gate passed"
echo "OpenSandbox SDK=0.1.15 server=0.2.2 execd=1.0.21 egress=1.1.4"
echo "Runner image=$RUNNER_IMAGE"
echo "PostgreSQL=$(docker compose -p "$PG_PROJECT" -f compose.integration.yaml exec -T postgres psql -U workspace -d workspace_test -Atc 'show server_version;')"
