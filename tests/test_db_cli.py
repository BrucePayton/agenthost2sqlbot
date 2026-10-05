from __future__ import annotations


async def test_migrate_initializes_and_disposes_database(
    settings_factory, monkeypatch
) -> None:
    from app.db.cli import migrate

    calls: list[str] = []

    class FakeDatabase:
        async def initialize(self) -> None:
            calls.append("initialize")

        async def dispose(self) -> None:
            calls.append("dispose")

    database = FakeDatabase()
    monkeypatch.setattr("app.db.cli.Database", lambda _url: database)

    await migrate(settings_factory())

    assert calls == ["initialize", "dispose"]


async def test_migrate_disposes_database_when_initialization_fails(
    settings_factory, monkeypatch
) -> None:
    from app.db.cli import migrate

    calls: list[str] = []

    class FakeDatabase:
        async def initialize(self) -> None:
            calls.append("initialize")
            raise RuntimeError("migration failed")

        async def dispose(self) -> None:
            calls.append("dispose")

    database = FakeDatabase()
    monkeypatch.setattr("app.db.cli.Database", lambda _url: database)

    try:
        await migrate(settings_factory())
    except RuntimeError as exc:
        assert str(exc) == "migration failed"
    else:
        raise AssertionError("migrate should preserve initialization errors")

    assert calls == ["initialize", "dispose"]
