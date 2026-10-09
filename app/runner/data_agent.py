"""Forward governed SQL questions over the existing Runner/Worker control channel.

The sandbox receives SQL and rows, never database credentials or Host API tokens.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from app.data_mcp.schemas import DataAskResult
from app.errors import AppError
from app.runtime.contracts import RuntimeEvent


class RunnerDataAgentService:
    def __init__(self, events: asyncio.Queue, control_dir: Path = Path('/session/control')):
        self.events = events
        self.lock = asyncio.Lock()
        self.control_dir = control_dir

    async def ask(self, *, question: str, context_mode: str = 'continue', **_host_fields):
        async with self.lock:
            return await self._ask(question=question, context_mode=context_mode)

    async def call_table_tool(self, *, name: str, arguments: dict, **_host_fields):
        async with self.lock:
            return await self._exchange("data.table.request", {"name": name, "arguments": arguments})

    async def _ask(self, *, question: str, context_mode: str):
        body = await self._exchange("data.ask.request", {"question": question, "contextMode": context_mode})
        return DataAskResult.model_validate(body)

    async def _exchange(self, event_type: str, payload: dict):
        request_id = uuid.uuid4().hex
        path = self.control_dir / f'data-response-{request_id}.json'
        await self.events.put(RuntimeEvent(event_type, {'request_id': request_id, **payload}, 'system'))
        try:
            async with asyncio.timeout(300):
                while True:
                    try:
                        body = json.loads(path.read_text())
                        break
                    except (FileNotFoundError, json.JSONDecodeError):
                        await asyncio.sleep(.1)
            if 'error' in body:
                raise AppError(body['error']['code'], body['error']['message'], 502)
            return body
        finally:
            path.unlink(missing_ok=True)


class DataAgentRelayRuntime:
    def __init__(self, settings):
        from app.runtime.claude import ClaudeAgentRuntime
        self.events = asyncio.Queue()
        self.service = RunnerDataAgentService(self.events)
        self.runtime = ClaudeAgentRuntime(settings, data_agent_service=self.service)

    async def run(self, request, cancel_event):
        async def produce():
            try:
                async for event in self.runtime.run(request, cancel_event):
                    await self.events.put(event)
            except BaseException as exc:  # noqa: BLE001 - forward producer cancellation and errors to the consumer.
                await self.events.put(exc)
            finally:
                await self.events.put(None)
        task = asyncio.create_task(produce())
        try:
            while True:
                event = await self.events.get()
                if event is None:
                    break
                if isinstance(event, BaseException):
                    raise event
                yield event
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
