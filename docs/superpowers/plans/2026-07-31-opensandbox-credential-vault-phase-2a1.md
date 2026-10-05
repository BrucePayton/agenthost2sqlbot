# OpenSandbox Credential Vault Phase 2A.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing Claude Agent SDK path inside an OpenSandbox Docker sandbox with a real configured Anthropic-compatible endpoint while keeping the real model API key exclusively in the Worker and OpenSandbox Credential Vault.

**Architecture:** Add a small application-owned credential provider and endpoint parser on the trusted Worker side. Extend the existing `SandboxPort` so `OpenSandboxAdapter` provisions the official per-sandbox Credential Vault before any Runner request is written, while the Runner receives only the configured Base URL and a fixed fake key. Keep the existing PostgreSQL Turn authority, Runner protocol, usage persistence, Skill materialization, Auto Memory, and Web/API contracts.

**Tech Stack:** Python 3.12, Pydantic 2, SQLAlchemy/PostgreSQL, Claude Agent SDK `>=0.2.128,<0.3`, OpenSandbox Python SDK `0.1.15`, OpenSandbox Server `0.2.2`, execd `1.0.21`, egress `1.1.4`, Docker Compose, pytest/pytest-asyncio.

## Global Constraints

- Use the official OpenSandbox Python SDK for Credential Vault create/get/patch; do not add a custom egress HTTP client.
- Keep OpenSandbox SDK `0.1.15`, Server `0.2.2`, execd `1.0.21`, and egress `1.1.4` pinned.
- Keep `[egress].mode = "dns+nft"`, `credentialProxy.enabled = true`, and network `defaultAction = "deny"` for real Claude sandboxes.
- The real `ANTHROPIC_API_KEY` may exist only in the Worker process and OpenSandbox egress sidecar memory.
- The Runner environment must use exactly `opensandbox-vault-placeholder` as `ANTHROPIC_API_KEY`.
- Credential injection binds only exact HTTPS host, canonical port `443`, methods `GET`/`POST`, the derived `/v1/*` path prefix, and header `x-api-key`.
- Credential Vault failure must prevent Runner request writing and command start; no plaintext fallback is allowed.
- Do not add database tables, a Model Gateway, billing aggregation, per-Workspace credential UI, MCP credential brokering, or production enablement.
- Preserve the four unrelated untracked files `agents.json`, `description.md`, `members.json`, and `squads.json`.
- Follow TDD: add a focused failing test, observe the intended failure, add the minimum implementation, rerun the focused test, then run the related suite.
- Every commit stages only the files listed by its task.

---

## File and Responsibility Map

| File | Responsibility |
|---|---|
| `app/sandbox/credentials.py` | Trusted credential scope, endpoint normalization, shared platform credential provider, Vault constants. |
| `app/sandbox/models.py` | Secret-free `RunnerModelConfig` and Session sandbox specification. |
| `app/config.py` | OpenSandbox-Claude startup validation using the shared endpoint parser. |
| `app/sandbox/contracts.py` | Backend-neutral `ensure_model_credential` operation and stable credential failure type. |
| `app/sandbox/opensandbox_adapter.py` | Official SDK Credential Vault create/get/patch, revision retry, safe Runner environment. |
| `app/sandbox/worker.py` | Resolve trusted Session scope, provision/refresh Vault before ready/request/barrier, fail closed and clean up. |
| `app/sandbox/main.py` | Construct and inject `PlatformModelCredentialProvider` only for the Claude runner. |
| `tests/test_model_credentials.py` | Endpoint and secret-model unit tests. |
| `tests/test_opensandbox_config.py` | Runtime-mode startup validation tests. |
| `tests/test_opensandbox_adapter.py` | Runner env mapping and official SDK Vault contract tests. |
| `tests/test_sandbox_worker.py` | New/warm ordering, fake bypass, and closed-failure lifecycle tests. |
| `tests/test_sandbox_main.py` | Worker dependency wiring tests. |
| `tests/security/test_opensandbox_boundary.py` | Static plaintext-fallback and deployment-boundary assertions. |
| `tests/integration/test_opensandbox_docker.py` | Real Docker Credential Vault injection with a synthetic credential. |
| `tests/live/test_opensandbox_claude.py` | Opt-in real Qwen/Claude chat, resume, Skill, Auto Memory, usage, and secret-absence Gate. |
| `scripts/verify-phase-2a.sh` | Mandatory regression Gate plus optional live Phase 2A.1 Gate. |
| `docs/operations/runtime-v2-verification-ledger.md` | Exact commands, component versions, live evidence, and remaining exclusions. |

---

### Task 1: Trusted Model Credential and Endpoint Contracts

**Files:**
- Create: `app/sandbox/credentials.py`
- Modify: `app/sandbox/models.py:1-110`
- Create: `tests/test_model_credentials.py`

**Interfaces:**
- Produces: `CredentialScope(workspace_id: str, user_id: str, session_id: str)`.
- Produces: `ModelEndpoint(base_url: str, host: str, binding_path: str)`.
- Produces: `ModelCredential(endpoint: ModelEndpoint, api_key: SecretStr)` with the key excluded from `repr`.
- Produces: `ModelCredentialProvider.resolve(scope: CredentialScope) -> ModelCredential`.
- Produces: `PlatformModelCredentialProvider(base_url: str, api_key: SecretStr, allowed_hosts: tuple[str, ...])`.
- Produces: `parse_model_endpoint(base_url: str, allowed_hosts: tuple[str, ...]) -> ModelEndpoint`.
- Produces: `RunnerModelConfig(base_url: str, api_key_placeholder: str)` and constant `MODEL_API_KEY_PLACEHOLDER = "opensandbox-vault-placeholder"`.

