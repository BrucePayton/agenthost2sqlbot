from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import update

from app.errors import AppError
from tests.test_workspaces import write_workspace


@dataclass(frozen=True)
class _Harness:
    service: object
    database: object
    workspace: object
    identity: object
    template: Path


@asynccontextmanager
async def _harness(
    tmp_path: Path,
    *,
    template_claude: str | None,
    seed_claude: str | None,
    max_bytes: int = 16384,
) -> AsyncIterator[_Harness]:
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.instructions.service import InstructionService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.resolver import WorkspaceTemplateResolver

    root = tmp_path / "workspaces"
    root.mkdir(parents=True)
    template = write_workspace(root, "actual", skills=())
    if template_claude is not None:
        (template / "CLAUDE.md").write_text(template_claude, encoding="utf-8")
    if seed_claude is not None:
        seed = template / "seed"
        seed.mkdir(exist_ok=True)
        (seed / "CLAUDE.md").write_text(seed_claude, encoding="utf-8")

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'instructions.db'}")
    await database.initialize()
    async with database.session() as db:
        db.add_all(
            [
                UserRecord(
                    id="owner-a",
                    external_subject="owner-a",
                    display_name="Owner A",
                    provider="test",
                ),
                WorkspaceRecord(
                    id="personal-a",
                    name="Personal A",
                    kind="team",
                    config_json="{}",
                ),
            ]
        )
        await db.flush()
        db.add(
            WorkspaceMemberRecord(
                workspace_id="personal-a", user_id="owner-a", role="owner"
            )
        )
        await db.flush()
        await db.execute(
            update(WorkspaceRecord)
            .where(WorkspaceRecord.id == "personal-a")
            .values(kind="personal", owner_user_id="owner-a", template_id="actual")
        )
        await db.commit()
    async with database.session() as db:
        workspace = await db.get(WorkspaceRecord, "personal-a")

    registry = WorkspaceRegistry(root, "claude-default", environ={})
    registry.scan()
    service = InstructionService(
        database,
        WorkspaceTemplateResolver(registry),
        max_bytes=max_bytes,
    )
    try:
        yield _Harness(
            service=service,
            database=database,
            workspace=workspace,
            identity=IdentityContext("owner-a", "owner-a", "Owner A"),
            template=template,
        )
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_default_resolution_prefers_seed_over_template(tmp_path: Path) -> None:
    """默认内容 = 没有用户覆盖时实际会用到的那一份。"""
    async with _harness(
        tmp_path, template_claude="# template", seed_claude="# seed"
    ) as harness:
        default = await harness.service.get_default(harness.workspace)
        assert default.content == "# seed"
        assert default.source == "seed"
        current = await harness.service.get(harness.workspace, harness.identity)
        assert current.content == "# seed"
        assert current.source == "seed"
        assert current.updated_at is None
        assert current.updated_by is None


@pytest.mark.asyncio
async def test_default_falls_back_to_template_then_builtin(tmp_path: Path) -> None:
    from app.workspaces.materializer import DEFAULT_CLAUDE_MD

    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        default = await harness.service.get_default(harness.workspace)
        assert default.content == "# template"
        assert default.source == "template"

    async with _harness(
        tmp_path / "bare", template_claude=None, seed_claude=None
    ) as harness:
        default = await harness.service.get_default(harness.workspace)
        assert default.content == DEFAULT_CLAUDE_MD
        assert default.source == "builtin"


