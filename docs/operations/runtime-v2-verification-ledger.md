# Runtime V2 Verification Ledger

This ledger records reproducible evidence for Runtime V2 gates. Secrets, tokens, and
proxy URLs must never be copied into this file.

## Phase 0 — Local Runtime Boundaries

| Field | Evidence |
|---|---|
| Date | 2026-07-30 (Asia/Shanghai) |
| Verified implementation tip | `3a26226` (`codex/runtime-v2-phase-0`) |
| Claude Agent SDK | `0.2.128` |
| Bundled Claude CLI | reported as `bundled-with-sdk` |
| MCP Python SDK | `1.29.0`; MCP Python SDK v2 support reported `false` |
| Internal Runner protocol | `1` |
| Runtime mode / cohort / image | `local_inline` / `local` / `local` |
| Model used by live smoke | configured `qwen3.7-max` through Claude Agent SDK |
| Reviewer | Codex implementation agent; human review pending |

### Static and automated gates

Commands:

```bash
uv run ruff check app tests
node --test tests/js/test_skill_manager.cjs
uv run pytest -q
```

Exact results:

```text
All checks passed!
33 JavaScript tests passed
382 passed, 3 skipped in 130.52s (0:02:10)
```

The three skipped tests are opt-in live proxy tests. Their critical Runtime behavior
was exercised separately by the live service smoke below.

### Service smoke

The service was started from the candidate worktree on isolated port `18765` with
an isolated `APP_DATA_DIR`. Existing service data and the service on port `8765`
were not modified. MCP URLs and local entrypoints came from the operator environment;
credentials are intentionally omitted.

```bash
APP_PORT=18765 \
APP_DATA_DIR=/tmp/claude-workspace-runtime-v2-smoke \
WORKSPACES_ROOT="$PWD/workspaces" \
APP_RUNTIME_MODE=local_inline \
APP_RUNTIME_COHORT=local \
APP_RUNTIME_IMAGE_DIGEST=local \
APP_RUNTIME_PROTOCOL_VERSION=1 \
uv run uvicorn app.main:app --host 127.0.0.1 --port 18765

curl -fsS http://127.0.0.1:18765/api/health
curl -fsS http://127.0.0.1:18765/api/workspaces
```

Observed health report:

```json
{
  "status": "ok",
  "database": "ok",
  "memory": "ok",
  "workspace_count": 1,
  "valid_workspace_count": 1,
  "runtime": {
    "mode": "local_inline",
    "cohort": "local",
    "image_digest": "local",
    "protocol_version": "1",
    "capabilities": ["resume", "interrupt", "auto_memory", "mcp", "skills"],
    "dependencies": {
      "claude_agent_sdk": "0.2.128",
      "claude_cli": "bundled-with-sdk",
      "mcp_python_sdk": "1.29.0",
      "mcp_python_sdk_v2": false
    }
  }
}
```

Observed product flow:

- Workspace `example` was available with `29` managed Skills and `5` configured MCP
  servers; a new Session exposed all 29 fixed Skill snapshots.
- The first live Turn emitted `connecting_mcp`, `mcp_ready`, streamed assistant
  deltas, and completed with `MEMORY_SAVED`.
- A second Turn in the same Session completed with `RESUME_OK`; the persisted
  transcript contained both assistant messages (`30` events total).
- An explicit memory Turn wrote `PHASE0-AUTOMEMORY-20260730` to the scoped
  `MEMORY.md`; a newly created Session in the same user/Workspace scope read back
  exactly `PHASE0-AUTOMEMORY-20260730`.
- The service stopped cleanly and disposed its local resources.

A negative-control run used an invalid placeholder MCP entrypoint and correctly
failed with `mcp_unavailable` before the real-entrypoint run. This confirms the
`mcp_ready` event above represents successful connection checks rather than merely
counting configured servers.

### Phase 0 gate decision

- [x] The SDK import boundary test passes; only `app/runtime/claude.py` imports
  `claude_agent_sdk`.
- [x] `TurnService` delegates execution location and task ownership to an
  `ExecutionDispatcher`.
- [x] Health exposes a redacted Runtime protocol and capability report.
- [x] Health distinguishes Claude Agent SDK, bundled CLI, MCP Python SDK, internal
  Runner protocol, and verified MCP feature support without claiming MCP v2.
