# Davinci OBID Workspace Session Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在单实例 UAT 上把 Davinci 登录人的 OBID 绑定为 Agent Host 身份，为每个用户幂等创建一个逻辑 Personal Workspace，并让该 Workspace 下的多个 Session 在身份、目录、会话历史和长期记忆边界上正确隔离。

**Architecture:** Davinci 只传当前登录人的 `defaultObId`，Agent Host 将 `(issuer="davinci", subject="obid:" + canonical_obid)` 映射为内部 UUID。Product Workspace 是 PostgreSQL 中的逻辑实例，静态 `WORKSPACES_ROOT` 仅作为模板目录；Session 继续拥有独立物理目录，共享范围仅为 `(internal_user_id, workspace_id)` 的 Auto Memory。本期明确采用可信网络内的裸 OBID Header，限制为单实例 UAT，禁止把它描述为生产强鉴权。

**Tech Stack:** React/TypeScript、FastAPI、Pydantic Settings、SQLAlchemy 2、Alembic、PostgreSQL/asyncpg、Claude Agent SDK、AG-UI、Jest、Node test runner、pytest。

---

## Global Constraints

- Agent Host repository: `/Users/a110356/work/code/claude_workspace_mvp`, branch `codex/identity-workspace-session`. Execute in a clean isolated worktree from this branch because the current checkout contains unrelated SDK upgrade/test changes.
- Davinci repository: `/Users/a110356/work/code/watcher_agent/davinci`; implementation must use an isolated worktree because the current checkout contains unrelated generated artifacts. At plan time `origin/main` does not yet contain Agent MVP, so branch from `origin/codex/davinci-agent-mvp-embed`; if that feature is merged first, prove the updated `origin/main` contains `webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.ts` and branch from that exact merge commit instead.
- Preserve unrelated dirty files in Agent Host (`pyproject.toml`, `uv.lock`, `tests/test_api.py`, local data and squad metadata) by leaving the current checkout untouched; stage only task files inside the clean worktree.
- Preserve Davinci's existing tracked/untracked `webapp/build` and local diagnostic artifacts. Never implement this plan in the dirty checkout.
- Read the applicable `AGENTS.md` before editing each repository or nested module. In Davinci, also read `webapp/share/containers/WorkBenchNew/DashboardV2/AGENTS.md` before Task 5.
- Phase 1 UAT must be exactly one Agent Host process/host and external PostgreSQL. Existing `service_instance_lock(APP_DATA_DIR)` prevents duplicate use of one local data directory only; it is not a cluster-wide singleton.
- `obId` is opaque. Normalize by trimming surrounding whitespace, validate `[A-Za-z0-9._-]{1,64}`, preserve case and leading zeroes, and never use it as a database primary key, directory name, model prompt field, AG-UI forwarded prop, URL parameter, or artifact payload.
- `defaultObId` is the authenticated actor. `defaultUser` is a potentially switched "view-as" business subject and must never become Agent identity.
- Do not add Redis, object storage, Kubernetes, a container per Workspace, a new memory engine, or dynamic MCP user tokens in Phase 1.
- Follow TDD for every behavioral change: run the stated RED test, confirm the expected failure, implement the minimum behavior, rerun GREEN, then run the listed regression set.
- Each task gets its own commit. Never use `git add .`.

## Target File Map

### Agent Host

- Modify: `app/config.py`, `.env.example`
- Modify: `app/auth/identity_repository.py`, `app/auth/oidc.py`, `app/auth/provider.py`
- Create: `app/auth/obid.py`
- Modify: `app/api/dependencies.py`, `app/api/routes.py`, `app/bootstrap.py`
- Modify: `app/embed/local.py`
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0008_personal_workspace_identity.py`
- Create: `app/workspaces/repository.py`, `app/workspaces/provisioner.py`, `app/workspaces/resolver.py`
- Modify: `app/sessions/service.py`, `app/skills/service.py`
- Create: `web/embed/identity.js`
- Modify: `web/embed/main.js`, generated `app/web/static/embed.js`
- Create/modify tests named in Tasks 1-4 and 7-9
- Create: `scripts/verify-davinci-obid-phase-1.sh`
- Create: `docs/operations/davinci-obid-uat.md`

### Davinci

- Modify: `webapp/share/containers/WorkBenchNew/index.tsx`
- Modify: `webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.tsx`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.lifecycle.test.tsx`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.ts`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.test.ts`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/index.tsx`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.ts`
- Modify: launcher tests
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/index.tsx`
- Create: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.ts`
- Create: corresponding Jest test
- Regenerate only the tracked `webapp/build` output produced by `npm run build`

## Task 1: Gate the UAT OBID Profile on PostgreSQL and One Instance

