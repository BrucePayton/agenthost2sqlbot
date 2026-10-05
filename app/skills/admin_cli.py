from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import Settings
from app.db.base import Database
from app.db.models import PlatformRoleBindingRecord, UserRecord


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage platform Skill administrators.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("grant", "revoke"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--subject", required=True)
    commands.add_parser("list")
    return parser


async def run(settings: Settings, argv: list[str]) -> int:
    arguments = _parser().parse_args(argv)
    database = Database(settings.resolved_database_url)
    try:
        await database.initialize()
        if arguments.command == "list":
            return await _list(database)
        subject = arguments.subject.strip()
        async with database.session() as db:
            user = await db.scalar(
                select(UserRecord).where(UserRecord.external_subject == subject)
            )
        if user is None:
            print(f"Unknown user subject: {subject}", file=sys.stderr)
            return 2
        if arguments.command == "grant":
            await _grant(database, user.id)
            print(f"Granted skill_admin to {subject}")
            return 0
        await _revoke(database, user.id)
        print(f"Revoked skill_admin from {subject}")
        return 0
    finally:
        await database.dispose()


async def _grant(database: Database, user_id: str) -> None:
    async with database.session() as db:
        statement = (
            postgresql_insert(PlatformRoleBindingRecord)
            if db.bind is not None and db.bind.dialect.name == "postgresql"
            else sqlite_insert(PlatformRoleBindingRecord)
        )
        await db.execute(
            statement.values(
                user_id=user_id,
                role="skill_admin",
                granted_by=None,
            ).on_conflict_do_nothing(index_elements=("user_id", "role"))
        )
        await db.commit()


async def _revoke(database: Database, user_id: str) -> None:
    async with database.session() as db:
        await db.execute(
            delete(PlatformRoleBindingRecord).where(
                PlatformRoleBindingRecord.user_id == user_id,
                PlatformRoleBindingRecord.role == "skill_admin",
            )
        )
        await db.commit()


async def _list(database: Database) -> int:
    async with database.session() as db:
        users = (
            await db.execute(
                select(UserRecord.external_subject, UserRecord.display_name)
                .join(
                    PlatformRoleBindingRecord,
                    PlatformRoleBindingRecord.user_id == UserRecord.id,
                )
                .where(PlatformRoleBindingRecord.role == "skill_admin")
                .order_by(UserRecord.external_subject)
            )
        ).all()
    for subject, display_name in users:
        print(f"{subject}\t{display_name}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run(Settings(), sys.argv[1:])))


if __name__ == "__main__":
    main()
