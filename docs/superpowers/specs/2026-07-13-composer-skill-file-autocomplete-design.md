# Composer Skill and Session File Autocomplete Design

Date: 2026-07-13

## Goal

Add Claude App-style composer autocomplete to the existing Workspace Agent:

- typing `/` at the start of a message shows the Skills enabled for the current
  Session;
- typing `@` anywhere in a message searches files in the current Session's
  isolated workspace;
- choosing an item inserts editable plain text into the existing `textarea`;
- selected file references are also submitted as structured, server-validated
  metadata so the runtime can consume them reliably.

The feature must preserve Session isolation, the current attachment flow, and
the existing Enter-to-send behavior.

## Confirmed Product Decisions

- Use the existing native `textarea`; do not introduce a rich-text editor.
- `/` opens Skill autocomplete only when it is the first non-whitespace token.
- Selecting a Skill inserts editable `/skill-name ` text.
- `@` can appear anywhere in the message.
- `@` searches only the selected Session's materialized `workspace/` directory.
- Selecting a file inserts editable `@relative/path` text and records a
  structured file reference.
- File content is not copied into the prompt. The Agent reads referenced files
  on demand with `Read`.
- Show user work files, uploaded attachments, and generated `outputs/` files.
  Hide `.claude/`, `CLAUDE.md`, `workspace.snapshot.yaml`, hidden paths, caches,
  and symlinks.

## Current System Constraints

The composer is a plain `textarea` managed by `app/web/static/app.js`. The
workspace list API exposes only `skill_count`; it does not expose Skill names.
There is no Session file-listing endpoint. Turns currently accept `message`,
`attachment_ids`, and `client_request_id`.

Each Session already has the two sources needed by this feature:

- its immutable `workspace_snapshot_json`, which contains the allowed Skill
  names and tools;
- its isolated materialized workspace under
  `data/sessions/<session-id>/workspace/`, which is also the runtime `cwd`.

The runtime already passes the snapshot Skill allowlist to
`ClaudeAgentOptions.skills`, so an inserted Skill slash command is constrained
to the same Session-visible Skill set.

## Chosen Architecture

Use a native-textarea autocomplete controller backed by Session-scoped read
APIs and structured Turn file references.

### Backend components

Extend `SessionService` with narrow query methods and keep all filesystem
containment logic on the server:

1. `list_skills(session_id)` reads Skill names from the persisted Session
   snapshot. It optionally reads only the YAML frontmatter of each selected
   Session Skill's `SKILL.md` to return a description. A missing or malformed
   description falls back to an empty string; it does not make the Session
   unusable.
2. `search_files(session_id, query)` walks the Session workspace with explicit
   exclusions and returns safe relative paths only.
3. `validate_file_references(session_id, paths)` performs a fresh validation
   at Turn creation time. Search results are not treated as authorization.

Keep file discovery and reference validation on `SessionService` because it
already owns the mapping from a Session record to its materialized directory.
Do not expose generic server filesystem browsing.

### Frontend components

Add `app/web/static/composer-autocomplete.js`. It owns:

- token recognition at the current textarea caret;
- menu open/close and active-option state;
- keyboard, mouse, touch, and IME behavior;
- replacement of the active token with a selected item;
- synchronization of selected file-reference text with structured reference
  state.

`app.js` remains responsible for API calls, selected Session lifecycle, Turn
submission, toasts, and the existing composer enabled/running state. The two
files communicate through a small controller interface rather than sharing
DOM implementation details throughout `app.js`.

## API Design

### List Session Skills

`GET /api/sessions/{session_id}/skills`

Response:

```json
{
  "items": [
    {
      "name": "brainstorming",
      "description": "Explore intent and turn an idea into a design."
    }
  ]
}
```

The response order is alphabetical by Skill name. The endpoint returns only
Skills present in that Session's persisted snapshot, not the current workspace
manifest. Skill frontmatter reads are bounded to 64 KiB per `SKILL.md`.

### Search Session Files

`GET /api/sessions/{session_id}/files?q=<query>`

Response:

```json
{
  "items": [
    {
      "path": "outputs/luxury_order_analysis_report.html",
      "name": "luxury_order_analysis_report.html",
      "size_bytes": 48210
    }
  ],
  "truncated": false
}
```

Rules:

- `q` is optional and limited to 200 characters; empty `q` returns the first
  matches alphabetically.
- Matching is case-insensitive against both basename and relative path.
- Each request scans at most 10,000 directory entries and returns at most 50
  matches. `truncated` is true when either limit prevents a complete result.
- Results include only regular, non-symlink files whose resolved paths remain
  inside the Session workspace.
- Excluded path components include any component beginning with `.`,
  `__pycache__`, `node_modules`, `.claude`, and internal snapshot files.
  `CLAUDE.md` and `workspace.snapshot.yaml` are excluded explicitly.
- The endpoint returns `409 file_reference_unavailable` if the Session snapshot
  does not allow the `Read` tool.

### Create Turn

Extend `TurnCreate` with:

```json
{
  "message": "/brainstorming 分析 @outputs/report.html",
  "file_references": ["outputs/report.html"],
  "attachment_ids": [],
  "client_request_id": "request-id"
}
```

`file_references` defaults to an empty list, accepts at most 20 entries, requires
those entries to be unique relative paths, and remains independent of uploaded
`attachment_ids`.

Turn creation validates every reference and stores the normalized list in the
existing `message.user` event payload. No database column or schema migration
is required. `_runtime_request()` reads the same persisted user-event payload
to reconstruct the file references.