**Files:**
- Modify: `app/config.py`
- Modify: `app/api/routes.py`, `app/embed/local.py`, `app/bootstrap.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py`, `tests/test_api.py`, `tests/test_web_page.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write the failing settings tests**

Add cases proving:

```python
def test_uat_obid_requires_postgresql(settings_factory):
    with pytest.raises(ValueError, match="UAT OBID requires PostgreSQL"):
        settings_factory(
            APP_ENV="uat",
            APP_IDENTITY_MODE="obid",
            APP_RUNTIME_MODE="local_inline",
            DATABASE_URL="sqlite+aiosqlite:///tmp/uat.db",
        )


def test_uat_obid_accepts_single_instance_postgresql(settings_factory):
    settings = settings_factory(
        APP_ENV="uat",
        APP_IDENTITY_MODE="obid",
        APP_RUNTIME_MODE="local_inline",
        DATABASE_URL="postgresql+asyncpg://agent:test@localhost/agent",
    )
    assert settings.deployment_constraint == "single_instance"


def test_production_rejects_obid_identity(settings_factory):
    with pytest.raises(ValueError, match="production requires oidc"):
        settings_factory(APP_ENV="production", APP_IDENTITY_MODE="obid")
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_config.py tests/test_database.py -q`

Expected: tests fail because `uat`, `obid`, and `deployment_constraint` are not accepted yet.

- [ ] **Step 3: Implement the minimal configuration matrix**

Extend the literals and validation without creating a separate settings class:

```python
app_env: Literal["development", "test", "uat", "production"] = "development"
identity_mode: Literal["mock", "obid", "oidc"] = "mock"

@property
def deployment_constraint(self) -> str:
    return "single_instance" if self.app_env == "uat" else "configured"
```

In the model validator enforce:

```python
if self.app_env == "uat" and self.identity_mode == "obid":
    require(self.app_runtime_mode == "local_inline", "UAT OBID requires local_inline")
    require(self.database_url.startswith("postgresql+asyncpg://"),
            "UAT OBID requires PostgreSQL via asyncpg")
if self.app_env == "production":
    require(self.identity_mode == "oidc", "production requires oidc")
```

Add only non-secret flags to `redacted_summary()`: `app_env`, `identity_mode`, `runtime_mode`, and `deployment_constraint`.

In this mode emit a startup warning with marker `UAT_OBID_UNVERIFIED`, include
the same marker in the embed page configuration/banner, and extend `/api/health`
with exact fields `identity_mode="obid"`, `deployment_constraint="single_instance"`,
and `security_marker="UAT_OBID_UNVERIFIED"`. Add API/page assertions; these are
operational truth, not only logs.

- [ ] **Step 4: Document the exact UAT environment**

Add an `.env.example` block with safe example values:

```dotenv
APP_ENV=uat
APP_IDENTITY_MODE=obid
APP_RUNTIME_MODE=local_inline
DATABASE_URL=postgresql+asyncpg://agent_user:change-me@postgres.internal:5432/agent_host
DAVINCI_LOCAL_INTEGRATION=1
DAVINCI_LOCAL_PUBLIC_ORIGIN=https://uat-agent.aihuishou.com
DAVINCI_LOCAL_PARENT_ORIGINS=["https://abdavinci-uat-up.aihuishou.com"]
```

State in the comment that only one replica is supported and `X-Davinci-ObId` is trusted only behind the UAT network boundary.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_config.py tests/test_database.py tests/test_api.py tests/test_web_page.py -q`

Commit:

```bash
git add app/config.py app/api/routes.py app/embed/local.py app/bootstrap.py \
  .env.example tests/test_config.py tests/test_database.py tests/test_api.py \
  tests/test_web_page.py
git commit -m "feat: gate UAT OBID profile on PostgreSQL"
```

## Task 2: Resolve Stable Internal Users from OBID

**Files:**
- Create: `app/auth/obid.py`
- Modify: `app/auth/identity_repository.py`
- Modify: `app/auth/oidc.py`
- Modify: `app/auth/provider.py`
- Modify: `app/api/dependencies.py`
- Modify: `app/bootstrap.py`
- Create: `tests/test_obid.py`
- Modify: `tests/test_oidc.py`, `tests/test_auth.py`, `tests/test_bootstrap.py`

- [ ] **Step 1: Write RED tests for normalization, rejection, and stable mapping**

Cover:

Assert `normalize_ob_id(" 00123 ") == "00123"` and
`normalize_ob_id("Actor.A-7_b") == "Actor.A-7_b"`. Parameterize missing,
blank, slash-containing, and 65-character headers and assert a stable HTTP 401.
Create two repository instances over the same database, resolve issuer
`davinci` and subject `obid:00123` through each with `provider="obid"`, and
assert the internal `user_id` is identical.

