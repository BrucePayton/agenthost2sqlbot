# Browser Directory Skill Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Workspace owner or admin import a complete Skill folder directly from the browser, while keeping archive import under the same entry and making same-name conflicts explicit, idempotent, and safe.

**Architecture:** Add a browser-upload adapter that converts relative-path file parts into the existing immutable `SkillBundle`, then route both new and future import transports through one service-level conflict policy. Keep multipart parsing bounded and streaming, keep Skill persistence atomic, and reuse the existing lifecycle-aware Skill manager so late uploads cannot mutate a reopened manager or another Workspace.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy asyncio, PyYAML, `python-multipart`, browser-native JavaScript/CSS, Node `node:test`, pytest, Playwright Chromium.

## Global Constraints

- The browser uploads bytes and relative paths; the server never reads an absolute path from the user's computer.
- One visible `导入` action exposes exactly two choices: a Skill folder or a `.skill`/`.zip` archive.
- Directory upload requires one selected top-level folder and exactly one `SKILL.md` after stripping that folder component.
- Preserve supporting files and paths byte-for-byte; do not create temporary source directories and do not execute uploaded scripts.
- New and renamed imports start disabled.
- Same name plus same bundle hash returns `already_imported` without changing data or timestamps.
- Same name plus different hash returns `skill_import_conflict` until the user explicitly chooses overwrite, rename, or cancel.
- Overwrite preserves the Skill ID and enabled state, replaces the complete bundle atomically, updates origin, and checks `expected_hash`.
- Rename changes the uploaded copy's YAML `name`, rebuilds the canonical bundle/hash, leaves the original untouched, and inserts a new disabled Skill.
- Existing Session snapshots remain immutable; only Sessions created after enablement receive the imported Skill.
- Enforce 10 MiB per supporting file, 50 MiB total bundle bytes, 200 supporting files, and bounded multipart overhead through the existing configured limits.
- Authenticate and require Workspace manager access before reading the request body.
- Keep `POST /api/workspaces/{workspace_id}/skills/import` and its archive semantics unchanged.
- No database migration is needed: `skills.config_json` already stores origin and `skill_files` already stores full bundle files.
- Runtime scanning, Git/marketplace import, continuous sync, multiple Skill roots, version history, and script security auditing are out of scope.
- Do not add, delete, stage, or modify the unrelated untracked files `agents.json`, `description.md`, `members.json`, or `squads.json`.

---

## File Structure

The implementation keeps domain parsing, transport parsing, persistence, and UI state separate:

- Modify `app/skills/bundle.py`: uploaded-path normalization, top-level folder stripping, canonical rename, and existing bundle validation reuse.
- Create `app/skills/uploads.py`: bounded multipart readers for archive and directory uploads.
- Modify `app/skills/repository.py`: active-name lookup and origin-aware optimistic bundle replacement.
- Modify `app/skills/service.py`: import result type and `fail`/`overwrite`/`rename` policy.
- Modify `app/skills/schemas.py`: directory-import result envelope.
- Modify `app/skills/routes.py`: archive parser extraction and new directory endpoint.
- Modify `app/web/templates/index.html`: unified import menu, two hidden inputs, and conflict dialog.
- Modify `app/web/static/skill-manager.js`: directory upload and conflict-resolution state machine.
- Modify `app/web/static/app.js`: wire the new elements and retain structured API error details.
- Modify `app/web/static/app.css`: import menu and conflict dialog styling.
- Modify `README.md`: explain folder/archive imports, limits, disablement, and conflict behavior.
- Modify `tests/test_skill_bundle.py`: uploaded-folder and rename unit coverage.
- Modify `tests/test_skill_service.py`: idempotency, conflict, overwrite, rename, concurrency, and authorization coverage.
- Create `tests/test_skill_uploads.py`: multipart streaming, limit, and early-stop coverage.
- Modify `tests/test_skill_api.py`: route contract, status, authorization-before-read, and stable error envelope coverage.
- Modify `tests/js/test_skill_manager.cjs`: import-menu, `FormData`, conflict, cancellation, and stale-response controller coverage.
- Modify `tests/test_web_page.py`: required DOM contract and script ordering smoke coverage.
- Modify `tests/browser/test_workbench.py`: real Chromium folder selection, conflict resolution, snapshot pinning, and slash-menu regression coverage.

---

### Task 1: Convert browser-selected files into a canonical Skill bundle

**Files:**
- Modify: `app/skills/bundle.py`
- Modify: `tests/test_skill_bundle.py`

**Interfaces:**
- Produces: `UploadedSkillDirectory(source_name: str, bundle: SkillBundle)`.
- Produces: `load_bundle_from_uploaded_files(files, limits=None) -> UploadedSkillDirectory`.
- Produces: `rename_bundle(bundle, target_name, limits=None) -> SkillBundle`.
- Reuses: `_validate_relative_path()`, `_has_path_conflict()`, `build_bundle()`, and `SKILL_NAME_RE`.

- [ ] **Step 1: Add failing tests for path normalization and exact one-level stripping**

Append focused tests to `tests/test_skill_bundle.py`:

