import shutil
import sys
from pathlib import Path

import pytest
import yaml


def resolved_bundle(skill_id, bundle):
    from datetime import UTC, datetime

    from app.skills.artifacts import build_skill_artifact
    from app.skills.models import (
        ManagedSkillSummary,
        ResolvedSkillBundle,
        SkillVersionRef,
    )

    artifact = build_skill_artifact(bundle)
    return ResolvedSkillBundle(
        ManagedSkillSummary(
            id=skill_id,
            scope="workspace",
            workspace_id="actual",
            name=bundle.name,
            description=bundle.description,
            enabled=True,
            origin={"type": "test"},
            version=SkillVersionRef(
                id=f"version:{skill_id}",
                version_no=1,
                bundle_hash=artifact.bundle_hash,
                artifact_key=artifact.artifact_key,
                artifact_sha256=artifact.artifact_sha256,
                manifest_json=artifact.manifest_json,
                size_bytes=artifact.size_bytes,
            ),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        bundle,
    )


def write_workspace(
    root: Path,
    directory: str,
    *,
    workspace_id: str | None = None,
    skills: tuple[str, ...] = ("summary",),
    mcp: str = "{}",
) -> Path:
    workspace = root / directory
    workspace.mkdir()
    workspace_id = workspace_id or directory
    skill_yaml = "\n".join(f"  - {name}" for name in skills)
    (workspace / "workspace.yaml").write_text(
        f"""version: 1
id: {workspace_id}
name: {directory.title()}
description: Test workspace {directory}
skills:
{skill_yaml or "  []"}
allowed_tools:
  - Read
  - Skill
mcp_servers: {mcp}
""",
        encoding="utf-8",
    )
    for skill in skills:
        skill_dir = workspace / ".claude" / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: {skill}\n---\n# {skill}\n",
            encoding="utf-8",
        )
    return workspace


def test_example_workspace_declares_ahs_hive_mcp() -> None:
    root = Path(__file__).parents[1]
    manifest = yaml.safe_load(
        (root / "workspaces/example/workspace.yaml").read_text(encoding="utf-8")
    )

    assert manifest["mcp_servers"]["ahs_hive_query"] == {
        "type": "stdio",
        "command": "/Users/a110356/miniconda3/bin/python",
        "entrypoint_env": "AHS_HIVE_QUERY_MCP_ENTRYPOINT",
    }
    assert "AHS_HIVE_QUERY_MCP_ENTRYPOINT=" in (root / ".env.example").read_text(
        encoding="utf-8"
    )


def test_example_workspace_allows_hive_and_mcp_resource_discovery() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).parents[1] / "workspaces/example/workspace.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert "mcp__ahs_hive_query__*" in manifest["allowed_tools"]
    assert "ListMcpResourcesTool" in manifest["allowed_tools"]


def test_example_workspace_does_not_require_remote_documentation_mcps() -> None:
    manifest = yaml.safe_load(
        (Path(__file__).parents[1] / "workspaces/example/workspace.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert "anthropicDeveloperDocs" not in manifest["mcp_servers"]
    assert "openaiDeveloperDocs" not in manifest["mcp_servers"]
    assert not any("DeveloperDocs" in tool for tool in manifest["allowed_tools"])


def test_registry_isolates_invalid_workspaces(tmp_path: Path) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(root, "valid")
    invalid = root / "invalid"
    invalid.mkdir()
    (invalid / "workspace.yaml").write_text("version: [broken", encoding="utf-8")

    registry = WorkspaceRegistry(root, default_model="claude-default", environ={})
    entries = registry.scan()

    assert [entry.id for entry in entries] == ["invalid", "valid"]
    assert entries[0].available is False
    assert entries[0].validation_errors
    assert entries[1].available is True
    assert entries[1].manifest is not None
    assert entries[1].manifest.model == "claude-default"


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("id-mismatch", "must match directory"),
        ("missing-mcp-env", "MCP_URL"),
    ],
)
def test_registry_rejects_invalid_workspace_contracts(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    if mutation == "id-mismatch":
        write_workspace(root, "actual", workspace_id="different")
    else:
        write_workspace(
            root,
            "actual",
            mcp="{gateway: {type: http, url_env: MCP_URL}}",
        )

    entry = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]

    assert entry.available is False
    assert expected_error in " ".join(entry.validation_errors)