Also assert the OIDC provider still stores `provider="oidc"` and no OBID failure falls back to the mock user.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_obid.py tests/test_oidc.py tests/test_auth.py tests/test_bootstrap.py -q`

Expected: import/signature/configuration failures for the absent provider-neutral implementation.

- [ ] **Step 3: Generalize the identity repository**

Change the repository contract to:

```python
async def resolve_or_create(
    self,
    issuer: str,
    subject: str,
    profile: Mapping[str, str | None],
    *,
    provider: str,
) -> ResolvedIdentity:
    raise NotImplementedError
```

Keep `IdentityMappingRecord` provider-neutral and uniquely keyed only by
`(issuer, subject)`; do not add a redundant provider column. Use the supplied
provider only for `UserRecord.provider` and for a collision-safe opaque
`UserRecord.external_subject` such as
`provider + ":" + sha256(issuer + "\\0" + subject)`. Update OIDC's single call
site to pass `provider="oidc"` and test both opaque prefixes.

- [ ] **Step 4: Add `ObIdIdentityProvider` and dependency wiring**

Implement:

```python
OBID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

def normalize_ob_id(value: str | None) -> str:
    canonical = (value or "").strip()
    if not OBID_PATTERN.fullmatch(canonical):
        raise ObIdIdentityError("missing or invalid Davinci OBID")
    return canonical

class ObIdIdentityError(LookupError):
    pass

class ObIdIdentityProvider(IdentityProvider):
    async def resolve(self, request: Request) -> IdentityContext:
        ob_id = normalize_ob_id(request.headers.get("X-Davinci-ObId"))
        resolved = await self._repository.resolve_or_create(
            "davinci",
            f"obid:{ob_id}",
            {"name": f"Davinci {ob_id}"},
            provider="obid",
        )
        return IdentityContext(
            user_id=resolved.user_id,
            external_subject=ob_id,
            display_name=resolved.display_name,
            issuer="davinci",
            email=None,
        )

    async def resolve_bootstrap_identity(self) -> IdentityContext | None:
        return None
```

Map `ObIdIdentityError` to the existing stable unauthorized response in the
identity dependency. Select this provider only when
`APP_IDENTITY_MODE=obid`. Public health/static/bootstrap endpoints remain
unauthenticated; existing identity-dependent REST, AG-UI, Skill, Attachment,
and artifact routes use the provider automatically.

- [ ] **Step 5: Run GREEN, targeted regression, and commit**

Run:

```bash
uv run pytest tests/test_obid.py tests/test_oidc.py tests/test_auth.py tests/test_bootstrap.py -q
uv run pytest tests/test_api.py tests/test_agui_api.py -q
```

Commit:

```bash
git add app/auth/obid.py app/auth/identity_repository.py app/auth/oidc.py \
  app/auth/provider.py app/api/dependencies.py app/bootstrap.py \
  tests/test_obid.py tests/test_oidc.py tests/test_auth.py tests/test_bootstrap.py
git commit -m "feat: resolve stable Davinci OBID identities"
```

## Task 3: Bind OBID into the One-Time iframe Bootstrap

**Files:**
- Modify: `app/embed/local.py`
- Modify: `tests/test_local_davinci_bootstrap.py`

- [ ] **Step 1: Write RED tests for the bootstrap binding**

Test these exact behaviors: `test_bootstrap_requires_obid` (422),
`test_bootstrap_rejects_blank_obid` (422), `test_bootstrap_code_is_single_use`
(second consume 409), `test_bootstrap_code_binds_origin_protocol_and_contract`,
`test_embed_config_uses_obid_from_consumed_record`, and
`test_embed_form_has_no_obid_identity_source`.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_local_davinci_bootstrap.py -q`

Expected: request/model/store assertions fail because bootstrap stores only origin and protocol.

- [ ] **Step 3: Make the store record explicit and single-source**

Use these contracts:

```python
@dataclass(frozen=True)
class LocalBootstrapRecord:
    ob_id: str
    parent_origin: str
    protocol_version: str
    contract_version: str
    contract_digest: str
    expires_at: datetime

def issue(
    self, parent_origin: str, protocol_version: str,
    contract_version: str, contract_digest: str, ob_id: str,
) -> tuple[str, datetime]:
    raise NotImplementedError

def consume(
    self, code: str, parent_origin: str, protocol_version: str,
    contract_version: str, contract_digest: str,
) -> LocalBootstrapRecord:
    raise NotImplementedError
```

Normalize `obId` once at issue time. Store it with the code. The `/embed/local` form must contain only the code and already-bound protocol/origin/contract fields; never accept an `obId` form field as authoritative.

- [ ] **Step 4: Inject the consumed identity into `embed_config`**

After atomic consume, render only the canonical record value:

```python
embed_config["obId"] = record.ob_id
```

Do not log the code or OBID. Preserve current TTL, origin checks, CSP, and V2 contract digest validation.

- [ ] **Step 5: Run GREEN and commit**

