# Docker Web Deployment Phase 2A.2 Design

## 1. Decision

Phase 2A.2 packages the completed single-machine OpenSandbox runtime as a usable,
one-command Web deployment for development and acceptance testing. The selected
topology uses one immutable application image with separate API and Worker containers,
plus PostgreSQL and OpenSandbox Server under Docker Compose.

The first release deliberately uses one Mock identity and binds all Compose-declared
Web and management ports to `127.0.0.1`. It is suitable only for a trusted developer
workstation or a private single-host test environment reached through an SSH tunnel.
It is not a production multi-tenant or untrusted-LAN claim.

This phase does not replace the Phase 2B Kubernetes design. It packages and exercises
the existing runtime contracts so that the same API/Worker separation can later map to
separate Kubernetes Deployments.

## 2. Goals

Phase 2A.2 must provide:

- one command that builds, configures, starts, waits for, and verifies the complete Web
  stack;
- separate API and Worker processes built from the same immutable application image;
- persistent PostgreSQL, application data, Session workspace, Claude transcript, and
  personal-memory state across normal Compose restarts;
- a deterministic, read-only Workspace-definition source inside the application image
  so a clean deployment always contains the Mock user's `example` Workspace;
- explicit fake and real-Claude modes that both execute through OpenSandbox;
- secret separation between the API, Worker, OpenSandbox Server, and Runner;
- an application-visible Worker heartbeat so the UI does not present an unavailable
  execution backend as healthy;
- deterministic status, logs, restart, stop, and destructive reset operations;
- automated configuration, Compose smoke, recovery, and opt-in real-model Gates.

## 3. Non-goals

This phase does not add:

- OIDC, a Space authority, team identities, or production authentication;
- public network exposure or TLS termination;
- Kubernetes, multi-machine scheduling, automatic scaling, or production failover;
- Redis, a message queue, object storage, or a new monitoring platform;
- a custom sandbox scheduler, secret vault, or model gateway;
- changes to the Session-per-sandbox, durable command, or Credential Vault protocols;
- production-grade tenant isolation.

## 4. Considered approaches

### 4.1 Shared application image, separate API and Worker containers

This is the selected approach. It preserves the existing control-plane/execution-plane
boundary, gives each process an independent lifecycle, and translates directly to the
later Kubernetes architecture. The additional container is operationally cheap and
does not require a new service protocol.

### 4.2 Compose infrastructure with host API and Worker processes

This remains useful for inner-loop debugging and is close to the current manual setup.
It does not provide a portable one-command Web deployment, so it is retained as a
developer workflow rather than the Phase 2A.2 deliverable.

### 4.3 API and Worker in one application container

This reduces the visible container count but couples restart, health, scaling, and
secret boundaries. It also creates migration work for Kubernetes and is rejected.

## 5. Deployment topology

```text
Browser
  | 127.0.0.1:8765
  v
API container
  |-- static Web application
  |-- REST and SSE
  |-- persist Turn and event records
  v
PostgreSQL <---------------- Worker heartbeat
  |
  | queued Turn
  v
Worker container
  |-- claim/reconcile Turn
  |-- manage Credential Vault
  v
OpenSandbox Server
  |
  v
one reusable Runner sandbox per Session
```

The API and Worker use the same application image and different commands. PostgreSQL
is the execution authority and persistent event store. OpenSandbox Server continues to
use the host Docker daemon to manage sandbox containers and their named volumes.

Host publication is restricted to:

- Web API: `127.0.0.1:8765` by default;
- optional diagnostic PostgreSQL/OpenSandbox ports: disabled by default and, when
  explicitly enabled for debugging, still bound to `127.0.0.1`.

No Compose service binds directly to `0.0.0.0`.

### 5.1 Accepted OpenSandbox Docker limitation

OpenSandbox Server `v0.2.2` hard-codes dynamically allocated Runner/egress port
bindings to `0.0.0.0`. Its `[docker].host_ip` option changes advertised endpoint URLs,
not Docker's bind address. Phase 2A.2 does not fork OpenSandbox, install host firewall
rules, or introduce a privileged Docker-in-Docker daemon to hide those dynamic ports.

Therefore the loopback guarantee applies to Compose-declared Web, PostgreSQL, and
OpenSandbox management ports, not to OpenSandbox-created Runner/egress ports in the
configured `40000-60000` range. The launcher and `status` output must display this
limitation. The Gate records the observed bindings, verifies they belong to the exact
Session sandboxes created by the isolated project, and rejects any dynamic port outside
the configured range. This profile must not run on an untrusted or shared LAN host
without an independently managed host firewall.