```python
def test_uploaded_directory_strips_one_root_and_preserves_binary_files() -> None:
    from app.skills.bundle import load_bundle_from_uploaded_files

    uploaded = load_bundle_from_uploaded_files(
        [
            (
                "brainstorming/SKILL.md",
                b"---\nname: brainstorming\ndescription: Generate ideas\n---\nBody\n",
            ),
            ("brainstorming/scripts/run.py", b"print('ok')\n"),
            ("brainstorming/assets/icon.bin", b"\x00\xff"),
        ]
    )

    assert uploaded.source_name == "brainstorming"
    assert uploaded.bundle.name == "brainstorming"
    assert [(item.path, item.content) for item in uploaded.bundle.files] == [
        ("assets/icon.bin", b"\x00\xff"),
        ("scripts/run.py", b"print('ok')\n"),
    ]


@pytest.mark.parametrize(
    "paths",
    [
        ["SKILL.md"],
        ["one/SKILL.md", "two/script.py"],
        ["one/nested/SKILL.md"],
        ["one/../SKILL.md"],
        ["one\\SKILL.md"],
        ["/one/SKILL.md"],
        ["one/SKILL.md", "one/SKILL.md"],
        ["one/SKILL.md", "one/scripts", "one/scripts/run.py"],
    ],
)
def test_uploaded_directory_rejects_invalid_roots_and_paths(paths: list[str]) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_uploaded_files

    entries = [
        (
            path,
            b"---\nname: uploaded\ndescription: Uploaded\n---\nBody\n"
            if path.endswith("SKILL.md")
            else b"support",
        )
        for path in paths
    ]
    with pytest.raises(AppError) as exc_info:
        load_bundle_from_uploaded_files(entries)
    assert (exc_info.value.status_code, exc_info.value.code) == (
        422,
        "invalid_skill_bundle",
    )
```

Also add exact-boundary tests using `SkillBundleLimits(max_file_bytes=4, max_total_bytes=128, max_files=1)`: one root plus one four-byte supporting file passes; a fifth byte, a second supporting file, or total size plus one fails with `413 skill_bundle_too_large`.

- [ ] **Step 2: Add failing tests for canonical rename**

```python
def test_rename_bundle_changes_only_uploaded_identity_and_rehashes() -> None:
    from app.skills.bundle import build_bundle, rename_bundle

    original = build_bundle(
        b"---\nname: original\ndescription: Keep this text\nmetadata: kept\n---\n# Body\n",
        [("references/policy.bin", b"\x00\xff")],
    )
    renamed = rename_bundle(original, "renamed-copy")

    assert original.name == "original"
    assert renamed.name == "renamed-copy"
    assert renamed.description == original.description
    assert "metadata: kept" in renamed.content
    assert renamed.content.endswith("# Body\n")
    assert renamed.files == original.files
    assert renamed.bundle_hash != original.bundle_hash
```

Add invalid target cases for an empty name, whitespace-only name, slash, and more than 128 characters; each must return `422 invalid_skill_bundle`.

- [ ] **Step 3: Run the new bundle tests and verify they fail**

Run:

```bash
uv run pytest tests/test_skill_bundle.py -q
```

Expected: FAIL because the uploaded-directory value object and both public helpers do not exist.

- [ ] **Step 4: Implement the uploaded-directory adapter**

Add this value object and public function in `app/skills/bundle.py`:

```python
@dataclass(frozen=True)
class UploadedSkillDirectory:
    source_name: str
    bundle: SkillBundle


def load_bundle_from_uploaded_files(
    files: Iterable[tuple[str, bytes]],
    limits: SkillBundleLimits | None = None,
) -> UploadedSkillDirectory:
    limits = limits or SkillBundleLimits()
    _validate_limits(limits)
    source_name: str | None = None
    contents: dict[str, bytes] = {}
    total_bytes = 0
    supporting_files = 0

    for position, entry in enumerate(files, start=1):
        if position > limits.max_files + 1:
            raise bundle_too_large("Skill bundle exceeds the file count limit.")
        try:
            browser_path, content = entry
        except (TypeError, ValueError) as exc:
            raise invalid_bundle("Skill bundle file entry is invalid.") from exc
        normalized = _validate_relative_path(browser_path)
        parts = normalized.split("/")
        if len(parts) < 2:
            raise invalid_bundle("Select one Skill directory, not individual files.")
        if source_name is None:
            source_name = parts[0]
        elif parts[0] != source_name:
            raise invalid_bundle("Skill upload must contain exactly one directory root.")
        relative_path = _validate_relative_path("/".join(parts[1:]))
        if relative_path in contents or _has_path_conflict(relative_path, set(contents)):
            raise invalid_bundle("Skill bundle contains duplicate or conflicting file paths.")
        if not isinstance(content, bytes):
            raise invalid_bundle("Skill bundle file content must be bytes.")
        if relative_path != "SKILL.md":
            supporting_files += 1
            if supporting_files > limits.max_files:
                raise bundle_too_large("Skill bundle exceeds the file count limit.")
            if len(content) > limits.max_file_bytes:
                raise bundle_too_large(f"{relative_path} exceeds the per-file size limit.")
        total_bytes += len(content)
        if total_bytes > limits.max_total_bytes:
            raise bundle_too_large("Skill bundle exceeds the total size limit.")
        contents[relative_path] = content

    if source_name is None or "SKILL.md" not in contents:
        raise invalid_bundle("Skill bundle must contain exactly one root SKILL.md.")
    bundle = build_bundle(contents.pop("SKILL.md"), contents.items(), limits)
    return UploadedSkillDirectory(source_name=source_name, bundle=bundle)
```

Keep the adapter platform-independent: no temporary directory, `Path`, filesystem stat, or symlink logic belongs in this function because the browser has already reduced entries to names plus bytes.

- [ ] **Step 5: Implement canonical rename through the existing builder**

Add `rename_bundle()` next to the other public bundle helpers:

```python
def rename_bundle(
    bundle: SkillBundle,
    target_name: str,
    limits: SkillBundleLimits | None = None,
) -> SkillBundle:
    normalized_name = target_name.strip() if isinstance(target_name, str) else ""
    if not SKILL_NAME_RE.fullmatch(normalized_name):
        raise invalid_bundle("Skill name is invalid.")
    raw = bundle.content.encode("utf-8")
    closing = raw.find(b"\n---\n", 4)
    metadata = yaml.safe_load(raw[4:closing].decode("utf-8"))
    if not isinstance(metadata, dict):
        raise invalid_bundle("SKILL.md frontmatter is invalid.")
    metadata["name"] = normalized_name
    frontmatter = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).encode("utf-8")
    rewritten = b"---\n" + frontmatter + b"---\n" + raw[closing + len(b"\n---\n") :]
    return build_bundle(
        rewritten,
        [(item.path, item.content) for item in bundle.files],
        limits,
    )
```

