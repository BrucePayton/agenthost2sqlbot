#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT=${DOCKER_WEB_PROJECT:-workspace-agent-docker-web}
RUNTIME_DIR=${DOCKER_WEB_RUNTIME_DIR:-$ROOT/.runtime/docker-web}
CONFIG_FILE=${DOCKER_WEB_CONFIG_FILE:-$ROOT/.env.docker.local}
COMPOSE_FILE=$ROOT/deploy/docker-web/compose.yaml
GATE_COMPOSE_FILE=$ROOT/deploy/docker-web/compose.gate.yaml
READY_TIMEOUT=${DOCKER_WEB_READY_TIMEOUT_SECONDS:-120}

usage() {
  cat <<'EOF'
Usage: bash scripts/docker-web.sh COMMAND [OPTIONS]

Commands:
  up [--fake|--claude]  Build and start the local Docker Web stack (default: fake)
  status                Show Compose and sanitized application health
  logs [SERVICE]        Tail api, worker, postgres, or opensandbox-server logs
  restart               Restart execution services and wait for readiness
  down                   Stop containers while preserving persistent volumes
  reset [--yes]          Delete only this marked deployment and its exact volumes
  --help                 Show this help
EOF
}

die_usage() {
  printf 'Error: %s\n' "$1" >&2
  usage >&2
  exit 2
}

die() {
  printf 'Error: %s\n' "$1" >&2
  exit 1
}

warning() {
  cat <<'EOF'
Local development only: OpenSandbox v0.2.2 publishes dynamic Runner ports on
0.0.0.0 within 40000-60000. Do not use this profile on an untrusted/shared LAN.
EOF
}

validate_project() {
  if [[ ! "$PROJECT" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]]; then
    die "invalid DOCKER_WEB_PROJECT"
  fi
}

validate_gate() {
  case "${DOCKER_WEB_GATE:-0}" in
    0|"") ;;
    1) ;;
    *) die "DOCKER_WEB_GATE must be 0 or 1" ;;
  esac
}

require_runtime_config() {
  [[ -f "$RUNTIME_DIR/compose.env" ]] || die "Docker Web is not configured; run up first"
}

build_compose_args() {
  COMPOSE_ARGS=(
    --project-name "$PROJECT"
    --env-file "$RUNTIME_DIR/compose.env"
    -f "$COMPOSE_FILE"
  )
  if [[ "${DOCKER_WEB_GATE:-0}" == "1" ]]; then
    COMPOSE_ARGS+=(-f "$GATE_COMPOSE_FILE")
  fi
}

compose() {
  docker compose "${COMPOSE_ARGS[@]}" "$@"
}

require_docker() {
  docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
  docker compose version >/dev/null 2>&1 || die "Docker Compose is unavailable"
}

public_port() {
  uv run python -c \
    'from dotenv import dotenv_values; import sys; print(dotenv_values(sys.argv[1])["DOCKER_WEB_PORT"])' \
    "$RUNTIME_DIR/compose.env"
}

validate_ports() {
  local gate=${DOCKER_WEB_GATE:-0}
  if [[ -n "$(compose ps --status running -q api 2>/dev/null || true)" ]]; then
    return
  fi
  uv run python - "$RUNTIME_DIR/compose.env" "$gate" <<'PY'
import socket
import sys
import time

from dotenv import dotenv_values

values = dotenv_values(sys.argv[1])
names = ["DOCKER_WEB_PORT"]
if sys.argv[2] == "1":
    names.extend(("DOCKER_WEB_POSTGRES_PORT", "DOCKER_WEB_OPENSANDBOX_PORT"))
deadline = time.monotonic() + 10
while True:
    unavailable = []
    for name in names:
        port = int(values[name])
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                unavailable.append((name, port, exc))
    if not unavailable:
        break
    if time.monotonic() >= deadline:
        name, port, error = unavailable[0]
        raise SystemExit(
            f"loopback port {port} ({name}) is unavailable after 10s: {error}"
        )
    time.sleep(0.1)
PY
}

health_is_ready() {
  uv run python -c \
    'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("status") == "ready" else 1)'
}

sanitize_stream() {
  uv run python -c '
import sys
from dotenv import dotenv_values
payload = sys.stdin.read()
secrets = []
for path in sys.argv[1:]:
    for name, value in dotenv_values(path).items():
        if value and ("PASSWORD" in name or "KEY" in name):
            secrets.append(value)
for value in sorted(secrets, key=len, reverse=True):
    payload = payload.replace(value, "[REDACTED]")
sys.stdout.write(payload)
' "$RUNTIME_DIR/compose.env" "$RUNTIME_DIR/worker.env" "$RUNTIME_DIR/opensandbox.env"
}

