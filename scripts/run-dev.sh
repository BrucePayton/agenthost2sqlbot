#!/usr/bin/env bash
set -euo pipefail

exec uv run uvicorn app.main:app \
  --host "${APP_HOST:-127.0.0.1}" \
  --port "${APP_PORT:-8000}" \
  --reload

