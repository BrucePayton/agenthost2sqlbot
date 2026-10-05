from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import synchronize_workspaces
from tests.test_workspaces import write_workspace


def _entry_with(
    tmp_path: Path,
    *,
    template_claude: str | None,
    seed_claude: str | None,
):
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir(parents=True)
    workspace = write_workspace(root, "actual", skills=())
    if template_claude is not None:
        (workspace / "CLAUDE.md").write_text(template_claude, encoding="utf-8")
    if seed_claude is not None:
        seed = workspace / "seed"
        seed.mkdir(exist_ok=True)
        (seed / "CLAUDE.md").write_text(seed_claude, encoding="utf-8")
    return WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]


def _materialize(entry, session_id: str, tmp_path: Path, **kwargs) -> str:
    from app.sessions.snapshot import build_session_snapshot
    from app.workspaces.materializer import materialize_session_workspace

    result = materialize_session_workspace(
        entry,
        session_id,
        tmp_path / "data",
        build_session_snapshot(entry, ()),
        (),
        **kwargs,
    )
    return (result.workspace_dir / "CLAUDE.md").read_text(encoding="utf-8")


def test_user_instructions_win_over_template_and_seed(tmp_path: Path) -> None:
    """三方优先级：用户覆盖 > seed > 模板 > 内置兜底。

    用户版本必须写在 seed 拷贝之后——seed 里如果也有 CLAUDE.md，
    copytree(dirs_exist_ok=True) 会把先写的盖掉。
    """
    entry = _entry_with(tmp_path, template_claude="# template", seed_claude="# seed")

    assert _materialize(entry, "s1", tmp_path, instructions="# mine") == "# mine"


def test_seed_wins_over_template_when_no_override(tmp_path: Path) -> None:
    entry = _entry_with(tmp_path, template_claude="# template", seed_claude="# seed")

    assert _materialize(entry, "s2", tmp_path) == "# seed"


@pytest.mark.parametrize("seed_claude", [None, "# seed"])
def test_override_can_replace_read_only_instructions(tmp_path: Path, seed_claude) -> None:
    entry = _entry_with(tmp_path, template_claude="# template", seed_claude=seed_claude)
    sources = [entry.directory / "CLAUDE.md"]
    if seed_claude is not None:
        sources.append(entry.directory / "seed" / "CLAUDE.md")
    for source in sources:
        source.chmod(0o444)

    assert _materialize(entry, "readonly", tmp_path, instructions="# mine") == "# mine"
    for source in sources:
        assert source.stat().st_mode & 0o222 == 0
    assert sources[0].read_text() == "# template"


def test_template_is_used_when_there_is_no_seed(tmp_path: Path) -> None:
    entry = _entry_with(tmp_path, template_claude="# template", seed_claude=None)

    assert _materialize(entry, "s3", tmp_path) == "# template"


def test_the_builtin_default_is_used_when_the_template_has_none(
    tmp_path: Path,
) -> None:
    from app.workspaces.materializer import DEFAULT_CLAUDE_MD

    entry = _entry_with(tmp_path, template_claude=None, seed_claude=None)

    assert _materialize(entry, "s4", tmp_path) == DEFAULT_CLAUDE_MD


def test_an_override_also_replaces_the_builtin_default(tmp_path: Path) -> None:
    entry = _entry_with(tmp_path, template_claude=None, seed_claude=None)

    assert _materialize(entry, "s5", tmp_path, instructions="# mine") == "# mine"


def test_the_seed_still_brings_its_other_files_along(tmp_path: Path) -> None:
    from app.sessions.snapshot import build_session_snapshot
    from app.workspaces.materializer import materialize_session_workspace

    entry = _entry_with(tmp_path, template_claude="# template", seed_claude="# seed")
    (entry.directory / "seed" / "reference.md").write_text("Ref", encoding="utf-8")

    result = materialize_session_workspace(
        entry,
        "s6",
        tmp_path / "data",
        build_session_snapshot(entry, ()),
        (),
        instructions="# mine",
    )

    assert (result.workspace_dir / "reference.md").read_text() == "Ref"
    assert (result.workspace_dir / "CLAUDE.md").read_text() == "# mine"


@pytest.mark.asyncio
async def test_a_new_session_starts_from_the_saved_override(
    settings_factory,
) -> None:
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.instructions.service import InstructionService
    from app.sessions.service import SessionService
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.repository import WorkspaceRepository
    from app.workspaces.resolver import WorkspaceTemplateResolver

    settings = settings_factory()
    template = write_workspace(settings.workspaces_root, "actual", skills=())
    (template / "CLAUDE.md").write_text("# template", encoding="utf-8")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    templates = WorkspaceTemplateResolver(registry, settings.skill_bundle_limits)
    instructions = InstructionService(database, templates)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=SkillService(
            SkillRepository(database),
            WorkspaceAccessService(database),
            settings.skill_bundle_limits,
        ),
        workspace_templates=templates,
        instructions=instructions,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    try:
        workspace = await WorkspaceRepository(database).get("actual")

        default_session = await sessions.create("actual", owner)
        await instructions.save(workspace, owner, "# mine", expected_hash=None)
        custom_session = await sessions.create("actual", owner)

        assert _claude_md_of(settings.app_data_dir, default_session) == "# template"
        assert _claude_md_of(settings.app_data_dir, custom_session) == "# mine"
    finally:
        await database.dispose()


