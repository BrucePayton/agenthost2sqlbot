from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.api.dependencies import AppServices, Identity, get_services
from app.data_mcp.schemas import (
    AuthorizedDataset,
    DataAgentContextUpdate,
    DataAgentCreate,
    DataAgentHealth,
    DataAgentOpenOut,
    DataAgentOut,
    DataAgentQuestion,
    DataAgentUpdate,
    DataAskResult,
)

router = APIRouter(prefix="/api", tags=["data-agents"])
Services = Annotated[AppServices, Depends(get_services)]


@router.get("/data-agents/health", response_model=DataAgentHealth)
async def data_agent_health(services: Services) -> DataAgentHealth:
    return services.data_agents.health()


@router.get("/data-agents/datasets", response_model=list[AuthorizedDataset])
async def authorized_datasets(services: Services, identity: Identity) -> list[AuthorizedDataset]:
    return await services.data_agents.list_datasets(identity)


@router.get("/sqlbot/catalog")
async def sqlbot_catalog(services: Services, identity: Identity) -> list[dict[str, object]]:
    return await services.data_agents.sqlbot_catalog_sources(identity)


@router.get("/sqlbot/catalog/{group}/connection")
async def sqlbot_catalog_connection(group: str, services: Services, identity: Identity) -> dict[str, object]:
    return await services.data_agents.sqlbot_catalog_connection(identity, group)


@router.get("/sqlbot/catalog/{group}/tables")
async def sqlbot_catalog_tables(group: str, services: Services, identity: Identity) -> list[dict[str, object]]:
    return await services.data_agents.sqlbot_catalog_tables(identity, group)


@router.get("/sqlbot/catalog/{group}/tables/{table_name}/schema")
async def sqlbot_catalog_table_schema(
    group: str, table_name: str, services: Services, identity: Identity, live: bool = True
) -> dict[str, object]:
    return await services.data_agents.sqlbot_catalog_table_schema(identity, group, table_name, live=live)


@router.get("/data-agents", response_model=list[DataAgentOut])
async def list_agents(services: Services, identity: Identity) -> list[DataAgentOut]:
    return await services.data_agents.list_agents(identity)


@router.post("/data-agents", response_model=DataAgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: DataAgentCreate, services: Services, identity: Identity
) -> DataAgentOut:
    return await services.data_agents.create_agent(payload, identity)


@router.get("/data-agents/{agent_id}", response_model=DataAgentOut)
async def get_agent(agent_id: str, services: Services, identity: Identity) -> DataAgentOut:
    return await services.data_agents.get_agent(agent_id, identity)


@router.put("/data-agents/{agent_id}", response_model=DataAgentOut)
async def update_agent(
    agent_id: str, payload: DataAgentUpdate, services: Services, identity: Identity
) -> DataAgentOut:
    return await services.data_agents.update_agent(agent_id, payload, identity)


@router.post("/data-agents/{agent_id}/publish", response_model=DataAgentOut)
async def publish_agent(agent_id: str, services: Services, identity: Identity) -> DataAgentOut:
    return await services.data_agents.publish_agent(agent_id, identity)


@router.post("/data-agents/{agent_id}/disable", response_model=DataAgentOut)
async def disable_agent(agent_id: str, services: Services, identity: Identity) -> DataAgentOut:
    return await services.data_agents.disable_agent(agent_id, identity)


