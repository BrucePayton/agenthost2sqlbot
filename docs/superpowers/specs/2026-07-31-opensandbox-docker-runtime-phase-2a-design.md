# OpenSandbox Docker Runtime Phase 2A Design

## 1. Decision

Phase 2 is split into two delivery gates:

- **Phase 2A:** run the existing Web chat end to end through OpenSandbox's Docker
  runtime on a development machine.
- **Phase 2B:** switch the same application adapter to OpenSandbox's Kubernetes
  runtime and execute the original CSI, RWOP, VolumeAttachment, hard-fence, and
  multi-machine gates.

Phase 2A replaces custom container lifecycle infrastructure with OpenSandbox. It
does not weaken or replace the production Kubernetes gate.

The application keeps authority for identity, Workspace membership, creator-private
Sessions, Turn idempotency, execution barriers, event history, Skills, Memory, and
Artifacts. OpenSandbox owns sandbox lifecycle, command and file execution, Docker
volumes, network egress enforcement, and credential brokering.

## 2. Goals

Phase 2A must provide these user-visible behaviors through the existing Web page:

- A submitted Turn runs inside an OpenSandbox-managed Docker sandbox.
- Consecutive Turns in one Session reuse a warm sandbox.
- An idle sandbox is deleted after five minutes while its Session volume remains.
- A later Turn recreates the sandbox with the same volume and resumes transcript
  and Auto Memory state.
- Cancel stops the active OpenSandbox command without deleting Session state.
- Existing Skill selection, MCP configuration, attachments, SSE, and Session privacy
  continue to work.
- Automated tests use a fake model; one opt-in live smoke uses the currently
  configured model proxy through Claude Agent SDK.

Phase 2A also creates a stable application-owned interface that can switch from the
Docker backend to the Kubernetes backend without changing Web/API contracts.

## 3. Non-goals

Phase 2A does not claim or implement:

- Kubernetes scheduling, Namespace isolation, CSI, RWOP, VolumeAttachment evidence,
  node fencing, or cross-machine recovery.
- Production multi-tenant isolation or production users and credentials.
- S3-backed Attachment, Artifact, Memory, or transcript durability.
- Team dynamic memory or Session sharing.
- A custom Kubernetes Controller, CRD, Pod/PVC builder, Supervisor container,
  ingress controller, egress sidecar, command daemon, or credential vault.

Phase 2B remains blocked until a real development cluster exposes the Kubernetes,
CSI, storage, and hard-fence facts required by the approved Runtime V2 design.

## 4. Why OpenSandbox

Three approaches were considered:

1. Keep the original custom Controller, Gateway, Supervisor, Pod, and volume stack.
   This preserves maximum control but duplicates a complete sandbox platform.
2. Use OpenSandbox for all infrastructure and retain only an application Worker,
   adapter, and thin Claude Runner. This is the selected approach.
3. Put a custom trusted Supervisor inside each OpenSandbox sandbox. This preserves
   the original process topology but duplicates lifecycle and credential functions
   already supplied by OpenSandbox.

The selected approach minimizes custom infrastructure while preserving the product
semantics that a generic sandbox cannot own.

## 5. Architecture

```text
Browser
  |
  v
Workspace Agent API replicas
  |
  | durable queued Turn
  v
PostgreSQL
  |
  | FOR UPDATE SKIP LOCKED
  v
OpenSandbox Execution Worker
  |
  | application-owned OpenSandboxAdapter
  v
OpenSandbox Python SDK -> OpenSandbox Server -> Docker Runtime
                                             |
                                             +-- Session sandbox
                                             +-- named Session volume
                                             +-- execd command/file APIs
                                             +-- egress policy
                                             +-- Credential Vault
                                                        |
                                                        v
                                                  ClaudeRunner
```

The API process never imports the Docker client, opens the Docker socket, or creates
sandboxes. The Execution Worker is a separate process and is the only application
component allowed to call OpenSandbox lifecycle APIs.

OpenSandbox Server is deployed locally with its Docker backend. The server and its
runtime state are reconstructible infrastructure; PostgreSQL remains authoritative
for product state.

