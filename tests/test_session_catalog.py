from pathlib import Path

import pytest


def snapshot(*, read=True, skills=("summary",), descriptions=None):
    pinned_descriptions = {
        "summary": "Summarize files",
        "review": "Review a change",
        **(descriptions or {}),
    }
    return {
        "schema_version": 3,
        "skills": [
            {
                "name": name,
                "description": pinned_descriptions.get(name, ""),
            }
            for name in skills
        ],
        "allowed_tools": ["Read", "Skill"] if read else ["Skill"],
    }


def write_skill(workspace: Path, name: str, description: str = "Summarize files"):
    skill_dir = workspace / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n",
        encoding="utf-8",
    )


def test_list_skills_uses_snapshot_order_and_safe_frontmatter(tmp_path: Path):
    from app.sessions.catalog import list_skills

    write_skill(tmp_path, "summary")
    write_skill(tmp_path, "review", "Review a change")
    items = list_skills(tmp_path, snapshot(skills=("review", "summary")))

    assert [(item.name, item.description) for item in items] == [
        ("review", "Review a change"),
        ("summary", "Summarize files"),
    ]


@pytest.mark.parametrize(
    "workspace_state",
    ["missing", "session-symlink", "broken-symlink"],
)
def test_schema_v2_list_skills_never_accesses_the_session_filesystem(
    tmp_path: Path,
    workspace_state: str,
) -> None:
    from app.sessions.catalog import SkillCatalogItem, list_skills

    workspace = tmp_path / "workspace"
    if workspace_state == "session-symlink":
        target = tmp_path / "other-session" / "workspace"
        target.mkdir(parents=True)
        workspace.symlink_to(target, target_is_directory=True)
    elif workspace_state == "broken-symlink":
        workspace.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    pinned_snapshot = {
        "schema_version": 2,
        "skills": [
            {
                "id": "skill-1",
                "name": "review",
                "description": "Pinned review guidance",
                "bundle_hash": "sha256:" + "1" * 64,
                "files": [],
            }
        ],
    }

    assert list_skills(workspace, pinned_snapshot) == (
        SkillCatalogItem("review", "Pinned review guidance"),
    )


def test_schema_v2_session_catalog_remains_readable(tmp_path: Path) -> None:
    from app.sessions.catalog import SkillCatalogItem, list_skills

    snapshot = {
        "schema_version": 2,
        "skills": [
            {
                "id": "legacy-review",
                "name": "review",
                "description": "Pinned schema-v2 review",
                "bundle_hash": "sha256:" + "1" * 64,
                "files": [],
            }
        ],
    }

    assert list_skills(tmp_path / "missing-workspace", snapshot) == (
        SkillCatalogItem("review", "Pinned schema-v2 review"),
    )


def test_schema_v3_list_skills_rejects_external_materialized_skill_link(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "external-skills" / "summary"
    external_skill.mkdir(parents=True)
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Summarize linked files\n---\n",
        encoding="utf-8",
    )
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)
    external_snapshot = snapshot(descriptions={"summary": "Summarize linked files"})
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == ""


def test_schema_v3_list_skills_rejects_linked_skill_file(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "external-skills" / "summary"
    external_skill.mkdir(parents=True)
    linked_skill_file = tmp_path / "linked-SKILL.md"
    linked_skill_file.write_text(
        "---\nname: summary\ndescription: Summarize linked files\n---\n",
        encoding="utf-8",
    )
    (external_skill / "SKILL.md").symlink_to(linked_skill_file)
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)
    external_snapshot = snapshot(descriptions={"summary": "Summarize linked files"})
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == ""


def test_list_skills_rejects_linked_claude_directory_in_external_mode(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external_claude = tmp_path / "external-claude"
    skill_root = external_claude / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "external-skills" / "summary"
    external_skill.mkdir(parents=True)
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Must stay hidden\n---\n",
        encoding="utf-8",
    )
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)
    (workspace / ".claude").symlink_to(external_claude, target_is_directory=True)
    external_snapshot = snapshot(descriptions={"summary": "Must stay hidden"})
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == ""


def test_list_skills_rejects_linked_skills_directory_in_external_mode(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    external_skill_root = tmp_path / "external-skill-root"
    external_skill_root.mkdir()
    external_skill = tmp_path / "external-skills" / "summary"
    external_skill.mkdir(parents=True)
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Must stay hidden\n---\n",
        encoding="utf-8",
    )
    (external_skill_root / "summary").symlink_to(
        external_skill, target_is_directory=True
    )
    (workspace / ".claude" / "skills").symlink_to(
        external_skill_root, target_is_directory=True
    )
    external_snapshot = snapshot(descriptions={"summary": "Must stay hidden"})
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == ""