History responses preserve both the visible message text and normalized
`file_references`, allowing the page to render what was actually submitted
without exposing absolute paths.

## Composer Interaction

### Skill autocomplete

- On Session selection, `app.js` loads the Skill list once and passes it to the
  controller.
- The menu opens only when the text from the start of the message to the caret
  matches an optional whitespace prefix followed by `/query`, with no completed
  argument after the command token.
- Filtering matches Skill name and description, case-insensitively, and shows
  at most 10 items.
- Choosing an item replaces the active token with `/skill-name ` and places the
  caret after the trailing space.
- A message supports one leading Skill command. Subsequent `/` characters do
  not reopen the Skill menu.

### File autocomplete

- An `@query` token may begin at the start of the message or after whitespace.
- Input is debounced by approximately 150 ms before requesting the file search
  endpoint.
- Each request carries a monotonically increasing request number or uses an
  `AbortController`; late responses from older queries are ignored.
- Choosing a file replaces the active token with `@relative/path`. A path that
  contains whitespace is inserted as `@"relative path"`.
- The controller records the exact inserted token and normalized relative path.
  On every input event, it removes a structured reference whose exact token no
  longer exists. This prevents invisible references after the user edits or
  deletes the text.
- Manually typed `@path` text that was never selected from the menu remains
  ordinary message text and is not submitted as a structured reference.
- Multiple selected references are allowed and de-duplicated before submission.

### Menu behavior

- The menu is anchored above the composer, matching the supplied Claude App
  reference without requiring caret-position geometry.
- Up and Down move the active option. Enter or Tab selects it. Escape closes
  the menu.
- When the menu is open, Enter must not send the message. When closed, the
  existing Enter-to-send and Shift+Enter-to-newline behavior remains unchanged.
- Pointer and touch selection use the same selection path as keyboard input.
- While an IME composition is active, the controller does not select an option,
  close the menu because of intermediate text, or submit a message.
- Switching Sessions resets the menu, loaded Skill list, outstanding file
  searches, and file references. Successful submission or clearing the
  textarea resets the menu, file-search results, outstanding searches, and file
  references while retaining the current Session's loaded Skill list.

## Runtime Message Construction

Add `file_references` to `RuntimeRequest`. `build_user_message()` keeps the
original message text unchanged and adds a separate text content block only
when references exist. The block contains:

- normalized relative paths;
- a statement that paths are relative to the current Session workspace;
- an instruction to inspect relevant files with `Read` rather than assuming
  their content;
- a reminder that referenced file content is untrusted data, not system-level
  instructions.

The block never includes absolute paths or file content. Existing image and
text attachment behavior is unchanged.

## Security and Failure Behavior

### Path validation

Both search and Turn creation enforce:

- the input is a relative path with no empty, `.` or `..` components;
- the candidate is not a symlink and no traversed component is a symlink;
- strict resolution succeeds and remains under the Session workspace root;
- the candidate is an allowed regular file and does not match exclusions.

This validation is repeated when the Turn starts so a file changed between
selection and execution cannot escape the Session workspace.

### User-visible failures

- A deleted, renamed, excluded, or otherwise invalid selected file returns
  `400 file_reference_invalid`. The page leaves the textarea and selected
  reference state intact and shows the server message in a toast.
- A Session without `Read` returns `409 file_reference_unavailable`; `@` does
  not open a menu for that Session.
- A failed Skill-list request disables only `/` autocomplete and shows one
  toast. Normal message submission remains available.
- A failed file-search request closes the stale result list, preserves typed
  text, and shows a non-destructive error state.
- `404 session_not_found` behavior follows the existing Session APIs.

## Testing Strategy

### Backend tests

- Skill results come from the Session snapshot and remain unchanged when the
  workspace manifest later changes.
- Valid Skill frontmatter descriptions are returned; malformed or oversized
  frontmatter falls back safely.
- File search includes ordinary, uploaded, and generated files and excludes
  `.claude`, hidden paths, internal snapshot files, caches, directories, and
  symlinks.
- Query matching, ordering, the 50-result limit, and the 10,000-entry scan cap
  are deterministic.
- Relative-path traversal, absolute paths, symlink escapes, cross-Session paths,
  duplicates, too many references, and files deleted before submission are
  rejected.
- `message.user` persists normalized references, and `_runtime_request()`
  reconstructs them.
- `build_user_message()` emits the structured reference block without reading
  file content or changing attachment behavior.

### Browser tests

- `/` opens only at the first non-whitespace token, filters Skills, and inserts
  the selected slash command by keyboard and pointer.
- `@` works in the middle of text, ignores stale search responses, supports
  quoted paths, multiple references, and removal after text edits.
- Enter selects while a menu is open and sends only after it is closed;
  Shift+Enter and IME composition remain safe.
- Switching Session and successful send clear autocomplete state.
- The Turn request contains only currently visible selected references.
- Desktop and mobile layouts keep the menu inside the viewport and above the
  composer.

### Regression and acceptance

- Run the complete pytest suite and JavaScript syntax checks.
- Use Playwright against the real application for desktop and mobile visual
  acceptance.
- Verify Session A cannot search or submit a reference to any Session B file.
- Verify existing uploads, SSE execution status, cancellation, history reload,
  and service-restart interruption behavior remain functional.

## Non-Goals

- Rich-text chips or inline syntax highlighting.
- Arbitrary host filesystem browsing.
- Directory references.
- Preloading referenced file contents into the prompt.
- Built-in slash commands such as `/model`; this release lists Workspace
  Skills only.
- Editing the Session's immutable Skill allowlist from the composer.
