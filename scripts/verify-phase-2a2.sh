#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LIVE_REQUESTED=${RUN_LIVE_DOCKER_WEB_CLAUDE:-0}
case "$LIVE_REQUESTED" in
  0|1) ;;
  *) printf 'RUN_LIVE_DOCKER_WEB_CLAUDE must be 0 or 1\n' >&2; exit 2 ;;
esac
RUN_ID="phase2a2-$PPID-$$-$RANDOM"
PROJECT="workspace-${RUN_ID}"
RUNTIME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/workspace-${RUN_ID}.XXXXXX")
CONFIG_FILE=$RUNTIME_DIR/operator.env
TEST_STATE=$RUNTIME_DIR/test-state.json
SENTINEL_VOLUME="workspace-${RUN_ID}-unrelated"

pick_port() {
  uv run python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

WEB_PORT=$(pick_port)
POSTGRES_PORT=$(pick_port)
while [[ "$POSTGRES_PORT" == "$WEB_PORT" ]]; do POSTGRES_PORT=$(pick_port); done
OPENSANDBOX_PORT=$(pick_port)
while [[ "$OPENSANDBOX_PORT" == "$WEB_PORT" || "$OPENSANDBOX_PORT" == "$POSTGRES_PORT" ]]; do
  OPENSANDBOX_PORT=$(pick_port)
done

cat > "$CONFIG_FILE" <<EOF
DOCKER_WEB_PORT=$WEB_PORT
DOCKER_WEB_POSTGRES_PORT=$POSTGRES_PORT
DOCKER_WEB_OPENSANDBOX_PORT=$OPENSANDBOX_PORT
DOCKER_WEB_MODEL=claude-sonnet-4-6
ANTHROPIC_BASE_URL=https://api.anthropic.com
OPENSANDBOX_ALLOWED_HOSTS=api.anthropic.com
OPENSANDBOX_IDLE_TTL_SECONDS=300
OPENSANDBOX_SANDBOX_TIMEOUT_SECONDS=600
OPENSANDBOX_WORKER_CONCURRENCY=2
WORKER_HEARTBEAT_INTERVAL_SECONDS=2
WORKER_HEARTBEAT_STALE_SECONDS=6
EOF
chmod 600 "$CONFIG_FILE"

export DOCKER_WEB_PROJECT=$PROJECT
export DOCKER_WEB_RUNTIME_DIR=$RUNTIME_DIR
export DOCKER_WEB_CONFIG_FILE=$CONFIG_FILE
export DOCKER_WEB_GATE=1
export DOCKER_WEB_READY_TIMEOUT_SECONDS=120
export DOCKER_WEB_URL="http://127.0.0.1:$WEB_PORT"
export DOCKER_WEB_POSTGRES_URL="postgresql+asyncpg://workspace@127.0.0.1:$POSTGRES_PORT/workspace"
export DOCKER_WEB_OPENSANDBOX_URL="http://127.0.0.1:$OPENSANDBOX_PORT"
export DOCKER_WEB_TEST_STATE=$TEST_STATE

cleanup() {
  local status=$?
  set +e
  if [[ $status -ne 0 && -f "$RUNTIME_DIR/compose.env" ]]; then
    bash "$ROOT/scripts/docker-web.sh" status >&2
    bash "$ROOT/scripts/docker-web.sh" logs api >&2
    bash "$ROOT/scripts/docker-web.sh" logs worker >&2
    bash "$ROOT/scripts/docker-web.sh" logs opensandbox-server >&2
  fi
  if [[ -f "$RUNTIME_DIR/compose.env" ]]; then
    if ! bash "$ROOT/scripts/docker-web.sh" reset --yes >/dev/null 2>&1; then
      printf 'Docker Web reset failed during Gate cleanup\n' >&2
      status=1
    fi
  else
    rm -rf -- "$RUNTIME_DIR"
  fi
  if [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ]]; then
    printf 'Gate cleanup left project containers behind\n' >&2
    status=1
  fi
  if [[ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" ]]; then
    printf 'Gate cleanup left project volumes behind\n' >&2
    status=1
  fi
  if ! docker volume inspect "$SENTINEL_VOLUME" >/dev/null 2>&1; then
    printf 'Unrelated sentinel volume was removed by the Gate\n' >&2
    status=1
  else
    docker volume rm "$SENTINEL_VOLUME" >/dev/null
  fi
  return "$status"
}
trap cleanup EXIT

cd "$ROOT"
docker info >/dev/null
docker volume create "$SENTINEL_VOLUME" >/dev/null
uv lock --check
bash -n scripts/docker-web.sh scripts/verify-phase-2a2.sh
env \
  -u DOCKER_WEB_PROJECT \
  -u DOCKER_WEB_RUNTIME_DIR \
  -u DOCKER_WEB_CONFIG_FILE \
  -u DOCKER_WEB_GATE \
  -u DOCKER_WEB_READY_TIMEOUT_SECONDS \
  uv run pytest \
  tests/security/test_opensandbox_boundary.py \
  tests/security/test_docker_web_boundary.py \
  tests/test_docker_web_config.py \
  tests/test_docker_web_launcher.py \
  -q

bash scripts/docker-web.sh up --fake
RUN_DOCKER_WEB_STACK=1 \
  uv run pytest tests/integration/test_docker_web_stack.py -q

test -f "$TEST_STATE"
printf 'Phase 2A.2 Docker Web fake/recovery Gate passed\n'

if [[ "$LIVE_REQUESTED" == "1" ]]; then
  missing=()
  for name in ANTHROPIC_BASE_URL ANTHROPIC_API_KEY; do
    [[ -n "${!name:-}" ]] || missing+=("$name")
  done
  [[ -n "${DOCKER_WEB_MODEL:-${CLAUDE_MODEL:-}}" ]] || missing+=("DOCKER_WEB_MODEL")
  if (( ${#missing[@]} > 0 )); then
    printf 'Missing packaged Claude gate configuration: %s\n' "$(IFS=', '; echo "${missing[*]}")" >&2
    exit 1
  fi

  # Finish the fake deployment before switching the trap to a separately
  # marked live project. No fake volume or generated secret is reused.
  bash scripts/docker-web.sh reset --yes

  RUN_ID="phase2a2-live-$PPID-$$-$RANDOM"
  PROJECT="workspace-${RUN_ID}"
  RUNTIME_DIR=$(mktemp -d "${TMPDIR:-/tmp}/workspace-${RUN_ID}.XXXXXX")
  CONFIG_FILE=$RUNTIME_DIR/operator.env
  WEB_PORT=$(pick_port)
  POSTGRES_PORT=$(pick_port)
  while [[ "$POSTGRES_PORT" == "$WEB_PORT" ]]; do POSTGRES_PORT=$(pick_port); done
  OPENSANDBOX_PORT=$(pick_port)
  while [[ "$OPENSANDBOX_PORT" == "$WEB_PORT" || "$OPENSANDBOX_PORT" == "$POSTGRES_PORT" ]]; do
    OPENSANDBOX_PORT=$(pick_port)
  done

  export DOCKER_WEB_MODEL=${DOCKER_WEB_MODEL:-$CLAUDE_MODEL}
  export DOCKER_WEB_PROJECT=$PROJECT
  export DOCKER_WEB_RUNTIME_DIR=$RUNTIME_DIR
  export DOCKER_WEB_CONFIG_FILE=$CONFIG_FILE
  export DOCKER_WEB_URL="http://127.0.0.1:$WEB_PORT"
  export DOCKER_WEB_OPENSANDBOX_URL="http://127.0.0.1:$OPENSANDBOX_PORT"

  uv run python - "$CONFIG_FILE" "$WEB_PORT" "$POSTGRES_PORT" "$OPENSANDBOX_PORT" <<'PY'
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from scripts.docker_web_config import _env_text

base_url = os.environ["ANTHROPIC_BASE_URL"]
host = urlsplit(base_url).hostname
if host is None:
    raise SystemExit("ANTHROPIC_BASE_URL must include a hostname")
allowed_hosts = os.environ.get("OPENSANDBOX_ALLOWED_HOSTS", host)
path = Path(sys.argv[1])
path.write_text(
    _env_text(
        {
            "DOCKER_WEB_PORT": sys.argv[2],
            "DOCKER_WEB_POSTGRES_PORT": sys.argv[3],
            "DOCKER_WEB_OPENSANDBOX_PORT": sys.argv[4],
            "DOCKER_WEB_MODEL": os.environ["DOCKER_WEB_MODEL"],
            "ANTHROPIC_BASE_URL": base_url,
            "ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"],
            "OPENSANDBOX_ALLOWED_HOSTS": allowed_hosts,
            "OPENSANDBOX_IDLE_TTL_SECONDS": 300,
            "OPENSANDBOX_SANDBOX_TIMEOUT_SECONDS": 900,
            "OPENSANDBOX_WORKER_CONCURRENCY": 2,
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": 2,
            "WORKER_HEARTBEAT_STALE_SECONDS": 6,
        }
    ),
    encoding="utf-8",
)
path.chmod(0o600)
PY

  bash scripts/docker-web.sh up --claude
  export OPENSANDBOX_API_KEY=$(uv run python -c \
    'from dotenv import dotenv_values; import sys; print(dotenv_values(sys.argv[1])["OPENSANDBOX_SERVER_API_KEY"])' \
    "$RUNTIME_DIR/opensandbox.env")
  export DOCKER_WEB_POSTGRES_URL=$(uv run python -c \
    'from dotenv import dotenv_values; from urllib.parse import quote; import sys; value=dotenv_values(sys.argv[1]); print("postgresql://workspace:" + quote(value["DOCKER_WEB_POSTGRES_PASSWORD"], safe="") + "@127.0.0.1:" + value["DOCKER_WEB_POSTGRES_PORT"] + "/workspace")' \
    "$RUNTIME_DIR/compose.env")

  RUN_LIVE_DOCKER_WEB_CLAUDE=1 \
    uv run pytest tests/live/test_docker_web_claude.py -q
  printf 'Phase 2A.2 packaged Claude Gate passed\n'
fi