- [ ] **Step 1: Write failing endpoint-normalization tests**

Create `tests/test_model_credentials.py` with exact root, prefixed, and existing-`/v1` cases:

```python
import pytest
from pydantic import SecretStr


@pytest.mark.parametrize(
    ("base_url", "host", "binding_path", "normalized"),
    [
        (
            "https://api.anthropic.com",
            "api.anthropic.com",
            "/v1/*",
            "https://api.anthropic.com",
        ),
        (
            "https://dashscope.aliyuncs.com/apps/anthropic/",
            "dashscope.aliyuncs.com",
            "/apps/anthropic/v1/*",
            "https://dashscope.aliyuncs.com/apps/anthropic",
        ),
        (
            "https://gateway.example.com/anthropic/v1",
            "gateway.example.com",
            "/anthropic/v1/*",
            "https://gateway.example.com/anthropic/v1",
        ),
    ],
)
def test_parse_model_endpoint_derives_exact_binding(
    base_url, host, binding_path, normalized
) -> None:
    from app.sandbox.credentials import parse_model_endpoint

    endpoint = parse_model_endpoint(base_url, (host, "mcp.example.com"))
    assert endpoint.host == host
    assert endpoint.binding_path == binding_path
    assert endpoint.base_url == normalized
```

- [ ] **Step 2: Write failing unsafe-endpoint and secret-redaction tests**

Add parameterized rejections for HTTP, userinfo, query, fragment, IP, non-443 port,
dot segments, encoded separators, and an exact-host allowlist miss:

```python
@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com",
        "https://user:pass@api.example.com",
        "https://api.example.com/v1?key=x",
        "https://api.example.com/v1#fragment",
        "https://127.0.0.1/v1",
        "https://api.example.com:8443/v1",
        "https://api.example.com/a/../v1",
        "https://api.example.com/a%2fv1",
    ],
)
def test_parse_model_endpoint_rejects_unsafe_urls(base_url) -> None:
    from app.sandbox.credentials import parse_model_endpoint

    with pytest.raises(ValueError, match="model endpoint"):
        parse_model_endpoint(base_url, ("api.example.com",))


def test_platform_credential_is_redacted_and_scope_aware() -> None:
    from app.sandbox.credentials import (
        CredentialScope,
        PlatformModelCredentialProvider,
    )

    provider = PlatformModelCredentialProvider(
        "https://api.example.com/anthropic",
        SecretStr("phase2a-real-canary"),
        ("api.example.com",),
    )
    scope = CredentialScope("workspace-1", "user-1", "session-1")
    assert "phase2a-real-canary" not in repr(provider)
    assert "phase2a-real-canary" not in repr(provider._credential)
```

- [ ] **Step 3: Run the new tests and observe the missing module failure**

Run:

```bash
uv run pytest tests/test_model_credentials.py -q
```

Expected: collection or import fails because `app.sandbox.credentials` and
`RunnerModelConfig` do not exist.

- [ ] **Step 4: Implement the endpoint parser and credential provider**

Create `app/sandbox/credentials.py` using the existing
`app.sandbox.models.validate_allowed_host` function. The implementation must:

```python
MODEL_CREDENTIAL_NAME = "workspace-agent-model-api-key"
MODEL_BINDING_NAME = "workspace-agent-model-endpoint"


@dataclass(frozen=True)
class ModelCredential:
    endpoint: ModelEndpoint
    api_key: SecretStr = field(repr=False)


def parse_model_endpoint(base_url: str, allowed_hosts: tuple[str, ...]) -> ModelEndpoint:
    parsed = urlsplit(base_url.strip())
    # Validate HTTPS, credentials, query, fragment, host, port, and path first.
    # Normalize the exact host through validate_allowed_host.
    # Reject percent-encoded slash/backslash and dot segments.
    # Return either <prefix>/* when prefix ends /v1 or <prefix>/v1/*.
```

Implement `PlatformModelCredentialProvider.resolve` as an async method returning its
immutable credential. Reject blank scope identifiers and a blank API key in constructors.
Do not compute or persist a key hash.

- [ ] **Step 5: Add the secret-free Runner model configuration**

Modify `app/sandbox/models.py`:

```python
MODEL_API_KEY_PLACEHOLDER = "opensandbox-vault-placeholder"


@dataclass(frozen=True)
class RunnerModelConfig:
    base_url: str
    api_key_placeholder: str = MODEL_API_KEY_PLACEHOLDER

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("runner model base URL cannot be blank")
        if self.api_key_placeholder != MODEL_API_KEY_PLACEHOLDER:
            raise ValueError("runner model API key must use the fixed placeholder")
```

Add `model_config: RunnerModelConfig | None = None` to `SessionSandboxSpec`. Require a
model config when `credential_proxy_required` is true and forbid it when false.

- [ ] **Step 6: Run focused tests and the existing sandbox-model tests**

Run:

```bash
uv run pytest tests/test_model_credentials.py tests/test_opensandbox_config.py -q
```

