#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_ID="phase1-$PPID-$$"
TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/workspace-${RUN_ID}.XXXXXX")
COMPOSE_PROJECT_NAME="workspace-${RUN_ID}"
export COMPOSE_PROJECT_NAME

pick_port() {
  uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

POSTGRES_PORT=$(pick_port)
AUTHORITY_PORT=$(pick_port)
REPLICA_A_PORT=$(pick_port)
REPLICA_B_PORT=$(pick_port)
export POSTGRES_PORT
DATABASE_URL="postgresql+asyncpg://workspace:workspace@127.0.0.1:${POSTGRES_PORT}/workspace_test"
AUTHORITY_URL="http://127.0.0.1:${AUTHORITY_PORT}"
PIDS=()

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo "Phase 1 verification failed; service logs follow" >&2
    for log in "$TMP_DIR"/*.log; do
      if [[ -f "$log" ]]; then
        echo "==> $log <==" >&2
        tail -n 120 "$log" >&2 || true
      fi
    done
  fi
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait "${PIDS[@]:-}" 2>/dev/null || true
  docker compose -f "$ROOT/compose.integration.yaml" down -v >/dev/null 2>&1 || true
  rm -r "$TMP_DIR"
  return "$status"
}
trap cleanup EXIT INT TERM

cd "$ROOT"
docker compose -f compose.integration.yaml up -d --wait postgres

uv run uvicorn scripts.phase1_test_authority:app \
  --host 127.0.0.1 --port "$AUTHORITY_PORT" >"$TMP_DIR/authority.log" 2>&1 &
PIDS+=("$!")

start_replica() {
  local port=$1
  local name=$2
  env \
    ANTHROPIC_BASE_URL=https://proxy.invalid \
    ANTHROPIC_API_KEY=phase1-unused \
    CLAUDE_SKILLS_ROOT="${CLAUDE_SKILLS_ROOT:-$HOME/.claude/skills}" \
    CODEX_SECURITY_MCP_ENTRYPOINT="$ROOT/scripts/phase1_test_authority.py" \
    CODEX_HOME="$TMP_DIR/codex-home" \
    DATA_ANALYTICS_MCP_ENTRYPOINT="$ROOT/scripts/phase1_test_authority.py" \
    AHS_HIVE_QUERY_MCP_ENTRYPOINT="$ROOT/scripts/phase1_test_authority.py" \
    WORKSPACES_ROOT="$ROOT/workspaces" \
    APP_DATA_DIR="$TMP_DIR/$name" \
    APP_ENV=production \
    APP_RUNTIME_MODE=execution_disabled \
    APP_IDENTITY_MODE=oidc \
    APP_OIDC_ISSUER="$AUTHORITY_URL" \
    APP_OIDC_AUDIENCE=workspace-agent \
    APP_OIDC_JWKS_URI="$AUTHORITY_URL/.well-known/jwks.json" \
    APP_SPACE_AUTHORITY_URL="$AUTHORITY_URL" \
    APP_SPACE_AUTHORITY_TOKEN=phase1-space-token \
    APP_MEMBERSHIP_PROJECTION_TTL_SECONDS=5 \
    DATABASE_URL="$DATABASE_URL" \
    uv run uvicorn app.main:app --host 127.0.0.1 --port "$port" \
      >"$TMP_DIR/$name.log" 2>&1 &
  PIDS+=("$!")
}

start_replica "$REPLICA_A_PORT" replica-a
start_replica "$REPLICA_B_PORT" replica-b

wait_http() {
  local url=$1
  for _ in $(seq 1 100); do
    if curl --fail --silent "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

wait_http "$AUTHORITY_URL/.well-known/jwks.json"
wait_http "http://127.0.0.1:${REPLICA_A_PORT}/api/health"
wait_http "http://127.0.0.1:${REPLICA_B_PORT}/api/health"

POSTGRES_VERSION=$(docker compose -f compose.integration.yaml exec -T postgres \
  psql -U workspace -d workspace_test -Atc 'show server_version;')
echo "Phase 1 environment: postgres=${POSTGRES_VERSION} database_port=${POSTGRES_PORT} authority_port=${AUTHORITY_PORT} replica_a_port=${REPLICA_A_PORT} replica_b_port=${REPLICA_B_PORT}"

uv run python scripts/verify_phase1_scenario.py \
  --database-url "$DATABASE_URL" \
  --authority "$AUTHORITY_URL" \
  --replica-a "http://127.0.0.1:${REPLICA_A_PORT}" \
  --replica-b "http://127.0.0.1:${REPLICA_B_PORT}"

echo "Phase 1 multi-replica verification passed"