Run: `uv run pytest tests/test_local_davinci_bootstrap.py tests/test_web_page.py -q`

Commit:

```bash
git add app/embed/local.py tests/test_local_davinci_bootstrap.py
git commit -m "feat: bind Davinci OBID to iframe bootstrap"
```

## Task 4: Add Personal Workspace Ownership and Template Identity

**Files:**
- Modify: `app/db/models.py`
- Create: `app/db/alembic/versions/rev_0008_personal_workspace_identity.py`
- Modify: `tests/test_migrations.py`
- Modify: `tests/test_database.py`
- Modify: `tests/integration/test_postgres_authority.py`

- [ ] **Step 1: Write RED migration/model tests**

Assert:

```python
assert migration_head == "0008"
assert personal_workspace.owner_user_id == owner.user_id
assert personal_workspace.template_id == personal_workspace.id  # legacy coupling backfill
```

Cover concurrent duplicate personal owners, a successful upgrade from the current `example` personal workspace, invalid legacy data with zero/two owner memberships failing the migration, and the pre-existing SQLite constraints that personal ownership/membership cannot be mutated illegally.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_migrations.py tests/test_database.py -q`

Expected: missing columns/migration head failures.

- [ ] **Step 3: Extend the model with nullable columns and runtime invariants**

Add:

```python
owner_user_id: Mapped[str | None] = mapped_column(
    ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
)
template_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

For `kind='personal'`, both fields must be non-null. Define one
`uq_workspaces_personal_owner` index with both `postgresql_where` and
`sqlite_where` predicates `kind = 'personal'`, so the one-personal-Workspace
invariant holds in every supported store.

- [ ] **Step 4: Implement rev_0008 with deterministic fail-closed backfill**

Migration sequence:

1. Add nullable columns.
2. For each existing personal workspace, query owner memberships and abort with the workspace ID unless exactly one `role='owner'` exists.
3. Set `owner_user_id` to that member and `template_id` to the legacy workspace ID. This preserves the old invariant that a product Workspace ID selected the same static template; service startup/resolver later marks a missing template unavailable instead of inventing one.
4. Add personal-field CHECK and dialect-appropriate partial unique index with
   the same name/predicate in PostgreSQL and SQLite.
5. On SQLite, explicitly drop and recreate all rev_0002 personal guard triggers after any batch table rebuild: `trg_workspaces_personal_*` and `trg_workspace_members_personal_*`.

Never read the filesystem from Alembic.

- [ ] **Step 5: Run SQLite and PostgreSQL GREEN, then commit**

Run:

```bash
uv run pytest tests/test_migrations.py tests/test_database.py -q
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_postgres_authority.py -q
```

Commit:

```bash
git add app/db/models.py \
  app/db/alembic/versions/rev_0008_personal_workspace_identity.py \
  tests/test_migrations.py tests/test_database.py \
  tests/integration/test_postgres_authority.py
git commit -m "feat: persist personal workspace ownership and template"
```

## Task 5: Pass the Authenticated Actor through Davinci

**Working directory:** isolated Davinci worktree from the Agent MVP feature baseline identified in Global Constraints.

**Files:**
- Modify: `webapp/share/containers/WorkBenchNew/index.tsx`
- Modify: `webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.tsx`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.lifecycle.test.tsx`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.ts`
- Create: `webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.test.ts`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/index.tsx`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.ts`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.test.ts`
- Modify: `webapp/share/containers/WorkBenchNew/agent/launcher/index.test.tsx`
- Modify: `webapp/share/containers/WorkBenchNew/DashboardV2/index.tsx`
- Create: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.ts`
- Create: `webapp/share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.test.ts`

- [ ] **Step 1: Create the clean worktree and prove baseline tests**

Run from the Davinci checkout:

```bash
git fetch origin codex/davinci-agent-mvp-embed
git worktree add ../davinci-obid-phase1 -b codex/davinci-obid-workspace-session \
  origin/codex/davinci-agent-mvp-embed
cd ../davinci-obid-phase1/webapp
npx cross-env NODE_ENV=test jest \
  share/containers/WorkBenchNew/agent/launcher/bootstrap.test.ts \
  share/containers/WorkBenchNew/agent/launcher/index.test.tsx \
  share/containers/WorkBenchNew/DashboardV2/agent/DashboardPageDataProvider.test.ts \
  --runInBand --no-coverage
```

Expected baseline: all existing targeted tests pass. If not, stop and record the baseline failure; do not patch around it.

- [ ] **Step 2: Write RED actor lifecycle/bootstrap tests**

Test the public contracts:

```typescript
export interface AgentRuntimeProps {
  actorObId?: string
  // existing props
}

export interface AgentLauncherProps {
  actorObId: string
  // existing props
}
```

