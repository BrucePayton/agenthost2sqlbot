from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def settings_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., object]:
    workspaces_root = tmp_path / "workspaces"
    workspaces_root.mkdir()
    data_dir = tmp_path / "data"

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "top-secret-test-key")
    monkeypatch.setenv("WORKSPACES_ROOT", str(workspaces_root))
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MOCK_PERSONAL_WORKSPACE_ID", "actual")
    monkeypatch.setenv("MOCK_WORKSPACE_ROLES", '{"actual":"owner"}')
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def factory(**overrides: object) -> object:
        from app.config import Settings

        return Settings(_env_file=None, **overrides)

    return factory


async def synchronize_workspaces(database, registry, settings) -> None:
    from app.auth.models import IdentityContext
    from app.workspaces.sync import WorkspaceSyncService

    identity = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    await WorkspaceSyncService(database, settings).sync(registry.scan(), identity)