## 6. Components

### 6.1 OpenSandboxAdapter

The adapter is the sole application boundary around the official OpenSandbox Python
SDK. Its interface is intentionally backend-neutral:

```python
create_session_sandbox(session, generation) -> SandboxHandle
get_session_sandbox(sandbox_id) -> SandboxStatus
run_turn(handle, request) -> AsyncIterator[RunnerFrame]
cancel_turn(handle, command_execution_id) -> CancelResult
renew_lease(handle) -> None
destroy_sandbox(handle) -> None
```

The adapter converts official SDK types and errors into application-owned contracts.
No OpenSandbox SDK type crosses into Session, Turn, API, or runtime modules.

### 6.2 OpenSandbox Execution Worker

The Worker:

- scans PostgreSQL for queued Turns with `FOR UPDATE SKIP LOCKED`;
- serializes work per Session;
- reconciles the Session's current sandbox record with OpenSandbox;
- creates or warm-reuses a sandbox;
- commits the execution barrier before starting Claude Agent SDK;
- consumes structured Runner frames and appends durable `turn_events`;
- enforces cancel and finalization ordering;
- reconciles commands after Worker restarts;
- deletes idle sandboxes after five minutes without deleting their volumes.

The Worker does not implement Docker, networking, file transfer, credential storage,
or container process control. Those operations go through OpenSandbox.

### 6.3 ClaudeRunner

ClaudeRunner is a thin command-line entrypoint in the sandbox image. It reuses the
existing application-owned runtime contracts and `app/runtime/claude.py` adapter.
It accepts one immutable request document from a fixed Session path and emits
newline-delimited `RunnerFrame` JSON to stdout.

ClaudeRunner:

- runs exactly one Turn per invocation;
- uses fixed paths below `/session`;
- cannot choose trusted user, Workspace, Session, Turn, attempt, or generation IDs;
- never connects to PostgreSQL, Docker, OpenSandbox Server, or object storage;
- never receives the OpenSandbox API key or a real model/MCP credential;
- exits only after the runtime stream and local transcript writes are complete.

### 6.4 OpenSandbox Server

OpenSandbox provides:

- Docker sandbox creation, inspection, timeout, and deletion;
- command execution and command-session cancellation;
- file transfer and diagnostics;
- named volume attachment;
- egress enforcement;
- Credential Vault for outbound model credentials.

The project pins the OpenSandbox server, SDK, execd, egress, and sandbox image to
exact compatible versions. The verification ledger records those versions and image
digests for every passing gate.

## 7. Ownership and trust boundaries

| Resource | Authority | Visibility |
|---|---|---|
| Identity and Workspace membership | OIDC and Space authority projected into PostgreSQL | Authorized principal |
| Session, Turn, attempt, event | Workspace Agent PostgreSQL | Session creator only |
| Sandbox lifecycle mapping | Workspace Agent PostgreSQL | Internal control plane |
| OpenSandbox runtime record | OpenSandbox Server | Execution Worker only |
| Session workdir/transcript | Per-Session Docker named volume | Current Session sandbox only |
| Personal Auto Memory | Per-user+Workspace Docker named volume | Current user's Sessions in that Workspace |
| Managed Skill snapshot | Session volume, copied from immutable Session snapshot | Current Session sandbox only |
| Real model credential | OpenSandbox Credential Vault | Never readable by ClaudeRunner |
| OpenSandbox API key | Execution Worker secret | Worker only |

Browser clients never receive OpenSandbox credentials or sandbox endpoint access.
Workspace Agent API replicas never receive Docker socket access.

## 8. Persistence model

Phase 2A adds a `session_sandboxes` authority table with:

- Session ID as the unique owner;
- current sandbox ID;
- deterministic Docker volume name;
- monotonically increasing generation;
- lifecycle status;
- OpenSandbox background command execution ID for the active attempt;
- runtime cohort and image digest;
- created, ready, heartbeat, idle, and ended timestamps;
- last reconciliation error and recovery reason.