The lifecycle test must mount actor A, rerender actor B, then undefined. Mock `AgentLauncher` so it calls the supplied `onIframeElement` with a fake iframe/contentWindow and allowed origin, then calls it with `null` on unmount. Assert the old launcher/iframe unmount destroys its Bridge and the new Bridge is created only after the new iframe callback. Assert bootstrap JSON is exactly `{ parentOrigin, protocolVersion, obId }`; `contractVersion` and `contractDigest` remain response/form fields and the follow-up form never contains `obId`.

- [ ] **Step 3: Wire only `defaultObId` into AgentRuntime**

In `WorkBenchNew/index.tsx`:

Add `actorObId={defaultObId == null ? undefined : String(defaultObId)}` to the
existing `<AgentRuntime>` invocation without changing its other props.

In `AgentRuntimeContext.tsx`, return `null` until the actor exists and key the launcher:

```tsx
if (!actorObId) return null
return <AgentLauncher key={actorObId} actorObId={actorObId} {...props} />
```

`AgentLauncher` passes `obId: actorObId` to `bootstrapAgent`. Do not add `defaultUser` to any Agent interface or dependency list.

- [ ] **Step 4: Write RED snapshot tests and add an actor-specific uploader**

Implement the helper contract only after RED:

```typescript
export async function uploadDashboardSnapshot({
  actorObId,
  pageInstanceId,
  artifact,
  fetcher = fetch
}: UploadDashboardSnapshotArgs): Promise<SnapshotUploadResult>
```

It must fail before fetch when actor is missing/blank and send:

```typescript
await fetcher('/agent-api/artifacts/snapshots', {
  method: 'POST',
  credentials: 'same-origin',
  headers: {
    'Content-Type': 'application/json',
    'X-Davinci-ObId': String(actorObId)
  },
  body: JSON.stringify({ pageInstanceId, artifact })
})
```

Add a pure `actorIdentityProps(defaultObId, defaultUser)` helper returning `{ actorObId, obId }`, cover actor A + viewed user B in `actorIdentityProps.test.ts`, and spread it into `DashboardV2`. This proves `actorObId` comes from `defaultObId` while business `obId` remains `defaultUser`. Store the latest actor in a ref used by the existing provider callback. In the uploader test assert A is the header and identity is absent from the artifact body.

- [ ] **Step 5: Run GREEN and commit only logic/tests**

Run:

```bash
npx cross-env NODE_ENV=test jest \
  share/containers/WorkBenchNew/agent/launcher/bootstrap.test.ts \
  share/containers/WorkBenchNew/agent/launcher/index.test.tsx \
  share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.lifecycle.test.tsx \
  share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.test.ts \
  share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.test.ts \
  share/containers/WorkBenchNew/DashboardV2/agent/DashboardPageDataProvider.test.ts \
  --runInBand --no-coverage
```

Commit:

```bash
git add webapp/share/containers/WorkBenchNew/index.tsx \
  webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.tsx \
  webapp/share/containers/WorkBenchNew/agent/runtime/AgentRuntimeContext.lifecycle.test.tsx \
  webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.ts \
  webapp/share/containers/WorkBenchNew/agent/runtime/actorIdentityProps.test.ts \
  webapp/share/containers/WorkBenchNew/agent/launcher/index.tsx \
  webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.ts \
  webapp/share/containers/WorkBenchNew/agent/launcher/bootstrap.test.ts \
  webapp/share/containers/WorkBenchNew/agent/launcher/index.test.tsx \
  webapp/share/containers/WorkBenchNew/DashboardV2/index.tsx \
  webapp/share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.ts \
  webapp/share/containers/WorkBenchNew/DashboardV2/agent/capture/uploadDashboardSnapshot.test.ts
git commit -m "feat: bind Agent bootstrap and snapshots to actor OBID"
```

## Task 6: Provision One Logical Personal Workspace per Internal User

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Create: `app/workspaces/repository.py`
- Create: `app/workspaces/provisioner.py`
- Create: `app/workspaces/resolver.py`
- Modify: `app/workspaces/sync.py`
- Modify: `app/api/dependencies.py`, `app/api/routes.py`, `app/bootstrap.py`
- Modify: `app/sessions/service.py`, `app/skills/service.py`
- Create: `tests/test_workspace_provisioner.py`
- Create: `tests/test_workspace_template_resolver.py`
- Create: `tests/integration/test_personal_workspace_race.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_workspaces.py`, `tests/test_sessions.py`, `tests/test_api.py`, `tests/test_auth.py`, `tests/test_skill_bootstrap.py`

- [ ] **Step 1: Write RED tests for provisioning and template resolution**

Cover tests named `test_ensure_returns_one_personal_workspace_under_concurrency`,
`test_two_users_receive_distinct_workspace_ids`,
`test_dynamic_workspace_resolves_shared_example_template`,
`test_template_skills_are_baseline_and_personal_skill_overrides_by_name`,
`test_missing_template_is_listed_unavailable_and_session_create_fails`, and
`test_old_session_snapshot_does_not_change_after_template_update`.