print_sanitized_failure() {
  printf 'Docker Web did not become ready. Current services:\n' >&2
  compose ps >&2 || true
  printf 'Recent sanitized logs:\n' >&2
  compose logs --no-color --tail 50 api worker opensandbox-server 2>&1 \
    | sanitize_stream >&2 || true
}

wait_ready() {
  local port deadline payload
  port=$(public_port)
  deadline=$((SECONDS + READY_TIMEOUT))
  while (( SECONDS < deadline )); do
    payload=$(curl --fail --silent --show-error --max-time 2 \
      "http://127.0.0.1:${port}/api/health" 2>/dev/null || true)
    if [[ -n "$payload" ]] && printf '%s' "$payload" | health_is_ready; then
      return 0
    fi
    sleep 1
  done
  print_sanitized_failure
  return 1
}

render_config() {
  local mode=$1 app_image=$2 runner_image=$3
  local render_args=(
    uv run python -m scripts.docker_web_config
    --mode "$mode"
    --runtime-dir "$RUNTIME_DIR"
    --app-image "$app_image"
    --runner-image "$runner_image"
    --project-name "$PROJECT"
  )
  if [[ -f "$CONFIG_FILE" ]]; then
    render_args+=(--source "$CONFIG_FILE")
  elif [[ "$mode" == "claude" ]]; then
    die "Claude mode requires operator config: $CONFIG_FILE"
  fi
  "${render_args[@]}"
}