def test_registry_keeps_workspace_available_when_legacy_skill_source_is_missing(
    tmp_path: Path,
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(root, "actual", skills=("missing",))
    shutil.rmtree(root / "actual/.claude/skills/missing")

    entry = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]

    assert entry.available is True
    assert entry.skills_source_root == root / "actual/.claude/skills"


def test_registry_accepts_valid_http_sse_and_stdio_mcp_servers(tmp_path: Path) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    entrypoint = tmp_path / "server.py"
    entrypoint.write_text("print('mcp')\n", encoding="utf-8")
    write_workspace(
        root,
        "actual",
        mcp=f"""
  docs:
    type: http
    url_env: DOCS_MCP_URL
  sqlbot:
    type: sse
    url_env: SQLBOT_MCP_URL
  davinci_data:
    type: http
    url_env: DAVINCI_DATA_MCP_URL
    authorization_source: davinci_session
  local:
    type: stdio
    command: {sys.executable}
    entrypoint_env: LOCAL_MCP_ENTRYPOINT
    args: [--stdio]
    env_vars: [LOCAL_MCP_TOKEN]
""",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={
            "DOCS_MCP_URL": "https://mcp.example.test",
            "SQLBOT_MCP_URL": "https://sqlbot.example.test/mcp",
            "DAVINCI_DATA_MCP_URL": "https://data-mcp.example.test",
            "LOCAL_MCP_ENTRYPOINT": str(entrypoint),
            "LOCAL_MCP_TOKEN": "configured",
        },
    ).scan()[0]

    assert entry.available is True
    assert entry.validation_errors == ()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/mcp",
        "http://localhost:8000/mcp",
        "http://[::1]:8000/mcp",
    ],
)
def test_registry_accepts_explicit_loopback_http_when_enabled(
    tmp_path: Path, url: str
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(
        root,
        "actual",
        mcp="{davinci_data: {type: http, url_env: DAVINCI_DATA_MCP_URL}}",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={"DAVINCI_DATA_MCP_URL": url},
        allow_loopback_http_mcp=True,
    ).scan()[0]

    assert entry.available is True


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test:8000/mcp",
        "http://localhost.evil.test:8000/mcp",
        "http://user@localhost:8000/mcp",
        "http://user:secret@127.0.0.1:8000/mcp",
        "http://localhost/mcp",
        "http://localhost:invalid/mcp",
        "https://user:secret@mcp.example.test/mcp",
        "https://mcp.example.test:invalid/mcp",
    ],
)
def test_registry_rejects_unsafe_http_even_when_loopback_is_enabled(
    tmp_path: Path, url: str
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(
        root,
        "actual",
        mcp="{davinci_data: {type: http, url_env: DAVINCI_DATA_MCP_URL}}",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={"DAVINCI_DATA_MCP_URL": url},
        allow_loopback_http_mcp=True,
    ).scan()[0]

    assert entry.available is False
    assert "HTTPS or explicit loopback HTTP URL" in " ".join(entry.validation_errors)


def test_registry_rejects_loopback_http_by_default(tmp_path: Path) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(
        root,
        "actual",
        mcp="{davinci_data: {type: http, url_env: DAVINCI_DATA_MCP_URL}}",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={"DAVINCI_DATA_MCP_URL": "http://127.0.0.1:8000/mcp"},
    ).scan()[0]

    assert entry.available is False


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing-entrypoint", "LOCAL_MCP_ENTRYPOINT"),
        ("relative-entrypoint", "absolute file"),
        ("directory-entrypoint", "regular file"),
        ("symlink-entrypoint", "symbolic link"),
        ("missing-command", "command"),
        ("missing-forwarded-env", "LOCAL_MCP_TOKEN"),
    ],
)
def test_registry_rejects_invalid_stdio_mcp_servers(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    entrypoint = tmp_path / "server.py"
    entrypoint.write_text("print('mcp')\n", encoding="utf-8")
    configured_entrypoint = str(entrypoint)
    command = sys.executable
    environ = {
        "LOCAL_MCP_ENTRYPOINT": configured_entrypoint,
        "LOCAL_MCP_TOKEN": "configured",
    }

    if mutation == "missing-entrypoint":
        environ.pop("LOCAL_MCP_ENTRYPOINT")
    elif mutation == "relative-entrypoint":
        environ["LOCAL_MCP_ENTRYPOINT"] = "server.py"
    elif mutation == "directory-entrypoint":
        environ["LOCAL_MCP_ENTRYPOINT"] = str(tmp_path)
    elif mutation == "symlink-entrypoint":
        linked_entrypoint = tmp_path / "linked-server.py"
        linked_entrypoint.symlink_to(entrypoint)
        environ["LOCAL_MCP_ENTRYPOINT"] = str(linked_entrypoint)
    elif mutation == "missing-command":
        command = str(tmp_path / "missing-python")
    else:
        environ.pop("LOCAL_MCP_TOKEN")

    write_workspace(
        root,
        "actual",
        mcp=f"""
  local:
    type: stdio
    command: {command}
    entrypoint_env: LOCAL_MCP_ENTRYPOINT
    args: [--stdio]
    env_vars: [LOCAL_MCP_TOKEN]
""",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ=environ,
    ).scan()[0]

    assert entry.available is False
    error = " ".join(entry.validation_errors)
    assert "local" in error
    assert expected_error in error


def test_registry_requires_https_mcp_url(tmp_path: Path) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    write_workspace(
        root,
        "actual",
        mcp="{docs: {type: http, url_env: DOCS_MCP_URL}}",
    )

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={"DOCS_MCP_URL": "http://mcp.example.test"},
    ).scan()[0]

    assert entry.available is False
    error = " ".join(entry.validation_errors)
    assert "docs" in error
    assert "HTTPS" in error