Add a PostgreSQL integration case using two independent transactions/apps that
call `ensure()` concurrently for the same OBID and assert exactly one
Workspace UUID and one owner membership survive.

Also assert `/api/workspaces` reads DB memberships/records, reports `WorkspaceRecord.kind`, and does not intersect with product IDs in `WorkspaceRegistry`.

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/test_workspace_provisioner.py \
  tests/test_workspace_template_resolver.py tests/test_workspaces.py \
  tests/test_sessions.py tests/test_api.py tests/test_auth.py \
  tests/test_skill_bootstrap.py -q
```

Expected: missing repository/provisioner/resolver and fixed `registry.get(workspace_id)` failures.

- [ ] **Step 3: Implement DB Workspace repository and idempotent provisioner**

Use these public contracts:

```python
class WorkspaceRepository:
    async def get(self, workspace_id: str) -> WorkspaceRecord | None:
        raise NotImplementedError
    async def list_for_user(self, user_id: str) -> tuple[WorkspaceRecord, ...]:
        raise NotImplementedError
    async def get_personal_for_owner(self, user_id: str) -> WorkspaceRecord | None:
        raise NotImplementedError

class PersonalWorkspaceProvisioner:
    async def ensure(self, identity: IdentityContext) -> WorkspaceRecord:
        raise NotImplementedError
```

The provisioner uses an opaque UUID and a new validated setting
`APP_PERSONAL_WORKSPACE_TEMPLATE_ID` / `personal_workspace_template_id`
(default `example`). Add that setting to
`app/config.py`, `.env.example`, and `tests/test_config.py` in this task. To
preserve existing SQLite personal triggers, insert a temporary `team` Workspace
with `owner_user_id` and `template_id`, insert the owner membership, then
promote it to `personal` in the same transaction. On the partial-unique race,
roll back and query by owner. Never call `WorkspaceSyncService` for OBID
requests.

Update legacy `WorkspaceSyncService` so an existing/static personal Workspace
also fills `owner_user_id` and `template_id=entry.id` before promotion, while
retaining its mock/team-only startup role. This keeps mock startup valid under
the new CHECK; cover it in auth and Skill bootstrap tests.

- [ ] **Step 4: Decouple Product Workspace from static template and merge Skills**

Resolver contract:

```python
class WorkspaceTemplateResolver:
    def resolve(self, workspace: WorkspaceRecord) -> WorkspaceEntry:
        raise NotImplementedError
    async def resolve_session_bundles(
        self,
        workspace: WorkspaceRecord,
        personal_bundles: tuple[tuple[str, SkillBundle], ...],
    ) -> tuple[tuple[str, SkillBundle], ...]:
        raise NotImplementedError
```

Resolve through the startup `WorkspaceRegistry` cache; do not rescan or copy template directories. Load baseline template Skills using existing `load_bundle_from_directory`. Build a case-folded map with deterministic IDs `template:{template_id}:{skill_name.casefold()}`, then replace entries with enabled product-Workspace DB bundles of the same case-folded name. Sort by case-folded name before snapshotting.

Change `SessionService.create()` to:

1. authorize/read `WorkspaceRecord` from DB,
2. resolve its template entry,
3. fetch enabled personal bundles by product `workspace_id`,
4. merge template/personal bundles,
5. call existing `build_session_snapshot(entry, merged_bundles)`,
6. pass that exact merged bundle tuple to the existing
   `materialize_session_workspace` call so
   template baseline files and personal overrides are both physically present,
7. persist the immutable Session row/directory.

`/api/workspaces` calls `ensure(identity)` for OBID users and lists authorized DB Workspace records, enriching availability through the resolver. Existing mock/team startup sync remains untouched.

- [ ] **Step 5: Run GREEN, PostgreSQL concurrency, and commit**

Run:

```bash
uv run pytest tests/test_workspace_provisioner.py \
  tests/test_workspace_template_resolver.py tests/test_workspaces.py \
  tests/test_sessions.py tests/test_api.py tests/test_auth.py \
  tests/test_skill_bootstrap.py -q
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_postgres_authority.py \
  tests/integration/test_personal_workspace_race.py -q
```

Commit:

```bash
git add app/config.py .env.example tests/test_config.py \
  app/workspaces/repository.py app/workspaces/provisioner.py \
  app/workspaces/resolver.py app/workspaces/sync.py app/api/dependencies.py app/api/routes.py \
  app/bootstrap.py app/sessions/service.py app/skills/service.py \
  tests/test_workspace_provisioner.py tests/test_workspace_template_resolver.py \
  tests/integration/test_personal_workspace_race.py \
  tests/test_workspaces.py tests/test_sessions.py tests/test_api.py \
  tests/test_auth.py tests/test_skill_bootstrap.py