@pytest.mark.asyncio
async def test_default_rejects_a_symlinked_instructions_file(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        (harness.template / "CLAUDE.md").unlink()
        (harness.template / "CLAUDE.md").symlink_to(outside)
        with pytest.raises(AppError) as exc:
            await harness.service.get_default(harness.workspace)
        assert exc.value.code == "workspace_invalid"


@pytest.mark.asyncio
async def test_save_rejects_content_over_the_byte_limit(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        with pytest.raises(AppError) as exc:
            await harness.service.save(
                harness.workspace, harness.identity, "x" * 16385, expected_hash=None
            )
        assert exc.value.code == "instructions_too_large"
        assert exc.value.status_code == 413
        # 超限不得落库
        assert await harness.service.load_override_content("personal-a") is None


@pytest.mark.asyncio
async def test_the_limit_counts_utf8_bytes_not_characters(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None, max_bytes=1024
    ) as harness:
        # 342 个三字节汉字 = 1026 字节，超限；341 个 = 1023 字节，放得下。
        with pytest.raises(AppError) as exc:
            await harness.service.save(
                harness.workspace, harness.identity, "汉" * 342, expected_hash=None
            )
        assert exc.value.code == "instructions_too_large"
        saved = await harness.service.save(
            harness.workspace, harness.identity, "汉" * 341, expected_hash=None
        )
        assert saved.size_bytes == 1023


@pytest.mark.asyncio
async def test_save_rejects_a_nul_byte(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        with pytest.raises(AppError) as exc:
            await harness.service.save(
                harness.workspace, harness.identity, "a\x00b", expected_hash=None
            )
        assert exc.value.code == "instructions_invalid"
        assert exc.value.status_code == 422
        assert await harness.service.load_override_content("personal-a") is None


@pytest.mark.asyncio
async def test_save_is_guarded_by_the_expected_hash(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        first = await harness.service.save(
            harness.workspace, harness.identity, "# A", expected_hash=None
        )
        with pytest.raises(AppError) as exc:
            await harness.service.save(
                harness.workspace,
                harness.identity,
                "# B",
                expected_hash="sha256:" + "0" * 64,
            )
        assert exc.value.code == "instructions_changed"
        assert exc.value.status_code == 409
        # 带正确 hash 能写
        second = await harness.service.save(
            harness.workspace,
            harness.identity,
            "# B",
            expected_hash=first.content_hash,
        )
        assert second.source == "custom"
        assert second.content == "# B"
        assert await harness.service.load_override_content("personal-a") == "# B"


@pytest.mark.asyncio
async def test_expected_hash_never_matches_when_there_is_no_override(
    tmp_path: Path,
) -> None:
    """无覆盖行时任何 expected_hash 都算不匹配——包括默认内容的 hash。"""
    from app.instructions.service import instructions_hash

    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        with pytest.raises(AppError) as exc:
            await harness.service.save(
                harness.workspace,
                harness.identity,
                "# A",
                expected_hash=instructions_hash("# template"),
            )
        assert exc.value.code == "instructions_changed"


@pytest.mark.asyncio
async def test_reset_restores_the_default(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude="# seed"
    ) as harness:
        await harness.service.save(
            harness.workspace, harness.identity, "# mine", expected_hash=None
        )
        await harness.service.reset(
            harness.workspace, harness.identity, expected_hash=None
        )
        current = await harness.service.get(harness.workspace, harness.identity)
        assert current.source in {"seed", "template", "builtin"}
        assert await harness.service.load_override_content("personal-a") is None
        # 幂等：没有覆盖行时再删一次也成功
        await harness.service.reset(
            harness.workspace, harness.identity, expected_hash=None
        )


@pytest.mark.asyncio
async def test_reset_is_guarded_by_the_expected_hash(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        await harness.service.save(
            harness.workspace, harness.identity, "# mine", expected_hash=None
        )
        with pytest.raises(AppError) as exc:
            await harness.service.reset(
                harness.workspace,
                harness.identity,
                expected_hash="sha256:" + "0" * 64,
            )
        assert exc.value.code == "instructions_changed"
        assert exc.value.status_code == 409
        assert await harness.service.load_override_content("personal-a") == "# mine"


@pytest.mark.asyncio
async def test_size_is_measured_in_utf8_bytes(tmp_path: Path) -> None:
    from app.instructions.service import instructions_hash

    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        saved = await harness.service.save(
            harness.workspace, harness.identity, "汉", expected_hash=None
        )
        assert saved.size_bytes == 3
        assert saved.content_hash == instructions_hash("汉")
        assert saved.updated_by == "owner-a"
        assert saved.updated_at is not None


@pytest.mark.asyncio
async def test_saving_twice_keeps_the_original_created_at(tmp_path: Path) -> None:
    from app.db.models import WorkspaceInstructionRecord

    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        first = await harness.service.save(
            harness.workspace, harness.identity, "# A", expected_hash=None
        )
        async with harness.database.session() as db:
            record = await db.get(WorkspaceInstructionRecord, "personal-a")
            created_at = record.created_at
        await harness.service.save(
            harness.workspace,
            harness.identity,
            "# B",
            expected_hash=first.content_hash,
        )
        async with harness.database.session() as db:
            record = await db.get(WorkspaceInstructionRecord, "personal-a")
        assert record.created_at == created_at
        assert record.content == "# B"


@pytest.mark.asyncio
async def test_override_that_differs_from_template_is_flagged(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        await harness.service.save(
            harness.workspace,
            harness.identity,
            "# 覆盖版\n自定义内容",
            expected_hash=None,
        )
        resolved = await harness.service.get(harness.workspace, harness.identity)
        assert resolved.source == "custom"
        assert resolved.drifted_from_template is True


@pytest.mark.asyncio
async def test_override_equal_to_template_is_not_flagged(tmp_path: Path) -> None:
    async with _harness(
        tmp_path, template_claude="# template", seed_claude=None
    ) as harness:
        template = await harness.service.get_default(harness.workspace)
        await harness.service.save(
            harness.workspace, harness.identity, template.content, expected_hash=None
        )
        resolved = await harness.service.get(harness.workspace, harness.identity)
        assert resolved.drifted_from_template is False


def test_settings_expose_the_instruction_byte_limit(settings_factory) -> None:
    settings = settings_factory()
    assert settings.max_instructions_bytes == 16384


def test_bootstrap_wires_the_instruction_service(settings_factory) -> None:
    from app.bootstrap import build_app_services
    from app.runtime.fake import FakeAgentRuntime

    settings = settings_factory()
    write_workspace(settings.workspaces_root, "actual")

    services = build_app_services(settings, runtime=FakeAgentRuntime())

    assert services.instructions.database is services.database
    assert services.instructions.templates is services.workspace_templates
    assert services.instructions.max_bytes == settings.max_instructions_bytes