## 6. Image and command model

### 6.1 Application image

A repository-owned application Dockerfile builds a pinned Python environment from the
lock file and copies the application and static Web assets. It defines a non-root
runtime user, while Compose attaches Docker's built-in init process for correct signal
forwarding. The same digest is used for both:

- API command: run the FastAPI application;
- Worker command: run `app.sandbox.main`.

The image does not bake deployment credentials or a mutable local `.env` file into a
layer.

A minimal Docker-Web-specific `deploy/docker-web/workspaces/` tree is copied into the
image at build time and made read-only, with `WORKSPACES_ROOT` set to that absolute
image path. Its `example` Workspace contains no host-only MCP executable or local Skill
root, so a clean container is valid without mounting the developer's machine. A
Workspace configuration change therefore produces a new application image. Session
creation continues to persist an immutable Workspace snapshot, so an existing Session
does not silently change when a later image is deployed. Arbitrary host Workspace
mounts are not part of the one-command profile; an operator who needs one may use an
explicit development override outside this supported Gate.

### 6.2 Runner image

The existing non-root OpenSandbox Runner image remains separate. The launcher builds
it before Compose startup, resolves its immutable local image ID, and passes that ID to
the Worker. The Worker continues to reject a mutable tag.

### 6.3 Wrapper command

Docker Compose remains the lifecycle engine. A thin repository script supplies the
validation, immutable Runner image resolution, readiness waits, and safe operator
interface that Compose cannot express directly:

```bash
bash scripts/docker-web.sh up --fake
bash scripts/docker-web.sh up --claude
bash scripts/docker-web.sh status
bash scripts/docker-web.sh logs
bash scripts/docker-web.sh restart
bash scripts/docker-web.sh down
bash scripts/docker-web.sh reset
```

The script uses a fixed, repository-specific Compose project name unless an explicit
safe override is supplied. It never enumerates or deletes resources from unrelated
Compose projects.

`down` preserves named volumes. `reset` is the only operation that deletes this
deployment's persistent volumes; it requires an interactive confirmation or an
explicit `--yes` flag for automation. Generated state includes a non-secret
`deployment.json` ownership marker. Destructive reset canonicalizes the configured
runtime directory, refuses unsafe roots such as `/`, the user's home, or the repository
root, and deletes it only when the marker names the exact validated Compose project.

## 7. Startup and readiness

`up` performs these steps:

1. Validate Docker, Docker Compose, the selected mode, required variables, writable
   generated-state directory, and host port availability.
2. Build the application and Runner images.
3. Resolve the immutable Runner image ID and create generated, Git-ignored per-service
   environment files plus the non-secret deployment ownership marker.
4. Start PostgreSQL and OpenSandbox Server.
5. Wait for PostgreSQL readiness and OpenSandbox's unauthenticated `/health` endpoint
   without printing secrets.
6. Run the existing idempotent database initialization/migration path.
7. Start API and Worker containers.
8. Wait for the API health endpoint and a current Worker heartbeat.
9. Print the local URL and a compact service summary.

Container dependencies are:

```text
PostgreSQL healthy ----+--> API ready
                       +--> Worker eligible
OpenSandbox healthy ------> Worker eligible
current Worker heartbeat -> execution ready
```

The API may serve history while execution is degraded. It must never fall back from
`opensandbox_docker` to `local_inline`.

## 8. Configuration and secret boundaries

The repository commits `.env.docker.example`. Operators copy it to
`.env.docker.local`, which is ignored by Git. Generated values live under
`.runtime/docker-web/`, which is also ignored.

The application configuration contract is narrowed so the API and fake Worker do not
need a real model key merely to instantiate `Settings`. A model credential becomes
optional at the shared settings layer and is required at the actual ownership
boundary:

- `local_inline` requires a model credential when constructing its runtime;
- `opensandbox_docker` with a Claude Worker requires a model credential when building
  the credential provider;
- the OpenSandbox API process and fake Worker reject no request merely because a model
  credential is absent.

This is a configuration-ownership correction, not a second credential source. Empty
credentials remain invalid, and `--claude` validates all real-model settings before
startup.

