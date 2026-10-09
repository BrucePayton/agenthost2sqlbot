from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import secrets
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth.models import IdentityContext
from app.config import Settings
from app.data_mcp.asset_mcp import (
    AssetAuthorizationPort,
    AssetMcpClient,
    AuthorizedAsset,
)
from app.data_mcp.providers import KnowledgeMysqlProvider, project_fields
from app.data_mcp.repository import DataAgentRepository
from app.data_mcp.schemas import (
    AuthorizedDataset,
    DataAgentCreate,
    DataAgentDatasetOut,
    DataAgentHealth,
    DataAgentOut,
    DataAgentUpdate,
    DataAskResult,
)
from app.data_mcp.starrocks import StarRocksPocProvider
from app.db.base import Database
from app.db.models import (
    DataAgentDatasetRecord,
    DataAgentRecord,
    DataAgentResultRecord,
    DataAgentTicketRecord,
)
from app.errors import AppError
from app.sqlbot.client import SQLBotClient


class DataAgentService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        sqlbot: SQLBotClient | None = None,
        asset_authorization: AssetAuthorizationPort | None = None,
    ) -> None:
        from app.sessions.locks import SessionLockRegistry
        self.context_locks = SessionLockRegistry()
        from app.data_mcp.table_mcp import TableMcpClient
        self.table_mcp = TableMcpClient(settings)
        self.settings = settings
        self.repository = DataAgentRepository(database)
        self.provider = (StarRocksPocProvider(settings) if settings.data_agent_provider == "starrocks_poc" else KnowledgeMysqlProvider(settings))
        self.sqlbot = sqlbot or SQLBotClient(settings)
        if settings.data_agent_starrocks_metadata_source == "service2":
            if settings.sqlbot_catalog_base_url is None or settings.sqlbot_catalog_admin_password is None:
                raise ValueError("Service-2 catalog URL and admin credentials are required")
            catalog_settings = settings.model_copy(update={
                "sqlbot_base_url": settings.sqlbot_catalog_base_url,
                "sqlbot_admin_account": settings.sqlbot_catalog_admin_account,
                "sqlbot_admin_password": settings.sqlbot_catalog_admin_password,
            })
            self.catalog_sqlbot = SQLBotClient(catalog_settings)
        else:
            self.catalog_sqlbot = self.sqlbot
        self.asset_authorization = asset_authorization or AssetMcpClient(settings)

    async def call_table_tool(self, *, host_session_key: str, name: str, arguments: dict) -> dict:
        from app.db.models import SessionRecord, UserRecord
        async with self.repository.database.session() as db:
            session = await db.get(SessionRecord, host_session_key)
            if session is None or session.workspace_id != "data-question" or session.data_backend != "mcp":
                raise AppError("table_mcp_forbidden", "当前会话未启用 MCP 取数。", 403)
            user = await db.get(UserRecord, session.created_by)
            if user is None:
                raise AppError("table_mcp_forbidden", "会话用户不存在。", 403)
            subject = user.external_subject
        return await self.table_mcp.call_tool(subject, name, arguments)

    async def aclose(self) -> None:
        await self.sqlbot.aclose()
        if self.catalog_sqlbot is not self.sqlbot:
            await self.catalog_sqlbot.aclose()
        await self.asset_authorization.aclose()

    def health(self) -> DataAgentHealth:
        return DataAgentHealth(
            enabled=self.settings.data_agent_enabled,
            provider=self.settings.data_agent_provider,
            runtime_configured=(self.provider.runtime_configured if isinstance(self.provider, StarRocksPocProvider) else bool(self.settings.mysql_host and self.settings.mysql_readonly_user and self.settings.mysql_readonly_password)),
            custom_model=self.settings.sqlbot_custom_model,
            sqlbot_url=str(self.settings.sqlbot_base_url),
            sqlbot_admin_configured=self.settings.sqlbot_admin_password is not None,
            sqlbot_secret_configured=self.settings.sqlbot_secret_key is not None,
            mysql_runtime_configured=bool(
                self.settings.mysql_host
                and self.settings.mysql_readonly_user
                and self.settings.mysql_readonly_password
            ),
            asset_mcp_url=str(self.settings.asset_mcp_url),
            asset_mapping_configured=self.provider.configured,
            callback_source_restricted=bool(self.settings.data_agent_callback_cidrs),
            callback_url=(
                f"{str(self.settings.data_agent_public_base_url).rstrip('/')}/api/sqlbot/datasources"
            ),
        )

    def require_identity(self, identity: IdentityContext) -> str:
        self._require_enabled()
        self.provider.require_subject(identity.external_subject)
        return identity.external_subject

    async def list_datasets(self, identity: IdentityContext) -> list[AuthorizedDataset]:
        subject = self.require_identity(identity)
        if not self.provider.configured:
            return []
        if isinstance(self.provider, StarRocksPocProvider):
            return self.provider.list_authorized(subject)
        assets = await self._authorize_refs(subject, [self.provider.runtime.ref])
        return self.provider.list_authorized(subject, assets.get(self.provider.runtime.ref))

    async def sqlbot_catalog_sources(self, identity: IdentityContext) -> list[dict[str, Any]]:
        self._require_sqlbot_catalog_identity(identity)
        if not isinstance(self.provider, StarRocksPocProvider):
            raise AppError("sqlbot_catalog_unavailable", "StarRocks catalog is unavailable.", 404)
        result = []
        for group in ("dw", "dm", "rpt"):
            source_id, remote = await self._sqlbot_catalog_source(group)
            tables = await self.catalog_sqlbot.datasource_tables(source_id)
            selected = [table for table in tables if table.get("checked")]
            expected = self.provider.sources[f"starrocks:hive.{group}"]
            result.append({
                "group": group, "datasource_id": source_id, "database": f"hive.{group}",
                "name": remote["name"], "description": remote.get("description") or "",
                "type": "StarRocks", "status": remote.get("status"),
                "selected_table_count": len(selected),
                "matches_configured_tables": {table.get("table_name") for table in selected} ==
                                             {table["name"] for table in expected["tables"]},
            })
        return result

    async def sqlbot_catalog_connection(self, identity: IdentityContext, group: str) -> dict[str, Any]:
        self._require_sqlbot_catalog_identity(identity)
        source_id, _ = await self._sqlbot_catalog_source(group)
        return {"group": group, "datasource_id": source_id,
                "connected": await self.catalog_sqlbot.datasource_connected(source_id)}

    async def sqlbot_catalog_tables(self, identity: IdentityContext, group: str) -> list[dict[str, Any]]:
        self._require_sqlbot_catalog_identity(identity)
        source_id, _ = await self._sqlbot_catalog_source(group)
        tables = await self.catalog_sqlbot.datasource_tables(source_id)
        return [
            {"id": int(table["id"]), "name": table["table_name"],
             "comment": table.get("custom_comment") or table.get("table_comment") or "",
             "custom_comment": table.get("custom_comment") or "",
             "database_comment": table.get("table_comment") or ""}
            for table in tables if table.get("checked")
        ]

    async def sqlbot_catalog_table_schema(
        self, identity: IdentityContext, group: str, table_name: str, *, live: bool = True
    ) -> dict[str, Any]:
        self._require_sqlbot_catalog_identity(identity)
        source_id, _ = await self._sqlbot_catalog_source(group)
        tables = await self.catalog_sqlbot.datasource_tables(source_id)
        table = next((item for item in tables if item.get("checked") and item.get("table_name") == table_name), None)
        if table is None:
            raise AppError("sqlbot_table_not_selected", "Table is not selected in this datasource.", 404)
        stored = await self.catalog_sqlbot.datasource_fields(source_id, int(table["id"]))
        live_fields = await self.catalog_sqlbot.datasource_live_fields(source_id, table_name) if live else []
        live_by_name = {field.get("fieldName"): field for field in live_fields}
        fields = []
        for field in stored:
            if not field.get("checked"):
                continue
            name = field["field_name"]
            item = {
                "name": name, "type": field.get("field_type") or "",
                "comment": field.get("custom_comment") or field.get("field_comment") or "",
                "custom_comment": field.get("custom_comment") or "",
                "database_comment": field.get("field_comment") or "",
            }
            if live:
                actual = live_by_name.get(name)
                item["in_live_schema"] = actual is not None
                item["live_type"] = actual.get("fieldType") if actual else None
                item["live_comment"] = actual.get("fieldComment") if actual else None
            fields.append(item)
        return {
            "group": group, "datasource_id": source_id, "database": f"hive.{group}",
            "table": table_name, "comment": table.get("custom_comment") or table.get("table_comment") or "",
            "live_checked": live, "fields": fields,
        }

    async def _sqlbot_catalog_source(self, group: str) -> tuple[int, dict[str, Any]]:
        if not isinstance(self.provider, StarRocksPocProvider) or not self.provider.configured or group not in {"dw", "dm", "rpt"}:
            raise AppError("sqlbot_catalog_unavailable", "StarRocks datasource is unavailable.", 404)
        source_id = self.settings.data_agent_sqlbot_source_ids.get(group)
        if source_id is None or source_id <= 0:
            raise AppError("sqlbot_catalog_unconfigured", "SQLBot datasource mapping is not configured.", 503)
        sources = await self.catalog_sqlbot.datasource_list()
        remote = next((item for item in sources if int(item.get("id") or 0) == source_id), None)
        expected = self.provider.sources[f"starrocks:hive.{group}"]
        if remote is None or remote.get("type") != "starrocks" or remote.get("name") != expected["name"]:
            raise AppError("sqlbot_catalog_mismatch", "SQLBot datasource mapping does not match the configured catalog.", 502)
        return source_id, remote

    def _require_sqlbot_catalog_identity(self, identity: IdentityContext) -> None:
        if not self.settings.data_agent_sqlbot_catalog_enabled:
            raise AppError("sqlbot_catalog_unavailable", "SQLBot catalog is not enabled.", 503)
        self.provider.require_subject(identity.external_subject)

    async def list_agents(self, identity: IdentityContext) -> list[DataAgentOut]:
        self.require_identity(identity)
        return [self._agent_out(agent, datasets) for agent, datasets in await self.repository.list_agents(identity.user_id)]

    async def get_agent(self, agent_id: str, identity: IdentityContext) -> DataAgentOut:
        self.require_identity(identity)
        agent, datasets = await self.repository.get_agent(agent_id, identity.user_id)
        return self._agent_out(agent, datasets)

    async def create_agent(self, payload: DataAgentCreate, identity: IdentityContext) -> DataAgentOut:
        subject = self.require_identity(identity)
        datasets = await self._resolve_datasets(subject, payload.dataset_refs)
        agent, records = await self.repository.create_agent(
            created_by=identity.user_id,
            name=payload.name,
            description=payload.description,
            datasets=datasets,
        )
        await self.repository.audit(
            user_subject=subject, action="data_agent.create", status="success",
            agent_id=agent.id, details={"dataset_count": len(records)},
        )
        return self._agent_out(agent, records)

    async def update_agent(
        self, agent_id: str, payload: DataAgentUpdate, identity: IdentityContext
    ) -> DataAgentOut:
        subject = self.require_identity(identity)
        datasets = await self._resolve_datasets(subject, payload.dataset_refs)
        agent, records = await self.repository.update_agent(
            agent_id=agent_id,
            created_by=identity.user_id,
            name=payload.name,
            description=payload.description,
            datasets=datasets,
        )
        await self.repository.audit(
            user_subject=subject, action="data_agent.update", status="success",
            agent_id=agent.id, details={"dataset_count": len(records)},
        )
        return self._agent_out(agent, records)

    async def publish_agent(self, agent_id: str, identity: IdentityContext) -> DataAgentOut:
        subject = self.require_identity(identity)
        agent, datasets = await self.repository.get_agent(agent_id, identity.user_id)
        await self._authorize_records(subject, datasets)
        try:
            assistant_id = await self.sqlbot.ensure_assistant(
                assistant_id=agent.sqlbot_assistant_id,
                agent_id=agent.id,
                name=agent.name,
                description=agent.description,
            )
        except AppError as exc:
            await self.repository.set_publication(
                agent_id=agent.id, created_by=identity.user_id, status=agent.status,
                assistant_id=agent.sqlbot_assistant_id, sync_status="failed",
                sync_error=exc.message[:500],
            )
            await self.repository.audit(
                user_subject=subject, action="data_agent.publish", status="failed",
                agent_id=agent.id, details={"error_code": exc.code},
            )
            raise
        agent = await self.repository.set_publication(
            agent_id=agent.id, created_by=identity.user_id, status="published",
            assistant_id=assistant_id, sync_status="synced",
        )
        await self.repository.audit(
            user_subject=subject, action="data_agent.publish", status="success",
            agent_id=agent.id, details={"assistant_id": assistant_id},
        )
        return self._agent_out(agent, datasets)

    async def disable_agent(self, agent_id: str, identity: IdentityContext) -> DataAgentOut:
        subject = self.require_identity(identity)
        agent, datasets = await self.repository.get_agent(agent_id, identity.user_id)
        agent = await self.repository.set_publication(
            agent_id=agent.id, created_by=identity.user_id, status="disabled",
            assistant_id=agent.sqlbot_assistant_id, sync_status=agent.sqlbot_sync_status,
        )
        await self.repository.audit(
            user_subject=subject, action="data_agent.disable", status="success", agent_id=agent.id,
        )
        return self._agent_out(agent, datasets)

    async def delete_agent(self, agent_id: str, identity: IdentityContext) -> None:
        subject = self.require_identity(identity)
        await self.repository.delete_agent(agent_id, identity.user_id)
        await self.repository.audit(
            user_subject=subject, action="data_agent.delete", status="success", agent_id=None,
            details={"agent_id": agent_id},
        )

    async def bind_host_session(
        self, *, agent_id: str, host_session_key: str, identity: IdentityContext
    ) -> None:
        subject = self.require_identity(identity)
        agent, _ = await self.repository.get_agent(agent_id, identity.user_id)
        if agent.status != "published":
            raise AppError("data_agent_not_published", "Publish the data agent before opening it.", 409)
        await self.repository.bind_host_session(
            agent_id=agent_id, user_subject=subject, host_session_key=host_session_key
        )

    async def resolve_session_agent(self, *, host_session_key: str, user_subject: str) -> str:
        self.provider.require_subject(user_subject)
        return (await self.repository.get_ask_session(host_session_key, user_subject)).agent_id

    async def callback_payload(
        self, ticket_value: str, *, source_ip: str | None = None
    ) -> list[dict[str, Any]]:
        self._require_enabled()
        self._require_callback_source(source_ip)
        token_hash = _hash(ticket_value)
        ticket = await self.repository.consume_ticket(
            token_hash, max_callbacks=self.settings.data_agent_max_ticket_callbacks
        )
        agent, datasets = await self.repository.get_agent(ticket.agent_id)
        if agent.status != "published":
            raise AppError("data_agent_not_published", "Data agent is not published.", 409)
        try:
            authorized_assets = await self._authorize_records(
                ticket.user_subject, datasets
            )
        except AppError as exc:
            await self.repository.audit(
                user_subject=ticket.user_subject,
                action="data_agent.callback",
                status="failed",
                agent_id=agent.id,
                details={"error_code": exc.code, "callback_count": ticket.callback_count},
            )
            raise
        if isinstance(self.provider, StarRocksPocProvider):
            refs = [d.dataset_ref for d in datasets]
            payload = self.provider.callback_payload(ticket.user_subject, refs)
            if self.settings.data_agent_starrocks_metadata_source == "service2":
                payload = await self._service2_dynamic_payload(payload)
            await self.repository.audit(
                user_subject=ticket.user_subject, action="data_agent.callback", status="success",
                agent_id=agent.id, details={"provider": "starrocks_poc", "callback_count": ticket.callback_count,
                    "dataset_refs": [d.dataset_ref for d in datasets],
                    "table_count": sum(len(d["tables"]) for d in payload),
                    "field_count": sum(len(t["fields"]) for d in payload for t in d["tables"])},
            )
            return payload
        tables = []
        fields_used: list[str] = []
        for dataset in datasets:
            runtime = self.provider.resolve_authorized(
                ticket.user_subject,
                dataset.dataset_ref,
                authorized_assets.get(dataset.dataset_ref),
            )
            authorized = [field.model_dump() for field in runtime.fields]
            projected = project_fields(authorized, ticket.question, ticket.projection_limit)
            fields_used.extend(str(field["name"]) for field in projected)
            tables.append({
                "id": _stable_numeric_id(f"{agent.id}:{dataset.dataset_ref}"),
                "name": runtime.virtual_table_name,
                "comment": runtime.description,
                "sql": runtime.base_sql,
                "fields": [
                    {
                        "id": _stable_numeric_id(f"{dataset.dataset_ref}:{field['name']}"),
                        "name": field["name"],
                        "type": field["type"],
                        "comment": str(field.get("comment") or "")[:40],
                    }
                    for field in projected
                ],
            })
        connection = self.provider.connection_payload()
        await self.repository.audit(
            user_subject=ticket.user_subject,
            action="data_agent.callback",
            status="success",
            agent_id=agent.id,
            details={
                "callback_count": ticket.callback_count,
                "dataset_refs": [dataset.dataset_ref for dataset in datasets],
                "fields_exposed": fields_used,
                "metadata_versions": {
                    ref: asset.metadata_version
                    for ref, asset in authorized_assets.items()
                },
            },
        )
        return [{
            "id": _stable_numeric_id(agent.id),
            "name": agent.name,
            "description": agent.description,
            **connection,
            "tables": tables,
        }]

    async def _service2_dynamic_payload(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use service-2 selected metadata; local runtime supplies DB credentials."""
        sources = await self.catalog_sqlbot.datasource_list()
        by_id = {int(item.get("id") or 0): item for item in sources}
        for source in payload:
            group = source["dataBase"].removeprefix("hive.")
            source_id = self.settings.data_agent_sqlbot_source_ids.get(group)
            remote = by_id.get(source_id or 0)
            if (remote is None or remote.get("type") != "starrocks"
                    or remote.get("name") != source["name"]):
                raise AppError("sqlbot_catalog_mismatch", "Service-2 datasource mapping changed.", 502)
            selected = [table for table in await self.catalog_sqlbot.datasource_tables(source_id)
                        if table.get("checked")]
            expected = {table["name"]: table for table in source["tables"]}
            if {table.get("table_name") for table in selected} != set(expected):
                raise AppError("sqlbot_catalog_mismatch", "Service-2 selected tables changed.", 502)
            semaphore = asyncio.Semaphore(8)
            async def bounded(table: dict[str, Any], *, source_id: int = source_id,
                              database: str = source["dataBase"], expected: dict = expected,
                              semaphore: asyncio.Semaphore = semaphore) -> dict[str, Any]:
                async with semaphore:
                    return await self._service2_table_payload(source_id, database, table, expected)
            source["tables"] = await asyncio.gather(*(bounded(table) for table in selected))
        return payload

    async def _service2_table_payload(self, source_id: int, database: str, table: dict[str, Any],
                                      expected: dict[str, dict[str, Any]]) -> dict[str, Any]:
        name = str(table["table_name"])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise AppError("sqlbot_catalog_invalid", "Service-2 table name is invalid.", 502)
        stored = await self.catalog_sqlbot.datasource_fields(source_id, int(table["id"]))
        selected_fields = [field for field in stored if field.get("checked")]
        expected_fields = {field["name"] for field in expected[name]["fields"]}
        if {field.get("field_name") for field in selected_fields} != expected_fields:
            raise AppError("sqlbot_catalog_mismatch", "Service-2 selected fields changed.", 502)
        return {
            "id": _stable_numeric_id(f"{database}:{name}"),
            "name": name,
            "comment": table.get("custom_comment") or table.get("table_comment") or "",
            "sql": "",
            "fields": [{
                "id": _stable_numeric_id(f"{database}:{name}:{field['field_name']}"),
                "name": field["field_name"],
                "type": field.get("field_type") or "",
                "comment": field.get("custom_comment") or field.get("field_comment") or "",
            } for field in selected_fields],
        }

    async def ask(self, *, user_subject: str, host_session_key: str, question: str,
                  requested_agent_id: str | None = None, context_mode: str = "continue",
                  on_event: Callable[[dict], Awaitable[None]] | None = None) -> DataAskResult:
        if context_mode not in {"new", "continue"}:
            raise AppError("invalid_context_mode", "Invalid SQLBot context mode.", 400)
        async with self.context_locks.acquire(host_session_key):
            self._require_enabled()
            self.provider.require_subject(user_subject)
            binding = await self.repository.get_ask_session(host_session_key, user_subject)
            if requested_agent_id and requested_agent_id != binding.agent_id:
                raise AppError("data_agent_session_conflict", "The requested agent does not match this session.", 409)
            actual_mode = "new" if context_mode == "new" or binding.sqlbot_chat_id is None else "continue"
            if context_mode == "new":
                await self.repository.set_sqlbot_chat(binding.id, None)
            result = await self._ask_in_context(
                user_subject=user_subject, host_session_key=host_session_key,
                question=question, requested_agent_id=requested_agent_id, on_event=on_event)
            result.evidence["contextMode"] = actual_mode
            return result

    async def _ask_in_context(
        self,
        *,
        user_subject: str,
        host_session_key: str,
        question: str,
        requested_agent_id: str | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
    ) -> DataAskResult:
        self._require_enabled()
        self.provider.require_subject(user_subject)
        ask_session = await self.repository.get_ask_session(host_session_key, user_subject)
        if requested_agent_id and requested_agent_id != ask_session.agent_id:
            raise AppError("data_agent_session_conflict", "The requested agent does not match this session.", 409)
        agent, datasets = await self.repository.get_agent(ask_session.agent_id)
        if agent.status != "published" or agent.sqlbot_assistant_id is None:
            raise AppError("data_agent_not_published", "Data agent is not ready for questions.", 409)
        try:
            return await self._ask_once(
                agent=agent, datasets=datasets, ask_session=ask_session,
                user_subject=user_subject, host_session_key=host_session_key,
                question=question, projection_limit=40, on_event=on_event,
            )
        except AppError as exc:
            if "cannot_generate" not in exc.message.casefold():
                await self.repository.audit(
                    user_subject=user_subject, action="data_agent.ask", status="failed",
                    agent_id=agent.id, details={"error_code": exc.code},
                )
                raise
            if on_event:
                await on_event({"type": "retry", "content": "首次未能生成 SQL，正在扩大字段候选范围重试。"})
            return await self._ask_once(
                agent=agent, datasets=datasets, ask_session=ask_session,
                user_subject=user_subject, host_session_key=host_session_key,
                question=question, projection_limit=100, on_event=on_event,
            )

    async def get_result(
        self, *, agent_id: str, result_id: str, identity: IdentityContext,
        require_fresh: bool = False,
    ) -> DataAskResult:
        subject = self.require_identity(identity)
        await self.repository.get_agent(agent_id, identity.user_id)
        record = await self.repository.get_result(
            result_id=result_id, user_subject=subject, agent_id=agent_id
        )
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        # Persisted history is a snapshot, not a fresh query. Only follow-up
        # actions expire; viewing an owned snapshot must survive cache TTL.
        if require_fresh and datetime.now(UTC) - created_at > timedelta(
            seconds=self.settings.data_agent_result_ttl_seconds
        ):
            raise AppError(
                "data_agent_result_expired",
                "该结果已超过后续分析的有效期，请重新问数；历史查询快照仍可查看。",
                410,
            )
        return self._result_out(
            record, row_limit=self.settings.data_agent_cache_row_limit
        )

    async def followup(self, *, agent_id: str, result_id: str, action: str,
                       identity: IdentityContext, on_event: Callable[[dict], Awaitable[None]]) -> dict:
        result = await self.get_result(agent_id=agent_id, result_id=result_id, identity=identity, require_fresh=True)
        record = await self.repository.get_result(result_id=result_id, user_subject=identity.external_subject, agent_id=agent_id)
        agent, datasets = await self.repository.get_agent(agent_id, identity.user_id)
        if agent.status != "published" or agent.sqlbot_assistant_id is None:
            raise AppError("data_agent_not_published", "Data agent is not ready.", 409)
        await self._authorize_records(identity.external_subject, datasets)
        ticket, token_hash = await self._issue_ticket(
            agent_id=agent_id, user_subject=identity.external_subject,
            host_session_key=record.host_session_key, question=record.question, projection_limit=40)
        try:
            return await self.sqlbot.followup(
                assistant_token=self.sqlbot.assistant_token(assistant_id=agent.sqlbot_assistant_id,
                    virtual_user_id=_stable_numeric_id(identity.external_subject)),
                ticket=ticket, record_id=result.record_id, action=action, on_event=on_event)
        finally:
            await self.repository.complete_ticket(token_hash)

    async def _ask_once(
        self,
        *,
        agent: DataAgentRecord,
        datasets: list[DataAgentDatasetRecord],
        ask_session: Any,
        user_subject: str,
        host_session_key: str,
        question: str,
        projection_limit: int,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
    ) -> DataAskResult:
        ticket, token_hash = await self._issue_ticket(
            agent_id=agent.id, user_subject=user_subject,
            host_session_key=host_session_key, question=question,
            projection_limit=projection_limit,
        )
        chat_id = ask_session.sqlbot_chat_id
        try:
            if on_event:
                await on_event({"type": "host-stage", "stage": "session", "content": "已绑定助手与会话，正在连接 SQLBot。"})
            assistant_token = self.sqlbot.assistant_token(
                assistant_id=int(agent.sqlbot_assistant_id),
                virtual_user_id=_stable_numeric_id(user_subject),
            )
            if chat_id is None:
                chat_id = await self.sqlbot.start_chat(
                    assistant_token=assistant_token, ticket=ticket,
                    datasource_id=(None if isinstance(self.provider, StarRocksPocProvider) else _stable_numeric_id(agent.id)), question=question,
                )
                await self.repository.set_sqlbot_chat(ask_session.id, chat_id)
            answer = await self.sqlbot.ask_question(
                assistant_token=assistant_token, ticket=ticket,
                chat_id=chat_id, question=question,
                **({"on_event": on_event} if on_event else {}),
            )
            if on_event:
                await on_event({"type": "host-stage", "stage": "result", "content": "正在读取查询结果与执行明细。"})
            record_data = await self.sqlbot.record_data(
                assistant_token=assistant_token, ticket=ticket,
                record_id=answer["record_id"],
            )
        finally:
            await self.repository.complete_ticket(token_hash)
        rows = list(record_data.get("data") or [])
        columns = [str(item) for item in (record_data.get("fields") or [])]
        if not columns and rows:
            columns = [str(item) for item in rows[0]]
        cache_limit = self.settings.data_agent_cache_row_limit
        cached_rows = rows[:cache_limit]
        fields_used = columns or [
            str(field["name"])
            for dataset in datasets
            for field in json.loads(dataset.fields_json)
        ]
        chart_hint = dict(answer.get("chart_hint") or {})
        result = DataAgentResultRecord(
            id=str(uuid.uuid4()), agent_id=agent.id, user_subject=user_subject,
            host_session_key=host_session_key, sqlbot_chat_id=chat_id,
            sqlbot_record_id=answer["record_id"], question=question,
            logical_sql=answer.get("sql") or "",
            columns_json=json.dumps(columns, ensure_ascii=False, separators=(",", ":")),
            rows_json=json.dumps(cached_rows, ensure_ascii=False, separators=(",", ":"), default=str),
            chart_hint_json=json.dumps(chart_hint, ensure_ascii=False, separators=(",", ":")),
            evidence_json=json.dumps({
                "agentId": agent.id,
                "datasetRefs": [dataset.dataset_ref for dataset in datasets],
                "sqlbotRecordId": answer["record_id"],
                "projectionLimit": projection_limit,
                "presentation": answer.get("presentation") or {},
            }, ensure_ascii=False, separators=(",", ":")),
            fields_used_json=json.dumps(fields_used, ensure_ascii=False, separators=(",", ":")),
            row_count=len(rows), truncated=len(rows) > cache_limit, created_at=datetime.now(UTC),
        )
        await self.repository.save_result(result)
        await self.repository.audit(
            user_subject=user_subject, action="data_agent.ask", status="success",
            agent_id=agent.id,
            details={
                "record_id": answer["record_id"],
                "row_count": len(rows),
                "projection_limit": projection_limit,
                "logical_sql_sha256": _hash(answer.get("sql") or ""),
            },
        )
        return self._result_out(
            result, row_limit=self.settings.data_agent_result_row_limit
        )

    async def _issue_ticket(
        self,
        *,
        agent_id: str,
        user_subject: str,
        host_session_key: str,
        question: str,
        projection_limit: int,
    ) -> tuple[str, str]:
        value = secrets.token_urlsafe(32)
        token_hash = _hash(value)
        now = datetime.now(UTC)
        await self.repository.add_ticket(DataAgentTicketRecord(
            token_hash=token_hash, agent_id=agent_id, user_subject=user_subject,
            host_session_key=host_session_key, question_hash=_hash(question),
            question=question, projection_limit=projection_limit,
            callback_count=0,
            expires_at=now + timedelta(seconds=self.settings.data_agent_ticket_ttl_seconds),
            created_at=now,
        ))
        return value, token_hash

    async def _resolve_datasets(
        self, subject: str, refs: list[str]
    ) -> list[dict[str, object]]:
        assets = await self._authorize_refs(subject, refs)
        resolved = []
        for ref in refs:
            dataset = self.provider.resolve_authorized(subject, ref, assets.get(ref))
            resolved.append({
                "dataset_ref": dataset.ref,
                "name": dataset.name,
                "description": dataset.description,
                "virtual_table_name": dataset.virtual_table_name,
                "datasource_id": dataset.datasource_id,
                "fields": [field.model_dump() for field in dataset.fields],
                "base_sql": dataset.base_sql,
            })
        return resolved

    async def _authorize_records(
        self, subject: str, datasets: list[DataAgentDatasetRecord]
    ) -> dict[str, AuthorizedAsset]:
        assets = await self._authorize_refs(
            subject, [dataset.dataset_ref for dataset in datasets]
        )
        for dataset in datasets:
            self.provider.resolve_authorized(
                subject, dataset.dataset_ref, assets.get(dataset.dataset_ref)
            )
        return assets

    async def _authorize_refs(
        self, subject: str, refs: list[str]
    ) -> dict[str, AuthorizedAsset]:
        if isinstance(self.provider, StarRocksPocProvider):
            for ref in refs:
                self.provider.resolve_authorized(subject, ref, None)
            return {}
        if self.settings.data_agent_allow_unverified_local_dataset:
            return {}
        if not self.settings.data_agent_runtime_asset_ref:
            raise AppError(
                "asset_mapping_not_verified",
                "No verified Asset MCP resource is mapped to the physical dataset.",
                503,
            )
        return await self.asset_authorization.authorize(
            user_subject=subject, dataset_refs=refs
        )

    def _require_callback_source(self, source_ip: str | None) -> None:
        cidrs = self.settings.data_agent_callback_cidrs
        if not cidrs:
            return
        try:
            address = ipaddress.ip_address(source_ip or "")
            allowed = any(
                address in ipaddress.ip_network(cidr, strict=False) for cidr in cidrs
            )
        except ValueError as exc:
            raise AppError(
                "sqlbot_callback_source_forbidden",
                "SQLBot callback source is not allowed.",
                403,
            ) from exc
        if not allowed:
            raise AppError(
                "sqlbot_callback_source_forbidden",
                "SQLBot callback source is not allowed.",
                403,
            )

    @staticmethod
    def _agent_out(agent: DataAgentRecord, datasets: list[DataAgentDatasetRecord]) -> DataAgentOut:
        return DataAgentOut(
            id=agent.id, name=agent.name, description=agent.description,
            status=agent.status, sqlbot_assistant_id=agent.sqlbot_assistant_id,
            sqlbot_sync_status=agent.sqlbot_sync_status,
            sqlbot_sync_error=agent.sqlbot_sync_error,
            datasets=[DataAgentDatasetOut(
                ref=item.dataset_ref, name=item.name, description=item.description,
                virtual_table_name=item.virtual_table_name,
                field_count=len(json.loads(item.fields_json)),
            ) for item in datasets],
            created_at=agent.created_at, updated_at=agent.updated_at,
        )

    @staticmethod
    def _result_out(
        record: DataAgentResultRecord, *, row_limit: int | None = None
    ) -> DataAskResult:
        rows = json.loads(record.rows_json)
        if row_limit is not None:
            rows = rows[:row_limit]
        evidence = json.loads(record.evidence_json)
        presentation = evidence.pop("presentation", {})
        return DataAskResult(
            resultId=record.id, agentId=record.agent_id,
            chatId=record.sqlbot_chat_id, recordId=record.sqlbot_record_id,
            sql=record.logical_sql, columns=json.loads(record.columns_json),
            rows=rows, rowCount=record.row_count,
            truncated=record.row_count > len(rows), chartHint=json.loads(record.chart_hint_json),
            evidence=evidence, fieldsUsed=json.loads(record.fields_used_json),
            presentation=presentation,
        )

    def _require_enabled(self) -> None:
        if not self.settings.data_agent_enabled:
            raise AppError("data_agent_disabled", "Data-agent integration is disabled.", 503)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_numeric_id(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:7], "big")