def test_registry_keeps_workspace_available_when_legacy_skill_is_symlinked(
    tmp_path: Path,
) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    workspace = write_workspace(root, "actual", skills=())
    external = tmp_path / "external-skill"
    external.mkdir()
    (external / "SKILL.md").write_text("# external", encoding="utf-8")
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    (skill_root / "linked").symlink_to(external, target_is_directory=True)
    manifest = (workspace / "workspace.yaml").read_text(encoding="utf-8")
    (workspace / "workspace.yaml").write_text(
        manifest.replace("skills:\n  []", "skills:\n  - linked"),
        encoding="utf-8",
    )

    entry = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]

    assert entry.available is True
    assert entry.validation_errors == ()


def test_external_skill_root_is_only_a_bootstrap_source_for_new_sessions(
    tmp_path: Path,
) -> None:
    from app.sessions.snapshot import build_session_snapshot
    from app.skills.bundle import build_bundle
    from app.workspaces.materializer import materialize_session_workspace
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    workspace = write_workspace(root, "actual", skills=("summary",))
    manifest_path = workspace / "workspace.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "skills:\n", "skills_root_env: CLAUDE_SKILLS_ROOT\nskills:\n"
        ),
        encoding="utf-8",
    )

    missing = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]
    assert missing.available is True
    assert missing.skills_source_root is None

    actual_skill = tmp_path / "actual-summary-skill"
    actual_skill.mkdir()
    (actual_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Live external summary\n---\nLive.\n",
        encoding="utf-8",
    )
    external_root = tmp_path / "user-skills"
    external_root.mkdir()
    (external_root / "summary").symlink_to(actual_skill, target_is_directory=True)

    entry = WorkspaceRegistry(
        root,
        "claude-default",
        environ={"CLAUDE_SKILLS_ROOT": str(external_root)},
    ).scan()[0]
    assert entry.available is True
    assert entry.skills_source_root == external_root
    assert entry.link_skills is True

    bundle = build_bundle(
        b"---\nname: summary\ndescription: Pinned managed summary\n---\nPinned.\n",
        [],
    )
    skills = (resolved_bundle("skill-1", bundle),)
    snapshot = build_session_snapshot(entry, skills)
    materialized = materialize_session_workspace(
        entry,
        "external-skills-session",
        tmp_path / "data",
        snapshot,
        skills,
    )
    pinned_skill = materialized.workspace_dir / ".claude/skills/summary"
    assert pinned_skill.is_symlink() is False
    assert (pinned_skill / "SKILL.md").read_text(encoding="utf-8") == bundle.content
    assert "skills_root_env" not in snapshot.data