Turn attempts store the sandbox generation, execution nonce, and background command
execution ID. The compatibility `command_session_id` column mirrors the execution ID
in Phase 2A because the SDK's resumable background command API does not create a shell
session.
Existing `turn_attempts` and `turn_events` remain the only attempt and event records;
OpenSandbox logs are diagnostic evidence, not product history.

The deterministic Session volume name derives from the opaque Session UUID, not the
user or Workspace name. A sandbox may be recreated, but the named volume remains
until Session deletion.

Personal Auto Memory uses a separate deterministic volume derived from an opaque
hash of the internal user ID and Workspace ID. It is mounted only into that user's
Session sandboxes for the same Workspace. Because two Sessions could otherwise write
the same Markdown files concurrently, the Worker obtains a PostgreSQL lease for the
user+Workspace memory scope before crossing the execution barrier. Phase 2A permits
only one memory-writing Turn per scope at a time; other Turns remain queued. The
lease is released only after transcript and Memory writes have completed. Phase 3
replaces this local volume/lease mechanism with the approved versioned Memory bundle
and CAS service.

## 9. Turn lifecycle

1. API validates current membership and creator ownership.
2. API inserts an idempotent queued Turn and returns SSE coordinates.
3. Worker locks one eligible Session/Turn.
4. Worker reconciles or creates the Session sandbox and fixed named volume.
5. Worker materializes the immutable Session request and any pending attachments.
6. Worker commits `assigned -> running`, one Turn attempt, sandbox generation, and a
   unique execution nonce in one PostgreSQL transaction.
7. Only after commit, Worker starts ClaudeRunner through OpenSandbox command API.
8. Worker validates each Runner frame and appends it to durable `turn_events`.
9. Worker observes cancellation and interrupts the background command when requested.
10. Worker waits for runtime completion and local transcript writes, then commits the
    terminal Turn state.
11. Sandbox becomes idle and is eligible for warm reuse for five minutes.
12. The idle reaper deletes the sandbox but retains the Session and Memory volumes.

Different Sessions may execute concurrently up to configured global, per-user, and
per-Workspace limits when they do not share a writable Memory scope. One Session
never has two active commands, and one user+Workspace Memory scope never has two
memory-writing commands.

## 10. Failure semantics

### Before the execution barrier

Sandbox creation, volume mounting, file materialization, egress readiness, or
Credential Vault failure leaves the Turn queued or records `failed_before_execution`.
The Worker may retry with bounded exponential backoff because Claude Agent SDK has
not started.

### After the execution barrier

The whole Turn is never automatically replayed. If the Worker loses its connection,
it reconciles the stored OpenSandbox background execution and diagnostics:

- a still-running command is reattached or monitored;
- a terminal command is finalized from its durable output/status;
- a missing or ambiguous command sets the Session to `recovery_required` and the
  Turn to an explicit unknown/interrupted outcome.

No new generation is admitted while the previous Docker sandbox is Running or its
state is ambiguous. Docker process termination is sufficient only for the local
single-machine Phase 2A gate; it is not evidence for Kubernetes storage fencing.

### Cancellation

Cancellation is first persisted in PostgreSQL. The Worker terminates the active
OpenSandbox background command, waits for its terminal state, and records the Turn as
cancelled/interrupted. The named volume is retained.

### OpenSandbox outage

Queued Turns remain durable. Worker retries read-only reconciliation with bounded
backoff. It does not infer sandbox termination from an unavailable server.

## 11. Secrets and networking

- OpenSandbox Server API authentication is mandatory.
- The OpenSandbox API key exists only in the Execution Worker environment/secret.
- Sandbox creation uses deny-by-default egress.
- The sandbox cannot reach PostgreSQL, the Docker socket, OpenSandbox management API,
  object storage, metadata endpoints, or arbitrary private networks.
- Model and approved remote MCP hosts are explicit allowlist entries.
- Real model credentials use OpenSandbox Credential Vault. Failure to establish the
  vault fails closed; there is no plaintext environment-variable fallback.
