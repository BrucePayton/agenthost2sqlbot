import json
from datetime import UTC, datetime
from typing import Any

from app.runtime.base import RuntimeEvent


def progress_event(phase: str, message: str) -> RuntimeEvent:
    return RuntimeEvent(
        "turn.progress",
        {
            "phase": phase,
            "message": message,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        "system",
    )


def preview(value: Any, limit: int = 8_000) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]"
