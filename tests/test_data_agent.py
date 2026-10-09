from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.auth.models import IdentityContext
from app.data_mcp.asset_mcp import AssetField, AuthorizedAsset
from app.data_mcp.providers import SYNC_JOB_FIELDS, SYNC_JOB_REF, project_fields
from app.data_mcp.schemas import DataAgentCreate
from app.data_mcp.service import DataAgentService
from app.db.base import Database
from app.db.models import DataAgentResultRecord, DataAgentTicketRecord, UserRecord
from app.errors import AppError
from app.sqlbot.client import SQLBotClient


class FakeSQLBot:
    def __init__(self, *, row_count: int = 1) -> None:
        self.service: DataAgentService | None = None
        self.tickets: list[str] = []
        self.callback_payloads: list[list[dict[str, object]]] = []
        self.row_count = row_count

    async def aclose(self) -> None:
        return None

    async def ensure_assistant(self, **_kwargs: object) -> int:
        return 7001

    def assistant_token(self, **_kwargs: object) -> str:
        return "signed-assistant-token"

    async def start_chat(self, *, ticket: str, **_kwargs: object) -> int:
        assert self.service is not None
        self.tickets.append(ticket)
        self.callback_payloads.append(await self.service.callback_payload(ticket))
        return 8001

    async def ask_question(self, *, ticket: str, **_kwargs: object) -> dict[str, object]:
        assert self.service is not None
        self.callback_payloads.append(await self.service.callback_payload(ticket))
        return {
            "record_id": 9001,
            "sql": "SELECT status, COUNT(*) AS total FROM sync_job GROUP BY status",
            "chart_hint": {"type": "bar", "x": "status", "y": ["total"]},
        }

    async def record_data(self, **_kwargs: object) -> dict[str, object]:
        return {
            "fields": ["status", "total"],
            "data": [
                {"status": f"status-{index}", "total": index + 1}
                for index in range(self.row_count)
            ],
        }