git commit -m "feat: provision personal workspaces from templates"
```

## Task 7: Scope iframe API Calls and Session Restoration by Identity

**Files:**
- Create: `web/embed/identity.js`
- Modify: `web/embed/main.js`
- Create: `tests/js/test_embed_identity.cjs`
- Modify: `tests/browser/test_davinci_agui_mvp.py`, `tests/live/test_davinci_agui_qwen.py`
- Regenerate: `app/web/static/embed.js`

- [ ] **Step 1: Write RED JavaScript tests**

Define the expected API:

```javascript
const headers = createIdentityHeaders(' 00123 ')
assert.deepEqual(headers, { 'X-Davinci-ObId': '00123' })

assert.equal(
  sessionStorageKey('Actor/A', 'workspace 1'),
  'davinci-agent:session:v1:Actor%2FA:workspace%201'
)
```

Test that blank/invalid OBID throws, REST calls and every `HttpAgent.runAgent()` include the same identity header, session restore reads only the current `(obId, workspaceId)` key, and the legacy global `davinci-mvp-session` key is never read or cleared.

- [ ] **Step 2: Run RED**

Run: `node --test tests/js/test_embed_identity.cjs tests/js/test_frontend_tool_runner.cjs`

Expected: missing module and header/storage assertions fail.

- [ ] **Step 3: Implement identity helpers and wire both network paths**

Implement:

```javascript
export function createIdentityHeaders(obId) {
  const value = String(obId || '').trim()
  if (!/^[A-Za-z0-9._-]{1,64}$/.test(value)) throw new Error('invalid OBID')
  return { 'X-Davinci-ObId': value }
}

export function sessionStorageKey(obId, workspaceId) {
  return `davinci-agent:session:v1:${encodeURIComponent(obId)}:${encodeURIComponent(workspaceId)}`
}
```

Read `obId` only from bootstrap `embed_config`. Merge its header into the existing REST helper and `HttpAgent` constructor `headers`. Use the namespaced key consistently for create, restore, replacement, and cleanup. Do not put OBID in `forwardedProps`, postMessage payloads, query strings, or rendered chat text.

- [ ] **Step 4: Build the checked-in browser bundle**

Run:

```bash
npm run test:js
npm run build:agui
git diff --check
```

Confirm `app/web/static/embed.js` contains the new header and key prefix and no legacy restore path.

- [ ] **Step 5: Run regression and commit**

Run:

```bash
uv run pytest tests/test_local_davinci_bootstrap.py tests/test_web_page.py -q
node --test tests/js/test_embed_identity.cjs tests/js/test_frontend_tool_runner.cjs tests/js/test_host_bridge_v2.cjs
```

Commit:

```bash
git add web/embed/identity.js web/embed/main.js app/web/static/embed.js \
  tests/js/test_embed_identity.cjs tests/browser tests/live
git commit -m "feat: scope iframe sessions by Davinci identity"
```

Before committing, inspect `git diff --cached --name-only`; remove any browser/live file not actually changed.

## Task 8: Build Davinci and Commit Generated Share Assets Separately

**Working directory:** clean Davinci implementation worktree.

**Files:**
- Regenerate tracked files under `webapp/build/`

- [ ] **Step 1: Run TypeScript/Jest regression before the build**

Run the Task 5 Jest command again, then `npx tsc --noEmit --pretty false`. If full TypeScript has pre-existing errors, save the output and prove no error points to changed files.

- [ ] **Step 2: Build**

Run: `npm run build`

Expected: successful production build.

- [ ] **Step 3: Inspect generated output**

Run:

```bash
git status --short webapp/build
git diff --check -- webapp/build
python - <<'PY'
from html.parser import HTMLParser
from pathlib import Path

class Sources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = []
    def handle_starttag(self, tag, attrs):
        if tag == "script":
            value = dict(attrs).get("src")
            if value and not value.startswith(("http://", "https://", "//")):
                self.values.append(value)

root = Path("webapp/build")
parser = Sources()
parser.feed((root / "share.html").read_text())
missing = [src for src in parser.values if not (root / src.lstrip("/")).is_file()]
if missing:
    raise SystemExit(f"missing share assets: {missing}")