Expected: the new endpoint and redaction tests pass; existing configuration tests remain
green until Task 2 adds runtime-mode validation.

- [ ] **Step 7: Commit the contracts**

```bash
git add app/sandbox/credentials.py app/sandbox/models.py tests/test_model_credentials.py
git commit -m "feat(sandbox): define model credential contracts"
```

---

### Task 2: OpenSandbox-Claude Configuration and Worker Dependency Wiring

**Files:**
- Modify: `app/config.py:285-330`
- Modify: `app/sandbox/main.py:20-52`
- Modify: `tests/test_opensandbox_config.py`
- Create: `tests/test_sandbox_main.py`

**Interfaces:**
- Consumes: `parse_model_endpoint` and `PlatformModelCredentialProvider` from Task 1.
- Produces: an `OpenSandboxExecutionWorker` with `credential_provider` set for
  `runner_runtime="claude"` and `None` for `runner_runtime="fake"`.

- [ ] **Step 1: Add failing runtime-mode validation tests**

Extend `tests/test_opensandbox_config.py`:

```python
def opensandbox_claude_settings(settings_factory, **overrides):
    values = {
        "app_runtime_mode": "opensandbox_docker",
        "database_url": "postgresql+asyncpg://workspace:workspace@db/workspace",
        "opensandbox_api_url": "http://127.0.0.1:8080",
        "opensandbox_api_key": "sandbox-secret",
        "opensandbox_runner_runtime": "claude",
        "anthropic_base_url": "https://proxy.example.test/apps/anthropic",
        "opensandbox_allowed_hosts": ("proxy.example.test",),
    }
    return settings_factory(**{**values, **overrides})


def test_opensandbox_claude_requires_safe_allowlisted_model_endpoint(settings_factory):
    settings = opensandbox_claude_settings(settings_factory)
    assert settings.opensandbox_runner_runtime == "claude"

    with pytest.raises(ValidationError, match="model endpoint"):
        opensandbox_claude_settings(
            settings_factory,
            anthropic_base_url="http://proxy.example.test/apps/anthropic",
        )
    with pytest.raises(ValidationError, match="allowlist"):
        opensandbox_claude_settings(
            settings_factory,
            opensandbox_allowed_hosts=("different.example.test",),
        )


def test_opensandbox_fake_does_not_require_model_host(settings_factory):
    settings = opensandbox_claude_settings(
        settings_factory,
        opensandbox_runner_runtime="fake",
        anthropic_base_url="http://proxy.example.test",
        opensandbox_allowed_hosts=(),
    )
    assert settings.opensandbox_runner_runtime == "fake"
```

- [ ] **Step 2: Run configuration tests and observe acceptance of unsafe Claude config**

Run:

```bash
uv run pytest tests/test_opensandbox_config.py -q
```

Expected: the unsafe endpoint or allowlist assertion fails because the cross-field model
validator does not call `parse_model_endpoint`.

- [ ] **Step 3: Validate only the real Claude OpenSandbox profile**

In the existing `Settings` model validator, after the OpenSandbox PostgreSQL/API checks:

```python
if (
    self.app_runtime_mode == "opensandbox_docker"
    and self.opensandbox_runner_runtime == "claude"
):
    from app.sandbox.credentials import parse_model_endpoint

    parse_model_endpoint(
        str(self.anthropic_base_url),
        self.opensandbox_allowed_hosts,
    )
```

Do not tighten `local_inline` or the fake Docker Gate.

- [ ] **Step 4: Add failing Worker-wiring tests**

Create `tests/test_sandbox_main.py`. Monkeypatch `build_app_services`,
`OpenSandboxAdapter`, and `OpenSandboxExecutionWorker` with capturing fakes. Assert:

```python
def test_build_worker_injects_platform_provider_for_claude(settings_factory, monkeypatch):
    settings = settings_factory(
        app_runtime_mode="opensandbox_docker",
        database_url="postgresql+asyncpg://workspace:workspace@db/workspace",
        opensandbox_api_url="http://127.0.0.1:8080",
        opensandbox_api_key="sandbox-secret",
        anthropic_base_url="https://proxy.example.test/apps/anthropic",
        opensandbox_allowed_hosts=("proxy.example.test",),
    )
    _services, worker = build_execution_worker(settings)
    assert type(worker.credential_provider).__name__ == "PlatformModelCredentialProvider"
    assert "top-secret-test-key" not in repr(worker.credential_provider)


def test_build_worker_omits_provider_for_fake(settings_factory, monkeypatch):
    # Same OpenSandbox control-plane settings, but runner_runtime="fake" and no hosts.
    _services, worker = build_execution_worker(settings)
    assert worker.credential_provider is None
```

- [ ] **Step 5: Wire the provider in `build_execution_worker`**

Construct exactly one `PlatformModelCredentialProvider` for Claude:

```python
credential_provider = (
    PlatformModelCredentialProvider(
        str(settings.anthropic_base_url),
        settings.anthropic_api_key,
        settings.opensandbox_allowed_hosts,
    )
    if settings.opensandbox_runner_runtime == "claude"
    else None
)
```

Pass it into `OpenSandboxExecutionWorker`. The API process remains unchanged.

- [ ] **Step 6: Run focused configuration and wiring tests**

```bash
uv run pytest tests/test_opensandbox_config.py tests/test_sandbox_main.py -q
```