def test_schema_v3_ignores_obsolete_external_mode_for_copied_skill(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    write_skill(workspace, "summary", "Must stay hidden")
    external_snapshot = snapshot(descriptions={"summary": "Must stay hidden"})
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == (
        "Must stay hidden"
    )


def test_list_skills_rejects_unexpected_link_for_copied_skills(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "unexpected-summary"
    external_skill.mkdir()
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Must stay hidden\n---\n",
        encoding="utf-8",
    )
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)

    assert (
        list_skills(
            workspace,
            snapshot(descriptions={"summary": "Must stay hidden"}),
        )[0].description
        == ""
    )


@pytest.mark.parametrize(
    "body",
    [
        "not frontmatter",
        "---\nname: summary\ndescription: [broken\n---\n",
        "---\nname: summary\ndescription: missing close",
        "---\nname: summary\ndescription: malformed close\n---garbage\n",
        "---\nname: summary\ndescription: " + ("x" * (64 * 1024)) + "\n---\n",
    ],
)
def test_invalid_or_oversized_skill_frontmatter_has_empty_description(
    tmp_path: Path, body: str
):
    from app.sessions.catalog import list_skills

    skill = tmp_path / ".claude/skills/summary/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(body, encoding="utf-8")
    assert list_skills(tmp_path, snapshot())[0].description == ""


