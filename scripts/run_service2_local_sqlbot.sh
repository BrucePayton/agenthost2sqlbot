#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root/.runtime/starrocks-poc/service2-local-sqlbot.env"
manifest="$root/.runtime/starrocks-poc/manifest.json"

if [[ ! -f "$env_file" || ! -f "$manifest" ]]; then
  echo "Missing isolated 07D environment or complete 50-table manifest" >&2
  exit 1
fi

if docker container inspect agenthost-07d >/dev/null 2>&1; then
  docker start agenthost-07d >/dev/null
  echo "agenthost-07d is running at http://127.0.0.1:18765"
  exit 0
fi

docker run -d \
  --name agenthost-07d \
  --restart unless-stopped \
  --network workspace-agent-docker-web_default \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:18765:8000 \
  --env-file "$env_file" \
  -v "$root/app:/app/app:ro" \
  -v "$manifest:/app/starrocks-manifest.json:ro" \
  -v agenthost-07d-data:/var/lib/workspace-agent \
  agenthost:starrocks-catalog-20260930 \
  python -m app.cli >/dev/null

echo "agenthost-07d is running at http://127.0.0.1:18765"