Expected: all tests pass and no secret appears in failure output.

- [ ] **Step 7: Commit configuration and wiring**

```bash
git add app/config.py app/sandbox/main.py tests/test_opensandbox_config.py tests/test_sandbox_main.py
git commit -m "feat(worker): wire platform model credential provider"
```

---

### Task 3: Safe Runner Environment at Sandbox Creation

**Files:**
- Modify: `app/sandbox/opensandbox_adapter.py:70-125`
- Modify: `tests/test_opensandbox_adapter.py:55-125`
- Modify: `tests/integration/test_opensandbox_docker.py:24-55`

**Interfaces:**
- Consumes: `SessionSandboxSpec.model_config` and `MODEL_API_KEY_PLACEHOLDER`.
- Produces: Claude sandbox environment containing only safe routing/configuration values.

- [ ] **Step 1: Make the existing SDK mapping test require the safe environment**

Update `sandbox_spec()` in `tests/test_opensandbox_adapter.py` to pass:

```python
model_config=RunnerModelConfig(
    base_url="https://api.anthropic.com",
)
```

Add these assertions to `test_create_maps_application_spec_to_official_sdk_types`:

```python
assert kwargs["env"] == {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_API_KEY": "opensandbox-vault-placeholder",
    "WORKSPACES_ROOT": "/session/workspace",
    "APP_DATA_DIR": "/session/runtime-data",
}
assert "top-secret-test-key" not in repr(kwargs["env"])
```

Add a fake-runtime assertion that `env` is omitted when `model_config is None`.

- [ ] **Step 2: Run the focused adapter test and observe missing `env`**

```bash
uv run pytest tests/test_opensandbox_adapter.py::test_create_maps_application_spec_to_official_sdk_types -q
```

Expected: FAIL because `OpenSandboxAdapter` does not pass `env` to `Sandbox.create`.

- [ ] **Step 3: Pass only the safe Runner environment**

In `create_session_sandbox`, build `runner_env` only when a model config is present:

```python
runner_env = None
if spec.model_config is not None:
    runner_env = {
        "ANTHROPIC_BASE_URL": spec.model_config.base_url,
        "ANTHROPIC_API_KEY": spec.model_config.api_key_placeholder,
        "WORKSPACES_ROOT": "/session/workspace",
        "APP_DATA_DIR": "/session/runtime-data",
    }
```

Pass `env=runner_env` only for the Claude path. Do not pass `DATABASE_URL`,
`OPENSANDBOX_API_KEY`, host `PATH`, or any host environment mapping.

- [ ] **Step 4: Update fake Docker integration construction**

Keep the real Phase 2A fake Docker spec explicit with `model_config=None`. Add a command
assertion that `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` are absent in the fake
sandbox.

- [ ] **Step 5: Run adapter and fake Docker unit coverage**

```bash
uv run pytest tests/test_opensandbox_adapter.py tests/security/test_opensandbox_boundary.py -q
```

Expected: all deterministic tests pass. The opt-in Docker test remains skipped here.

- [ ] **Step 6: Commit the safe Runner environment**

```bash
git add app/sandbox/opensandbox_adapter.py tests/test_opensandbox_adapter.py tests/integration/test_opensandbox_docker.py
git commit -m "feat(sandbox): inject placeholder model environment"
```

---

### Task 4: Official OpenSandbox Credential Vault Adapter

**Files:**
- Modify: `app/sandbox/contracts.py:1-70`
- Modify: `app/sandbox/opensandbox_adapter.py:1-285`
- Modify: `tests/test_opensandbox_adapter.py`

**Interfaces:**
- Consumes: `ModelCredential`, `MODEL_CREDENTIAL_NAME`, and `MODEL_BINDING_NAME`.
- Produces: `SandboxPort.ensure_model_credential(handle, credential) -> None`.
- Produces: one retry for HTTP `409`; maps every terminal Vault error to
  `CredentialProxyUnavailable` without including the source exception text.

- [ ] **Step 1: Extend the fake SDK with a Credential Vault facade**

Add a `FakeCredentialVault` used by `FakeSandbox`:

```python
class FakeCredentialVault:
    def __init__(self) -> None:
        self.state = None
        self.created = []
        self.patched = []

    async def get(self):
        if self.state is None:
            raise SandboxApiException("missing", status_code=404)
        return self.state

    async def create(self, *, credentials, bindings):
        self.created.append((credentials, bindings))
        self.state = CredentialVaultState(revision=1, credentials=[], bindings=[])
        return self.state

    async def patch(self, **kwargs):
        self.patched.append(kwargs)
        self.state = CredentialVaultState(
            revision=kwargs["expected_revision"] + 1,
            credentials=[],
            bindings=[],
        )
        return self.state
```

- [ ] **Step 2: Add failing create and refresh contract tests**

Add tests which call `ensure_model_credential` twice and inspect official SDK models:

```python
credential = ModelCredential(
    endpoint=parse_model_endpoint(
        "https://dashscope.aliyuncs.com/apps/anthropic",
        ("dashscope.aliyuncs.com",),
    ),
    api_key=SecretStr("phase2a-real-canary"),
)
await adapter.ensure_model_credential(handle, credential)
assert vault.created[0][0][0].name == "workspace-agent-model-api-key"
assert vault.created[0][0][0].source["value"] == "phase2a-real-canary"
assert vault.created[0][1][0].match["hosts"] == ["dashscope.aliyuncs.com"]
assert vault.created[0][1][0].match["paths"] == ["/apps/anthropic/v1/*"]
assert vault.created[0][1][0].auth == {
    "type": "apiKey",
    "name": "x-api-key",
    "credential": "workspace-agent-model-api-key",
}

await adapter.ensure_model_credential(handle, credential)
assert vault.patched[0]["expected_revision"] == 1
assert vault.patched[0]["credentials"].replace[0].name == "workspace-agent-model-api-key"
```

Assert the canary is absent from `repr(adapter)`, returned application models, and
raised application errors.

- [ ] **Step 3: Add failing conflict and redacted-error tests**

Configure the fake Vault to raise `SandboxApiException(status_code=409)` once and then
succeed; assert two sanitized reads and one successful patch. Configure two conflicts;
assert `CredentialProxyUnavailable.code == "credential_proxy_unavailable"` and that the
canary and SDK response body are absent from `str(error)`.

- [ ] **Step 4: Run focused tests and observe the missing port operation**

```bash
uv run pytest tests/test_opensandbox_adapter.py -q
```

Expected: FAIL because neither `SandboxPort` nor `OpenSandboxAdapter` defines
`ensure_model_credential`.

- [ ] **Step 5: Add the backend-neutral port operation**

Extend `SandboxPort`:

```python
async def ensure_model_credential(
    self,
    handle: SandboxHandle,
    credential: ModelCredential,
) -> None: ...
```

Keep `CredentialProxyUnavailable` as the stable application error.

- [ ] **Step 6: Implement official SDK create/get/patch**

In `OpenSandboxAdapter`, construct only official SDK models:

```python
sdk_credential = Credential(
    name=MODEL_CREDENTIAL_NAME,
    source={"value": credential.api_key.get_secret_value()},
)
sdk_binding = CredentialBinding(
    name=MODEL_BINDING_NAME,
    match={
        "schemes": ["https"],
        "hosts": [credential.endpoint.host],
        "methods": ["GET", "POST"],
        "paths": [credential.endpoint.binding_path],
    },
    auth={
        "type": "apiKey",
        "name": "x-api-key",
        "credential": MODEL_CREDENTIAL_NAME,
    },
)
```

On `get` HTTP 404, call `create`. Otherwise call `patch` with
`CredentialMutationSet(replace=[sdk_credential])` and
`CredentialBindingMutationSet(replace=[sdk_binding])`. Treat create/patch HTTP 409 as
one retry of the whole get/create-or-patch loop. Extract status only from the SDK
exception and its cause chain; never incorporate exception text into the application
error.

- [ ] **Step 7: Run adapter tests**

```bash
uv run pytest tests/test_opensandbox_adapter.py -q
```

Expected: all SDK mapping, create, patch, retry, and redaction tests pass.

- [ ] **Step 8: Commit the official Vault adapter**

```bash
git add app/sandbox/contracts.py app/sandbox/opensandbox_adapter.py tests/test_opensandbox_adapter.py
git commit -m "feat(sandbox): provision opensandbox credential vault"
```

---

### Task 5: Worker Ordering, Warm Refresh, and Closed Failure

**Files:**
- Modify: `app/sandbox/worker.py:25-245`
- Modify: `app/sandbox/repository.py:217-233`
- Modify: `tests/test_sandbox_worker.py`

**Interfaces:**
- Consumes: `ModelCredentialProvider`, `CredentialScope`, `RunnerModelConfig`, and
  `SandboxPort.ensure_model_credential`.
- Produces: Vault provisioning before `record_sandbox_ready`, `write_request`, and the
  existing execution barrier.
- Produces: `failed_before_execution` with error code
  `credential_proxy_unavailable` on a closed Vault failure.

- [ ] **Step 1: Upgrade `FakeSandboxPort` with ordering evidence**

Add fields and operation recording:

```python
self.operations = []
self.fail_credential = False

async def ensure_model_credential(self, handle, credential):
    self.operations.append("credential")
    if self.fail_credential:
        raise CredentialProxyUnavailable("Credential proxy is unavailable.")

async def write_request(self, handle, request_bytes):
    self.operations.append("write_request")
    ...

async def run_turn(self, handle, request_path):
    self.operations.append("run_turn")
    ...
```

Add a `FakeCredentialProvider` which records exact `CredentialScope` values and returns
a `ModelCredential` containing a canary `SecretStr`.

- [ ] **Step 2: Add failing new and warm sandbox ordering tests**

For the first Turn assert:

```python
assert sandbox.operations.index("credential") < sandbox.operations.index("write_request")
assert sandbox.operations.index("write_request") < sandbox.operations.index("run_turn")
assert provider.scopes == [CredentialScope(session.workspace_id, session.created_by, session.id)]
assert b"phase2a-real-canary" not in sandbox.request_bytes
assert sandbox.spec.model_config.api_key_placeholder == "opensandbox-vault-placeholder"
```

Run a second Turn in the same Session and assert one sandbox creation but two
`credential` operations.

- [ ] **Step 3: Add failing fake-runtime bypass test**

Construct the Worker with `runner_runtime="fake"` and `credential_provider=None`.
Assert `ensure_model_credential` is never called, `allowed_hosts == ()`, and
`model_config is None`.

- [ ] **Step 4: Add failing credential failure cleanup tests**