def _claude_md_of(data_dir: Path, session) -> str:
    return (data_dir / session.session_dir / "workspace" / "CLAUDE.md").read_text(
        encoding="utf-8"
    )


def test_snapshot_records_which_instructions_the_session_got(tmp_path: Path) -> None:
    """指令是唯一进模型上下文却不在快照里的资产；补上 hash 与字节数才能复盘。"""
    import hashlib
    import json

    from app.instructions.service import WorkspaceInstructions, instructions_hash
    from app.sessions.snapshot import build_session_snapshot

    entry = _entry_with(tmp_path, template_claude="# 模板", seed_claude=None)
    content = "# 我自己的口径\n"
    custom = WorkspaceInstructions(
        content=content,
        source="custom",
        content_hash=instructions_hash(content),
        size_bytes=len(content.encode("utf-8")),
    )

    data = json.loads(build_session_snapshot(entry, (), instructions=custom).json)

    assert data["schema_version"] == 4
    assert data["instructions"] == {
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "size_bytes": len(content.encode("utf-8")),
        "source": "custom",
        "drifted": False,
    }


def test_snapshot_records_a_drifted_override(tmp_path: Path) -> None:
    """漂移的覆盖也要在快照里可见，否则事后没法解释「这份指令为什么跟模板不一样」。"""
    import json

    from app.instructions.service import WorkspaceInstructions, instructions_hash
    from app.sessions.snapshot import build_session_snapshot

    entry = _entry_with(tmp_path, template_claude="# 模板", seed_claude=None)
    content = "# 漂移的口径\n"
    custom = WorkspaceInstructions(
        content=content,
        source="custom",
        content_hash=instructions_hash(content),
        size_bytes=len(content.encode("utf-8")),
        drifted_from_template=True,
    )

    data = json.loads(build_session_snapshot(entry, (), instructions=custom).json)

    assert data["instructions"]["drifted"] is True


def test_snapshot_carries_forward_a_pre_drift_instructions_block_unchanged(
    tmp_path: Path,
) -> None:
    """旧快照里没有 drifted 键的 instructions 字典不会被本次改动破坏或拒读。"""
    import dataclasses
    import json

    from app.sessions.snapshot import build_session_snapshot

    entry = _entry_with(tmp_path, template_claude="# 模板", seed_claude=None)
    old_style_snapshot = json.dumps(
        {
            "schema_version": 4,
            "instructions": {
                "sha256": "abc123",
                "size_bytes": 9,
                "source": "custom",
            },
        }
    )
    entry = dataclasses.replace(entry, snapshot_json=old_style_snapshot)

    # 本次不传新的 instructions：旧快照里已有的字典原样透传，不因为缺 drifted 键报错。
    data = json.loads(build_session_snapshot(entry, ()).json)

    assert data["instructions"] == {
        "sha256": "abc123",
        "size_bytes": 9,
        "source": "custom",
    }
    assert "drifted" not in data["instructions"]


def test_snapshot_omits_instructions_when_none_were_resolved(tmp_path: Path) -> None:
    import json

    from app.sessions.snapshot import build_session_snapshot

    entry = _entry_with(tmp_path, template_claude="# 模板", seed_claude=None)
    data = json.loads(build_session_snapshot(entry, ()).json)

    assert data["schema_version"] == 4
    assert "instructions" not in data