def test_registry_rejects_skill_path_traversal(tmp_path: Path) -> None:
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    workspace = write_workspace(root, "actual", skills=())
    manifest_path = workspace / "workspace.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "skills:\n  []", "skills:\n  - ../outside"
        ),
        encoding="utf-8",
    )

    entry = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]

    assert entry.available is False
    assert "simple directory name" in " ".join(entry.validation_errors)


def test_materializer_copies_only_managed_bundles_and_workspace_seed(
    tmp_path: Path,
) -> None:
    from app.sessions.snapshot import build_session_snapshot
    from app.skills.bundle import build_bundle
    from app.workspaces.materializer import materialize_session_workspace
    from app.workspaces.registry import WorkspaceRegistry

    root = tmp_path / "workspaces"
    root.mkdir()
    workspace = write_workspace(root, "actual", skills=("summary",))
    extra = workspace / ".claude" / "skills" / "not-selected"
    extra.mkdir(parents=True)
    (extra / "SKILL.md").write_text("# hidden", encoding="utf-8")
    (workspace / "CLAUDE.md").write_text("Persistent rules", encoding="utf-8")
    seed = workspace / "seed"
    seed.mkdir()
    (seed / "reference.md").write_text("Reference", encoding="utf-8")
    seeded_skill = seed / ".claude" / "skills" / "seed-only"
    seeded_skill.mkdir(parents=True)
    (seeded_skill / "SKILL.md").write_text(
        "---\nname: seed-only\ndescription: Must not be materialized\n---\n",
        encoding="utf-8",
    )

    entry = WorkspaceRegistry(root, "claude-default", environ={}).scan()[0]
    bundle = build_bundle(
        b"---\nname: review\ndescription: Managed review\n---\nReview.\n",
        [("references/policy.bin", b"\x00\xffpolicy")],
    )
    skills = (resolved_bundle("skill-1", bundle),)
    snapshot = build_session_snapshot(entry, skills)
    materialized = materialize_session_workspace(
        entry,
        "session-1",
        tmp_path / "data",
        snapshot,
        skills,
    )

    assert materialized.workspace_dir.joinpath("CLAUDE.md").read_text() == (
        "Persistent rules"
    )
    assert materialized.workspace_dir.joinpath("reference.md").read_text() == (
        "Reference"
    )
    assert (
        materialized.workspace_dir.joinpath(".claude/skills/review/SKILL.md").read_text(
            encoding="utf-8"
        )
        == bundle.content
    )
    assert (
        materialized.workspace_dir.joinpath(
            ".claude/skills/review/references/policy.bin"
        ).read_bytes()
        == b"\x00\xffpolicy"
    )
    assert not materialized.workspace_dir.joinpath(".claude/skills/summary").exists()
    assert not materialized.workspace_dir.joinpath(
        ".claude/skills/not-selected"
    ).exists()
    assert not materialized.workspace_dir.joinpath(".claude/skills/seed-only").exists()
    assert materialized.workspace_dir.joinpath("attachments").is_dir()
    assert materialized.workspace_dir.joinpath("outputs").is_dir()
    assert materialized.claude_config_dir.is_dir()
    assert len(materialized.snapshot_hash) == 64
    assert '"id":"actual"' in materialized.snapshot_json
    assert '"schema_version":4' in materialized.snapshot_json
    assert str(workspace) not in materialized.snapshot_json
