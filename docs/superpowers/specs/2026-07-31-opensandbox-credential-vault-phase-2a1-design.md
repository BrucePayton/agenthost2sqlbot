# OpenSandbox Credential Vault Phase 2A.1 Design

## 1. Decision

Phase 2A.1 adds the first real-model execution Gate to the completed OpenSandbox
Docker runtime. The selected design uses OpenSandbox Credential Vault directly:

- the Worker owns the platform model credential;
- the Docker sandbox receives only a recognizable fake API key;
- OpenSandbox's egress sidecar injects the real key into precisely matched HTTPS
  requests;
- any credential-proxy failure stops execution instead of falling back to a
  plaintext environment variable.

The first release uses one platform-owned model account shared by all Workspaces.
Existing Turn records continue to attribute token and cost usage to the Session,
Workspace, and Session creator. A small application-owned credential-provider
interface preserves the future option of per-Workspace or per-user credentials.

The configured model endpoint remains runtime-configurable. The design supports the
current DashScope Anthropic-compatible endpoint and does not hard-code
`api.anthropic.com`.

## 2. Relationship to Phase 2A and Runtime V2

Phase 2A already proves the queue, PostgreSQL execution authority, warm Session
sandbox reuse, durable command identity, cancellation, recovery, idle reaping,
Session and Memory volumes, deny-by-default egress, and a real OpenSandbox Docker
vertical slice with the fake Runner.

Phase 2A.1 closes the deliberate credential gap recorded by that Gate. It does not
replace or weaken later Runtime V2 gates:

- Docker remains a local development and acceptance runtime.
- Production users and production credentials remain disabled until the production
  security Gate.
- Kubernetes scheduling, storage fencing, multi-machine recovery, secure RuntimeClass,
  Secret Manager integration, and production network controls remain later work.

## 3. Goals

Phase 2A.1 must provide these behaviors through the existing Web application:

- a real Turn executes in an OpenSandbox-managed Docker sandbox against the configured
  Anthropic-compatible model endpoint;
- the real model API key is absent from the Runner environment, request document,
  command line, filesystem, mounted volumes, stdout, stderr, and product events;
- a warm sandbox refreshes its Credential Vault before every Turn so a restarted
  Worker with a rotated credential does not need to wait for idle reaping;
- normal chat, same-Session resume, managed Skill invocation, and SDK-native Auto
  Memory work through the remote sandbox path;
- existing input-token, output-token, and cost fields continue to be populated from
  Claude Agent SDK result events;
- a missing, invalid, ambiguous, or unavailable Credential Vault fails closed.

## 4. Non-goals

Phase 2A.1 does not implement:

- a Model Gateway, HTTP forwarding service, token exchange service, billing system,
  quota service, or custom credential vault;
- per-user or per-Workspace credential settings or UI;
- database persistence of model credentials or credential fingerprints;
- external Vault, KMS, Kubernetes Secret, or cloud Secret Manager integration;
- Credential Vault brokering for MCP, Git, package registry, or arbitrary business
  APIs;
- support for model endpoints that require HTTP, IP literals, non-standard ports, URL
  userinfo, query-string credentials, or auth schemes other than the existing
  Anthropic-compatible `x-api-key` behavior;
- a production multi-tenant isolation claim.

## 5. Considered approaches

### 5.1 OpenSandbox Credential Vault

This is the selected approach. The application uses the official OpenSandbox Python
SDK to write credentials and bindings into the per-sandbox egress sidecar. It adds no
new network service and follows the platform boundary already selected in Phase 2A.

### 5.2 Application Model Gateway

A custom Model Gateway would provide centralized auth, rate limiting, accounting, and
tenant routing. It would also require implementing correct streaming, cancellation,
timeouts, request compatibility, retries, credential injection, and availability. That
work duplicates capability not required by the local Gate and is deferred until a
product requirement justifies it.

### 5.3 Credential Vault plus external Secret Manager

Resolving credentials from Vault or a cloud Secret Manager before writing them to
OpenSandbox is the intended production evolution. Adding that dependency to the local
Docker Gate would not improve the Runner boundary being tested, so Phase 2A.1 keeps the
provider interface but supplies it from the Worker process environment.

## 6. Architecture

