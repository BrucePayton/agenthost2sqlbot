"""Reconcile manifest-enabled Davinci Skills using the normal bootstrap protections.

Bootstrap already updates changed bundles from the same trusted source. This command
uses that same path, preserving administrator overrides and archived Skills.
--dry-run reads existing state only; it never initializes a database or artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.bootstrap import build_app_services
from app.config import Settings
from app.runtime.fake import FakeAgentRuntime
from app.skills.bundle import load_bundle_from_directory
from app.workspaces.registry import WorkspaceRegistry


async def read_existing_skills(database_url: str) -> list[dict] | None:
    """Read without the application's SQLite WAL/migration initialization hooks."""
    url = make_url(database_url)
    query = "SELECT name, bundle_hash, archived_at, config_json FROM skills WHERE scope='global'"
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "").resolve()
        if not path.is_file():
            return None
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(query)]
        except sqlite3.OperationalError:
            return None
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            return [
                dict(row) for row in (await connection.execute(text(query))).mappings()
            ]
    finally:
        await engine.dispose()


async def main(dry_run: bool, settings: Settings | None = None) -> int:
    """Inspect first, then reconcile only the Davinci manifest when explicitly publishing."""
    settings = settings or Settings()
    registry = WorkspaceRegistry(
        settings.workspaces_root,
        settings.claude_model,
        allow_loopback_http_mcp=settings.app_env in {"development", "test"},
    )
    entries = [entry for entry in registry.scan() if entry.id == "davinci-dashboard"]
    if not entries or not entries[0].available or entries[0].manifest is None:
        print("Davinci Workspace 不可用，未发布。")
        return 2
    entry = entries[0]
    existing = await read_existing_skills(settings.resolved_database_url)
    if existing is None:
        print("数据库未初始化，无法比较；未建库、迁移或发布。")
        return 2
    if dry_run:
        for name in entry.manifest.skills:
            bundle = load_bundle_from_directory(
                entry.skills_source_root / name, settings.skill_bundle_limits
            )
            matches = [
                row
                for row in existing
                if row["name"].casefold() == bundle.name.casefold()
            ]
            protected = any(
                row["archived_at"] is not None
                or json.loads(row["config_json"] or "{}").get("type")
                != "global_bootstrap"
                for row in matches
            )
            same = any(row["bundle_hash"] == bundle.bundle_hash for row in matches)
            status = (
                "保留管理员/归档版本"
                if protected
                else "未变化"
                if same
                else "待同源校验后发布"
            )
            print(f"{name}: {status} ({bundle.bundle_hash[:20]})")
        return 0
    services = build_app_services(settings, runtime=FakeAgentRuntime())
    try:
        report = await services.skill_bootstrap.run(
            entries, None, settings.mock_personal_workspace_id, None
        )
        for item in report.items:
            print(f"{item.name}: {item.status}")
        return 1 if any(item.status in {"failed", "conflict"} for item in report.items) else 0
    finally:
        await services.database.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    raise SystemExit(asyncio.run(main(parser.parse_args().dry_run)))
