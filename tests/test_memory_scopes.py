import os
import stat
from pathlib import Path

import pytest


def test_memory_scope_is_stable_isolated_and_owner_only(tmp_path: Path) -> None:
    from app.memory.scopes import MemoryScopeService

    service = MemoryScopeService(tmp_path / "data")
    service.initialize()

    first = service.resolve("user-a", "workspace-a")
    same = service.resolve("user-a", "workspace-a")
    other_user = service.resolve("user-b", "workspace-a")
    other_workspace = service.resolve("user-a", "workspace-b")

    assert first == same
    assert len({first.directory, other_user.directory, other_workspace.directory}) == 3
    assert first.directory.is_relative_to(service.root)
    assert "user-a" not in str(first.directory)
    assert "workspace-a" not in str(first.directory)
    assert stat.S_IMODE(service.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.directory.stat().st_mode) == 0o700


def test_hostile_scope_ids_are_opaque_and_contained(tmp_path: Path) -> None:
    from app.memory.scopes import MemoryScopeService

    service = MemoryScopeService(tmp_path / "data")
    service.initialize()

    scope = service.resolve("../../other-user", "../other-workspace")

    assert scope.directory.is_relative_to(service.root)
    assert ".." not in scope.key
    assert "other-user" not in str(scope.directory)
    assert "other-workspace" not in str(scope.directory)


def test_memory_root_symlink_is_rejected(tmp_path: Path) -> None:
    from app.errors import AppError
    from app.memory.scopes import MemoryScopeService

    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    data_dir.mkdir()
    (data_dir / "memories").symlink_to(outside, target_is_directory=True)
    service = MemoryScopeService(data_dir)

    with pytest.raises(AppError) as exc_info:
        service.initialize()

    assert exc_info.value.code == "memory_unavailable"
    assert service.ready is False


def test_scope_symlink_is_rejected_after_initialization(tmp_path: Path) -> None:
    from app.errors import AppError
    from app.memory.scopes import MemoryScopeService

    service = MemoryScopeService(tmp_path / "data")
    service.initialize()
    scope = service.resolve("user-a", "workspace-a")
    scope.directory.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    scope.directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AppError) as exc_info:
        service.resolve("user-a", "workspace-a")

    assert exc_info.value.code == "memory_unavailable"


def test_memory_readiness_fails_when_probe_cannot_be_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.errors import AppError
    from app.memory.scopes import MemoryScopeService

    real_open = os.open

    def fail_probe(path, flags, mode=0o777):
        if Path(path).name.startswith(".write-probe-"):
            raise PermissionError("read only")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fail_probe)
    service = MemoryScopeService(tmp_path / "data")

    with pytest.raises(AppError) as exc_info:
        service.initialize()

    assert exc_info.value.code == "memory_unavailable"
    assert service.ready is False