For a new sandbox and a warm sandbox, set `fail_credential=True`. Assert:

```python
assert "write_request" not in sandbox.operations
assert "run_turn" not in sandbox.operations
assert sandbox.destroyed == ["sandbox-1"]
failed = await turns.get(turn.id)
assert failed.status == "failed_before_execution"
assert failed.error_code == "credential_proxy_unavailable"
assert "phase2a-real-canary" not in (failed.error_message or "")
```

Add a cleanup-failure case: make `destroy_sandbox` raise and assert the sandbox authority
becomes `recovery_required` with reason `credential_proxy_cleanup_failed`, while the Turn
still ends `failed_before_execution` and no command starts.

- [ ] **Step 5: Run Worker tests and observe missing constructor/ordering behavior**

```bash
uv run pytest tests/test_sandbox_worker.py -q
```

Expected: FAIL because the Worker does not accept a credential provider, does not call
the Vault, and currently records a new sandbox ready before provisioning.

- [ ] **Step 6: Load Session identity once and resolve the credential**

Add `credential_provider: ModelCredentialProvider | None` to the Worker constructor.
Enforce `claude => provider` and `fake => no provider`. Before reserving a generation:

```python
owner = await self._session_owner(runtime_request.platform_session_id)
scope = CredentialScope(
    workspace_id=owner.workspace_id,
    user_id=owner.created_by,
    session_id=owner.id,
)
credential = (
    await self.credential_provider.resolve(scope)
    if self.credential_provider is not None
    else None
)
```

Pass the already-loaded owner into `_runner_request` so no second identity query is
needed.

- [ ] **Step 7: Provision before ready and before request writing**

For a Claude sandbox, construct:

```python
model_config=RunnerModelConfig(base_url=credential.endpoint.base_url)
```

Call `ensure_model_credential(handle, credential)` after create/inspect. For a new
sandbox, move `record_sandbox_ready` after that call. The existing execution barrier
remains after request writing and before `run_turn`.

- [ ] **Step 8: Implement the closed credential-failure branch**

Catch only `CredentialProxyUnavailable` before the generic ambiguity handler. Attempt
to destroy the sandbox. On successful destruction call `mark_reaped`; on failed
destruction call `mark_recovery_required` with
`credential_proxy_cleanup_failed`. Finish the Turn with:

```python
turn_status="failed_before_execution"
session_status="error"
error_code="credential_proxy_unavailable"
error_message="The sandbox credential proxy is unavailable."
event=RuntimeEvent(
    "turn.failed",
    {
        "code": "credential_proxy_unavailable",
        "message": "The sandbox credential proxy is unavailable.",
    },
    "system",
)
```

Return the Turn ID rather than crashing the worker loop. Always release the existing
Memory lease in `finally`.

- [ ] **Step 9: Generalize the recovery authority message**

Change `SandboxRepository.mark_recovery_required` to persist
`"Sandbox state requires reconciliation."` instead of the command-specific message.
Keep every existing reason code unchanged and add the new cleanup reason test.

- [ ] **Step 10: Run Worker, repository, and Turn state tests**

```bash
uv run pytest \
  tests/test_sandbox_worker.py \
  tests/test_sandbox_repository.py \
  tests/test_turn_state_machine.py -q
```

Expected: all tests pass, including existing barrier/restart/cancel/reap scenarios.

- [ ] **Step 11: Commit Worker fail-closed execution**

```bash
git add app/sandbox/worker.py app/sandbox/repository.py tests/test_sandbox_worker.py
git commit -m "feat(worker): fail closed on credential proxy errors"
```

---

### Task 6: Static Security and Real Docker Credential Injection

**Files:**
- Modify: `tests/security/test_opensandbox_boundary.py`
- Modify: `tests/integration/test_opensandbox_docker.py`
- Verify unchanged: `deploy/opensandbox/compose.yaml`
- Verify unchanged: `deploy/opensandbox/server.toml`

**Interfaces:**
- Consumes: the completed adapter and safe Runner environment.
- Produces: deterministic static assertions and an opt-in real Docker Vault test using
  a synthetic credential, never the deployment model key.
- Verifies: the already-pinned Compose stack and `dns+nft` server mode require no
  speculative deployment mutation for the official SDK path.

- [ ] **Step 1: Add static plaintext-fallback assertions**

Extend `tests/security/test_opensandbox_boundary.py` to inspect
`app/sandbox/worker.py`, `app/sandbox/opensandbox_adapter.py`, and the Runner Dockerfile.
Assert the Runner Dockerfile contains no secret env declarations, the fixed placeholder
appears in the application model, and the Worker never copies `os.environ` into a
`SessionSandboxSpec`.

- [ ] **Step 2: Add an opt-in real Docker synthetic Vault test**

Add a second test in `tests/integration/test_opensandbox_docker.py` guarded by
`RUN_OPENSANDBOX_CREDENTIAL_VAULT=1`. It must:

1. create a Claude-shaped sandbox with Credential Proxy enabled and a synthetic
   high-entropy canary;
2. call `ensure_model_credential` against an operator-supplied HTTPS echo endpoint
   configured by `OPENSANDBOX_CREDENTIAL_TEST_BASE_URL`;
