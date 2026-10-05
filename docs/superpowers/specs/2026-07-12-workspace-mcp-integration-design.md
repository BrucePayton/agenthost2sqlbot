# Workspace MCP Integration Design

## Goal

Expose four trusted MCP servers to newly created Claude Workspace sessions:

- `anthropicDeveloperDocs`
- `openaiDeveloperDocs`
- `codexSecurity`
- `dataAnalyticsWidgets`

Existing sessions retain their original snapshots and MCP configuration. This change exposes MCP
tools through the existing chat tool-event UI; it does not embed MCP App interactive surfaces.

## Configuration Model

`workspace.yaml` supports a discriminated MCP server union.

HTTP servers retain the existing environment-based configuration:

```yaml
anthropicDeveloperDocs:
  type: http
  url_env: ANTHROPIC_DOCS_MCP_URL
```

Stdio servers use a direct executable plus an entrypoint supplied through the environment:

```yaml
codexSecurity:
  type: stdio
  command: node
  entrypoint_env: CODEX_SECURITY_MCP_ENTRYPOINT
  args: [--stdio]
  env_vars: [CODEX_HOME]
```

The service resolves these environment variables while scanning the Workspace. Snapshots contain
only declarative configuration and environment variable names, never resolved paths, credentials,
or authorization values.

Required runtime variables for the example Workspace are:

- `ANTHROPIC_DOCS_MCP_URL`
- `OPENAI_DOCS_MCP_URL`
- `CODEX_SECURITY_MCP_ENTRYPOINT`
- `DATA_ANALYTICS_MCP_ENTRYPOINT`
- `CODEX_HOME`

## Validation

HTTP MCP URLs must resolve to valid HTTPS URLs. Existing optional bearer-token behavior remains
available through `authorization_env`.

Stdio MCP configuration is trusted administrator configuration, but it is still validated before a
session can be created:

- `command` is a simple executable name or absolute executable path and is resolved without a shell.
- `entrypoint_env` must exist and point to an absolute, regular, non-symlink file.
- Every declared `env_vars` entry must be present.
- Arguments are passed as individual argv entries and are never shell-expanded.

If any configured server is invalid, the Workspace is unavailable and its validation errors identify
the server and missing or invalid field.

## Runtime Resolution

`ClaudeAgentRuntime` converts the snapshotted declarations into Claude Agent SDK configurations at
turn time:

- HTTP becomes `{type: http, url, headers?}`.
- Stdio becomes `{type: stdio, command, args: [entrypoint, ...args], env}`.

The stdio server-specific `env` contains only variables explicitly declared by that server. Claude
credentials are not added to this MCP-specific configuration.

The Workspace allowlist adds:

- `mcp__anthropicDeveloperDocs__*`
- `mcp__openaiDeveloperDocs__*`
- `mcp__codexSecurity__*`
- `mcp__dataAnalyticsWidgets__*`

The existing `PreToolUse` hook remains authoritative, so an MCP tool not matching the Workspace
allowlist is denied.

## Session Semantics

MCP declarations and allowlist rules are part of the immutable Workspace snapshot captured when a
session is created. No database or filesystem migration is performed for existing sessions. After
the service restarts, only newly created sessions receive the four MCP servers.

## User Experience

The current timeline continues to show MCP calls through `tool.started` and `tool.completed` events.
Text results and generated files use existing rendering and Workspace output behavior. MCP App UI
resources, embedded widgets, host messaging, and custom CSP handling are outside this MVP.

Startup failures, protocol failures, and tool failures are surfaced through the existing turn error
and tool event paths. The error should retain the affected MCP server name where the SDK provides it.

## Verification

Automated coverage includes:

1. Manifest parsing and validation for HTTP and stdio configurations.
2. Rejection of missing, relative, non-file, and symlinked stdio entrypoints.
3. Runtime conversion into Claude Agent SDK HTTP and stdio dictionaries.
4. Explicit environment forwarding and MCP wildcard allowlist behavior.
5. Snapshot isolation proving old sessions stay unchanged and new sessions capture MCP declarations.
6. Existing application regression tests.

Live verification includes:

1. `tools/list` against all four configured servers.
2. A newly created web session invoking both documentation MCPs.
3. Confirmation that the two local plugin MCP tool families are advertised to Claude.
4. Application health and Workspace availability checks after restart.