@pytest.mark.asyncio
async def test_the_snapshot_hash_matches_the_instructions_on_disk(
    settings_factory,
) -> None:
    """快照的 hash 必须与真正写进 session 的那份字节一致，否则复盘会指向错的内容。"""
    import hashlib
    import json

    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.instructions.service import InstructionService
    from app.sessions.service import SessionService
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.repository import WorkspaceRepository
    from app.workspaces.resolver import WorkspaceTemplateResolver

    settings = settings_factory()
    template = write_workspace(settings.workspaces_root, "actual", skills=())
    (template / "CLAUDE.md").write_text("# template", encoding="utf-8")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    templates = WorkspaceTemplateResolver(registry, settings.skill_bundle_limits)
    instructions = InstructionService(database, templates)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=SkillService(
            SkillRepository(database),
            WorkspaceAccessService(database),
            settings.skill_bundle_limits,
        ),
        workspace_templates=templates,
        instructions=instructions,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    try:
        workspace = await WorkspaceRepository(database).get("actual")
        await instructions.save(workspace, owner, "# 我的口径\n", expected_hash=None)
        session = await sessions.create("actual", owner)

        on_disk = _claude_md_of(settings.app_data_dir, session).encode("utf-8")
        snapshot = json.loads(session.workspace_snapshot_json)

        assert snapshot["instructions"]["source"] == "custom"
        assert snapshot["instructions"]["sha256"] == hashlib.sha256(on_disk).hexdigest()
        assert snapshot["instructions"]["size_bytes"] == len(on_disk)
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_one_users_instructions_do_not_leak_into_another_workspace(
    settings_factory,
) -> None:
    """每个用户一个 personal workspace，所以按 workspace 存就是按用户隔离。"""
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.db.models import UserRecord, WorkspaceMemberRecord, WorkspaceRecord
    from app.instructions.service import InstructionService
    from app.sessions.service import SessionService
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.repository import WorkspaceRepository
    from app.workspaces.resolver import WorkspaceTemplateResolver

    settings = settings_factory()
    template = write_workspace(settings.workspaces_root, "actual", skills=())
    (template / "CLAUDE.md").write_text("# template", encoding="utf-8")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    templates = WorkspaceTemplateResolver(registry, settings.skill_bundle_limits)
    instructions = InstructionService(database, templates)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=SkillService(
            SkillRepository(database),
            WorkspaceAccessService(database),
            settings.skill_bundle_limits,
        ),
        workspace_templates=templates,
        instructions=instructions,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    neighbour = IdentityContext("user-2", "subject-2", "邻座")
    try:
        # 造一个属于另一个用户的 personal workspace。
        async with database.session() as db:
            db.add(
                UserRecord(
                    id="user-2",
                    external_subject="subject-2",
                    display_name="邻座",
                    provider="test",
                )
            )
            await db.flush()
            # 先以 team 落库再改 personal，绕开 partial unique index 的插入时序，
            # 与 PersonalWorkspaceProvisioner 的做法一致。
            db.add(
                WorkspaceRecord(
                    id="other",
                    name="邻座的工作区",
                    kind="team",
                    owner_user_id="user-2",
                    template_id="actual",
                    config_json="{}",
                )
            )
            await db.flush()
            db.add(
                WorkspaceMemberRecord(
                    workspace_id="other", user_id="user-2", role="owner"
                )
            )
            await db.flush()
            other = await db.get(WorkspaceRecord, "other")
            other.kind = "personal"
            await db.commit()

        repository = WorkspaceRepository(database)
        mine = await repository.get("actual")
        await instructions.save(mine, owner, "# 只属于我\n", expected_hash=None)

        theirs = await repository.get("other")
        neighbour_view = await instructions.get(theirs, neighbour)
        assert neighbour_view.source == "template"
        assert neighbour_view.content == "# template"

        neighbour_session = await sessions.create("other", neighbour)
        assert _claude_md_of(settings.app_data_dir, neighbour_session) == "# template"
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_saving_does_not_touch_sessions_that_already_exist(
    settings_factory,
) -> None:
    """已建 session 的指令是冻结的：改了只影响之后创建的会话。"""
    from app.auth.access import WorkspaceAccessService
    from app.auth.models import IdentityContext
    from app.db.base import Database
    from app.instructions.service import InstructionService
    from app.sessions.service import SessionService
    from app.skills.repository import SkillRepository
    from app.skills.service import SkillService
    from app.workspaces.registry import WorkspaceRegistry
    from app.workspaces.repository import WorkspaceRepository
    from app.workspaces.resolver import WorkspaceTemplateResolver

    settings = settings_factory()
    template = write_workspace(settings.workspaces_root, "actual", skills=())
    (template / "CLAUDE.md").write_text("# template", encoding="utf-8")
    registry = WorkspaceRegistry(settings.workspaces_root, settings.claude_model, {})
    registry.scan()
    database = Database(settings.resolved_database_url)
    await database.initialize()
    await synchronize_workspaces(database, registry, settings)
    templates = WorkspaceTemplateResolver(registry, settings.skill_bundle_limits)
    instructions = InstructionService(database, templates)
    sessions = SessionService(
        database,
        registry,
        settings.app_data_dir,
        skills=SkillService(
            SkillRepository(database),
            WorkspaceAccessService(database),
            settings.skill_bundle_limits,
        ),
        workspace_templates=templates,
        instructions=instructions,
    )
    owner = IdentityContext(
        settings.mock_user_id,
        settings.mock_user_subject,
        settings.mock_user_display_name,
    )
    try:
        workspace = await WorkspaceRepository(database).get("actual")
        existing = await sessions.create("actual", owner)
        before = _claude_md_of(settings.app_data_dir, existing)

        await instructions.save(workspace, owner, "# 改了口径\n", expected_hash=None)

        assert _claude_md_of(settings.app_data_dir, existing) == before
        fresh = await sessions.create("actual", owner)
        assert _claude_md_of(settings.app_data_dir, fresh) == "# 改了口径\n"
    finally:
        await database.dispose()