```text
Browser / API
  |
  | queued Turn, no model credential
  v
PostgreSQL
  |
  v
OpenSandbox Execution Worker
  |-- load Session creator and Workspace
  |-- CredentialProvider.resolve(CredentialScope)
  |-- create or inspect Session sandbox
  |-- OpenSandbox SDK: create/patch Credential Vault
  |-- write secret-free RunnerRequest
  v
OpenSandbox Docker sandbox
  |-- ANTHROPIC_BASE_URL=<configured compatible endpoint>
  |-- ANTHROPIC_API_KEY=opensandbox-vault-placeholder
  |-- Claude Agent SDK / Claude Code
  v
OpenSandbox egress sidecar
  |-- deny-by-default policy
  |-- exact host, method, and path binding
  |-- replace x-api-key with the real platform key
  v
Configured Anthropic-compatible model endpoint
```

The API process never receives an OpenSandbox lifecycle credential. The Runner never
receives a real model credential. The Worker is the only application component that
can hold both the OpenSandbox API key and the platform model key.

## 7. Application-owned contracts

### 7.1 CredentialScope

`CredentialScope` is immutable and contains opaque internal identifiers:

```python
@dataclass(frozen=True)
class CredentialScope:
    workspace_id: str
    user_id: str
    session_id: str
```

It is constructed from the authoritative Session record before the sandbox is prepared.
The credential-resolution object does not cross the Worker boundary. The same opaque
identifiers may continue to appear in the existing trusted Runner request for Session,
Workspace, and Memory isolation; they are not model credentials and this design does
not change that protocol.

### 7.2 ModelCredentialProvider

The Worker depends on an application-owned protocol:

```python
class ModelCredentialProvider(Protocol):
    async def resolve(self, scope: CredentialScope) -> ModelCredential: ...
```

`PlatformModelCredentialProvider` is the only Phase 2A.1 implementation. It returns
the configured `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`, with the key held in a
redacting secret wrapper. It deliberately ignores the scope when selecting the shared
credential, while the interface keeps future credential selection tenant-aware.

Settings are loaded when the Worker starts. Rotating the environment-backed key
therefore requires a Worker restart. On the first subsequent Turn, the Worker refreshes
the Vault even when the Session sandbox is warm.

Credential values, hashes, and fingerprints are not written to PostgreSQL or logs.
The Worker always refreshes the write-only Vault state, so it does not need to compare
secret values.

### 7.3 RunnerModelConfig

The sandbox receives only non-secret model configuration:

- validated `base_url`;
- model name from the immutable Workspace snapshot;
- the fixed placeholder `opensandbox-vault-placeholder`.

The placeholder is intentionally recognizable. If injection is absent or a request
does not match the binding, the upstream receives a useless key and rejects the call.

### 7.4 SandboxPort extension

The application-owned sandbox port gains one operation rather than exposing the
official SDK type to the Worker:

```python
ensure_model_credential(
    handle: SandboxHandle,
    credential: ModelCredential,
) -> None
```

`OpenSandboxAdapter` is responsible for translating this operation into official SDK
Credential Vault create, get, and patch calls. Fake adapters implement the same method
without enabling any real credential path.

## 8. Model endpoint validation and binding derivation

Credential injection is permitted only for a narrow, server-owned destination. In
`opensandbox_docker` mode, the configured Base URL must satisfy all of these rules:

- scheme is exactly `https`;
- hostname is a normalized FQDN, not an IP literal;
- userinfo, query, and fragment are absent;
- port is absent or exactly `443`;
- the hostname appears as an exact entry in `OPENSANDBOX_ALLOWED_HOSTS`;
- the normalized path contains no dot segments or encoded separators.

The Base URL remains visible inside the sandbox because it is routing configuration,
not a credential. The application derives a single credential binding from it:

| Base URL | Binding host | Binding path |
|---|---|---|
| `https://api.anthropic.com` | `api.anthropic.com` | `/v1/*` |
| `https://dashscope.aliyuncs.com/apps/anthropic` | `dashscope.aliyuncs.com` | `/apps/anthropic/v1/*` |
| `https://gateway.example.com/anthropic/v1` | `gateway.example.com` | `/anthropic/v1/*` |

If the normalized Base URL path already ends in `/v1`, the binding appends `/*`;
otherwise it appends `/v1/*`. The binding allows only:

- scheme `https`;
- canonical port `443`;
- exact hostname, never a wildcard;
- methods `GET` and `POST`;
- the derived path prefix;
- auth type `apiKey`, header name `x-api-key`.

The egress network policy remains deny-by-default. Other explicitly approved hosts may
remain available for existing runtime functions, but the model credential binding never
inherits those broader egress entries.