The input bundle is already validated, so `closing` is valid. Rebuilding still revalidates the renamed output and recomputes its deterministic hash.

- [ ] **Step 6: Run focused tests and commit**

```bash
uv run pytest tests/test_skill_bundle.py -q
git add app/skills/bundle.py tests/test_skill_bundle.py
git commit -m "feat: parse browser-uploaded skill directories"
```

Expected: all bundle tests pass.

---

### Task 2: Add idempotent import and explicit conflict policies

**Files:**
- Modify: `app/skills/repository.py`
- Modify: `app/skills/service.py`
- Modify: `tests/test_skill_service.py`

**Interfaces:**
- Produces: `SkillImportResult(status, skill)` with statuses `created`, `overwritten`, `renamed`, and `already_imported`.
- Produces: `SkillService.import_uploaded_directory(workspace_id, identity, uploaded, on_conflict, expected_hash, target_name) -> SkillImportResult`.
- Produces: `SkillRepository.get_active_by_name(workspace_id, name) -> StoredSkill | None`.
- Extends: `SkillRepository.replace_bundle(skill_id, expected_hash, bundle, user_id, origin=None)` while preserving existing callers.

- [ ] **Step 1: Write failing service tests for all four outcomes**

Add tests that construct `UploadedSkillDirectory` values through Task 1's loader. Cover these exact assertions:

```python
@pytest.mark.asyncio
async def test_directory_import_is_disabled_idempotent_and_reports_conflict(
    skill_service, owner
) -> None:
    from app.errors import AppError
    from app.skills.bundle import load_bundle_from_uploaded_files

    first_upload = load_bundle_from_uploaded_files(
        [("review/SKILL.md", VALID_SKILL_MD)]
    )
    created = await skill_service.import_uploaded_directory(
        "team", owner, first_upload, on_conflict="fail"
    )
    repeated = await skill_service.import_uploaded_directory(
        "team", owner, first_upload, on_conflict="fail"
    )

    assert created.status == "created"
    assert created.skill.enabled is False
    assert created.skill.origin == {
        "type": "browser_directory",
        "source_name": "review",
    }
    assert repeated.status == "already_imported"
    assert repeated.skill.id == created.skill.id

    changed_upload = load_bundle_from_uploaded_files(
        [
            (
                "review/SKILL.md",
                VALID_SKILL_MD.replace(b"Review changes", b"Review safely"),
            )
        ]
    )
    with pytest.raises(AppError) as exc_info:
        await skill_service.import_uploaded_directory(
            "team", owner, changed_upload, on_conflict="fail"
        )
    assert (exc_info.value.status_code, exc_info.value.code) == (
        409,
        "skill_import_conflict",
    )
    assert exc_info.value.details == {
        "skill_id": created.skill.id,
        "existing_hash": created.skill.bundle_hash,
        "incoming_hash": changed_upload.bundle.bundle_hash,
        "incoming_name": "review-changes",
    }
```

Add a separate overwrite test that enables the existing Skill first and proves:

- result status is `overwritten`;
- ID and `enabled=True` are preserved;
- content, full supporting-file set, hash, description, and origin are replaced;
- a stale `expected_hash` returns `409 skill_changed` and leaves the winner unchanged.

Add a rename test that proves:

- status is `renamed`;
- new ID and new name are returned;
- the renamed Skill is disabled;
- description, body, metadata, and supporting bytes match the upload;
- original Skill content, files, enabled state, and hash remain unchanged;
- collision on `target_name` returns `skill_import_conflict`.

Add an authorization test showing an admin succeeds while a member receives the existing fail-closed `skill_not_found` response.

- [ ] **Step 2: Add a failing concurrency test**

Use `asyncio.gather()` to import the same upload twice. Assert exactly one active row exists and the two results consist of one `created` plus one `already_imported`. Repeat with different content but the same case-insensitive name and assert one winner plus one `skill_import_conflict`.

- [ ] **Step 3: Run service tests and verify failure**

```bash
uv run pytest tests/test_skill_service.py -q
```

Expected: FAIL because repository lookup, origin replacement, result type, and directory import policy are missing.

- [ ] **Step 4: Add active-name lookup and origin-aware replacement**

Add this repository query before `insert_bundle()`:

```python
async def get_active_by_name(
    self, workspace_id: str, name: str
) -> StoredSkill | None:
    async with self.database.session() as db:
        record = await db.scalar(
            select(SkillRecord).where(
                SkillRecord.workspace_id == workspace_id,
                SkillRecord.archived_at.is_(None),
                func.lower(SkillRecord.name) == name.casefold(),
            )
        )
        if record is None:
            return None
        return _stored(record, await self._files(db, record.id))
```

Extend `replace_bundle()` with `origin: dict[str, object] | None = None`. Build the SQL values before calling `.values()`:

```python
values: dict[str, object] = {
    "name": bundle.name,
    "description": bundle.description,
    "content": bundle.content,
    "bundle_hash": bundle.bundle_hash,
    "updated_at": now,
}
if origin is not None:
    values["config_json"] = _origin_json(origin)
```

Use `.values(**values)`. Existing edit calls omit `origin` and retain their previous source; overwrite passes the new browser-directory origin. Do not include `enabled` in the update.

- [ ] **Step 5: Implement the service result and policy state machine**

Add imports for `re`, `UploadedSkillDirectory`, and `rename_bundle`, then define:

```python
SkillImportStatus = Literal[
    "created",
    "overwritten",
    "renamed",
    "already_imported",
]
SkillConflictPolicy = Literal["fail", "overwrite", "rename"]
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class SkillImportResult:
    status: SkillImportStatus
    skill: StoredSkill
```

Implement `SkillService.import_uploaded_directory()` with this public contract:

```python
async def import_uploaded_directory(
    self,
    workspace_id: str,
    identity: IdentityContext,
    uploaded: UploadedSkillDirectory,
    *,
    on_conflict: SkillConflictPolicy,
    expected_hash: str | None = None,
    target_name: str | None = None,
) -> SkillImportResult:
```

Apply the following ordered state machine:

1. Call `require_manager()` before any repository read.
2. Reject an unknown policy, fields forbidden for that policy, missing/invalid overwrite hash, and missing rename target with `422 invalid_skill_bundle`.
3. Build origin as `{"type": "browser_directory", "source_name": uploaded.source_name}`.
4. For `rename`, call `rename_bundle()`, reject any active target collision with `skill_import_conflict`, insert disabled, and return `renamed`.
5. For `fail` or `overwrite`, find the active case-insensitive incoming name.
6. If none exists, only `fail` may insert disabled and return `created`; an overwrite resolution whose target disappeared returns `skill_changed`.
7. If existing and incoming hashes match, return `already_imported` before any write.
8. If policy is `fail`, raise `skill_import_conflict` with only `skill_id`, `existing_hash`, `incoming_hash`, and `incoming_name` details.
9. For `overwrite`, require `existing.bundle_hash == expected_hash`, call `replace_bundle()` with the same optimistic hash and new origin, then return `overwritten`.
10. If an insert loses a uniqueness race, re-read the winner by name. Return `already_imported` only if hashes match; otherwise raise `skill_import_conflict`.

Use one private conflict constructor so normal and race paths return identical details:

```python
def _import_conflict(existing: StoredSkill, incoming: SkillBundle) -> AppError:
    return AppError(
        "skill_import_conflict",
        "An active Skill uses this name with different content.",
        409,
        details={
            "skill_id": existing.id,
            "existing_hash": existing.bundle_hash,
            "incoming_hash": incoming.bundle_hash,
            "incoming_name": incoming.name,
        },
    )
```

Catch only `AppError` with `code == "skill_name_conflict"` around inserts; re-raise every other failure unchanged. This preserves database atomicity and prevents infrastructure errors from being mislabeled as import conflicts.

- [ ] **Step 6: Run service and persistence regressions, then commit**

```bash
uv run pytest tests/test_skill_service.py tests/test_database.py tests/test_migrations.py -q
git add app/skills/repository.py app/skills/service.py tests/test_skill_service.py
git commit -m "feat: add skill directory import policies"
```

Expected: all selected tests pass and no migration file changes.

---

### Task 3: Isolate bounded multipart upload parsing

**Files:**
- Create: `app/skills/uploads.py`
- Modify: `app/skills/routes.py`
- Create: `tests/test_skill_uploads.py`
- Modify: `tests/test_skill_api.py`

**Interfaces:**
- Moves: `read_multipart_archive(request, maximum_size) -> bytes` without behavior change.
- Produces: `DirectoryUpload(files, on_conflict, expected_hash, target_name)`.
- Produces: `read_multipart_directory(request, limits) -> DirectoryUpload`.

- [ ] **Step 1: Preserve archive parser behavior with characterization tests**

Rename the test helper `_asgi_import()` in `tests/test_skill_api.py` to `_asgi_skill_post()` and add a `path` argument. Keep all current archive tests pointed at `/api/workspaces/personal/skills/import` and run them before extraction:

```bash
uv run pytest tests/test_skill_api.py -q
```

Expected: PASS. This is the characterization baseline for the refactor.

- [ ] **Step 2: Move the existing archive parser without semantic changes**

Move `_read_multipart_archive`, `_ArchiveMultipartCollector`, archive filename validation, and the four existing multipart constants from `app/skills/routes.py` into `app/skills/uploads.py`. Rename only the public function to remove the leading underscore:

```python
async def read_multipart_archive(request: Request, maximum_size: int) -> bytes:
```

Update the archive route to import and call it. Do not alter filename validation, error codes, early body termination, header limits, or compressed-size enforcement in this step.

Run:

```bash
uv run pytest tests/test_skill_api.py -q
```

Expected: PASS with the same archive contract.

- [ ] **Step 3: Write failing directory multipart tests**

Create `tests/test_skill_uploads.py` with a small `starlette.requests.Request` helper that yields controlled chunks. Test:

- repeated `files` parts and the three allowed text fields are returned exactly;
- omitted `on_conflict` defaults to `fail`;
- unknown fields, duplicate text fields, non-file `files` parts, and file parts under any other field are rejected;
- invalid UTF-8 or unsafe multipart filenames are rejected before bundle parsing;
- exact supporting-file, total-byte, and file-count limits pass;
- one byte or one file beyond a limit stops before a sentinel tail chunk;
- closing boundary stops consumption before tail chunks;
- individual header, per-part header count, aggregate header bytes, and pre-file overhead are bounded.

The successful assertion should use this shape:

```python
upload, chunks_read = await parse_directory_request(
    fields={"on_conflict": "overwrite", "expected_hash": "sha256:" + "a" * 64},
    files=[
        ("brainstorming/SKILL.md", b"skill"),
        ("brainstorming/scripts/run.py", b"print('ok')"),
    ],
)

assert upload.files == (
    ("brainstorming/SKILL.md", b"skill"),
    ("brainstorming/scripts/run.py", b"print('ok')"),
)
assert upload.on_conflict == "overwrite"
assert upload.expected_hash == "sha256:" + "a" * 64
assert upload.target_name is None
assert chunks_read == 1
```

