# Development Session Inspector Design

## Goal

Turn the standalone Workspace Agent page into a practical Session replay surface:
developers can locate a reported Session ID, switch to its existing Davinci OBID,
and inspect the persisted conversation, tools, results, and Agent Host context.

## Product boundary

- Session ID is visible and copyable for every authenticated owner.
- Human messages, assistant messages, and tool calls are rendered in one ordered
  timeline in every environment.
- Pure protocol continuation messages (`text == ""` with only tool results) are
  never rendered as human speech.
- Cross-user OBID selection and Session lookup exist only when
  `APP_ENV=development` and `APP_IDENTITY_MODE=obid`.
- UAT and production do not register debug APIs and do not render impersonation
  controls.

## Persisted context shown

The inspector shows only persisted, owner-authorized data:

- Session ID, Workspace ID, Claude Session ID, status and timestamps.
- Immutable Workspace snapshot, including model, allowed tools, Skill metadata,
  and MCP declarations that contain environment variable names rather than their
  values.
- Per-Turn user text, attachments, file references, page state, frontend tool
  definitions, tool results, progress, usage and errors.
- Tool public name, arguments, result, duration and error status.

It does not show secrets, API keys, MCP token values, process environment values,
or runtime-only system prompt content that was never persisted. This is a
persisted-context inspector, not a raw process-memory dump.

## Components

1. `GET /api/sessions/{session_id}/context` returns owner-authorized Session
   metadata and the parsed immutable Workspace snapshot.
2. The workbench header displays a copyable Session ID and opens a context dialog.
3. A timeline renderer treats `frontend_tool.deferred` as a tool event, correlates
   its later `tool_results` by tool call ID, and exposes raw persisted context in
   collapsed details.
4. `GET /api/debug/users` lists existing Davinci OBID mappings with Workspace and
   Session counts. It is registered only in the exact development/OBID profile.
5. `GET /api/debug/sessions/{session_id}` resolves a Session to its existing OBID,
   Workspace and title. It is registered under the same profile.
6. The development workbench stores selected OBID in session storage, injects
   `X-Davinci-ObId` into all API calls, and can locate a pasted Session ID.

## Failure behavior

- Unknown Session ID: show a non-destructive error and preserve current selection.
- Remembered OBID no longer exists: select the first returned user.
- User switch while a Turn is active: reject the switch.
- Debug route outside the approved profile: return 404.
- Existing historical continuation messages remain in the database for audit and
  resume; only their presentation changes.

## Verification

- Python tests prove owner-only Session context and conditional debug APIs.
- JavaScript tests prove continuation suppression, tool correlation, context
  disclosure boundaries, identity headers, session storage and Session lookup.
- Page tests prove Session controls always render while OBID controls are exact-
  profile only.
- A local browser smoke switches users, locates a Session ID, copies it, and reads
  the tool/context timeline without empty user bubbles.