print("verified share assets:", len(parser.values))
PY
rg -l 'X-Davinci-ObId' webapp/build/*.js
```

Verify generated assets reference the updated share bundle. Do not stage files outside `webapp/build`.

- [ ] **Step 4: Commit generated output independently**

Run:

```bash
git add -A webapp/build
git diff --cached --stat
git commit -m "build: refresh Davinci share bundle"
```

- [ ] **Step 5: Record the two Davinci commits in the Phase 1 handoff**

Run from the Davinci worktree: `git log -2 --format='%h %s'`

Expected subjects in order: generated build commit, then `feat: bind Agent bootstrap and snapshots to actor OBID`.

## Task 9: Prove Cross-User Isolation, Restart Recovery, and the UAT V2 Path

**Files:**
- Create: `tests/integration/test_obid_postgres_isolation.py`
- Modify: `tests/test_api.py`, `tests/test_agui_api.py`, `tests/test_attachments.py`, `tests/test_memory_scopes.py`
- Create: `scripts/verify-davinci-obid-phase-1.sh`
- Create: `docs/operations/davinci-obid-uat.md`

- [ ] **Step 1: Write RED end-to-end isolation tests**

With two distinct OBIDs and a real PostgreSQL URL, prove:

- each OBID resolves to a stable distinct internal UUID and exactly one personal Workspace;
- the same user creates multiple Session IDs and distinct Session directories/resume IDs;
- those Sessions share one `(user_id, workspace_id)` memory scope;
- different users have different memory scopes;
- user A receives 404 for user B's Workspace/Session/Turn/messages/SSE/Attachment/files/Skill and AG-UI continuation;
- recreating the FastAPI app with the same PostgreSQL/data directory restores A's UUID, Workspace, Sessions, and memory path.
- persisted messages and Attachment/Output retrieval survive restart;
- deleting one Session removes only its directory/artifact associations and
  does not delete the shared `(user_id, workspace_id)` memory.

- [ ] **Step 2: Run RED**

Run:

```bash
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_obid_postgres_isolation.py -q
```

Expected: failures reveal any route not wired to OBID identity or any Workspace still coupled to static IDs.

- [ ] **Step 3: Fix only discovered Phase 1 boundary defects**

Do not weaken 404 hiding semantics. Do not add a shared Workspace directory. Keep output/attachment paths inside each Session root and long-term memory inside the existing hashed `(user_id, workspace_id)` scope.

- [ ] **Step 4: Add an executable verification script and runbook**

`scripts/verify-davinci-obid-phase-1.sh` must fail fast and run:

```bash
set -euo pipefail
docker compose -p davinci-obid-phase1 -f compose.integration.yaml up -d --wait postgres
export TEST_POSTGRES_URL="postgresql+asyncpg://workspace:workspace@127.0.0.1:${POSTGRES_PORT:-55432}/workspace_test"
DATABASE_URL="$TEST_POSTGRES_URL" uv run python -m app.db.cli
uv run pytest tests/test_config.py tests/test_obid.py \
  tests/test_local_davinci_bootstrap.py tests/test_workspace_provisioner.py \
  tests/test_workspace_template_resolver.py -q
: "${TEST_POSTGRES_URL:?set TEST_POSTGRES_URL to a disposable PostgreSQL database}"
TEST_POSTGRES_URL="$TEST_POSTGRES_URL" \
  uv run pytest tests/integration/test_obid_postgres_isolation.py -q
npm run test:js
```

Install an EXIT trap that runs
`docker compose -p davinci-obid-phase1 -f compose.integration.yaml down -v`.
This makes the script authoritative; callers need Docker but do not predefine a
database URL.

The runbook must specify one replica, PostgreSQL backup/migration, exact HTTPS origins, allowed UAT network clients, the OBID trust limitation, rollback to the previous Agent Host/Davinci builds, and a browser checklist: actor A bootstrap, view-as B does not change actor, actor switch remounts iframe, snapshot header remains A, Native V2 read and write tool continuation succeeds.

- [ ] **Step 5: Run the final Gate and commit**

Run:

```bash
bash scripts/verify-davinci-obid-phase-1.sh
uv run pytest -q
git diff --check
```

Commit:

```bash
git add tests/integration/test_obid_postgres_isolation.py tests/test_api.py \
  tests/test_agui_api.py tests/test_attachments.py tests/test_memory_scopes.py \
  scripts/verify-davinci-obid-phase-1.sh docs/operations/davinci-obid-uat.md
git commit -m "test: prove OBID workspace and session isolation"
```

## Phase 1 Completion Gate

Phase 1 is complete only when all are true:

- Agent Host starts with `APP_ENV=uat`, `APP_IDENTITY_MODE=obid`, `local_inline`, and external PostgreSQL; a second instance is operationally forbidden.
- Davinci sends only `defaultObId`; switching only `defaultUser` does not change Agent identity.
- Two OBIDs produce distinct internal users, Personal Workspaces, Session visibility, file roots, and memory scopes.
- One user's multiple Sessions are independent conversations/directories but share the intended Workspace-scoped Auto Memory.
- Native V2 handshake, registry, one read tool, one mutating tool, deferred result continuation, and session recovery pass in the real UAT browser.
- No raw OBID appears in URL, localStorage value, AG-UI forwarded props, model input, artifact body, server logs, or filesystem path.
- The runbook explicitly marks this as UAT-only trust, not production authentication.
