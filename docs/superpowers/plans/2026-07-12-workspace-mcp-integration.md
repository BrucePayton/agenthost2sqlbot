# Workspace MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add environment-resolved HTTP and stdio MCP servers to newly created Claude Workspace sessions.

**Architecture:** Extend the Workspace manifest with a discriminated HTTP/stdio MCP union, validate every external dependency during Workspace scanning, and convert the immutable session snapshot into Claude Agent SDK MCP dictionaries at turn time. Configure the example Workspace with two official documentation HTTP servers and two trusted local plugin stdio servers; do not migrate existing session snapshots.

**Tech Stack:** Python 3.11+, Pydantic 2, Claude Agent SDK, FastAPI, PyYAML, pytest

## Global Constraints

- Existing sessions retain their original snapshots and MCP configuration.
- MCP App interactive surfaces are outside scope; use the existing tool-event UI.
- Resolved paths, authorization values, and credentials must not be written into Workspace snapshots.
- Stdio commands must execute directly without shell interpolation.
- The four configured server names are `anthropicDeveloperDocs`, `openaiDeveloperDocs`, `codexSecurity`, and `dataAnalyticsWidgets`.

---

### Task 1: Add and validate stdio MCP manifest support

**Files:**
- Modify: `app/workspaces/models.py`
- Modify: `app/workspaces/registry.py`
- Test: `tests/test_workspaces.py`

**Interfaces:**
- Produces: `McpHttpServerManifest`, `McpStdioServerManifest`, and `McpServerManifest`.
- Produces: Workspace validation that checks HTTP URL environment values and stdio executable, entrypoint, and forwarded environment values.

- [ ] **Step 1: Write failing manifest and registry tests**

Add tests that create a stdio entrypoint and assert a Workspace with this declaration is available:

```python
mcp = """
  local:
    type: stdio
    command: python
    entrypoint_env: LOCAL_MCP_ENTRYPOINT
    args: [--stdio]
    env_vars: [LOCAL_MCP_TOKEN]
"""
entry = WorkspaceRegistry(
    root,
    "claude-default",
    environ={
        "PATH": os.environ["PATH"],
        "LOCAL_MCP_ENTRYPOINT": str(entrypoint),
        "LOCAL_MCP_TOKEN": "configured",
    },
).scan()[0]
assert entry.available is True
```

Add parameterized failure cases for a missing entrypoint variable, relative entrypoint, directory entrypoint, symlink entrypoint, missing command, and missing forwarded variable. Add HTTP cases rejecting a non-HTTPS URL and accepting `https://mcp.example.test`.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_workspaces.py
```

Expected: failures because `type: stdio` is rejected and HTTP environment values are not URL-validated.

- [ ] **Step 3: Implement the discriminated manifest union**

Define these models in `app/workspaces/models.py`:

```python
class McpHttpServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["http"]
    url_env: str = Field(pattern=ENV_NAME_PATTERN)
    authorization_env: str | None = Field(default=None, pattern=ENV_NAME_PATTERN)


class McpStdioServerManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["stdio"]
    command: str = Field(min_length=1)
    entrypoint_env: str = Field(pattern=ENV_NAME_PATTERN)
    args: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)


McpServerManifest = Annotated[
    McpHttpServerManifest | McpStdioServerManifest,
    Field(discriminator="type"),
]
```

Validate trimmed command/args, unique environment names, and simple command names or absolute executable paths.

- [ ] **Step 4: Implement Registry preflight validation**

Split server checks into focused private functions in `app/workspaces/registry.py`:

```python
def _validate_http_server(name, server, environ, errors): ...
def _validate_stdio_server(name, server, environ, errors): ...
```

For HTTP, parse the environment value and require `https` plus a host. For stdio, resolve the executable with `shutil.which`, then require the entrypoint value to be absolute, non-symlinked, and a regular file. Require every `env_vars` name to exist in the supplied environment. Error strings must include the MCP server name.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
uv run pytest -q tests/test_workspaces.py
```

Expected: all tests pass.

Commit:

```bash
git add app/workspaces/models.py app/workspaces/registry.py tests/test_workspaces.py
git commit -m "feat: validate stdio workspace MCP servers"
```

---

### Task 2: Resolve stdio MCP declarations for Claude Agent SDK

**Files:**
- Modify: `app/runtime/claude.py`
- Test: `tests/test_claude_runtime.py`

**Interfaces:**
- Consumes: snapshotted HTTP/stdio MCP dictionaries from Task 1.
- Produces: `ClaudeAgentRuntime._resolve_mcp_servers(snapshot) -> dict[str, Any]` with SDK-compatible HTTP and stdio values.

- [ ] **Step 1: Write a failing runtime options test**

Extend the Workspace snapshot in `test_build_options_injects_only_required_environment`:

```python
"local": {
    "type": "stdio",
    "command": "node",
    "entrypoint_env": "LOCAL_MCP_ENTRYPOINT",
    "args": ["--stdio"],
    "env_vars": ["CODEX_HOME"],
}
```

Provide the entrypoint and `CODEX_HOME` in the runtime environment, then assert:

```python
assert options.mcp_servers["local"] == {
    "type": "stdio",
    "command": "node",
    "args": [str(entrypoint), "--stdio"],
    "env": {"CODEX_HOME": "/tmp/codex-home"},
}
```

