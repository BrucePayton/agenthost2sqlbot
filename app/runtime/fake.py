import asyncio

from app.errors import AppError
from app.runtime.base import (
    RuntimeCancelled,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeRequest,
    RuntimeResult,
)
from app.runtime.events import progress_event


class FakeAgentRuntime:
    def __init__(
        self,
        *,
        chunks: tuple[str, ...] = ("Fake response",),
        delay_seconds: float = 0,
        emit_tool: bool = False,
        emit_thinking: bool = False,
        fail_code: str | None = None,
        usage: dict | None = None,
    ) -> None:
        self.chunks = chunks
        self.delay_seconds = delay_seconds
        self.emit_tool = emit_tool
        self.emit_thinking = emit_thinking
        self.fail_code = fail_code
        self.usage = usage or {
            "input_tokens": 10,
            "uncached_input_tokens": 10,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_input_tokens": 10,
            "output_tokens": 20,
            "model_api_turns": 1,
            "frontend_tool_calls": 0,
            "tool_search_calls": 0,
            "tool_set_changes": 0,
            "catalog_digest_changes": 0,
            "cost_usd": 0.001,
        }
        self.requests: list[RuntimeRequest] = []

    @property
    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            protocol_version="1",
            supports_resume=True,
            supports_interrupt=True,
            supports_auto_memory=True,
            supports_mcp=True,
            supports_skills=True,
        )

    async def run(self, request: RuntimeRequest, cancel_event: asyncio.Event):
        self.requests.append(request)
        if self.fail_code:
            raise AppError(self.fail_code, "The fake runtime failed.", 502)
        yield progress_event("waiting_model", "已提交请求，等待模型响应")
        if self.emit_thinking:
            yield RuntimeEvent(
                "message.assistant.thinking.delta",
                {"index": 0, "text": "先确认这个文件存不存在"},
                "assistant",
            )
            yield RuntimeEvent(
                "message.assistant.thinking",
                {
                    "index": 0,
                    "started_at": "2026-08-30T01:00:00+00:00",
                    "completed_at": "2026-08-30T01:00:12+00:00",
                    "duration_ms": 12_000,
                    "chars": 13,
                    "truncated": False,
                },
                "assistant",
            )
        if self.emit_tool:
            yield RuntimeEvent(
                "tool.started",
                {"tool_use_id": "fake-tool", "name": "Read", "input_preview": "{}"},
                "tool",
            )
            yield RuntimeEvent(
                "tool.completed",
                {
                    "tool_use_id": "fake-tool",
                    "name": "Read",
                    "is_error": False,
                    "output_preview": "fake tool result",
                    "duration_ms": 0,
                },
                "tool",
            )
        for index, chunk in enumerate(self.chunks):
            if cancel_event.is_set():
                raise RuntimeCancelled()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if index == 0:
                yield progress_event("generating", "模型正在生成回复")
            yield RuntimeEvent(
                "message.assistant.delta", {"text": chunk}, "assistant"
            )
        if cancel_event.is_set():
            raise RuntimeCancelled()
        full_text = "".join(self.chunks)
        yield RuntimeEvent(
            "message.assistant.completed", {"text": full_text}, "assistant"
        )
        yield progress_event("finalizing", "正在保存执行结果")
        yield RuntimeEvent(
            "usage.updated",
            dict(self.usage),
            "system",
        )
        yield RuntimeResult(
            status="completed",
            claude_session_id=request.claude_session_id
            or f"fake-{request.platform_session_id}",
            duration_ms=1,
        ).to_event()
