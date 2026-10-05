from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update

from app.db.base import Database
from app.db.models import (
    DataAgentAskSessionRecord,
    DataAgentAuditRecord,
    DataAgentDatasetRecord,
    DataAgentRecord,
    DataAgentResultRecord,
    DataAgentTicketRecord,
)
from app.errors import AppError


class DataAgentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def list_agents(self, created_by: str) -> list[tuple[DataAgentRecord, list[DataAgentDatasetRecord]]]:
        async with self.database.session() as db:
            agents = list((await db.scalars(
                select(DataAgentRecord)
                .where(DataAgentRecord.created_by == created_by)
                .order_by(DataAgentRecord.updated_at.desc())
            )).all())
            datasets = list((await db.scalars(
                select(DataAgentDatasetRecord).where(
                    DataAgentDatasetRecord.agent_id.in_([agent.id for agent in agents])
                )
            )).all()) if agents else []
        grouped: dict[str, list[DataAgentDatasetRecord]] = {}
        for dataset in datasets:
            grouped.setdefault(dataset.agent_id, []).append(dataset)
        return [(agent, grouped.get(agent.id, [])) for agent in agents]

    async def get_agent(
        self, agent_id: str, created_by: str | None = None
    ) -> tuple[DataAgentRecord, list[DataAgentDatasetRecord]]:
        async with self.database.session() as db:
            agent = await db.get(DataAgentRecord, agent_id)
            if agent is None or (created_by is not None and agent.created_by != created_by):
                raise AppError("data_agent_not_found", "Data agent not found.", 404)
            datasets = list((await db.scalars(
                select(DataAgentDatasetRecord)
                .where(DataAgentDatasetRecord.agent_id == agent_id)
                .order_by(DataAgentDatasetRecord.name)
            )).all())
            return agent, datasets

    async def create_agent(
        self,
        *,
        created_by: str,
        name: str,
        description: str,
        datasets: list[dict[str, object]],
    ) -> tuple[DataAgentRecord, list[DataAgentDatasetRecord]]:
        now = datetime.now(UTC)
        agent = DataAgentRecord(
            id=str(uuid.uuid4()), created_by=created_by, name=name.strip(),
            description=description.strip(), status="draft", created_at=now, updated_at=now,
        )
        records = [self._dataset_record(agent.id, item, now) for item in datasets]
        async with self.database.session() as db:
            db.add(agent)
            await db.flush()
            db.add_all(records)
            await db.commit()
        return agent, records

    async def update_agent(
        self,
        *,
        agent_id: str,
        created_by: str,
        name: str,
        description: str,
        datasets: list[dict[str, object]],
    ) -> tuple[DataAgentRecord, list[DataAgentDatasetRecord]]:
        now = datetime.now(UTC)
        async with self.database.session() as db:
            agent = await db.get(DataAgentRecord, agent_id)
            if agent is None or agent.created_by != created_by:
                raise AppError("data_agent_not_found", "Data agent not found.", 404)
            if agent.status == "published":
                raise AppError("data_agent_published", "Disable the agent before editing it.", 409)
            agent.name = name.strip()
            agent.description = description.strip()
            agent.sqlbot_sync_status = "pending"
            agent.sqlbot_sync_error = None
            agent.updated_at = now
            await db.execute(delete(DataAgentDatasetRecord).where(DataAgentDatasetRecord.agent_id == agent_id))
            records = [self._dataset_record(agent.id, item, now) for item in datasets]
            db.add_all(records)
            await db.commit()
            return agent, records

    async def set_publication(
        self,
        *,
        agent_id: str,
        created_by: str,
        status: str,
        assistant_id: int | None,
        sync_status: str,
        sync_error: str | None = None,
    ) -> DataAgentRecord:
        async with self.database.session() as db:
            agent = await db.get(DataAgentRecord, agent_id)
            if agent is None or agent.created_by != created_by:
                raise AppError("data_agent_not_found", "Data agent not found.", 404)
            agent.status = status
            agent.sqlbot_assistant_id = assistant_id
            agent.sqlbot_sync_status = sync_status
            agent.sqlbot_sync_error = sync_error
            agent.updated_at = datetime.now(UTC)
            await db.commit()
            return agent

    async def delete_agent(self, agent_id: str, created_by: str) -> None:
        async with self.database.session() as db:
            agent = await db.get(DataAgentRecord, agent_id)
            if agent is None or agent.created_by != created_by:
                raise AppError("data_agent_not_found", "Data agent not found.", 404)
            if agent.status == "published":
                raise AppError("data_agent_published", "Disable the agent before deleting it.", 409)
            await db.delete(agent)
            await db.commit()

    async def bind_host_session(
        self, *, agent_id: str, user_subject: str, host_session_key: str
    ) -> DataAgentAskSessionRecord:
        now = datetime.now(UTC)
        async with self.database.session() as db:
            record = await db.scalar(select(DataAgentAskSessionRecord).where(
                DataAgentAskSessionRecord.host_session_key == host_session_key
            ))
            if record is None:
                record = DataAgentAskSessionRecord(
                    id=str(uuid.uuid4()), agent_id=agent_id, user_subject=user_subject,
                    host_session_key=host_session_key, created_at=now, updated_at=now,
                )
                db.add(record)
            elif record.agent_id != agent_id or record.user_subject != user_subject:
                raise AppError("data_agent_session_conflict", "Session is already bound to another data agent.", 409)
            await db.commit()
            return record

    async def get_ask_session(
        self, host_session_key: str, user_subject: str
    ) -> DataAgentAskSessionRecord:
        async with self.database.session() as db:
            record = await db.scalar(select(DataAgentAskSessionRecord).where(
                DataAgentAskSessionRecord.host_session_key == host_session_key,
                DataAgentAskSessionRecord.user_subject == user_subject,
            ))
            if record is None:
                raise AppError("data_agent_session_not_found", "Data-agent session is not bound.", 404)
            return record

    async def set_sqlbot_chat(self, ask_session_id: str, chat_id: int) -> None:
        async with self.database.session() as db:
            record = await db.get(DataAgentAskSessionRecord, ask_session_id)
            if record is None:
                raise AppError("data_agent_session_not_found", "Data-agent session is not bound.", 404)
            record.sqlbot_chat_id = chat_id
            record.updated_at = datetime.now(UTC)
            await db.commit()

    async def add_ticket(self, ticket: DataAgentTicketRecord) -> None:
        async with self.database.session() as db:
            db.add(ticket)
            await db.commit()

    async def consume_ticket(
        self, token_hash: str, *, max_callbacks: int = 2
    ) -> DataAgentTicketRecord:
        async with self.database.session() as db:
            now = datetime.now(UTC)
            ticket = await db.scalar(
                update(DataAgentTicketRecord)
                .where(
                    DataAgentTicketRecord.token_hash == token_hash,
                    DataAgentTicketRecord.completed_at.is_(None),
                    DataAgentTicketRecord.expires_at > now,
                    DataAgentTicketRecord.callback_count < max_callbacks,
                )
                .values(callback_count=DataAgentTicketRecord.callback_count + 1)
                .returning(DataAgentTicketRecord)
            )
            if ticket is None:
                existing = await db.get(DataAgentTicketRecord, token_hash)
                if (
                    existing is not None
                    and existing.completed_at is None
                    and _utc(existing.expires_at) > now
                    and existing.callback_count >= max_callbacks
                ):
                    raise AppError(
                        "data_ticket_replayed",
                        "Data ticket callback limit exceeded.",
                        401,
                    )
                raise AppError(
                    "invalid_data_ticket", "Data ticket is invalid or expired.", 401
                )
            await db.commit()
            return ticket

    async def complete_ticket(self, token_hash: str) -> None:
        async with self.database.session() as db:
            ticket = await db.get(DataAgentTicketRecord, token_hash)
            if ticket is not None and ticket.completed_at is None:
                ticket.completed_at = datetime.now(UTC)
                ticket.question = ""
                await db.commit()

    async def save_result(self, record: DataAgentResultRecord) -> None:
        async with self.database.session() as db:
            db.add(record)
            await db.commit()

    async def get_result(
        self, *, result_id: str, user_subject: str, agent_id: str
    ) -> DataAgentResultRecord:
        async with self.database.session() as db:
            result = await db.get(DataAgentResultRecord, result_id)
            if result is None or result.user_subject != user_subject or result.agent_id != agent_id:
                raise AppError("data_agent_result_not_found", "Result not found.", 404)
            return result

    async def audit(
        self,
        *,
        user_subject: str,
        action: str,
        status: str,
        agent_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        safe_details = dict(details or {})
        for key in list(safe_details):
            if any(secret in key.casefold() for secret in ("password", "secret", "ticket", "token")):
                safe_details[key] = "[REDACTED]"
        async with self.database.session() as db:
            db.add(DataAgentAuditRecord(
                id=str(uuid.uuid4()), agent_id=agent_id, user_subject=user_subject,
                action=action, status=status,
                details_json=json.dumps(safe_details, ensure_ascii=False, separators=(",", ":")),
                created_at=datetime.now(UTC),
            ))
            await db.commit()

    @staticmethod
    def _dataset_record(agent_id: str, item: dict[str, object], now: datetime) -> DataAgentDatasetRecord:
        return DataAgentDatasetRecord(
            id=str(uuid.uuid4()), agent_id=agent_id,
            dataset_ref=str(item["dataset_ref"]), name=str(item["name"]),
            description=str(item["description"]), virtual_table_name=str(item["virtual_table_name"]),
            datasource_id=int(item["datasource_id"]),
            fields_json=json.dumps(item["fields"], ensure_ascii=False, separators=(",", ":")),
            base_sql=str(item["base_sql"]), created_at=now,
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
