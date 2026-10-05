from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from app.config import Settings
from app.runner.claude_runner import run_request
from app.runner.protocol import MAX_REQUEST_BYTES, RunnerRequest
from app.runtime.claude import ClaudeAgentRuntime
from app.runtime.fake import FakeAgentRuntime


def _read_request(path: Path) -> RunnerRequest:
    if path.as_posix() != "/session/control/request.json":
        raise ValueError("Runner request path is fixed")
    data = path.read_bytes()
    if len(data) > MAX_REQUEST_BYTES:
        raise ValueError("Runner request exceeds the maximum size")
    return RunnerRequest.model_validate_json(data)


def _runtime_for(request: RunnerRequest):
    if request.runtime_kind == "fake":
        return FakeAgentRuntime()
    return ClaudeAgentRuntime(Settings())


async def _run(path: Path) -> int:
    request = _read_request(path)
    cancel_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, cancel_event.set)

    def emit(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    outcome = await run_request(
        request,
        _runtime_for(request),
        emit,
        cancel_event,
    )
    return 0 if outcome.status == "completed" else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    try:
        exit_code = asyncio.run(_run(args.request))
    except Exception:  # noqa: BLE001 - stdout must remain valid JSONL only
        exit_code = 2
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