Also assert `UNRELATED_SECRET`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL` are absent from the server-specific `env` dictionary.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_claude_runtime.py::test_build_options_injects_only_required_environment
```

Expected: failure because `_resolve_mcp_servers` assumes every server has `url_env`.

- [ ] **Step 3: Implement transport-specific runtime resolution**

Update `_resolve_mcp_servers` to branch exhaustively on `config["type"]`:

```python
if server_type == "http":
    resolved[name] = self._resolve_http_mcp(name, config)
elif server_type == "stdio":
    resolved[name] = self._resolve_stdio_mcp(name, config)
else:
    raise AppError("mcp_unavailable", f"MCP server {name!r} has an unsupported transport.", 502)
```

The stdio resolver must fetch the entrypoint by environment name, preserve argv boundaries, and build `env` solely from declared `env_vars`. Missing runtime values raise `AppError("mcp_unavailable", ...)` naming the server.

- [ ] **Step 4: Run runtime tests and commit**

Run:

```bash
uv run pytest -q tests/test_claude_runtime.py tests/test_runtime_events.py
```

Expected: all tests pass.

Commit:

```bash
git add app/runtime/claude.py tests/test_claude_runtime.py
git commit -m "feat: resolve stdio MCP runtime config"
```

---

### Task 3: Configure the four MCP servers for new sessions

**Files:**
- Modify: `workspaces/example/workspace.yaml`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: manifest and runtime support from Tasks 1 and 2.
- Produces: New example sessions with four MCP declarations and matching allowlist wildcards.

- [ ] **Step 1: Write a snapshot isolation test**

Create a session from a registry entry with no MCP declarations, change the source manifest to add an HTTP MCP, rescan, and create a second session. Assert the first session snapshot still has `{}` while the second contains the new server. Use whole-object equality on the parsed `mcp_servers` values.

- [ ] **Step 2: Run the session test and verify current immutable behavior passes**

Run:

```bash
uv run pytest -q tests/test_sessions.py
```

Expected: all tests pass, documenting that no migration code is needed.

- [ ] **Step 3: Add the four example Workspace declarations**

Add these allowlist rules:

```yaml
  - mcp__anthropicDeveloperDocs__*
  - mcp__openaiDeveloperDocs__*
  - mcp__codexSecurity__*
  - mcp__dataAnalyticsWidgets__*
```

Add HTTP declarations using `ANTHROPIC_DOCS_MCP_URL` and `OPENAI_DOCS_MCP_URL`. Add stdio declarations using `node`, `CODEX_SECURITY_MCP_ENTRYPOINT`, `DATA_ANALYTICS_MCP_ENTRYPOINT`, `--stdio`, and `CODEX_HOME` only for `codexSecurity`.

- [ ] **Step 4: Document runtime environment and limitations**

Add the five required variables to `.env.example` and `README.md`. Document that arbitrary Workspace environment references must be exported into the service process, existing sessions do not gain MCPs, and MCP App interactive surfaces are not rendered.

- [ ] **Step 5: Run configuration and full regression tests**

Run:

```bash
uv run pytest -q
```

Expected: all non-live tests pass.

Commit:

```bash
git add workspaces/example/workspace.yaml .env.example README.md tests/test_sessions.py
git commit -m "feat: mount trusted MCP servers in new sessions"
```

---

### Task 4: Restart and perform live MCP verification

**Files:**
- Modify only if verification exposes a defect in files owned by Tasks 1-3.

**Interfaces:**
- Consumes: the completed Workspace MCP configuration.
- Produces: a running service on `http://127.0.0.1:8765` and evidence that all four MCP servers are usable or advertised.

- [ ] **Step 1: Resolve current trusted plugin entrypoints**

Use these installed files:

```text
/Users/a110356/.codex/plugins/cache/openai-curated-remote/codex-security/0.1.11/mcp/server.mjs
/Users/a110356/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.8-13ceeea1f599/mcp/server.cjs
```

Verify they remain regular non-symlink files before restart.

- [ ] **Step 2: Restart the service with MCP variables**

Export the two official HTTPS endpoints, both absolute plugin entrypoints, and `CODEX_HOME=/Users/a110356/.codex`, while retaining the existing Claude Base URL, API key, model, Skills root, data directory, and port.

- [ ] **Step 3: Verify Workspace and server health**

Run:

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/workspaces
```

Expected: health is `ok`, the example Workspace is available, and `mcp_server_count` is `4`.

- [ ] **Step 4: Verify all MCP tool catalogs**

Create a temporary new session and run a tool-discovery turn. Confirm tool-start events contain both documentation families when explicitly requested, and use MCP protocol `tools/list` against both local stdio entrypoints to confirm the `codexSecurity` and `dataAnalyticsWidgets` catalogs. Delete the temporary session afterward.

- [ ] **Step 5: Verify documentation tool calls and preserve old sessions**

In another temporary new session, invoke one search operation from each documentation MCP and assert the turn completes without a Workspace denial. Query an existing pre-change session snapshot and assert its `mcp_servers` remains `{}`. Delete temporary sessions.

- [ ] **Step 6: Final checks**

Run:

```bash
git diff --check
git status --short
curl -fsS http://127.0.0.1:8765/api/health
```

Expected: no whitespace errors, clean tracked worktree, and healthy service.
