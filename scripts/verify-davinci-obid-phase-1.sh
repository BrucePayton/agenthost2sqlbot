#!/usr/bin/env bash
set -euo pipefail

compose=(
  docker compose
  -p davinci-obid-phase1
  -f compose.integration.yaml
)
test_data_dir="$(mktemp -d "${TMPDIR:-/tmp}/davinci-obid-phase1.XXXXXX")"

cleanup() {
  "${compose[@]}" down -v
  rm -rf "$test_data_dir"
}
trap cleanup EXIT

"${compose[@]}" up -d --wait postgres

export TEST_POSTGRES_URL="postgresql+asyncpg://workspace:workspace@127.0.0.1:${POSTGRES_PORT:-55432}/workspace_test"
export DATABASE_URL="$TEST_POSTGRES_URL"
export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-https://proxy.example.test}"
export WORKSPACES_ROOT="${WORKSPACES_ROOT:-$PWD/workspaces}"
export APP_DATA_DIR="$test_data_dir"
: "${TEST_POSTGRES_URL:?TEST_POSTGRES_URL was not initialized}"

APP_ENV=uat \
APP_IDENTITY_MODE=obid \
APP_RUNTIME_MODE=local_inline \
DAVINCI_LOCAL_INTEGRATION=1 \
DAVINCI_LOCAL_PUBLIC_ORIGIN=https://uat-agent.example.test \
DAVINCI_LOCAL_PARENT_ORIGINS='["https://davinci-uat.example.test"]' \
uv run python -m app.db.cli
APP_ENV=test APP_IDENTITY_MODE=mock APP_RUNTIME_MODE=execution_disabled uv run pytest \
  tests/test_config.py \
  tests/test_obid.py \
  tests/test_local_davinci_bootstrap.py \
  tests/test_workspace_provisioner.py \
  tests/test_workspace_template_resolver.py \
  -q
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" uv run pytest \
  tests/integration/test_personal_workspace_race.py \
  tests/integration/test_obid_postgres_isolation.py \
  -q
npm run test:js

APP_ENV=uat \
APP_IDENTITY_MODE=obid \
APP_RUNTIME_MODE=local_inline \
DAVINCI_LOCAL_INTEGRATION=1 \
DAVINCI_LOCAL_PUBLIC_ORIGIN=https://uat-agent.example.test \
DAVINCI_LOCAL_PARENT_ORIGINS='["https://davinci-uat.example.test"]' \
uv run python - <<'PY'
import asyncio

import httpx

from app.config import Settings
from app.main import create_app
from app.runtime.fake import FakeAgentRuntime


async def main() -> None:
    app = create_app(settings=Settings(), runtime=FakeAgentRuntime())
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["identity_mode"] == "obid"
        assert health.json()["deployment_constraint"] == "single_instance"
        assert health.json()["security_marker"] == "UAT_OBID_UNVERIFIED"
        bootstrap = await client.post(
            "/agent-api/session/bootstrap",
            json={
                "parentOrigin": "https://davinci-uat.example.test",
                "protocolVersion": "agui-native-v2",
                "obId": "uat-smoke-actor",
            },
        )
        assert bootstrap.status_code == 200


asyncio.run(main())
PY