Bootstrap also stops constructing an unused `ClaudeAgentRuntime` in externally
dispatched modes. `local_inline` owns that runtime; `execution_disabled` and
`opensandbox_docker` expose only sanitized runtime metadata in the API process, while
the Worker constructs the real credential provider when its selected Runner runtime
requires one. This makes the declared container secret boundary true in code rather
than relying on an unused API environment variable.

Environment ownership is deny-by-default:

| Component | Receives |
|---|---|
| API | database connection, Mock identity, non-secret application configuration |
| Worker | database connection, OpenSandbox API key, model endpoint and model credential |
| OpenSandbox Server | only its management API key and server configuration |
| Runner | non-secret request, fixed placeholder model key, Session and Memory mounts |
| Browser | public API responses only |

The API container must not contain the OpenSandbox management key or real model key.
The Runner continues to receive real model credentials only through the Phase 2A.1
Credential Vault binding. Logs, health output, generated files, database events, and
Runner request documents must remain secret-free.

For the local acceptance stack, environment-backed Worker secrets are acceptable at
the Docker host boundary. External Secret Manager integration remains a production
Kubernetes concern.

## 9. Fake and real-Claude modes

Both modes execute through the queued Worker and OpenSandbox Runner:

- `--fake` requires no model credential and emits deterministic fake-model output. It
  is the default when no mode is supplied and is used by normal CI smoke tests.
- `--claude` requires an Anthropic-compatible base URL, API key, model, and an exact
  outbound host allowlist. Missing or inconsistent settings fail before containers are
  started.

Changing modes recreates the Worker and affected Runner containers but does not delete
Session or application volumes. The launcher reports the selected mode explicitly so
fake output cannot be mistaken for a real-model result.

## 10. Persistence and recovery

| Data | Storage | Normal `down/up` behavior |
|---|---|---|
| Workspace, Session, Turn, Skill metadata and events | PostgreSQL named volume | retained |
| attachments, personal memory, and application working data | shared app-data named volume | retained |
| Session workspace and Claude transcript/config | per-Session named volumes | retained |
| disposable Runner container state | container writable layer | discarded |

Workspace definitions are versioned in the immutable application image rather than a
mutable volume. Their database snapshot and every Session snapshot retain the
configuration used when the corresponding record was created.

Recovery behavior remains:

- API restart reconnects clients and replays persisted SSE events;
- Worker restart reconciles queued/running Turns through the existing durable command
  and lease rules rather than starting a second execution;
- an idle Runner container is deleted after the configured TTL while its stable Session
  volumes are retained;
- the next Turn recreates the sandbox generation and resumes the saved Claude Session;
- OpenSandbox restart is handled by Worker reconciliation;
- Credential Vault deletion or injection failure fails closed.

The deployment documentation includes named-volume backup and restore commands. Backup
and restore are operator actions, not a new application backup subsystem.

## 11. Worker heartbeat and degraded execution

The Worker upserts a short-lived heartbeat record to PostgreSQL every five seconds. The
record contains only a generated instance ID, runtime cohort, protocol version, Runner
runtime, image digest, last-seen time, and a sanitized status. It contains no network
endpoint or credential. A migration creates this small table; a new Worker instance
uses a new ID, and stale rows are harmless and may be pruned opportunistically. The
database supplies `last_seen` so host clock skew cannot make a stale Worker appear
current.

The API treats at least one heartbeat with matching cohort, protocol version, Runner
runtime, and immutable application image digest seen within fifteen seconds as an
available executor. Thresholds are configuration constants with conservative bounds;
they are not a general scheduler or membership protocol.

Public state distinguishes:

- `ready`: API, database, and a compatible Worker execution path are available;
- `degraded`: history remains available but no current compatible Worker exists;
- `unavailable`: the API or database cannot serve requests.

When execution is degraded, new Turn submission fails with a stable service-unavailable
error before presenting the Turn as running. Existing Session history, Skill metadata,
and Memory remain readable. The Web UI renders a direct execution-service warning and
does not imply that queued work is progressing.

The heartbeat is an admission and operator signal, not a distributed transaction with
Turn submission. If a Worker fails after admission, the already-persisted Turn remains
durable for reconciliation by a restarted compatible Worker; the UI changes to
degraded instead of claiming ongoing execution.

## 12. Operational behavior

- API and Worker have independent restart policies.
- Worker concurrency defaults to one and remains configurable.
- Services log to standard output with existing secret redaction rules.
- `status` shows container health, API health, and sanitized Worker freshness.
- `logs` accepts an optional known service name and cannot expand to arbitrary Docker
  resources.