def test_deeply_nested_bounded_skill_frontmatter_has_empty_description(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import MAX_SKILL_FRONTMATTER_BYTES, list_skills

    nesting = 2_000
    body = (
        "---\ndescription: " + ("[" * nesting) + "nested" + ("]" * nesting) + "\n---\n"
    )
    assert len(body.encode("utf-8")) < MAX_SKILL_FRONTMATTER_BYTES
    skill = tmp_path / ".claude/skills/summary/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(body, encoding="utf-8")

    assert list_skills(tmp_path, snapshot())[0].description == ""


def test_search_files_filters_internal_hidden_and_symlink_paths(tmp_path: Path):
    from app.sessions.catalog import search_files

    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs/report.html").write_text("report", encoding="utf-8")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments/notes.txt").write_text("notes", encoding="utf-8")
    (tmp_path / ".hidden").write_text("hidden", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("system", encoding="utf-8")
    (tmp_path / "workspace.snapshot.yaml").write_text("snapshot", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "escape.txt").symlink_to(outside)

    result = search_files(tmp_path, snapshot(), "")

    assert [item.path for item in result.items] == [
        "attachments/notes.txt",
        "outputs/report.html",
    ]
    assert result.truncated is False


def test_search_files_excludes_cache_directories_and_keeps_quoted_file(
    tmp_path: Path,
):
    from app.sessions.catalog import search_files

    for directory in ("node_modules", "__pycache__", "cache"):
        excluded = tmp_path / directory / "matching-report.txt"
        excluded.parent.mkdir()
        excluded.write_text("excluded", encoding="utf-8")
    outside_dir = tmp_path.parent / "outside-directory"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "matching-report.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "nested-link").symlink_to(outside_dir, target_is_directory=True)
    quoted = tmp_path / 'quoted "report".txt'
    quoted.write_text("quoted", encoding="utf-8")

    result = search_files(tmp_path, snapshot(), "report")

    assert [item.path for item in result.items] == ['quoted "report".txt']
    assert result.items[0].name == 'quoted "report".txt'
    assert result.truncated is False


def test_search_files_matches_case_insensitively(tmp_path: Path):
    from app.sessions.catalog import search_files

    report = tmp_path / "outputs" / "report.html"
    report.parent.mkdir()
    report.write_text("report", encoding="utf-8")

    result = search_files(tmp_path, snapshot(), "REPORT")

    assert [item.path for item in result.items] == ["outputs/report.html"]


def test_search_limits_are_deterministic(tmp_path: Path):
    from app.sessions.catalog import search_files

    for name in ("c.txt", "a.txt", "b.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    limited = search_files(tmp_path, snapshot(), ".txt", result_limit=2)
    scanned = search_files(tmp_path, snapshot(), ".txt", scan_limit=2)
    assert [item.path for item in limited.items] == ["a.txt", "b.txt"]
    assert limited.truncated is True
    assert scanned.truncated is True


def test_search_does_not_consume_entries_past_scan_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.sessions import catalog

    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    real_scandir = catalog.os.scandir
    next_calls = 0

    class GuardedScandir:
        def __init__(self, path: Path):
            self.inner = real_scandir(path)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal next_calls
            if next_calls >= 2:
                raise AssertionError("scandir iterator consumed past scan_limit")
            next_calls += 1
            return next(self.inner)

    monkeypatch.setattr(catalog.os, "scandir", GuardedScandir)

    result = catalog.search_files(tmp_path, snapshot(), "", scan_limit=2)

    assert next_calls == 2
    assert result.truncated is True


def test_search_marks_directory_scan_errors_as_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from app.sessions import catalog

    def failing_scandir(path: Path):
        raise OSError(f"cannot scan {path}")

    monkeypatch.setattr(catalog.os, "scandir", failing_scandir)

    result = catalog.search_files(tmp_path, snapshot(), "")

    assert result.items == ()
    assert result.truncated is True


@pytest.mark.parametrize("operation", ["resolve", "stat"])
def test_search_marks_concurrent_file_changes_as_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    from app.sessions.catalog import search_files

    unstable = tmp_path / "unstable.txt"
    unstable.write_text("unstable", encoding="utf-8")
    original = getattr(Path, operation)

    def fail_for_unstable(self, *args, **kwargs):
        if self.name == unstable.name:
            raise FileNotFoundError("file disappeared during search")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail_for_unstable)

    result = search_files(tmp_path, snapshot(), "")

    assert result.items == ()
    assert result.truncated is True


def test_validate_file_references_rejects_escape_duplicates_and_missing_read(
    tmp_path: Path,
):
    from app.errors import AppError
    from app.sessions.catalog import validate_file_references

    (tmp_path / "report.txt").write_text("report", encoding="utf-8")
    assert validate_file_references(tmp_path, snapshot(), ["report.txt"]) == (
        "report.txt",
    )

    for references, expected_code in [
        (["../outside.txt"], "file_reference_invalid"),
        (["report.txt", "report.txt"], "file_reference_invalid"),
    ]:
        with pytest.raises(AppError) as exc_info:
            validate_file_references(tmp_path, snapshot(), references)
        assert exc_info.value.code == expected_code

    with pytest.raises(AppError) as exc_info:
        validate_file_references(tmp_path, snapshot(read=False), ["report.txt"])
    assert exc_info.value.code == "file_reference_unavailable"
    assert validate_file_references(tmp_path, snapshot(read=False), []) == ()


def test_validate_file_references_rejects_symlinks_exclusions_and_overflow(
    tmp_path: Path,
):
    from app.errors import AppError
    from app.sessions.catalog import validate_file_references

    outside = tmp_path.parent / "outside-reference.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)
    hidden = tmp_path / ".hidden" / "report.txt"
    hidden.parent.mkdir()
    hidden.write_text("hidden", encoding="utf-8")
    cached = tmp_path / "cache" / "report.txt"
    cached.parent.mkdir()
    cached.write_text("cached", encoding="utf-8")

    for references in (
        ["linked.txt"],
        [".hidden/report.txt"],
        ["cache/report.txt"],
        [f"report-{index}.txt" for index in range(21)],
    ):
        with pytest.raises(AppError) as exc_info:
            validate_file_references(tmp_path, snapshot(), references)
        assert exc_info.value.code == "file_reference_invalid"


def test_validate_file_references_rejects_symlinked_workspace_root(
    tmp_path: Path,
) -> None:
    from app.errors import AppError
    from app.sessions.catalog import validate_file_references

    outside_workspace = tmp_path / "outside-workspace"
    outside_workspace.mkdir()
    (outside_workspace / "report.txt").write_text("outside", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.symlink_to(outside_workspace, target_is_directory=True)

    with pytest.raises(AppError) as exc_info:
        validate_file_references(workspace, snapshot(), ["report.txt"])

    assert exc_info.value.code == "file_reference_invalid"


def test_validate_file_references_rejects_symlink_in_session_containment_chain(
    tmp_path: Path,
) -> None:
    from app.errors import AppError
    from app.sessions.catalog import validate_file_references

    external_sessions = tmp_path / "external-sessions"
    workspace = external_sessions / "session-id" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "report.txt").write_text("outside", encoding="utf-8")
    linked_sessions = tmp_path / "sessions"
    linked_sessions.symlink_to(external_sessions, target_is_directory=True)

    with pytest.raises(AppError) as exc_info:
        validate_file_references(
            linked_sessions / "session-id" / "workspace",
            snapshot(),
            ["report.txt"],
        )

    assert exc_info.value.code == "file_reference_invalid"


def test_validate_file_references_allows_alias_above_session_containment_chain(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import validate_file_references

    real_data = tmp_path / "real-data"
    workspace = real_data / "sessions" / "session-id" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "report.txt").write_text("report", encoding="utf-8")
    data_alias = tmp_path / "data-alias"
    data_alias.symlink_to(real_data, target_is_directory=True)

    assert validate_file_references(
        data_alias / "sessions" / "session-id" / "workspace",
        snapshot(),
        ["report.txt"],
    ) == ("report.txt",)


def test_catalogs_allow_alias_above_session_containment_chain(tmp_path: Path) -> None:
    from app.sessions.catalog import list_skills, search_files

    real_data = tmp_path / "real-data"
    workspace = real_data / "sessions" / "session-id" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "report.txt").write_text("report", encoding="utf-8")
    write_skill(workspace, "summary", "Summary through data alias")
    data_alias = tmp_path / "data-alias"
    data_alias.symlink_to(real_data, target_is_directory=True)
    aliased_workspace = data_alias / "sessions" / "session-id" / "workspace"

    aliased_snapshot = snapshot(descriptions={"summary": "Summary through data alias"})
    assert list_skills(aliased_workspace, aliased_snapshot)[0].description == (
        "Summary through data alias"
    )
    assert [
        item.path for item in search_files(aliased_workspace, snapshot(), "").items
    ] == ["report.txt"]