command_up() {
  local mode=fake
  case "${1:-}" in
    ""|--fake) mode=fake ;;
    --claude) mode=claude ;;
    *) die_usage "up accepts only --fake or --claude" ;;
  esac
  [[ $# -le 1 ]] || die_usage "up accepts a single mode option"

  require_docker
  local app_tag="${PROJECT}-app:phase2a2"
  local runner_tag="${PROJECT}-runner:phase2a2"
  docker build -f "$ROOT/deploy/docker/Dockerfile.web" -t "$app_tag" "$ROOT"
  docker build -f "$ROOT/deploy/docker/Dockerfile.opensandbox-runner" -t "$runner_tag" "$ROOT"
  local app_image runner_image
  app_image=$(docker image inspect --format '{{.Id}}' "$app_tag")
  runner_image=$(docker image inspect --format '{{.Id}}' "$runner_tag")
  render_config "$mode" "$app_image" "$runner_image"

  build_compose_args
  validate_ports
  compose config --quiet
  compose up -d postgres opensandbox-server
  compose --profile tools run --rm migrate
  compose up -d api worker
  wait_ready
  printf 'Docker Web is ready at http://127.0.0.1:%s/\n' "$(public_port)"
  warning
}

command_status() {
  [[ $# -eq 0 ]] || die_usage "status accepts no arguments"
  require_runtime_config
  require_docker
  build_compose_args
  compose ps
  local payload port
  port=$(public_port)
  payload=$(curl --fail --silent --show-error --max-time 2 \
    "http://127.0.0.1:${port}/api/health" 2>/dev/null || true)
  if [[ -n "$payload" ]]; then
    printf '%s' "$payload" | uv run python -c '
import json, sys
value = json.load(sys.stdin)
execution = value.get("execution") or {}
runtime = value.get("runtime") or {}
print(json.dumps({
    "status": value.get("status", "unknown"),
    "worker": execution.get("status", "unknown"),
    "worker_count": execution.get("worker_count", 0),
    "mode": runtime.get("mode", "unknown"),
}, ensure_ascii=False))
'
  else
    printf '%s\n' '{"status":"unavailable","worker":"unknown","mode":"unknown"}'
  fi
  warning
}

command_logs() {
  [[ $# -le 1 ]] || die_usage "logs accepts at most one service"
  local service=${1:-api}
  case "$service" in
    api|worker|postgres|opensandbox-server) ;;
    *) die_usage "unknown logs service: $service" ;;
  esac
  require_runtime_config
  require_docker
  build_compose_args
  compose logs --no-color --tail 200 "$service" | sanitize_stream
}

command_restart() {
  [[ $# -eq 0 ]] || die_usage "restart accepts no arguments"
  require_runtime_config
  require_docker
  build_compose_args
  compose restart api worker opensandbox-server
  wait_ready
  warning
}

command_down() {
  [[ $# -eq 0 ]] || die_usage "down accepts no arguments"
  require_runtime_config
  require_docker
  build_compose_args
  compose down
}

canonical_path() {
  uv run python -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1"
}

validate_reset_target() {
  local canonical home
  canonical=$(canonical_path "$RUNTIME_DIR")
  home=$(canonical_path "$HOME")
  if [[ "$canonical" == "/" || "$canonical" == "$home" || "$canonical" == "$ROOT" ]]; then
    die "unsafe runtime directory: $canonical"
  fi
  RUNTIME_DIR=$canonical
}

validate_marker() {
  local marker=$RUNTIME_DIR/deployment.json
  [[ -f "$marker" && ! -L "$marker" ]] || die "ownership marker is missing"
  if ! uv run python - "$marker" "$PROJECT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
expected = {"schema_version": 1, "project_name": sys.argv[2]}
raise SystemExit(0 if value == expected else 1)
PY
  then
    die "ownership marker does not match this project"
  fi
}

confirm_reset() {
  local confirmed=$1
  if [[ "$confirmed" == "yes" ]]; then
    return
  fi
  [[ -t 0 ]] || die "reset requires an interactive terminal or --yes"
  printf 'Type RESET to delete this deployment and its persistent data: ' >&2
  local response
  IFS= read -r response
  [[ "$response" == "RESET" ]] || die "reset cancelled"
}

collect_sandbox_volumes() {
  local exists
  exists=$(compose exec -T postgres psql -U workspace -d workspace -Atqc \
    "SELECT to_regclass('public.session_sandboxes') IS NOT NULL;")
  [[ "$exists" == "t" ]] || return 0
  compose exec -T postgres psql -U workspace -d workspace -Atqc \
    "SELECT session_volume_name FROM session_sandboxes UNION SELECT memory_volume_name FROM session_sandboxes;"
}

collect_sandbox_ids() {
  compose exec -T postgres psql -U workspace -d workspace -Atqc \
    "SELECT sandbox_id FROM session_sandboxes WHERE sandbox_id IS NOT NULL;"
}

remove_sandbox_runtime() {
  local sandbox_id=$1
  if [[ ! "$sandbox_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]]; then
    die "database contains an unsafe sandbox identifier"
  fi
  docker rm -f "sandbox-$sandbox_id" "sandbox-egress-$sandbox_id" \
    >/dev/null 2>&1 || true
  docker volume rm "opensandbox-runtime-$sandbox_id" >/dev/null 2>&1 || true
}

command_reset() {
  local confirmed=no
  case "${1:-}" in
    "") ;;
    --yes) confirmed=yes ;;
    *) die_usage "reset accepts only --yes" ;;
  esac
  [[ $# -le 1 ]] || die_usage "reset accepts only --yes"
  validate_reset_target
  validate_marker
  confirm_reset "$confirmed"
  require_runtime_config
  require_docker
  build_compose_args

  compose up -d postgres
  local deadline=$((SECONDS + READY_TIMEOUT))
  until compose exec -T postgres pg_isready -U workspace -d workspace >/dev/null 2>&1; do
    (( SECONDS < deadline )) || die "PostgreSQL did not become ready for reset"
    sleep 1
  done

  # Stop new claims before reading the deployment-owned sandbox identities.
  compose stop worker >/dev/null 2>&1 || true
  local sandbox_ids sandbox_id volumes volume
  sandbox_ids=$(collect_sandbox_ids)
  volumes=$(collect_sandbox_volumes)
  while IFS= read -r sandbox_id; do
    [[ -z "$sandbox_id" ]] && continue
    remove_sandbox_runtime "$sandbox_id"
  done <<< "$sandbox_ids"
  while IFS= read -r volume; do
    [[ -z "$volume" ]] && continue
    if [[ ! "$volume" =~ ^wa-(session|memory)-[0-9a-f]{40}$ ]]; then
      die "database contains an unsafe sandbox volume name"
    fi
    # OpenSandbox may have already removed a volume while reconciling a failed
    # sandbox. Reset remains idempotent and still targets only DB-owned names.
    docker volume rm "$volume" >/dev/null 2>&1 || true
  done <<< "$volumes"

  compose down -v --remove-orphans
  rm -rf -- "$RUNTIME_DIR"
}

main() {
  local command=${1:---help}
  if [[ "$command" == "--help" || "$command" == "-h" ]]; then
    usage
    return 0
  fi
  shift || true
  validate_project
  validate_gate
  case "$command" in
    up) command_up "$@" ;;
    status) command_status "$@" ;;
    logs) command_logs "$@" ;;
    restart) command_restart "$@" ;;
    down) command_down "$@" ;;
    reset) command_reset "$@" ;;
    *) die_usage "unknown command: $command" ;;
  esac
}

main "$@"
