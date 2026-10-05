# Open Global Skill Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow every authenticated, registered Agent Host user to create, replace, rename, inspect, and archive global Skills while preserving immutable Session snapshots.

**Architecture:** Keep the existing global-Skill API and storage contracts, replace the `skill_admin` mutation gate with an explicit `global_contributor` authorization, and revalidate the contributor inside each write transaction against the `users` table. Continue exposing the existing `can_manage_global_skills` capability so the current UI becomes available without a new frontend protocol.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, SQLite/PostgreSQL, Node.js tests, Playwright

**Spec:** `docs/superpowers/specs/2026-08-21-open-global-skill-collaboration-design.md`

## Global Constraints

- Keep all endpoints authenticated through the existing `Identity` dependency.
- Keep `/api/admin/global-skills/*` paths for client and gateway compatibility.
- Do not add a database migration or dependency.
- Preserve Bundle validation, conflict policies, Hash compare-and-swap, soft archive, and Session version pinning.
- Preserve personal-Skill permissions.
- Do not commit, push, deploy, or modify remote environments.

---

### Task 1: Specify open collaboration with failing tests

**Files:**
- Modify: `tests/test_skill_service.py`
- Modify: `tests/test_skill_api.py`
- Modify: `tests/test_api.py`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Specifies: `PlatformSkillAccessService.can_manage_global_skills(identity) -> bool`
- Specifies: ordinary registered users can call every existing global-Skill mutation route
- Specifies: a missing user record fails before an upload body is read
- Specifies: a newly published global Skill appears in later Sessions

- [x] **Step 1: Replace service admin-only expectations with registered-user behavior**

In `tests/test_skill_service.py`, replace `test_global_publish_requires_platform_skill_admin` and `test_workspace_owner_is_not_implicitly_platform_skill_admin` with:

```python
@pytest.mark.asyncio
async def test_registered_user_can_publish_global_skill(skill_service, owner) -> None:
    published = await skill_service.import_global_uploaded_directory(
        owner,
        uploaded_skill(),
        on_conflict="fail",
        expected_hash=None,
        target_name=None,
    )
    assert published.skill.scope == "global"


@pytest.mark.asyncio
async def test_registered_user_can_manage_global_skills(skill_service, owner) -> None:
    assert await skill_service.platform_access.can_manage_global_skills(owner) is True
```

Keep the `skill_admin` bootstrap test, but assert the new `is_skill_admin` method so role administration remains covered. Change the insert/replace/archive race test to delete the contributor's `UserRecord` after the service precheck and assert `404 skill_not_found` with no data mutation.

- [x] **Step 2: Replace API admin-only expectations with ordinary-user behavior**

In `tests/test_skill_api.py`, add an `unregistered` `IdentityContext` to `SkillApiIdentityProvider` without inserting its `UserRecord`. Use that identity for both streamed-upload early-rejection tests and retain `body_reads == 0` plus `(404, "skill_not_found")`.

Remove `_grant_skill_admin` from global create, overwrite, rename, detail, and archive cases. Issue at least one global mutation as `member` and assert the literal response status and payload. Rename tests so they describe registered contributors rather than administrators.

In `tests/test_api.py`, change the ordinary Workspace assertion to:

```python
assert workspaces.json()[0]["can_manage_global_skills"] is True
```

- [x] **Step 3: Convert the browser acceptance test to a non-admin collaboration flow**

In `tests/browser/test_workbench.py`, remove direct insertion of `PlatformRoleBindingRecord` from `test_platform_admin_can_upload_global_skill`, rename it to `test_authenticated_user_can_upload_global_skill`, and first assert that no `skill_admin` binding exists for `manager`.

After uploading through `#globalSkillArchiveInput`, return to chat, create a Session with `_create_browser_session(page)`, fetch `/api/sessions/{session_id}/skills`, and assert the global Skill name and description are present.

- [x] **Step 4: Run the new tests against the unchanged implementation**

Run:

```bash
uv run pytest -q \
  tests/test_skill_service.py::test_registered_user_can_publish_global_skill \
  tests/test_skill_service.py::test_registered_user_can_manage_global_skills \
  tests/test_skill_api.py \
  tests/test_api.py \
  tests/browser/test_workbench.py::Test_skill_management_acceptance::test_authenticated_user_can_upload_global_skill
```

Expected: registered-user service/API/browser assertions fail because the current implementation requires `skill_admin`; the missing-user early-rejection assertions continue to pass.

### Task 2: Implement registered-user global contribution

**Files:**
- Modify: `app/skills/access.py`
- Modify: `app/skills/repository.py`
- Modify: `app/skills/service.py`
- Modify: `app/skills/routes.py`
- Modify: `app/web/templates/embed.html`
- Modify: `app/web/templates/index.html`

**Interfaces:**
- Produces: `PlatformSkillAccessService.require_global_contributor(identity) -> None`
- Produces: `PlatformSkillAccessService.is_skill_admin(identity) -> bool`
- Produces: `SkillMutationAuthorization.global_contributor(user_id) -> SkillMutationAuthorization`
- Preserves: existing routes, schemas, status codes, conflicts, artifacts, and Session snapshot shape

- [x] **Step 1: Implement contributor capability without discarding admin-role support**