## 9. Turn preparation and ordering

The Worker performs the following steps before crossing the existing execution barrier:

1. Claim an eligible Turn using the existing PostgreSQL authority.
2. Load the Session's `workspace_id`, `created_by`, and Session ID.
3. Resolve the shared platform credential using `CredentialScope`.
4. Reserve the Session sandbox generation.
5. Inspect a warm sandbox or create a new sandbox with Credential Proxy enabled,
   deny-by-default egress, and the safe Runner model environment.
6. Call `ensure_model_credential`.
7. For a new sandbox, record `ready` only after step 6 succeeds.
8. Build and write the secret-free Runner request.
9. Commit the existing execution barrier and start ClaudeRunner.

For a new Vault, the adapter calls the official SDK `create` operation. For an existing
Vault, it reads the sanitized revision and performs a patch that replaces the named
credential and binding. One optimistic-revision conflict may be retried after a fresh
sanitized read. A second conflict is terminal for that preparation attempt.

The stable names are application constants, not user input:

- credential: `workspace-agent-model-api-key`;
- binding: `workspace-agent-model-endpoint`.

The secret-free request is never written until Vault provisioning succeeds.

## 10. Runner behavior

ClaudeRunner continues to reuse `app/runtime/claude.py`. No parallel Claude SDK wrapper
is introduced.

For `local_inline`, the existing Settings may still contain the real development key.
For `opensandbox_docker`, the sandbox process Settings contain the validated Base URL
and the fixed fake key established at sandbox creation. Consequently the existing
`ClaudeAgentRuntime._child_env()` passes only the fake key to Claude Code.

The Runner protocol does not add a credential field. Workspace snapshots, attachment
manifests, Skill snapshots, Memory paths, and MCP configuration keep their existing
contracts. Model credentials are not accepted from browser requests, Workspace YAML,
Session data, Skill packages, Memory, or MCP configuration.

## 11. Usage attribution

Phase 2A.1 reuses the existing usage path:

1. Claude Agent SDK emits `ResultMessage.usage` and `total_cost_usd`.
2. `ClaudeAgentRuntime` converts them to `usage.updated`.
3. ClaudeRunner emits a validated `usage` frame.
4. The Worker writes input tokens, output tokens, and cost into the existing Turn.
5. The existing Session relation supplies Workspace and creator attribution.

No usage table or billing aggregation is added. Provider-reported cost may remain null
or zero for compatible endpoints that do not supply it; token counts remain the primary
acceptance evidence.

## 12. Failure semantics

### 12.1 Validation failure

An unsafe model Base URL or a model host missing from the egress allowlist prevents the
OpenSandbox Worker from starting. This is a deployment error, not a retryable Turn error.

### 12.2 New sandbox Vault failure

If Vault creation fails, the Worker does not record the sandbox as ready and does not
write the Runner request. It attempts to destroy the sandbox and records a bounded,
sanitized preparation failure.

### 12.3 Warm sandbox Vault failure

If Vault refresh fails, the warm sandbox is no longer trusted for execution. The Worker
marks the generation `recovery_required`, attempts to destroy it, and does not start the
Turn. Failure to destroy is left to the existing reconciler; it never permits plaintext
credential fallback or execution in the ambiguous sandbox.

### 12.4 OpenSandbox error mapping

Credential create, get, patch, endpoint, transport, and revision failures are translated
to the application-owned `credential_proxy_unavailable` category. Logs may contain the
operation, sanitized OpenSandbox status, credential/binding names, target hostname, and
sandbox generation. They must not contain the API key, Vault request body, injected
header value, or complete upstream request.

### 12.5 Upstream authentication failure

An upstream 401/403 after a successfully provisioned Vault follows the existing Claude
runtime error path. The application does not retry by injecting the real key into the
Runner, command, file, or environment.

## 13. Deployment configuration

The Phase 2A.1 Gate keeps the compatible pinned stack:

- OpenSandbox Python SDK `0.1.15`;
- OpenSandbox Server `0.2.2`;
- execd `1.0.21`;
- egress `1.1.4`;
- server `[egress].mode = "dns+nft"`;
- sandbox `credentialProxy.enabled = true`;
- network policy `defaultAction = "deny"`.

The local Compose service binds the lifecycle API to loopback and requires the
OpenSandbox server API key. The Credential Vault is an in-memory per-sandbox data-plane
resource; it is reconstructed or refreshed by the Worker and is not treated as product
persistence.