- [x] Creator-private Session, Skills, MCP, resume, attachment, SSE, and Auto Memory
  behavior passed automated or live checks.
- [x] The local browser/API execution behavior is preserved by the complete browser
  and API suite plus live API flow.
- [x] This ledger contains the commands, versions, candidate commit, exact results,
  and reviewer status.

**Decision:** Phase 0 engineering gate passes. Human review remains required before
starting Phase 1 implementation or treating this local mode as production multi-tenancy.

## Phase 1 — PostgreSQL identity and multi-replica control plane

| Field | Evidence |
|---|---|
| Date | 2026-07-30 (Asia/Shanghai) |
| Candidate branch | `codex/runtime-v2-phase-0` |
| Database revision | Alembic `0005` |
| PostgreSQL | `16.14` in the reproducible integration container |
| Verified replica ports | `57818` and `57820` (dynamic on each run) |
| Verified authority / database ports | `57817` / `57816` (dynamic on each run) |
| Runtime mode | `execution_disabled` |
| Identity / membership | RS256 OIDC test issuer plus HTTP Space authority |
| Reviewer | Codex implementation agent; human review pending |

Automated and reproducible checks:

```bash
uv run ruff check app tests scripts/phase1_test_authority.py scripts/verify_phase1_scenario.py
uv lock --check
node --test tests/js/test_skill_manager.cjs
TEST_POSTGRES_URL='postgresql+asyncpg://workspace:workspace@127.0.0.1:55432/workspace_test' uv run pytest -q tests/integration
uv run pytest -q
bash scripts/verify-phase-1.sh
```

Exact results:

```text
All checks passed!
Resolved 61 packages in 1ms
33 JavaScript tests passed
4 PostgreSQL integration tests passed in 1.89s
410 passed, 7 skipped in 110.75s (0:01:50)
Phase 1 environment: postgres=16.14 database_port=57816 authority_port=57817 replica_a_port=57818 replica_b_port=57820
Phase 1 multi-replica verification passed
```

The seven skipped tests in the default suite are opt-in live or PostgreSQL tests; the
PostgreSQL integration directory was executed explicitly against the integration
container as shown above.

The script creates a unique Compose project, fresh PostgreSQL volume, OIDC/Space test
authority, isolated API data directories, and dynamically selected authority/replica/
database ports. It removes only those resources on exit. The scenario proved:

- concurrent replica startup serializes Alembic migrations with a PostgreSQL advisory lock;
- one client request ID submitted through both replicas resolves to one queued Turn;
- competing request IDs for one Session yield one `202` and one `409`;
- different Sessions can queue concurrently while both API replicas create zero execution
  attempts and leave the Turns in `queued`;
- a same-Workspace member who did not create the Session receives `404` for Session,
  messages, Skills, files, attachments, attachment content, Turn, and Turn SSE resources;
- an expired membership projection is refreshed and a removed owner loses access;
- replica A receives a durable event written through the controlled PostgreSQL writer and
  signaled with `LISTEN/NOTIFY`.

Known exceptions: Phase 1 deliberately has no Kubernetes Runner, S3 migration, Redis, or
production credential delivery. `execution_disabled` is therefore a queue-only rehearsal,
not an end-user production execution profile. The test issuer and Space authority are local
test doubles and do not certify a target deployment's OIDC or Space implementation.

## Phase 2A — OpenSandbox Docker execution

| Field | Evidence |
|---|---|
| Date | 2026-07-31 (Asia/Shanghai) |
| Candidate branch | `codex/runtime-v2-phase-0` |
| Database revision | Alembic `0006` |
| Host / Docker | macOS arm64 / Docker Engine 29.6.2 |
| OpenSandbox | SDK `0.1.15`, Server `0.2.2`, execd `1.0.21`, egress `1.1.4` |
| Runner | Python `3.12.10`, numeric UID/GID `10001:10001`, fake Runtime Gate |
| Runner image ID | `sha256:aa65a56b9ef0c6c6cd1dc0078e54cdeb1fe0d379b02dcf47643795f2653a371c` |
| PostgreSQL | `16.14` in an isolated integration Compose project |
| Reviewer | Codex implementation agent; human acceptance pending |

Reproducible Gate:

```bash
bash scripts/verify-phase-2a.sh
```

Final result:

```text
OpenSandbox static security tests: 3 passed
JavaScript tests: 55 passed
Python regression: 457 passed, 10 opt-in tests skipped
PostgreSQL sandbox authority: 2 passed
Real OpenSandbox Docker vertical slice: 1 passed
Phase 2A gate passed
```

The real Docker test proved a non-root Runner can receive the fixed request file,
execute the fake Runtime in a background OpenSandbox command, emit and recover the
validated JSONL terminal stream, and retain Session state across sandbox generations.
It also proved the Runner has no Docker socket, PostgreSQL URL, or OpenSandbox API key,
and that the deny-by-default egress policy blocks a non-allowlisted public destination.
Unit and PostgreSQL tests additionally cover one execution barrier, durable command
identity, cancellation before and after the barrier, restart reattachment without replay,
memory-scope serialization and renewal, warm reuse, idle reap, and CAS generation changes.

One earlier Gate attempt exposed and then fixed a SQLite event-sequence race. A later
attempt was interrupted only by a transient Docker Hub anonymous-token reset; pinning the
base image by digest made the final build reproducible from the local content store.

The live Claude/Credential Vault smoke was not run because no deployment-owned Vault and
short-lived credential issuer were provided. The fake-model Docker Gate is mandatory and
passed; the live smoke remains an explicit opt-in deployment Gate and plaintext model-key
injection is not an accepted substitute.

**Decision:** Phase 2A local Docker engineering Gate passes. It does not certify Kubernetes
scheduling, CSI/RWOP detach fencing, CNI isolation, RuntimeClass hardening, multi-machine
recovery, production credential delivery, or production multi-tenancy; those remain Phase 2B.

## Phase 2A.1 — OpenSandbox Credential Vault real-model execution

| Field | Evidence |
|---|---|
| Verification time | 2026-07-31 05:08 UTC / 13:08 Asia/Shanghai |
| Candidate branch / implementation commit | `codex/phase-2a1-credential-vault` / `431c37c` |
| Database revision | Alembic `0006` |
| Host / Docker | macOS arm64 / Docker Engine 29.6.2 |
| OpenSandbox | SDK `0.1.15`, Server `0.2.2`, execd `1.0.21`, egress `1.1.4` |
| Claude Agent SDK / model | `0.2.128` / `qwen3.7-max` |
| Runner | Python `3.12.10`, numeric UID/GID `10001:10001`, real Claude-compatible Runtime |
| Successful live Runner image ID | `sha256:9cad57ea263486c023bbad61192707cbea91218c86549ffe72df6afb06576f6a` |
| PostgreSQL | `16.14` in an isolated integration Compose project |
| Reviewer | Codex implementation agent; human acceptance pending |

Reproducible deterministic Gate:

```bash
bash scripts/verify-phase-2a.sh
```

Real-model Gate, using deployment-owned environment configuration:

```bash
RUN_LIVE_OPENSANDBOX_CLAUDE=1 bash scripts/verify-phase-2a.sh
```

Final evidence:

```text
Ruff: all checks passed
Focused credential/runtime regression: 57 passed
OpenSandbox static security tests: 3 passed
JavaScript regression: 55 passed
Python regression: 491 passed, 12 explicit opt-in tests skipped
PostgreSQL sandbox authority: 2 passed
Real OpenSandbox Docker Gate: 1 passed, 1 synthetic external-endpoint test skipped
Real OpenSandbox Claude/Vault Gate: 1 passed in 46.64s
```

The live Gate completed three real model Turns. It proved managed Skill discovery from the
Session snapshot, same-Session Claude resume identity, an actual `/memory/MEMORY.md` write,
cross-Session recall through the shared personal-Workspace Memory volume, and persisted
input/output token usage. It also checked that the Runner environment contains only
`opensandbox-vault-placeholder`, and that the real model key is absent from Runner requests,
application data, persisted Turn events, and Runner frames.

The Gate deletes the per-sandbox Credential Vault and replays the fixed Runner request only
as a negative probe. The command then exits non-zero with a failed terminal frame, proving
there is no plaintext credential fallback. The Worker also refreshes the Vault before both
new and warm execution and fails before request writing or the execution barrier when Vault
provisioning is unavailable.

