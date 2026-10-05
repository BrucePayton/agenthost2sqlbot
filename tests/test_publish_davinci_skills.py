"""Publishing previews must not create storage, migrations or Skill versions."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts import publish_davinci_skills as publisher
from tests.test_workspaces import write_workspace


def file_hashes(root: Path):
    """Capture file bytes so preview writes cannot hide behind unchanged row counts."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("initialized", [False, True])
async def test_dry_run_never_initializes_or_mutates_storage(
    settings_factory, monkeypatch, initialized
):
    """Compare both an absent database and an existing database with protected Skills."""
    settings = settings_factory()
    write_workspace(settings.workspaces_root, "davinci-dashboard", skills=("summary",))
    settings.app_data_dir.mkdir()
    db_path = settings.app_data_dir / "app.db"
    if initialized:
        with sqlite3.connect(db_path) as db:
            db.execute(
                "CREATE TABLE skills(name TEXT, bundle_hash TEXT, archived_at TEXT, config_json TEXT, scope TEXT)"
            )
            db.execute(
                "INSERT INTO skills VALUES ('summary', 'old', NULL, '{}', 'global')"
            )
            db.execute("CREATE TABLE alembic_version(version_num TEXT)")
            db.execute("INSERT INTO alembic_version VALUES ('old-version')")
        # Match the explicit DB created above, independent of Settings defaults.
    settings = settings_factory(database_url=f"sqlite+aiosqlite:///{db_path}")
    before = file_hashes(settings.app_data_dir)

    def forbidden(*_args, **_kwargs):
        """A preview must not even construct app services with write-capable bootstrap."""
        raise AssertionError("preview constructed app services")

    monkeypatch.setattr(publisher, "build_app_services", forbidden)
    status = await publisher.main(True, settings)
    assert status == (0 if initialized else 2)
    assert file_hashes(settings.app_data_dir) == before