In `app/skills/access.py`, import `UserRecord`, make `can_manage_global_skills` return whether `identity.user_id` exists in `users`, extract the current role query into `is_skill_admin`, make `require_skill_admin` call `is_skill_admin`, and add:

```python
async def require_global_contributor(self, identity: IdentityContext) -> None:
    if not await self.can_manage_global_skills(identity):
        raise AppError("skill_not_found", "Skill not found.", 404)
```

- [x] **Step 2: Add defense-in-depth authorization to Skill writes**

In `app/skills/repository.py`, import `UserRecord`, extend `SkillMutationAuthorization.mode` with `global_contributor`, and add:

```python
@classmethod
def global_contributor(cls, user_id: str) -> SkillMutationAuthorization:
    return cls("global_contributor", user_id)
```

In `_require_mutation_authorization`, lock and verify `UserRecord.id == authorization.user_id` for this mode. In `_require_insert_authorized` and `_require_record_authorized`, permit `global_contributor` only when the target is global and has no Workspace ID.

For PostgreSQL, keep the `FOR UPDATE` row lock. For SQLite, execute
`BEGIN IMMEDIATE` before reading `UserRecord`, because SQLite ignores `FOR UPDATE`
and otherwise permits a deprovision to commit after authorization but before the
Skill write.

- [x] **Step 3: Route global mutations through contributor authorization**

In `app/skills/service.py`, replace global import and archive calls to `require_skill_admin` and `SkillMutationAuthorization.skill_admin(...)` with `require_global_contributor` and `SkillMutationAuthorization.global_contributor(...)`. Leave trusted bootstrap publication unchanged.

In `app/skills/routes.py`, replace all four global route prechecks with `require_global_contributor(identity)`. Keep those prechecks before multipart parsing.

In both Web templates, describe global Skills as team-shared and uploadable by all users. Do not add a brittle exact-copy test; the browser test verifies the controls and real upload behavior.

- [x] **Step 4: Run the RED set to GREEN**

Run the exact command from Task 1 Step 4.

Expected: all selected tests pass; ordinary users see the control, publish globally, and a new Session resolves the Skill.

### Task 3: Verify preserved mutation and Session semantics

**Files:**
- Review: `tests/test_skill_service.py`
- Review: `tests/test_skill_api.py`
- Review: `tests/browser/test_workbench.py`

**Interfaces:**
- Verifies: conflict overwrite and rename
- Verifies: archive and transaction revalidation
- Verifies: existing Sessions remain pinned while later Sessions see current global versions

- [x] **Step 1: Run the complete focused Skill suites**

```bash
uv run pytest -q \
  tests/test_skill_service.py \
  tests/test_skill_repository.py \
  tests/test_skill_api.py \
  tests/test_sessions.py
```

- [x] **Step 2: Verify SQLite authorization/deprovision serialization**

Run:

```bash
uv run pytest -q \
  tests/test_skill_service.py::test_global_mutations_revalidate_contributor_in_write_transaction \
  tests/test_skill_service.py::test_global_archive_does_not_commit_after_contributor_is_deprovisioned
```

Expected: all four cases pass. The real SQLite/WAL interleaving must never produce
a deprovision commit followed by a successful global archive commit.

- [x] **Step 3: Run the three browser lifecycle cases**

```bash
uv run pytest -q \
  tests/browser/test_workbench.py::Test_skill_management_acceptance::test_authenticated_user_can_upload_global_skill \
  tests/browser/test_workbench.py::Test_skill_management_acceptance::test_existing_session_keeps_old_skill_after_replacement \
  tests/browser/test_workbench.py::Test_skill_management_acceptance::test_global_default_toggle_and_new_session_snapshot
```

Expected: ordinary-user publication works, later Sessions receive current Skills, and earlier Sessions retain their pinned versions.

### Task 4: Final verification and consistency review

**Files:**
- Review: `app/skills/access.py`
- Review: `app/skills/repository.py`
- Review: `app/skills/service.py`
- Review: `app/skills/routes.py`
- Review: all changed tests and design/plan documents

**Interfaces:**
- Verifies all interfaces produced by Tasks 1-3

- [x] **Step 1: Run focused Python and JavaScript suites**

```bash
uv run pytest -q \
  tests/test_skill_service.py \
  tests/test_skill_repository.py \
  tests/test_skill_api.py \
  tests/test_api.py \
  tests/test_sessions.py
npm run test:js
```

- [x] **Step 2: Run static checks on changed Python files**

```bash
uv run ruff check \
  app/skills/access.py \
  app/skills/repository.py \
  app/skills/service.py \
  app/skills/routes.py \
  tests/test_skill_service.py \
  tests/test_skill_api.py \
  tests/test_api.py \
  tests/browser/test_workbench.py
```

- [x] **Step 3: Run the complete deterministic test suite**

```bash
uv run pytest -q
```

Expected: zero failures. Live model and external Docker/PostgreSQL Gates may remain skipped by their existing environment flags.

- [x] **Step 4: Review the final diff and repository state**

```bash
git diff --check
git status --short
git diff --stat
```

Confirm there are no dependency, migration, generated-bundle, deployment, commit, or remote changes outside the documented scope.
