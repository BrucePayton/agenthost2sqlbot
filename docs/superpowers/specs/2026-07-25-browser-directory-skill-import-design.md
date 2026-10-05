# Browser Directory Skill Import Design

**Date:** 2026-07-25

## Goal

Allow a Workspace owner or admin to import a complete Skill directory from a
browser without first creating a `.skill` or `.zip` archive. Keep the existing
archive import available behind the same user-facing import entry.

This design targets a remotely deployed Web service. The browser uploads the
selected directory contents to the server; the server never attempts to read a
path on the user's computer. Runtime daemon discovery, Git/registry import, and
continuous synchronization remain future work.

## Confirmed Product Decisions

- The Skill manager exposes one `导入` button.
- Clicking it opens a choice between `选择 Skill 文件夹` and
  `选择 .skill / .zip`.
- Directory selection uses the browser's directory picker; archive selection
  continues to use the existing file picker and endpoint.
- The selected directory must contain a root `SKILL.md`. Supporting directories
  such as `scripts`, `agents`, `assets`, and `references` are preserved.
- The browser uploads file bytes and relative paths. It never submits an
  absolute client path, and the server never tries to access the client
  filesystem directly.
- A successfully imported Skill starts disabled.
- Same-name, same-hash content is treated as already imported and does not
  change the existing Skill.
- Same-name, different-hash content requires an explicit choice: overwrite,
  save under another name, or cancel.
- Overwrite preserves the Skill ID and enabled state. Existing Session
  snapshots remain immutable.
- Saving under another name changes only the uploaded copy's `name` frontmatter
  field and creates a new disabled Skill.

## Approaches Considered

### Selected: browser directory upload

The browser selects a directory with `webkitdirectory`, attaches every regular
file to a multipart request using its relative path as the multipart filename,
and uploads it to the Web service. The server streams, bounds, validates, and
stores the bundle.

This works when the Web service is deployed on another machine because the
browser transmits the selected bytes. It is the smallest design that removes
the manual archive step.

### Rejected for this phase: server-local filesystem scan

A server-side scan only sees directories on the server host. It cannot discover
Skills on the employee's computer when the application is remotely deployed.

### Deferred: Runtime daemon discovery

A daemon on each execution machine could scan Claude, Codex, shared, and plugin
Skill roots and report an opaque catalog to the server. This is the correct
long-term design for Multica-style Runtime discovery, but it introduces Runtime
registration, authentication, heartbeats, asynchronous request/result state,
and remote bundle transfer. Those concerns are outside this focused release.

## User Experience

The Skill manager keeps one primary import action:

```text
导入 Skill
├── 选择 Skill 文件夹
│   直接选择包含 SKILL.md 的文件夹
└── 选择 .skill / .zip
    导入已经打包的 Skill
```

The two choices are backed by separate hidden inputs because browsers cannot
reliably select either a directory or an archive from one native picker:

- directory input: `type=file`, `webkitdirectory`, `multiple`
- archive input: `type=file`, `accept=.skill,.zip`

Canceling either native picker returns the manager to its prior state. While an
import or conflict resolution is pending, both import choices and other Skill
mutations are disabled. Closing the manager or switching Workspaces invalidates
late responses using the controller's existing lifecycle generation.

After a successful directory import, the list refreshes, selects the imported
Skill, identifies its source as a browser directory, and shows it as disabled.
An already-imported result selects the existing Skill without changing it.

## API Surface

The existing archive endpoint remains unchanged:

```http
POST /api/workspaces/{workspace_id}/skills/import
Content-Type: multipart/form-data
```

A new endpoint handles directories:

```http
POST /api/workspaces/{workspace_id}/skills/import-directory
Content-Type: multipart/form-data
```

The client appends each selected file under the repeated `files` field and sets
the multipart filename to `File.webkitRelativePath`. Example part filenames:

```text
brainstorming/SKILL.md
brainstorming/scripts/example.py
brainstorming/agents/openai.yaml
```

Conflict resolution uses the same endpoint and re-uploads the still-selected
browser files:

```text
on_conflict=fail                         # initial request
on_conflict=overwrite
expected_hash=<current stored hash>      # required for overwrite
on_conflict=rename
target_name=<validated new Skill name>   # required for rename
```

The API returns a result envelope rather than overloading `201` with multiple
meanings:

```json
{
  "status": "created | overwritten | renamed | already_imported",
  "skill": {"id": "...", "bundle_hash": "..."}
}
```

An initial same-name, different-hash request returns HTTP 409
`skill_import_conflict` with the existing Skill ID, existing hash, incoming
hash, and incoming parsed name. It does not return either bundle's contents.

## Server Components

### `DirectoryUploadCollector`

The new collector extends the existing bounded multipart approach instead of
using an unbounded `request.form()` call. It accepts only:

- the repeated `files` file parts;
- the small text fields `on_conflict`, `expected_hash`, and `target_name`;
- the configured maximum file count;
- the configured per-file and total byte limits;
- the existing multipart header and pre-file overhead limits.

Authorization is checked before the request body is read. The collector stops
reading immediately after the legal closing boundary and aborts as soon as any
limit is exceeded.

### `load_bundle_from_uploaded_files`

The uploaded-file adapter takes `(relative_path, bytes)` pairs and produces the
existing immutable `SkillBundle`. It does not create temporary directories.

It performs these steps:

1. Validate each browser-provided path before normalization.
2. Require every path to share exactly one non-empty top-level directory.
3. Strip that one common directory component.
4. Reject empty paths, absolute paths, backslashes, `.`, `..`, NULs, duplicate
   paths, and file/directory prefix conflicts.
5. Require exactly one `SKILL.md` at the stripped bundle root.
6. Pass `SKILL.md` and supporting byte entries to the existing `build_bundle()`
   function so frontmatter, name, description, file limits, and deterministic
   hashing stay identical to archive and bootstrap imports.

The browser supplies ordinary file bytes. Empty directories and filesystem
link identity are not part of the Web upload contract and are not persisted.

### `SkillService`

The service receives a validated bundle plus an import policy:

- `fail`: insert a new disabled Skill, return `already_imported` for the same
  name and hash, or raise `skill_import_conflict` for different content;
- `overwrite`: require manager access and `expected_hash`, atomically replace
  the current bundle while preserving ID and enabled state;
- `rename`: validate `target_name`, rewrite only the uploaded copy's YAML
  frontmatter name, rebuild its hash, and insert a new disabled Skill.

Directory imports persist this origin without a client filesystem path:

```json
{
  "type": "browser_directory",
  "source_name": "brainstorming"
}
```

Overwrite updates the current origin to the newly imported source. Existing
Session snapshots and materialized files are never rewritten.

## Security and Limits

The directory endpoint uses the same Workspace manager authorization boundary
as archive import. A member or outsider is rejected before body consumption.

Default limits remain:

- 10 MiB per supporting file;
- 50 MiB total uncompressed bundle bytes, including `SKILL.md`;
- 200 supporting files in addition to the root `SKILL.md`;
- bounded multipart header count, individual header size, aggregate header
  size, and pre-file overhead.

The server treats relative paths and YAML as untrusted input. It never follows
paths, writes the upload to the source tree, executes included scripts, or
accepts an absolute client path. Database persistence remains atomic: either
the complete Skill and all supporting files are stored, or no mutation is
visible.

## Error Contract

- `413 skill_bundle_too_large`: file count, individual file, total bytes, or
  multipart overhead exceeds a configured limit.
- `422 invalid_skill_bundle`: invalid multipart structure, multiple selected
  roots, invalid relative path, missing root `SKILL.md`, invalid YAML, invalid
  Skill name, or duplicate/conflicting paths.
- `409 skill_import_conflict`: same name with different incoming content and no
  explicit resolution.
- `409 skill_changed`: the existing Skill changed after the conflict dialog was
  shown and before overwrite was submitted.
- Workspace access remains fail-closed and does not expose unauthorized
  Workspace or Skill existence.

All error responses use the application's existing stable error envelope and
request ID behavior. The UI preserves the selected local files after a
conflict so the user can resolve it without reopening the directory picker.

## Testing and Acceptance

### Bundle and multipart tests

- Valid directory with text and binary supporting files.
- A single selected folder component is stripped exactly once.
- Missing root `SKILL.md`, multiple roots, absolute/backslash/traversal paths,
  duplicate paths, and file/directory prefix conflicts are rejected.
- Exact file-count, per-file, total-byte, header, and pre-file boundaries are
  accepted; one byte/item beyond each limit is rejected.
- Chunked multipart parsing stops early after a limit violation and does not
  consume tail chunks after the closing boundary.

### Service and API tests

- Owner/admin can import; member and outsider cannot.
- New imports are disabled and store `browser_directory` origin without an
  absolute path.
- Same name plus same hash is idempotent.
- Different content returns conflict.
- Overwrite preserves ID and enabled state and uses optimistic concurrency.
- Rename changes only the imported copy and creates an independent disabled
  Skill.
- Any parse, authorization, conflict, or database failure leaves no partial
  Skill files.
- Existing archive import remains unchanged.

### Frontend and browser tests

- One import action exposes directory and archive choices.
- Native picker cancellation has no side effects.
- Directory selection uploads relative paths and displays pending state.
- Conflict UI supports overwrite, rename, and cancel.
- Late upload and conflict responses cannot affect a reopened manager or a
  different Workspace.
- A real browser imports a fixture directory, shows its description and
  `browser directory` source, and leaves it disabled.
- Enabling the imported Skill affects only subsequently created Sessions.
- Existing Session-pinning and 77-item slash-menu scrolling regressions remain
  green.

## Compatibility and Rollout

No database migration is required because the existing Skill and Skill-file
tables already store the complete bundle and JSON origin. The current archive
endpoint, manual creation, bootstrap imports, copies, and Session snapshot
format remain compatible.

The directory picker depends on `webkitdirectory`, which is supported by the
Chromium-based production browser. The archive option remains the fallback for
browsers without directory-picker support.

## Non-Goals

- Runtime daemon registration or local Runtime discovery.
- Server-side scanning of the user's computer.
- GitHub, skills.sh, ClawHub, or marketplace import.
- Continuous synchronization with the selected source directory.
- Importing multiple Skill roots in one directory selection.
- Executing or security-auditing third-party Skill scripts during import.
- New Skill release/version/rollback entities.

## Future Extension

The directory upload and future Runtime daemon import should converge at the
same `SkillBundle + import policy` service boundary. A daemon can later return
an authenticated opaque `runtime_id + source_key` and transfer a bundle without
changing Workspace persistence, conflict behavior, Session pinning, or the
Skill manager's result model.