- graceful shutdown stops new claims and relies on existing durable recovery rules for
  interrupted work.
- startup failure leaves diagnostic containers/logs available; it does not
  automatically run destructive cleanup.

## 13. Verification strategy

### 13.1 Configuration tests

- API and Worker receive only their allowed environment variables.
- missing Worker credentials/configuration fail closed.
- all Compose-declared ports are loopback-only; OpenSandbox-created dynamic ports are
  reported as the accepted `v0.2.2` local-runtime limitation and remain within
  `40000-60000`.
- image configuration contains no deployment secret.
- the resolved Runner reference is immutable.

### 13.2 Compose smoke Gate

From an empty Phase 2A.2 project state:

1. Run `up --fake`.
2. Verify PostgreSQL, OpenSandbox, API, Worker, and Worker heartbeat readiness.
3. Open `http://127.0.0.1:8765` and create a Session.
4. Submit a Turn and observe persisted progress and completion events.
5. Run `down`, then `up --fake`, and verify the Session and history remain.

### 13.3 Recovery Gate

- restart API and verify SSE replay;
- restart Worker during controlled work and verify there is no duplicate execution;
- stop Worker and verify API/UI degraded state and stable submission failure;
- let a Runner expire, then verify same-Session recreation and resume;
- verify ordinary `down` preserves every owned volume;
- verify `reset --yes` requires the exact deployment ownership marker and deletes only
  the fixed Compose project's owned resources.

### 13.4 Opt-in real-model Gate

The existing live Phase 2A.1 assertions are run through the packaged stack only when
explicitly enabled. They cover real conversation, same-Session resume, managed Skill,
cross-Session Auto Memory, usage, secret scans, and Credential Vault fail-closed
behavior.

## 14. Deliverables

- application Dockerfile and ignore rules;
- complete Docker Compose definition;
- `.env.docker.example` and generated-state ignore rules;
- `scripts/docker-web.sh` lifecycle wrapper;
- Worker heartbeat persistence, service logic, API status, and Web degraded-state UI;
- automated configuration, Compose smoke, and recovery tests;
- opt-in packaged real-model Gate;
- README operations, backup/restore, and troubleshooting documentation.

## 15. Exit criteria

Phase 2A.2 is complete when a clean machine with Docker can run one repository command,
open the local Web application, execute an OpenSandbox-backed fake Turn, preserve it
across `down/up`, observe a stopped Worker as degraded, and remove only the deployment's
own state through an explicit reset. The opt-in real-model Gate must retain all Phase
2A.1 credential-isolation guarantees.

## 16. Implemented outcome

Phase 2A.2 was implemented on `codex/docker-web-phase-2a2`; the last implementation
commit before documentation closeout is `c94032a`.

- `scripts/docker-web.sh` provides the single lifecycle entry point for fake and real
  Claude modes, status, logs, restart, ordinary shutdown, and explicit exact-scope reset.
- API and Worker use the same immutable application image. Only the Worker receives
  OpenSandbox and model credentials; fake mode needs no model credential.
- persisted Worker heartbeats distinguish ready, stale, incompatible, and unavailable
  execution capacity. Turn submission fails closed when no compatible Worker is ready;
  there is no local-inline fallback.
- ordinary `down/up` preserves PostgreSQL, application files, Session workspaces, and
  Memory. Reset validates deployment ownership and leaves unrelated Docker resources
  untouched.
- the backup/restore runbook uses the database-owned fixed volumes and the exact
  SHA-1-derived Session/Memory volume names; it never relies on `docker volume prune`.
- manually created or imported Skills intentionally start disabled. The packaged live
  Gate explicitly enables its managed Skill before creating the Session snapshot.
- OpenSandbox Server `0.2.2` still publishes dynamic sandbox ports on
  `0.0.0.0:40000-60000`. Phase 2A.2 accepts this only for a trusted local development
  host and documents the required host-firewall boundary.
- deterministic fake/recovery checks and the opt-in packaged real-Claude Gate passed on
  the final images. Exact commands, versions, image IDs, and assertions are recorded in
  `docs/operations/runtime-v2-verification-ledger.md`.

This outcome is a local Docker engineering deployment, not a production multi-tenant or
Kubernetes certification. Multi-machine scheduling, production secret delivery, network
policy, RuntimeClass hardening, and tenant isolation remain later Runtime V2 gates.
