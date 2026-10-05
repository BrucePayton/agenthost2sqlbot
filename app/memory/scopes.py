import os
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.errors import AppError


@dataclass(frozen=True)
class MemoryScope:
    key: str
    directory: Path


def _opaque_component(prefix: str, value: str) -> str:
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def user_scope_key(user_id: str) -> str:
    return _opaque_component("u", user_id)


class MemoryScopeService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "memories"
        self.ready = False

    def initialize(self) -> None:
        probe: Path | None = None
        try:
            if self.root.is_symlink():
                raise self._unavailable()
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
            probe = self.root / f".write-probe-{uuid.uuid4().hex}"
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            probe.unlink()
        except AppError:
            raise
        except OSError as exc:
            if probe is not None:
                probe.unlink(missing_ok=True)
            raise self._unavailable() from exc
        self.ready = True

    def resolve(self, user_id: str, workspace_id: str) -> MemoryScope:
        if not self.ready:
            raise self._unavailable()
        user_key = user_scope_key(user_id)
        workspace_key = _opaque_component("w", workspace_id)
        users_root = self.root / "users"
        user_root = users_root / user_key
        workspaces_root = user_root / "workspaces"
        directory = workspaces_root / workspace_key
        try:
            for path in (users_root, user_root, workspaces_root, directory):
                if path.is_symlink():
                    raise self._unavailable()
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(path, 0o700)
            resolved = directory.resolve()
            if not resolved.is_relative_to(self.root.resolve()):
                raise self._unavailable()
        except AppError:
            raise
        except OSError as exc:
            raise self._unavailable() from exc
        return MemoryScope(
            key=f"{user_key}/{workspace_key}",
            directory=resolved,
        )

    @staticmethod
    def _unavailable() -> AppError:
        return AppError(
            "memory_unavailable",
            "Personal Workspace memory is unavailable.",
            503,
        )