3. execute `curl` or Python `httpx` inside the sandbox with the fixed fake `x-api-key`;
4. assert the echo response sees the synthetic canary injected by the sidecar;
5. assert the sandbox environment still contains only the placeholder;
6. delete the Vault and repeat the request, asserting the upstream sees the placeholder
   or rejects auth, never the synthetic canary;
7. destroy the sandbox and volumes in `finally`.

The synthetic endpoint is an explicit operator dependency because a deterministic local
TLS origin requires trusted DNS and certificates. Do not weaken upstream TLS validation,
use HTTP, or send a real model credential to a generic echo service.

- [ ] **Step 3: Run static and deterministic integration coverage**

```bash
uv run pytest tests/security/test_opensandbox_boundary.py -q
uv run pytest tests/integration/test_opensandbox_docker.py -q
```

Expected: static tests pass and Docker tests skip without their explicit environment
flags.

- [ ] **Step 4: Run the real synthetic Vault Gate when its HTTPS fixture is configured**

```bash
RUN_OPENSANDBOX_DOCKER=1 \
RUN_OPENSANDBOX_CREDENTIAL_VAULT=1 \
OPENSANDBOX_CREDENTIAL_TEST_BASE_URL="https://credential-echo.example.test/v1" \
OPENSANDBOX_API_URL="$OPENSANDBOX_API_URL" \
OPENSANDBOX_API_KEY="$OPENSANDBOX_API_KEY" \
OPENSANDBOX_RUNNER_IMAGE="$OPENSANDBOX_RUNNER_IMAGE" \
uv run pytest \
  tests/integration/test_opensandbox_docker.py::test_real_credential_vault_injects_synthetic_key -q
```

Expected: PASS; test output contains neither the synthetic canary nor any platform key.

- [ ] **Step 5: Commit static and Docker security coverage**

```bash
git add tests/security/test_opensandbox_boundary.py tests/integration/test_opensandbox_docker.py
git commit -m "test(sandbox): prove credential vault injection boundary"
```

---

### Task 7: Opt-in Real Model, Resume, Skill, Memory, and Usage Gate

**Files:**
- Create: `tests/live/test_opensandbox_claude.py`
- Modify: `scripts/verify-phase-2a.sh`

**Interfaces:**
- Consumes: `build_execution_worker`, existing Workspace/Skill/Session/Turn services,
  and the real deployment-owned model configuration.
- Produces: one opt-in end-to-end Phase 2A.1 acceptance suite.

- [ ] **Step 1: Create an isolated live Workspace and managed Skill fixture**

Create `tests/live/test_opensandbox_claude.py`, skipped unless
`RUN_LIVE_OPENSANDBOX_CLAUDE=1`. Build a temporary Workspace whose allowed tools are
`Read`, `Write`, `Edit`, `Glob`, and `Grep`. Initialize the configured PostgreSQL
database, synchronize a unique Workspace and mock owner, and import this enabled bundle
before creating the Session:

```python
bundle = build_bundle(
    b"""---
name: vault-marker
description: Reply with the exact Phase 2A.1 skill marker when explicitly requested.
---
# Vault marker

When invoked, include the exact text SKILL_VAULT_PHASE_2A1 in the answer.
""",
    (),
)
await services.skills.import_bundle(
    workspace.id,
    identity,
    bundle,
    enabled=True,
    origin={"type": "phase2a1_live_test"},
)
```

Use a UUID suffix for Workspace, request, and Memory markers so reruns cannot reuse old
state.

- [ ] **Step 2: Add real chat, usage, and resume assertions**

Start a Session, queue a prompt asking the Agent to invoke `vault-marker`, and call
`worker.execute_one()`. Assert:

```python
first = await turns.get(first_turn.id)
assert first.status == "completed"
assert (first.input_tokens or 0) > 0
assert (first.output_tokens or 0) > 0
assert (await sessions.get(session.id)).claude_session_id
assert "SKILL_VAULT_PHASE_2A1" in assistant_text
```

Queue a second Turn in the same Session, execute it, and assert the persisted
`claude_session_id` is unchanged.

- [ ] **Step 3: Add Auto Memory cross-Session assertions**

Queue an explicit request to write a unique marker to `MEMORY.md`. After completion,
create a second Session for the same user and Workspace, ask it to read the marker,
execute through the Worker, and assert the second Session's assistant response contains
the marker. Query `SessionSandboxRecord` to confirm the Sessions have different Session
volumes but the same Memory volume.

- [ ] **Step 4: Add Runner secret-absence inspection**

Connect to each live sandbox through the official OpenSandbox SDK. Run a command that
prints only the selected safe variables and hashes/listings of the isolated request,
Session, and Memory files. Assert in the host test process:

```python
assert "ANTHROPIC_API_KEY=opensandbox-vault-placeholder" in inspection
assert real_key not in inspection
assert real_key not in request_bytes.decode("utf-8")
assert real_key not in "\n".join(event.payload_json for event in durable_events)
```

Never interpolate `real_key` into a sandbox command or command argument.

- [ ] **Step 5: Add a live no-fallback assertion**

After all product Turns complete, delete the sandbox Credential Vault through
`sandbox.credential_vault.delete()`. Execute one direct isolated request using the
existing secret-free Runner request file, without calling the Worker refresh path.
Assert the Runner emits `runtime_failed` or an upstream auth failure and that no output
contains the real key. This proves deletion cannot cause plaintext environment fallback.

- [ ] **Step 6: Add cleanup that preserves no live test resources**