class FakeAssetAuthorization:
    def __init__(
        self,
        *,
        permission: bool = True,
        omit_field: str | None = None,
        required_filters: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.permission = permission
        self.omit_field = omit_field
        self.required_filters = required_filters
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def aclose(self) -> None:
        return None

    async def authorize(
        self, *, user_subject: str, dataset_refs: list[str]
    ) -> dict[str, AuthorizedAsset]:
        self.calls.append((user_subject, tuple(dataset_refs)))
        if not self.permission:
            raise AppError("data_agent_no_permission", "not authorized", 403)
        fields = tuple(
            AssetField(
                ref=f"{dataset_refs[0]}/{index}",
                name=field.name,
                data_type=field.type,
                description=field.comment,
                required=field.required,
                roles=(),
            )
            for index, field in enumerate(SYNC_JOB_FIELDS, start=1)
            if field.name != self.omit_field
        )
        return {
            ref: AuthorizedAsset(
                ref=ref,
                name="知识库同步任务",
                description="verified",
                fields=fields,
                required_filters=self.required_filters,
                metadata_version="test-v1",
            )
            for ref in dataset_refs
        }

    async def search_authorized(
        self, *, user_subject: str, query: str, limit: int = 20
    ) -> list[dict[str, object]]:
        return []

@pytest.mark.asyncio
async def test_data_agent_full_host_sqlbot_callback_chain(settings_factory) -> None:
    settings = settings_factory(
        data_agent_enabled=True,
        data_agent_subject="159358",
        mysql_host="mysql.example.internal",
        mysql_database="knowledge",
        mysql_readonly_user="sqlbot_poc_ro",
        mysql_readonly_password=SecretStr("read-only-test-password"),
        sqlbot_secret_key=SecretStr("test-secret"),
        data_agent_allow_unverified_local_dataset=True,
        data_agent_result_row_limit=1,
        data_agent_cache_row_limit=3,
        data_agent_result_ttl_seconds=30,
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("owner", "159358", "Data Owner")
    async with database.session() as session:
        session.add(
            UserRecord(
                id=identity.user_id,
                external_subject=identity.external_subject,
                display_name=identity.display_name,
                provider="mock",
            )
        )
        await session.commit()

    fake = FakeSQLBot(row_count=3)
    service = DataAgentService(database, settings, fake)  # type: ignore[arg-type]
    fake.service = service
    created = await service.create_agent(
        DataAgentCreate(
            name="同步任务分析",
            description="受治理问数",
            dataset_refs=[SYNC_JOB_REF],
        ),
        identity,
    )
    published = await service.publish_agent(created.id, identity)
    assert published.status == "published"
    assert published.sqlbot_assistant_id == 7001

    await service.bind_host_session(
        agent_id=created.id,
        host_session_key="host-session-1",
        identity=identity,
    )
    result = await service.ask(
        user_subject="159358",
        host_session_key="host-session-1",
        question="各状态的任务数是多少？",
    )
    assert result.rows == [{"status": "status-0", "total": 1}]
    assert result.row_count == 3
    assert result.truncated is True
    assert result.evidence["datasetRefs"] == [SYNC_JOB_REF]
    assert result.chart_hint == {"type": "bar", "x": "status", "y": ["total"]}
    assert len(fake.callback_payloads) == 2
    supplied = fake.callback_payloads[0][0]
    assert supplied["user"] == "sqlbot_poc_ro"
    assert supplied["tables"][0]["sql"].startswith("SELECT id, source_id")

    cached = await service.get_result(
        agent_id=created.id, result_id=result.result_id, identity=identity
    )
    assert len(cached.rows) == 3
    assert cached.truncated is False

    async with database.session() as session:
        stored_result = await session.get(DataAgentResultRecord, result.result_id)
        assert stored_result is not None
        stored_result.created_at = datetime.now(UTC) - timedelta(seconds=31)
        await session.commit()
    historical = await service.get_result(
        agent_id=created.id, result_id=result.result_id, identity=identity
    )
    assert historical.rows == cached.rows
    assert historical.chart_hint == cached.chart_hint
    with pytest.raises(AppError):
        await service.get_result(
            agent_id=created.id, result_id=result.result_id,
            identity=IdentityContext("other-owner", "159358", "Other"),
        )
    with pytest.raises(AppError) as denied:
        await service.repository.get_result(
            result_id=result.result_id, agent_id=created.id, user_subject="other-subject"
        )
    assert denied.value.code == "data_agent_result_not_found"
    with pytest.raises(AppError) as expired:
        await service.get_result(
            agent_id=created.id, result_id=result.result_id, identity=identity, require_fresh=True
        )
    assert expired.value.code == "data_agent_result_expired"

    raw_ticket = fake.tickets[0]
    async with database.session() as session:
        stored = (await session.scalars(select(DataAgentTicketRecord))).one()
    assert stored.token_hash != raw_ticket
    assert raw_ticket not in stored.token_hash
    assert stored.question == ""
    with pytest.raises(AppError, match="invalid or expired"):
        await service.callback_payload(raw_ticket)
    await database.dispose()


@pytest.mark.asyncio
async def test_callback_rechecks_asset_mcp_before_disclosing_connection(
    settings_factory,
) -> None:
    asset_ref = "warehouseTable:knowledge.sync_job"
    settings = settings_factory(
        data_agent_enabled=True,
        data_agent_subject="159358",
        data_agent_runtime_asset_ref=asset_ref,
        mysql_host="mysql.example.internal",
        mysql_database="knowledge",
        mysql_readonly_user="privileged-test-user",
        mysql_readonly_password=SecretStr("must-not-leak"),
        sqlbot_secret_key=SecretStr("test-secret"),
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("owner", "159358", "Data Owner")
    async with database.session() as session:
        session.add(UserRecord(
            id=identity.user_id,
            external_subject=identity.external_subject,
            display_name=identity.display_name,
            provider="mock",
        ))
        await session.commit()
    acl = FakeAssetAuthorization()
    fake = FakeSQLBot()
    service = DataAgentService(database, settings, fake, acl)  # type: ignore[arg-type]
    fake.service = service
    created = await service.create_agent(
        DataAgentCreate(name="同步任务分析", dataset_refs=[asset_ref]), identity
    )
    await service.publish_agent(created.id, identity)
    await service.bind_host_session(
        agent_id=created.id, host_session_key="host-session-asset", identity=identity
    )
    acl.permission = False
    with pytest.raises(AppError, match="not authorized"):
        await service.ask(
            user_subject="159358",
            host_session_key="host-session-asset",
            question="任务数是多少？",
        )
    assert len(acl.calls) >= 3  # create, publish and callback
    await database.dispose()


@pytest.mark.asyncio
async def test_create_rejects_asset_schema_that_is_not_the_physical_table(
    settings_factory,
) -> None:
    asset_ref = "warehouseTable:not-the-physical-table"
    settings = settings_factory(
        data_agent_enabled=True,
        data_agent_subject="159358",
        data_agent_runtime_asset_ref=asset_ref,
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("owner", "159358", "Data Owner")
    service = DataAgentService(
        database,
        settings,
        FakeSQLBot(),  # type: ignore[arg-type]
        FakeAssetAuthorization(omit_field="status"),
    )
    with pytest.raises(AppError) as raised:
        await service.create_agent(
            DataAgentCreate(name="非法映射", dataset_refs=[asset_ref]), identity
        )
    assert raised.value.code == "asset_physical_schema_mismatch"
    await database.dispose()


@pytest.mark.asyncio
async def test_create_rejects_required_row_filters_until_runtime_can_enforce_them(
    settings_factory,
) -> None:
    asset_ref = "warehouseTable:knowledge.sync_job"
    settings = settings_factory(
        data_agent_enabled=True,
        data_agent_subject="159358",
        data_agent_runtime_asset_ref=asset_ref,
    )
    database = Database(settings.resolved_database_url)
    await database.initialize()
    identity = IdentityContext("owner", "159358", "Data Owner")
    service = DataAgentService(
        database,
        settings,
        FakeSQLBot(),  # type: ignore[arg-type]
        FakeAssetAuthorization(
            required_filters=({"field": "tenant_id", "operator": "eq"},)
        ),
    )
    with pytest.raises(AppError) as raised:
        await service.create_agent(
            DataAgentCreate(name="行权限门禁", dataset_refs=[asset_ref]), identity
        )
    assert raised.value.code == "row_permission_not_supported"
    await database.dispose()


def test_callback_source_allowlist_is_fail_closed(settings_factory) -> None:
    settings = settings_factory(
        data_agent_enabled=True,
        data_agent_callback_cidrs=("10.193.65.41/32",),
    )
    service = DataAgentService(
        Database(settings.resolved_database_url),
        settings,
        FakeSQLBot(),  # type: ignore[arg-type]
        FakeAssetAuthorization(),
    )
    service._require_callback_source("10.193.65.41")
    with pytest.raises(AppError) as raised:
        service._require_callback_source("127.0.0.1")
    assert raised.value.code == "sqlbot_callback_source_forbidden"


def test_dataset_projection_never_adds_unauthorized_fields(settings_factory) -> None:
    fields = [
        {"name": "id", "type": "bigint", "required": True},
        {"name": "status", "type": "varchar", "aliases": ["状态"]},
    ]
    projected = project_fields(fields, "查看状态和密码", 20)
    assert [field["name"] for field in projected] == ["id", "status"]


def test_assistant_certificate_encodes_only_ephemeral_ticket(settings_factory) -> None:
    client = SQLBotClient(settings_factory())
    headers = client._assistant_headers("assistant-jwt", "one-time-ticket")
    assert headers["X-SQLBOT-ASSISTANT-TOKEN"] == "Assistant assistant-jwt"
    decoded = json.loads(
        unquote(
            base64.b64decode(
                headers["X-SQLBOT-ASSISTANT-CERTIFICATE"]
            ).decode("utf-8")
        )
    )
    assert decoded == [
        {"key": "X-Davinci-Ticket", "value": "one-time-ticket", "target": "header"}
    ]


def test_data_question_workspace_gets_host_injected_data_mcp(
    settings_factory, tmp_path
) -> None:
    from app.runtime.claude import DATA_ASK_TOOL_NAME, ClaudeAgentRuntime
    from tests.test_runtime_events import runtime_request

    request = runtime_request(tmp_path)
    request.workspace_snapshot.update(
        {"id": "data-question", "allowed_tools": [DATA_ASK_TOOL_NAME]}
    )
    runtime = ClaudeAgentRuntime(
        settings_factory(data_agent_subject="159358"),
        environ={"PATH": "/usr/bin"},
        data_agent_service=object(),  # type: ignore[arg-type]
    )
    options = runtime.build_options(request)
    assert options.mcp_servers["data_mcp"]["type"] == "sdk"
    assert options.mcp_servers["data_mcp"]["alwaysLoad"] is True
    assert DATA_ASK_TOOL_NAME in options.allowed_tools
    assert "Host injects the authenticated identity" in options.system_prompt["append"]