@router.delete("/data-agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, services: Services, identity: Identity) -> Response:
    await services.data_agents.delete_agent(agent_id, identity)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/data-agents/{agent_id}/open", response_model=DataAgentOpenOut)
async def open_agent(agent_id: str, services: Services, identity: Identity) -> DataAgentOpenOut:
    await services.workspace_access.require_member(identity, "data-question")
    session = await services.sessions.create("data-question", identity)
    await services.data_agents.bind_host_session(
        agent_id=agent_id, host_session_key=session.id, identity=identity
    )
    return DataAgentOpenOut(
        agent_id=agent_id,
        session_id=session.id,
        workbench_url=f"/?workspace=data-question&session={session.id}&agentId={agent_id}",
    )


@router.get(
    "/data-agents/{agent_id}/results/{result_id}", response_model=DataAskResult
)
async def get_result(
    agent_id: str, result_id: str, services: Services, identity: Identity
) -> DataAskResult:
    return await services.data_agents.get_result(
        agent_id=agent_id, result_id=result_id, identity=identity
    )


@router.get("/sqlbot/datasources")
async def sqlbot_datasources(
    request: Request,
    services: Services,
    ticket: Annotated[str | None, Header(alias="X-Davinci-Ticket")] = None,
) -> dict[str, object]:
    if not ticket:
        from app.errors import AppError

        raise AppError("data_ticket_missing", "X-Davinci-Ticket is required.", 401)
    data = await services.data_agents.callback_payload(
        ticket, source_ip=request.client.host if request.client else None
    )
    return {"code": 0, "message": "ok", "data": data}


@router.post("/data-agents/{agent_id}/ask", response_model=DataAskResult)
async def ask_agent(
    agent_id: str, payload: DataAgentQuestion, services: Services, identity: Identity
) -> DataAskResult:
    subject = services.data_agents.require_identity(identity)
    await services.data_agents.get_agent(agent_id, identity)
    return await services.data_agents.ask(
        user_subject=subject, host_session_key=payload.session_id,
        question=payload.question, requested_agent_id=agent_id,
    )


# Keep the existing JSON endpoint for tool clients; browser clients opt into SSE.
def _event_response(run):
    import asyncio
    import contextlib
    import json
    import logging

    from fastapi.responses import StreamingResponse

    from app.errors import AppError

    async def stream():
        queue = asyncio.Queue(maxsize=64)

        async def emit(event):
            await queue.put(event)

        async def producer():
            try:
                result = await run(emit)
                value = result.model_dump(by_alias=True, mode="json") if hasattr(result, "model_dump") else result
                await emit({"type": "result", "result": value})
            except AppError as exc:
                await emit({"type": "error", "code": exc.code, "content": exc.message,
                            **({"details": exc.details} if exc.details else {})})
            except Exception:
                logging.getLogger(__name__).exception("Data Agent stream failed")
                await emit({"type": "error", "code": "data_agent_stream_failed", "content": "问数失败，请稍后重试。"})
            await emit({"type": "done"})

        task = asyncio.create_task(producer())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
                if event["type"] == "done":
                    break
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@router.post("/data-agents/{agent_id}/ask/stream")
async def ask_agent_stream(agent_id: str, payload: DataAgentQuestion, services: Services, identity: Identity):
    subject = services.data_agents.require_identity(identity)
    await services.data_agents.get_agent(agent_id, identity)
    return _event_response(lambda emit: services.data_agents.ask(
        user_subject=subject, host_session_key=payload.session_id,
        question=payload.question, requested_agent_id=agent_id, on_event=emit,
    ))


@router.post("/data-agents/{agent_id}/results/{result_id}/actions/{action}")
async def data_agent_result_action(
    agent_id: str, result_id: str, action: str, services: Services, identity: Identity,
):
    from app.errors import AppError
    if action not in {"analysis", "predict", "recommend"}:
        raise AppError("invalid_data_action", "Unsupported action", 400)
    await services.data_agents.get_result(agent_id=agent_id, result_id=result_id, identity=identity, require_fresh=True)
    return _event_response(lambda emit: services.data_agents.followup(
        agent_id=agent_id, result_id=result_id, action=action, identity=identity, on_event=emit,
    ))


@router.get("/sessions/{session_id}/data-agent")
async def session_data_agent(session_id: str, services: Services, identity: Identity) -> dict:
    from app.errors import AppError
    session = await services.workspace_access.require_session_owner(identity, session_id)
    if session.workspace_id != "data-question":
        raise AppError("invalid_workspace", "Data Agent requires the data-question workspace.", 400)
    # Reading the selection must not require SQLBot or Service-2 configuration.
    result = {"backend": session.data_backend, "agent_id": None, "chat_id": None,
              "mcp_available": False, "mcp_unavailable_reason": None}
    try:
        services.data_agents.table_mcp.require_subject(identity.external_subject)
        result["mcp_available"] = True
    except AppError as exc:
        result["mcp_unavailable_reason"] = exc.message
    try:
        binding = await services.data_agents.repository.get_ask_session(session_id, identity.external_subject)
    except AppError as exc:
        if exc.code != "data_agent_session_not_found":
            raise
    else:
        result.update(agent_id=binding.agent_id, chat_id=binding.sqlbot_chat_id)
    return result


@router.put("/sessions/{session_id}/data-agent")
async def update_session_data_agent(session_id: str, payload: DataAgentContextUpdate,
                                    services: Services, identity: Identity) -> dict:
    current = await session_data_agent(session_id, services, identity)
    service = services.data_agents
    target = payload.backend or current["backend"]
    async with services.sessions.locks.acquire(session_id), service.context_locks.acquire(session_id):
        from app.errors import AppError
        if (await services.sessions.get(session_id)).status == "running":
            raise AppError("data_context_busy", "请等待当前回答完成后再切换取数方式。", 409)
        tools = None
        if target == "mcp":
            tools = await service.table_mcp.list_tools(identity.external_subject)
        elif payload.agent_id:
            agent = await service.get_agent(payload.agent_id, identity)
            if agent.status != "published":
                raise AppError("data_agent_not_published", "Publish the data agent first.", 409)
        await service.repository.configure_context(
            host_session_key=session_id, user_subject=identity.external_subject,
            agent_id=payload.agent_id, reset=payload.reset, backend=target, mcp_tools=tools)
    return await session_data_agent(session_id, services, identity)