- [ ] **Step 4: Run upload tests and verify failure**

```bash
uv run pytest tests/test_skill_uploads.py -q
```

Expected: FAIL because `DirectoryUpload` and `read_multipart_directory()` do not exist.

- [ ] **Step 5: Implement the bounded directory collector**

Add this immutable result in `app/skills/uploads.py`:

```python
@dataclass(frozen=True)
class DirectoryUpload:
    files: tuple[tuple[str, bytes], ...]
    on_conflict: str
    expected_hash: str | None
    target_name: str | None
```

Implement the directory reader with the same chunk driver used by archive upload:

```python
async def read_multipart_directory(
    request: Request,
    limits: SkillBundleLimits,
) -> DirectoryUpload:
    collector = _DirectoryMultipartCollector(limits)
    await _consume_multipart(
        request,
        collector,
        invalid_message="Skill directory upload is invalid.",
    )
    return collector.finish()
```

The shared `_consume_multipart()` must:

- parse and validate `multipart/form-data` plus a non-empty boundary;
- use `MultipartParser` with the current 32-header per-part and 8 KiB individual-header bounds;
- call `collector.begin_chunk(received_bytes)` before every write;
- break immediately when `collector.ended` becomes true;
- enforce 64 KiB before the first legal file data begins;
- translate header/overhead excess to `413 skill_bundle_too_large`;
- translate malformed multipart input to `422 invalid_skill_bundle`;
- call `parser.finalize()` and then `collector.finish()`.

Implement `_DirectoryMultipartCollector` with these exact invariants:

- legal text names are `on_conflict`, `expected_hash`, and `target_name`;
- each text field appears at most once and is at most 1 KiB UTF-8;
- every file part has field name `files` and a UTF-8 filename;
- validate the filename immediately with the existing POSIX rules before storing bytes;
- a root `SKILL.md` candidate is a filename whose path after one top-level component is exactly `SKILL.md`;
- allow at most `limits.max_files + 1` file parts total and at most `limits.max_files` non-root candidates;
- enforce `limits.max_file_bytes` during streaming for every non-root candidate;
- include every file byte, including `SKILL.md`, in `limits.max_total_bytes`;
- reject duplicate multipart filenames before the bundle adapter runs;
- reset per-part header count and bytes on each part, while bounding aggregate directory-header bytes to `max(32 KiB, (limits.max_files + 4) * 1 KiB)` so the configured file-count boundary remains usable without permitting unbounded overhead;
- accept only `fail`, `overwrite`, or `rename` after UTF-8 decoding;
- return file tuples in upload order; canonical sorting remains `build_bundle()`'s responsibility.

Do not call `request.form()`, `UploadFile.read()`, or write temporary files. These paths would buffer or spill without the project's explicit limits and early-stop semantics.

- [ ] **Step 6: Run multipart and archive regressions, then commit**

```bash
uv run pytest tests/test_skill_uploads.py tests/test_skill_api.py -q
git add app/skills/uploads.py app/skills/routes.py tests/test_skill_uploads.py tests/test_skill_api.py
git commit -m "refactor: isolate bounded skill upload parsing"
```

Expected: directory collector tests pass and every existing archive import test remains green.

---

### Task 4: Expose the directory import API and stable result envelope

**Files:**
- Modify: `app/skills/schemas.py`
- Modify: `app/skills/routes.py`
- Modify: `tests/test_skill_api.py`

**Interfaces:**
- Adds: `POST /api/workspaces/{workspace_id}/skills/import-directory`.
- Returns: `SkillDirectoryImportOut(status, skill)`.
- Status codes: `201` for `created`/`renamed`; `200` for `overwritten`/`already_imported`.
- Errors: `413 skill_bundle_too_large`, `422 invalid_skill_bundle`, `409 skill_import_conflict`, `409 skill_changed`.

- [ ] **Step 1: Write failing API contract tests**

Add a helper that posts a directory as repeated multipart files:

```python
def _directory_parts(
    name: str,
    skill_markdown: bytes,
    supporting: list[tuple[str, bytes]] | None = None,
) -> list[tuple[str, tuple[str, bytes, str]]]:
    parts = [
        (
            "files",
            (f"{name}/SKILL.md", skill_markdown, "text/markdown"),
        )
    ]
    for path, content in supporting or []:
        parts.append(
            (
                "files",
                (f"{name}/{path}", content, "application/octet-stream"),
            )
        )
    return parts
```

Then verify the complete HTTP flow:

```python
created = await manager_client.post(
    "/api/workspaces/team/skills/import-directory",
    data={"on_conflict": "fail"},
    files=_directory_parts(
        "review-folder",
        VALID_SKILL_TEXT.encode(),
        [("references/policy.bin", b"\x00\xff")],
    ),
)
assert created.status_code == 201
assert created.json()["status"] == "created"
assert created.json()["skill"]["enabled"] is False
assert created.json()["skill"]["origin"] == {
    "type": "browser_directory",
    "source_name": "review-folder",
}

repeated = await manager_client.post(
    "/api/workspaces/team/skills/import-directory",
    data={"on_conflict": "fail"},
    files=_directory_parts(
        "review-folder",
        VALID_SKILL_TEXT.encode(),
        [("references/policy.bin", b"\x00\xff")],
    ),
)
assert repeated.status_code == 200
assert repeated.json()["status"] == "already_imported"
```

Use the exact same files on the repeated call so the hash includes the same supporting file set.

Add API cases for:

- changed content returning `409 skill_import_conflict` and the four non-content details;
- overwrite returning `200`, preserving ID and enabled state, and rejecting stale hash with `skill_changed`;
- rename returning `201`, a new ID, target name, and disabled state;
- malformed root/path/options returning `422`;
- member and outsider rejection before body consumption;
- total limit and file-count violation returning `413` and leaving the Workspace unchanged;
- archive endpoint behavior and response shape remaining unchanged.