During this Gate, two missing runtime boundaries were found and corrected: materialized
Session workspace files (including managed Skills and attachments) are now copied through
the official OpenSandbox Files API before execution, and `/memory` is passed through the
Claude Agent SDK's native `add_dirs` option. Both paths have focused tests; the former also
has a real Docker nested-Skill-file assertion.

**Decision:** Phase 2A.1 local Docker real-model engineering Gate passes. This does not
enable production mode or certify Kubernetes scheduling, CSI/RWOP detach fencing, CNI or
RuntimeClass hardening, multi-machine recovery, an external Secret Manager, production
credentials, MCP credential brokering, or production multi-tenancy. Those remain later
Runtime V2 gates.

## Phase 2A.2 — Docker Web packaged deployment

| Field | Evidence |
|---|---|
| Verification time | 2026-07-31 17:25 UTC / 2026-08-01 01:25 Asia/Shanghai |
| Candidate branch / implementation commit | `codex/docker-web-phase-2a2` / `c94032a` |
| Database revision | Alembic `0007` |
| Host / Docker | macOS arm64 / Docker Engine `29.6.2` |
| Application dependencies | Claude Agent SDK `0.2.128`, OpenSandbox SDK `0.1.15`, FastAPI `0.139.0`, SQLAlchemy `2.0.51`, asyncpg `0.31.0` |
| OpenSandbox components | Server `0.2.2`, execd `1.0.21`, egress `1.1.4` |
| Final application image | `sha256:ebd95c043159054290421f61e03bd92ac7505ca7b78384a1f8b4be39d9152dbb` |
| Final Runner image | `sha256:5e80b59a0100b7bd7bf36e012dbe9c45d399fc76d3097e0dadc9d9b2551369ac` |
| PostgreSQL | `16.14` |
| Reviewer | Codex implementation agent; human acceptance pending |

Reproducible deterministic Gate:

```bash
bash scripts/verify-phase-2a2.sh
```

Opt-in packaged real-model Gate, using deployment-owned environment configuration:

```bash
RUN_LIVE_DOCKER_WEB_CLAUDE=1 bash scripts/verify-phase-2a2.sh
```

Final evidence:

```text
uv lock --check: passed
Ruff: all checks passed
JavaScript regression: 57 passed
Python regression: 549 passed, 14 explicit opt-in tests skipped
Browser regression: 46 passed
Phase 2A.2 static security/configuration Gate: 41 passed
Docker fake/recovery Gate: 1 passed in 38.93s
Packaged Docker Web Claude/Vault Gate: 1 passed in 46.76s
git diff --check: passed
```

The 14 skipped Python cases are deliberate live/integration opt-ins. The packaged real
Claude/Vault Gate was enabled and run separately on the same final application and Runner
images.

The deterministic Gate proves loopback-only fixed Compose ports, immutable image
resolution, compatible Worker heartbeat readiness, stable degraded behavior for stale or
incompatible Workers, absence of any local-inline execution fallback, persistence across
ordinary `down/up`, and exact deployment-owned reset. The reset test preserves an
unrelated sentinel volume.

The live Gate creates and enables a managed Skill, runs it through the packaged Web stack,
resumes the same Claude Session identity, writes Auto Memory, recalls it from a second
Session, and persists non-zero input/output usage. It verifies that the API environment
does not contain Worker credentials, the Runner receives only the Vault placeholder, and
the real model key is absent from API requests, logs, database state, owned volumes, and
Runner frames. After deleting the sandbox Credential Vault, replay of the fixed request
fails non-zero and reaches a failed terminal state; no plaintext fallback exists.

One live-Gate investigation confirmed an intentional product rule rather than an SDK
usage defect: manually created and imported Skills start disabled. The Gate now enables
the Skill before the Session snapshot and no runtime usage-accounting workaround was
added.

OpenSandbox Server `0.2.2` dynamically publishes sandbox ports on
`0.0.0.0:40000-60000`. Phase 2A.2 accepts this upstream limitation only for trusted local
development and requires the host firewall to block untrusted access to that range.

**Decision:** Phase 2A.2 Docker Web local engineering Gate passes. This does not certify
production multi-tenancy, Kubernetes or multi-machine scheduling, CSI/RWOP detach fencing,
CNI isolation, RuntimeClass hardening, external production secret delivery, or production
MCP credential brokering. Those remain later Runtime V2 gates.