The local Docker Gate trusts the development host, Docker daemon, OpenSandbox control
plane, and egress sidecar. A passing Phase 2A.1 Gate is not evidence of production TLS,
host isolation, Kubernetes isolation, Secret Manager policy, or tenant-safe runtime
hardening.

## 14. Test strategy

### 14.1 Deterministic unit tests

- Base URL validation and binding derivation cover root paths, path prefixes, an existing
  `/v1` suffix, case normalization, and every rejected URL form.
- `PlatformModelCredentialProvider` returns a secret value without exposing it through
  `repr`, model dumps, or exceptions.
- Runner model configuration always uses the fixed fake key.
- Runner request serialization cannot contain the real test canary.
- Runtime logs and mapped exceptions omit the canary.

### 14.2 Adapter contract tests

- a missing Vault is created through official SDK models;
- an existing Vault is patched using its sanitized revision;
- one revision conflict is retried and a second conflict fails;
- no secret appears in returned application models or error messages;
- create/get/patch transport failures map to `credential_proxy_unavailable`.

### 14.3 Worker lifecycle tests

- a new sandbox is not recorded ready before Vault success;
- a warm sandbox refreshes Vault before writing a request;
- Vault failure prevents request writing and command start;
- the failure path destroys or marks the generation for reconciliation;
- the fake runtime path performs no credential-provider or Vault operation;
- restart recovery does not replay a post-barrier Turn.

### 14.4 Static security tests

The Runner image and Docker deployment must not contain:

- the real `ANTHROPIC_API_KEY` value;
- the OpenSandbox API key;
- a PostgreSQL URL;
- Docker socket access;
- a plaintext credential fallback branch.

Tests use high-entropy canary values and scan the request file, command arguments,
container environment, Runner logs, Session volume, Memory volume, and durable Turn
events.

### 14.5 Opt-in real Docker and model Gate

The deployment-owned live Gate proves, in order:

1. a real `qwen3.7-plus` chat Turn completes through Credential Vault;
2. a second Turn in the same Session resumes the Claude SDK Session;
3. an enabled managed Skill is available and invoked;
4. an explicit durable preference is written by Auto Memory;
5. a new Session for the same user and Workspace reads that Memory;
6. Turn token usage is persisted;
7. the real key is absent from the inspected Runner surfaces;
8. removing or invalidating Vault state causes closed failure;
9. a non-allowlisted destination remains blocked.

Live tests are explicit opt-in and are not run by ordinary CI. They consume the
deployment-owned model account and must never print the credential.

## 15. Phase 2A.1 completion Gate

Phase 2A.1 passes only when all of the following are true:

- the existing Phase 2A verification script remains green;
- all new unit, adapter, Worker, PostgreSQL, and security tests pass;
- the real OpenSandbox Docker Credential Vault test passes;
- the opt-in real-model chat, resume, Skill, Auto Memory, and usage scenarios pass;
- secret-canary inspection passes across every defined Runner surface;
- failure injection proves no plaintext fallback and no command start after Vault
  provisioning failure;
- the runtime verification ledger records the exact SDK, server, execd, egress, Runner
  image, model, test time, and result.

Passing this Gate enables the real-model Docker development path. It does not enable
production credentials or claim Phase 2B Kubernetes readiness.

## 16. Future evolution

The next credential step can add an external `ModelCredentialProvider` backed by the
deployment's Secret Manager. Its selection key can be Workspace or user scope without
changing the Runner protocol or OpenSandbox adapter contract.

If later requirements demand centralized quotas, provider failover, request policy, or
auditable tenant billing, a Model Gateway can be introduced behind the same configured
Base URL. Phase 2A.1 deliberately does not pre-build that service.

## 17. References

- [OpenSandbox Credential Vault guide](https://github.com/alibaba/OpenSandbox/blob/main/docs/guides/credential-vault.md)
- [OpenSandbox egress API specification](https://github.com/alibaba/OpenSandbox/blob/main/specs/egress-api.yaml)
- [OpenSandbox lifecycle API specification](https://github.com/alibaba/OpenSandbox/blob/main/specs/sandbox-lifecycle.yml)
- [Phase 2A Docker runtime design](./2026-07-31-opensandbox-docker-runtime-phase-2a-design.md)
- [Runtime V2 design](./2026-07-29-kubernetes-multi-tenant-runtime-v2-design.md)