- [ ] **Step 2: Run API tests and verify failure**

```bash
uv run pytest tests/test_skill_api.py -q
```

Expected: FAIL with 404 on the new endpoint and missing response schema.

- [ ] **Step 3: Add the result schema**

In `app/skills/schemas.py`, import `Literal` and add:

```python
class SkillDirectoryImportOut(BaseModel):
    status: Literal["created", "overwritten", "renamed", "already_imported"]
    skill: SkillOut
```

- [ ] **Step 4: Add the authorized route and dynamic success status**

Add imports for Task 1 and Task 3 helpers, then implement:

```python
@router.post(
    "/workspaces/{workspace_id}/skills/import-directory",
    response_model=SkillDirectoryImportOut,
)
async def import_skill_directory(
    workspace_id: str,
    request: Request,
    response: Response,
    services: Services,
    identity: Identity,
) -> SkillDirectoryImportOut:
    await services.workspace_access.require_manager(identity, workspace_id)
    upload = await read_multipart_directory(request, services.skills.limits)
    uploaded = load_bundle_from_uploaded_files(upload.files, services.skills.limits)
    result = await services.skills.import_uploaded_directory(
        workspace_id,
        identity,
        uploaded,
        on_conflict=upload.on_conflict,
        expected_hash=upload.expected_hash,
        target_name=upload.target_name,
    )
    response.status_code = (
        status.HTTP_201_CREATED
        if result.status in {"created", "renamed"}
        else status.HTTP_200_OK
    )
    return SkillDirectoryImportOut(
        status=result.status,
        skill=_skill_out(result.skill),
    )
```

Keep manager authorization in both route and service: the route prevents unauthorized body consumption, while the service remains secure for non-HTTP callers.

- [ ] **Step 5: Run API and OpenAPI-adjacent smoke tests, then commit**

```bash
uv run pytest tests/test_skill_api.py tests/test_api.py -q
git add app/skills/schemas.py app/skills/routes.py tests/test_skill_api.py
git commit -m "feat: add browser directory skill import API"
```

Expected: the route contract passes, archive import stays unchanged, and errors retain the application's request-ID envelope.

---

### Task 5: Add one import entry with folder and archive choices

**Files:**
- Modify: `app/web/templates/index.html`
- Modify: `app/web/static/app.css`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/skill-manager.js`
- Modify: `tests/js/test_skill_manager.cjs`
- Modify: `tests/test_web_page.py`

**Interfaces:**
- One visible trigger: `#skillManagerImportButton`.
- Choices: `#skillManagerDirectoryImportChoice` and `#skillManagerArchiveImportChoice`.
- Inputs: `#skillManagerDirectoryInput` with `webkitdirectory multiple`; existing archive input keeps `.zip,.skill` acceptance.
- Controller: `importDirectory(files) -> Promise<boolean>`.

- [ ] **Step 1: Write failing DOM and controller tests**

Extend `tests/test_web_page.py` to require all import IDs, one visible import trigger, the directory input attributes, and the archive input accept list. Extend `createSkillManagerElements()` in `tests/js/test_skill_manager.cjs` with the menu, both choices, and directory input.

Add this happy-path controller test:

```javascript
test("directory import uploads relative paths and selects the result", async () => {
  const elements = createSkillManagerElements();
  const calls = [];
  const imported = skill({
    id: "directory-skill",
    name: "brainstorming",
    enabled: false,
    origin: {type: "browser_directory", source_name: "brainstorming"},
  });
  const controller = SkillManager.createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (path.endsWith("/import-directory")) {
        return {status: "created", skill: imported};
      }
      if (path.endsWith("/skills/directory-skill")) {
        return {...imported, content: "content", files: []};
      }
      return {items: [imported]};
    },
    elements,
    getWorkspace: () => ({id: "team", name: "Team", can_manage_skills: true}),
    getWorkspaces: () => [],
    onChanged: () => {},
    onError: () => {},
  });
  await controller.open();
  const root = new File(["root"], "SKILL.md", {type: "text/markdown"});
  Object.defineProperty(root, "webkitRelativePath", {
    value: "brainstorming/SKILL.md",
  });
  const script = new File(["script"], "run.py");
  Object.defineProperty(script, "webkitRelativePath", {
    value: "brainstorming/scripts/run.py",
  });

  assert.equal(await controller.importDirectory([root, script]), true);

  const request = calls.find((item) => item.path.endsWith("/import-directory"));
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.body.get("on_conflict"), "fail");
  assert.deepEqual(
    request.options.body.getAll("files").map((file) => file.name),
    ["brainstorming/SKILL.md", "brainstorming/scripts/run.py"],
  );
  assert.equal(elements.editorTitle.textContent, "brainstorming");
});
```

Add tests that empty picker results do not call the API, a member sees no import entry, and pending mutations disable both choices and inputs.

- [ ] **Step 2: Run frontend tests and verify failure**

```bash
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/test_web_page.py -q
```

Expected: FAIL because the new elements and controller method are absent.

- [ ] **Step 3: Add accessible import markup**

Replace the current import button/input pair with one wrapper containing:

```html
<div id="skillManagerImportMenuRoot" class="skill-import-menu-root">
  <button id="skillManagerImportButton" class="button secondary" type="button"
          aria-haspopup="menu" aria-expanded="false">导入</button>
  <div id="skillManagerImportMenu" class="skill-import-menu" role="menu" hidden>
    <button id="skillManagerDirectoryImportChoice" type="button" role="menuitem">
      <strong>选择 Skill 文件夹</strong>
      <span>直接选择包含 SKILL.md 的文件夹</span>
    </button>
    <button id="skillManagerArchiveImportChoice" type="button" role="menuitem">
      <strong>选择 .skill / .zip</strong>
      <span>导入已经打包的 Skill</span>
    </button>
  </div>
</div>
<label class="sr-only" for="skillManagerDirectoryInput">导入 Skill 文件夹</label>
<input id="skillManagerDirectoryInput" type="file" webkitdirectory multiple hidden>
<label class="sr-only" for="skillManagerImportInput">导入 Skill 压缩包</label>
<input id="skillManagerImportInput" type="file"
       accept=".zip,.skill,application/zip" hidden>
```

Style the menu as an anchored surface above the sidebar list with a high local z-index, keyboard-visible focus, two stacked descriptions, and mobile width capped to the viewport. Do not change the Skill manager grid or composer autocomplete positioning.

- [ ] **Step 4: Wire the new elements and happy-path controller**

Add every new ID to `app/web/static/app.js` and pass it to `createController()`.

In `skill-manager.js`:

- make `originLabel({type: "browser_directory", source_name})` return `browser directory · <source_name>`;
- toggle `importMenu.hidden` and `aria-expanded` from the single import button;
- directory choice closes the menu and clicks the directory input;
- archive choice closes the menu and clicks the existing archive input;
- input `change` with no files is a no-op;
- `syncPendingState()` disables the trigger, choices, and both inputs;
- `syncWorkspace()` hides the menu and both inputs from non-managers;
- `cleanupControllerState()` closes the menu and clears both input values.

Implement the initial directory request with relative multipart filenames:

```javascript
function directoryForm(files, fields = {on_conflict: "fail"}) {
  const form = new FormData();
  for (const [name, value] of Object.entries(fields)) {
    if (value !== null && value !== undefined && value !== "") form.append(name, value);
  }
  for (const file of files) {
    form.append("files", file, file.webkitRelativePath || file.name);
  }
  return form;
}
```

`importDirectory(files)` must follow the current mutation pattern: capture Workspace ID plus lifecycle generation, acquire pending ownership, post with `on_conflict=fail`, reject stale responses, refresh the list/header, select `result.skill.id` while still owning the pending token, clear the input only after terminal success, and release pending in `finally`.

- [ ] **Step 5: Run frontend tests and commit**

```bash
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/test_web_page.py -q
git add app/web/templates/index.html app/web/static/app.css app/web/static/app.js app/web/static/skill-manager.js tests/js/test_skill_manager.cjs tests/test_web_page.py
git commit -m "feat: add unified skill import chooser"
```

Expected: folder and archive choices work through one entry, while the existing archive API request remains byte-for-byte compatible.

---

### Task 6: Add overwrite, rename, and cancel conflict resolution

**Files:**
- Modify: `app/web/templates/index.html`
- Modify: `app/web/static/app.css`
- Modify: `app/web/static/app.js`
- Modify: `app/web/static/skill-manager.js`
- Modify: `tests/js/test_skill_manager.cjs`

**Interfaces:**
- Conflict details consumed from `error.details`.
- Controller methods: `overwriteDirectoryImport()`, `renameDirectoryImport(targetName)`, and `cancelDirectoryImport()`.
- Conflict state retains `Array<File>` only until terminal resolution, cancel, Workspace switch, or manager close.

- [ ] **Step 1: Write failing controller state-machine tests**

Add unit tests for:

- initial `skill_import_conflict` opening a dialog and retaining files plus four conflict details;
- overwrite re-uploading the same files with `on_conflict=overwrite` and `expected_hash`;
- rename re-uploading the same files with `on_conflict=rename` and trimmed `target_name`;
- cancel making no second request and clearing the directory input;
- conflict idle state disabling all other Skill mutations while leaving its three resolution controls enabled;
- a second click during overwrite/rename producing no duplicate request;
- `skill_changed` on overwrite closing no data silently and surfacing an actionable error;
- closing, Escape, Workspace A-B, and Workspace A-B-A transitions invalidating late initial/conflict responses;
- success refreshing and selecting the response Skill;
- `already_imported` selecting the existing Skill without opening conflict UI.

The overwrite request assertion is:

```javascript
assert.equal(form.get("on_conflict"), "overwrite");
assert.equal(form.get("expected_hash"), "sha256:" + "a".repeat(64));
assert.equal(form.get("target_name"), null);
assert.deepEqual(
  form.getAll("files").map((file) => file.name),
  ["brainstorming/SKILL.md", "brainstorming/scripts/run.py"],
);
```

- [ ] **Step 2: Run controller tests and verify failure**

```bash
node --test tests/js/test_skill_manager.cjs
```

Expected: FAIL because API error details and the conflict UI/state are missing.

- [ ] **Step 3: Preserve structured API error details**

Extend `api()` in `app/web/static/app.js`:

```javascript
const error = new Error(message);
error.code = body?.error?.code || "request_failed";
error.details = body?.error?.details || null;
throw error;
```

Do not copy the whole response or request headers onto the error object.

- [ ] **Step 4: Add the conflict dialog**

Add a separate `#skillImportConflictDialog` after the existing manager dialog with:

- incoming name and existing/incoming hash summaries;
- a labeled `#skillImportRenameInput` using the server's 128-character name limit;
- `#skillImportOverwriteButton`, `#skillImportRenameButton`, and `#skillImportCancelButton`;
- an alert/status area for stale overwrite or validation errors;
- no implicit form submission that could close the dialog before the API resolves.

Wire every element through `app.js`. Style it with the existing dialog tokens; on narrow screens, stack actions without horizontal overflow.

- [ ] **Step 5: Implement conflict ownership and resolution**

Add controller state:

```javascript
let pendingDirectoryConflict = null;
```

Store only this shape:

```javascript
{
  files: Array.from(files),
  workspaceId: changedWorkspaceId,
  lifecycleGeneration: generation,
  details: error.details,
}
```

Update `isPending()` to return true when either `pendingOwner` or `pendingDirectoryConflict` exists. Allow only conflict-resolution methods to acquire a mutation token while conflict state exists. Resolution buttons are disabled while `pendingOwner` is non-null; all unrelated mutations remain disabled for the entire conflict lifetime.

Use one `submitDirectoryImport(files, fields, expectedLifecycle)` helper for initial, overwrite, and rename requests. On initial conflict, retain the files and open the dialog. On terminal success, clear conflict state, close the dialog, clear input values, refresh, and select the returned Skill. On cancel or lifecycle cleanup, clear the retained `File` objects immediately.

Validate before rename submission:

```javascript
const targetName = elements.importRenameInput.value.trim();
if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(targetName)) {
  elements.importConflictError.textContent = "Skill 名称格式不正确";
  elements.importConflictError.hidden = false;
  return false;
}
```

The server remains authoritative and revalidates the name and conflict policy.

- [ ] **Step 6: Run frontend regressions and commit**

```bash
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/test_web_page.py -q
git add app/web/templates/index.html app/web/static/app.css app/web/static/app.js app/web/static/skill-manager.js tests/js/test_skill_manager.cjs
git commit -m "feat: resolve skill directory import conflicts"
```

Expected: all controller lifecycle tests pass without changing existing save, enable, archive, copy, or archive-import behavior.

---

### Task 7: Prove the browser workflow, Session boundary, and full regression suite

**Files:**
- Modify: `tests/browser/test_workbench.py`
- Modify: `README.md`

**Interfaces:**
- Browser acceptance uses Chromium's directory input and real multipart filenames.
- Documentation exposes the user workflow and security boundary, not server-local scan instructions.

- [ ] **Step 1: Add a real Skill directory fixture and failing browser test**

Create a fixture under `tmp_path` containing:

```text
browser-import/
├── SKILL.md
├── assets/icon.bin
└── scripts/run.py
```

Write `test_skill_manager_imports_browser_directory_and_resolves_conflicts`. Use the actual directory input:

```python
await page.locator("#skillManagerImportButton").click()
await page.locator("#skillManagerDirectoryImportChoice").click()
await page.locator("#skillManagerDirectoryInput").set_input_files(str(skill_dir))
```

Assert:

- request URL ends with `/skills/import-directory`;
- response is `201` with `status=created`;
- list row shows the frontmatter description, `browser directory · browser-import`, and `已停用`;
- the Workspace header count increases once at import and does not change when the Skill is enabled;
- detail manifest lists `assets/icon.bin` and `scripts/run.py`;
- enabling it affects the candidate source only for subsequently created Sessions;
- an older Session retains its previous fixed autocomplete snapshot;
- reselecting changed directory contents opens the conflict dialog;
- overwrite retains the Skill ID and enabled state but changes the hash and description;
- a stale overwrite simulated by a competing PATCH surfaces `skill_changed`;
- rename creates a new disabled row and leaves the overwritten source unchanged;
- cancel performs no write.

- [ ] **Step 2: Run the new browser test and verify failure before implementation completion**

```bash
uv run pytest tests/browser/test_workbench.py::test_skill_manager_imports_browser_directory_and_resolves_conflicts -q
```

Expected before Tasks 1-6: FAIL because the folder endpoint/UI are absent. Expected after Tasks 1-6: PASS.

- [ ] **Step 3: Update the README**

Replace the current archive-only Skill-manager sentence with concise operator guidance:

- owner/admin click one `导入` button and choose folder or archive;
- folder selection uploads bytes and relative paths to the remote service;
- root `SKILL.md` is required and supporting files are preserved;
- new imports are disabled;
- same content is idempotent; changed content requires overwrite/rename/cancel;
- overwrite preserves ID and enabled state but does not mutate existing Session snapshots;
- configured 10 MiB/50 MiB/200-file limits apply;
- the feature is not a server filesystem scan or continuous sync.

- [ ] **Step 4: Run focused backend, frontend, and browser suites**

```bash
uv run pytest tests/test_skill_bundle.py tests/test_skill_uploads.py tests/test_skill_service.py tests/test_skill_api.py tests/test_web_page.py -q
node --test tests/js/test_skill_manager.cjs
uv run pytest tests/browser/test_workbench.py::test_skill_manager_imports_browser_directory_and_resolves_conflicts tests/browser/test_workbench.py::test_skill_manager_preserves_session_snapshots_and_copies_to_team tests/browser/test_workbench.py::test_skill_autocomplete_reaches_last_item_and_compacts_descriptions -q
```

Expected: all focused tests pass, including the existing archive flow, Session snapshot behavior, and final-item slash-menu scrolling.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest -q
```

Expected: the full suite passes with only documented environment-dependent skips.

- [ ] **Step 6: Review the diff for scope and security**

Run:

```bash
git diff --check
git status --short
git diff --stat
rg -n "request\.form|UploadFile\.read|webkitRelativePath|import-directory|skill_import_conflict" app tests README.md
```

Confirm:

- no `request.form()` or unbounded upload read was introduced;
- no client absolute path is persisted or logged;
- manager authorization precedes body consumption;
- archive import still uses its original endpoint and shape;
- overwrite does not update `enabled` or any Session snapshot;
- no migration, Runtime scan, Git import, or unrelated file entered the diff;
- `agents.json`, `description.md`, `members.json`, and `squads.json` remain untouched and untracked.

- [ ] **Step 7: Commit the acceptance coverage and documentation**

```bash
git add tests/browser/test_workbench.py README.md
git commit -m "test: verify browser directory skill import"
```

Expected: the branch contains seven focused commits and is ready for final review or local merge according to the user's chosen execution workflow.
