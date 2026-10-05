from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.runner.protocol import RunnerFrame, RunnerFrameValidator, RunnerRequest
from app.runtime.contracts import AgentRuntimePort, RuntimeCancelled, RuntimeEvent


@dataclass(frozen=True)
class RunnerOutcome:
    status: Literal["completed", "cancelled", "failed"]


def _frame_for_event(sequence: int, event: RuntimeEvent) -> RunnerFrame:
    if event.type == "turn.progress":
        kind = "phase"
    elif event.type == "message.assistant.delta":
        kind = "assistant_delta"
    elif event.type.startswith("tool."):
        kind = "tool_event"
    elif event.type.startswith("artifact."):
        kind = "artifact"
    elif event.type == "usage.updated":
        kind = "usage"
    elif event.type == "runtime.result":
        kind = "terminal"
    else:
        kind = "phase"
    payload = dict(event.payload)
    payload.setdefault("event_type", event.type)
    return RunnerFrame(
        sequence=sequence,
        kind=kind,
        payload=payload,
        role=event.role,
    )


async def run_request(
    request: RunnerRequest,
    runtime: AgentRuntimePort,
    emit: Callable[[str], None],
    cancel_event: asyncio.Event,
) -> RunnerOutcome:
    validator = RunnerFrameValidator()
    sequence = 0
    terminal_seen = False

    def emit_frame(frame: RunnerFrame) -> None:
        nonlocal terminal_seen
        validator.accept(frame)
        emit(frame.to_line())
        terminal_seen = frame.kind == "terminal"

    try:
        async for event in runtime.run(request.to_runtime_request(), cancel_event):
            sequence += 1
            frame = _frame_for_event(sequence, event)
            emit_frame(frame)
        if not terminal_seen:
            sequence += 1
            emit_frame(
                RunnerFrame(
                    sequence=sequence,
                    kind="terminal",
                    payload={
                        "status": "failed",
                        "code": "runtime_incomplete",
                        "message": "The sandbox runtime ended without a result.",
                    },
                )
            )
            return RunnerOutcome("failed")
        return RunnerOutcome("completed")
    except RuntimeCancelled:
        sequence += 1
        emit_frame(
            RunnerFrame(
                sequence=sequence,
                kind="terminal",
                payload={"status": "cancelled"},
            )
        )
        return RunnerOutcome("cancelled")
    except Exception:  # noqa: BLE001 - the trust boundary emits only a stable error
        if terminal_seen:
            return RunnerOutcome("failed")
        sequence += 1
        emit_frame(
            RunnerFrame(
                sequence=sequence,
                kind="terminal",
                payload={
                    "status": "failed",
                    "code": "runtime_failed",
                    "message": "The sandbox runtime failed.",
                },
            )
        )
        return RunnerOutcome("failed")