In `finally`, destroy all live sandboxes, remove only the UUID-named Docker volumes,
shut down Turn services, dispose the database, and remove temporary Workspace files.
Never use a broad Docker prune command.

- [ ] **Step 7: Add optional live execution to the Phase 2A script**

Keep the existing mandatory fake-model Gate unchanged. After it passes, add:

```bash
if [[ "${RUN_LIVE_OPENSANDBOX_CLAUDE:-0}" == "1" ]]; then
  RUN_LIVE_OPENSANDBOX_CLAUDE=1 \
  OPENSANDBOX_API_URL="http://127.0.0.1:${OPENSANDBOX_PORT}" \
  OPENSANDBOX_API_KEY="$SERVER_KEY" \
  OPENSANDBOX_RUNNER_IMAGE="$RUNNER_IMAGE" \
  DATABASE_URL="$TEST_POSTGRES_URL" \
    uv run pytest tests/live/test_opensandbox_claude.py -q
fi
```

The script inherits `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, and `CLAUDE_MODEL` from
the operator environment without printing them.

- [ ] **Step 8: Run collection and the mandatory fake Gate**

```bash
uv run pytest tests/live/test_opensandbox_claude.py --collect-only -q
bash scripts/verify-phase-2a.sh
```

Expected: the live test collects and skips without its flag; the complete mandatory
Phase 2A Gate remains green.

- [ ] **Step 9: Run the deployment-owned live Gate**

```bash
RUN_LIVE_OPENSANDBOX_CLAUDE=1 \
CLAUDE_MODEL=qwen3.7-plus \
bash scripts/verify-phase-2a.sh
```

Expected: fake Gate plus real chat, resume, Skill, Auto Memory, usage, secret absence,
and no-fallback assertions all pass. The command relies on already configured secret
environment variables and must not echo them.

- [ ] **Step 10: Commit the live Gate**

```bash
git add tests/live/test_opensandbox_claude.py scripts/verify-phase-2a.sh
git commit -m "test(sandbox): add live credential vault model gate"
```

---

### Task 8: Verification Ledger and Final Regression

**Files:**
- Modify: `docs/operations/runtime-v2-verification-ledger.md`
- Modify: `README.md` only if it currently documents the OpenSandbox Docker startup
  command without the new Claude allowlist requirement.

**Interfaces:**
- Consumes: exact command output from Tasks 1-7.
- Produces: an evidence-backed Phase 2A.1 Gate record without credentials.

- [ ] **Step 1: Run formatting and focused regression**

```bash
uv run ruff check app tests
uv run pytest \
  tests/test_model_credentials.py \
  tests/test_opensandbox_config.py \
  tests/test_sandbox_main.py \
  tests/test_opensandbox_adapter.py \
  tests/test_sandbox_worker.py \
  tests/test_sandbox_repository.py \
  tests/security/test_opensandbox_boundary.py -q
```

Expected: Ruff and every focused test pass.

- [ ] **Step 2: Run the full deterministic suite**

```bash
uv lock --check
node --test tests/js/*.cjs
uv run pytest -q
```

Expected: lock check, JavaScript tests, and Python tests pass; only explicit live tests
are skipped.

- [ ] **Step 3: Run the mandatory Docker/PostgreSQL Gate**

```bash
bash scripts/verify-phase-2a.sh
```

Expected: the existing Phase 2A Gate and new deterministic credential tests pass.

- [ ] **Step 4: Run the live Phase 2A.1 Gate when deployment credentials are present**

```bash
RUN_LIVE_OPENSANDBOX_CLAUDE=1 \
CLAUDE_MODEL=qwen3.7-plus \
bash scripts/verify-phase-2a.sh
```

Expected: all Phase 2A.1 scenarios pass without printing secrets. If deployment-owned
credentials or endpoint access are unavailable, record the live Gate as not run rather
than substituting a plaintext key path.

- [ ] **Step 5: Update the verification ledger with exact evidence**

Record:

- commit SHA;
- UTC and local verification time;
- SDK `0.1.15`, Server `0.2.2`, execd `1.0.21`, egress `1.1.4`;
- immutable Runner image digest;
- model name but not Base URL query data or API key;
- deterministic test counts;
- Docker/PostgreSQL Gate result;
- real chat/resume/Skill/Memory/usage/no-fallback result or an explicit not-run reason;
- the unchanged exclusions for Kubernetes, CSI/RWOP, multi-machine recovery, Secret
  Manager, production credentials, and production multi-tenancy.

- [ ] **Step 6: Review the final diff for secret leakage and unrelated files**

```bash
git diff --check
git status --short
git diff -- . ':!agents.json' ':!description.md' ':!members.json' ':!squads.json'
```

Search tracked changes for the actual secret canary used by tests and confirm there are
zero matches outside intentionally synthetic constant strings. Do not print the real
deployment key to perform the search.

- [ ] **Step 7: Commit verification evidence**

```bash
git add docs/operations/runtime-v2-verification-ledger.md README.md
git commit -m "docs: record phase 2a1 credential vault gate"
```

If `README.md` did not require a change, omit it from `git add`.

- [ ] **Step 8: Report the completion boundary**

Report the implemented user-visible behaviors, exact tests and Gate results, commits,
and any live Gate that could not run. State explicitly that local Docker Credential
Vault success does not enable production credentials or certify the Phase 2B Kubernetes
gate.