- ClaudeRunner receives a syntactically valid fake credential only when the client
  library requires one; the egress proxy injects the real credential on the matching
  HTTPS request.
- Stdio MCP servers must be platform-built, pinned, and read-only for Phase 2A.

Docker Desktop does not prove production isolation. Phase 2A validates behavior and
secret absence, while Phase 2B performs the live Kubernetes/CNI/security matrix.

## 12. Existing Web/API behavior

The Web UI and public API contracts do not change. `POST /turns` still returns an
accepted Turn and SSE URL. Runtime status events add provisioning, sandbox-ready,
warm-reuse, recovering, and idle-reaped phases without exposing sandbox IDs.

The production Phase 1 `execution_disabled` mode remains available until Phase 2A's
gate passes. After the gate, local/test configurations may select
`opensandbox_docker`; production still rejects this mode. `local_inline` remains a
development rollback path.

## 13. Test strategy and Phase 2A gate

### Deterministic automated tests

- Contract tests use a fake OpenSandbox adapter and fake model.
- State-machine/property tests cover duplicate claims, stale generations, cancel,
  idle reaping, and barrier ordering.
- API/browser tests prove the existing Web page receives remote execution events.
- Failure injection kills Worker control flow before and after every barrier.
- Security tests inspect Runner environment, request files, logs, and mounts for
  database, OpenSandbox, Docker, and real model secrets.

### Real Docker vertical slice

The local gate starts PostgreSQL, OpenSandbox Server with Docker runtime, one Worker,
and the existing API/UI. It proves:

- cold sandbox creation and one fake-model Turn;
- same-Session warm reuse;
- cancellation;
- five-minute idle deletion;
- sandbox recreation with the same volume;
- transcript resume plus Auto Memory persistence through the separate scoped volume;
- serialization of concurrent Turns that share one writable Memory scope;
- independent volumes for different Sessions;
- denial of database, Docker API, OpenSandbox management API, and non-allowlisted
  network destinations;
- no real credential in Runner environment, files, argv, or logs.

### Opt-in live smoke

Using the current configured model proxy, the existing Web flow must complete:

1. one chat Turn;
2. one same-Session resume Turn;
3. one managed Skill invocation;
4. one explicit Auto Memory write;
5. one new Session read of the same user+Workspace memory scope.

The gate records exact OpenSandbox component versions, image digests, Docker version,
Claude Agent SDK/CLI/MCP versions, commands, test counts, cold/warm latency, reviewer,
and known exceptions.

## 14. Migration to Phase 2B

Phase 2B keeps `OpenSandboxAdapter`, Execution Worker, ClaudeRunner, PostgreSQL
execution barrier, Web/API contracts, and event model. It changes:

- OpenSandbox Server backend from Docker to Kubernetes;
- named volume configuration to per-Session RWOP PVC;
- local termination evidence to Pod terminal plus CSI detach/VolumeAttachment proof;
- Docker egress evidence to production-class CNI and secure RuntimeClass evidence;
- local capacity limits to cluster quotas and scheduling policies.

The original Kubernetes Phase 2 gate remains mandatory before any production model
credential or real user is enabled.

## 15. Acceptance criteria

Phase 2A passes only when:

1. Existing Web chat executes through OpenSandbox Docker, not `local_inline`.
2. PostgreSQL proves one committed execution attempt per Turn.
3. One Session never has two active sandbox commands.
4. Same-Session warm reuse and five-minute idle deletion work.
5. Recreated sandboxes reuse the original Session volume and resume transcript.
6. New Sessions mount only their creator's user+Workspace Memory volume, and
   concurrent writers to that scope are serialized by a PostgreSQL lease.
7. Cancel is durable and does not delete Session state.
8. Post-barrier ambiguity never triggers automatic whole-Turn replay.
9. ClaudeRunner cannot read control-plane or real model credentials.
10. Fake-model automated tests and the opt-in real-model smoke pass.
11. The verification ledger explicitly states that Kubernetes and production safety
    remain unverified until Phase 2B.
