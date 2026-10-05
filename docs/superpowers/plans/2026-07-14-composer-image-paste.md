# Composer Image Paste Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude-App-style clipboard image paste to the current Composer, keep pasted images isolated to the selected Session, and run the service on the verified multimodal `qwen3.7-plus` model.

**Architecture:** Reuse the existing attachment upload and Turn submission APIs. A focused browser module extracts clipboard image files and hands them to the existing upload path; the backend adds cross-batch pending limits and a final Turn limit, while the UI renders pending and historical image previews. Provider compatibility is covered by a deterministic live Anthropic Messages smoke test before the running launchd service is switched.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async + SQLite, Claude Agent SDK, vanilla JavaScript, Node test runner, Jinja2, Playwright, pytest, DashScope Anthropic-compatible Messages API.

## Global Constraints

- The service default model must become exactly `qwen3.7-plus`; do not add a duplicate `model` field to `workspaces/example/workspace.yaml`.
- Pasted images upload immediately and are stored through the existing Session attachment API.
- Pasted images and ordinary files share `MAX_FILES_PER_TURN`; the current default is 5, and the browser must receive the configured value rather than hard-code 5.
- Supported image types remain PNG, JPEG, GIF, and WebP, validated from file bytes by the server.
- Pure-text paste must retain native browser behavior.
- When clipboard data includes image and text/HTML representations, consume only the image files.
- Never put image Base64 in the Turn JSON body; submit only `attachment_ids`.
- Do not stage or overwrite the unrelated modifications already present in `.env.example`, `.gitignore`, `README.md`, `tests/test_workspaces.py`, or `workspaces/example/workspace.yaml`.
- No API key, image payload, or clipboard content may be written to ordinary logs or committed files.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `app/attachments/service.py` | Enforce the per-Session pending attachment limit across upload batches. |
| `app/api/routes.py` | Expose the selected Session's pending attachment draft list. |
| `app/turns/service.py` | Reject Turn requests whose attachment ID count exceeds the configured limit. |
| `app/web/static/composer-paste.js` | Pure clipboard image extraction, naming, limit checks, and paste event lifecycle. |
| `app/web/static/app.js` | Connect pasted files to the selected Session, upload state, Turn submission, and attachment rendering. |
| `app/web/static/app.css` | Pending and historical image preview layout, failure fallback, and mobile overflow behavior. |
| `app/web/routes.py` | Pass `MAX_FILES_PER_TURN` to the rendered page. |
| `app/web/templates/index.html` | Expose the configured limit and load `composer-paste.js` before `app.js`. |
| `tests/test_attachments.py` | Cross-batch, concurrent pending-limit, and pending-draft listing regression coverage. |
| `tests/test_turns.py` | Final Turn attachment-count defense. |
| `tests/js/test_composer_paste.cjs` | Node unit tests for clipboard parsing and controller behavior. |
| `tests/test_web_page.py` | Static asset and page-configuration contract. |
| `tests/browser/test_workbench.py` | Browser paste, Session isolation, upload, thumbnail, history, and mobile checks. |
| `tests/live/test_qwen_multimodal_compat.py` | Deterministic direct-image and nested tool-result image checks against `qwen3.7-plus`. |

---

### Task 1: Enforce the Pending Attachment Limit Across Upload Batches

**Files:**
- Modify: `app/attachments/service.py:1-91`
- Test: `tests/test_attachments.py:1-102`

**Interfaces:**
- Consumes: `Settings.max_files_per_turn`, `AttachmentRecord.status`, and `SessionLockRegistry.acquire(session_id)`.
- Produces: `AttachmentService.upload(session_id, files) -> list[AttachmentRecord]` with an atomic in-process cross-batch pending limit.
- Produces: `AttachmentService.list_pending(session_id) -> list[AttachmentRecord]`, ordered by creation time and ID.

- [ ] **Step 1: Write failing cross-batch and concurrency tests**

Add `import asyncio` to `tests/test_attachments.py`, then add:

```python
@pytest.mark.asyncio
async def test_upload_enforces_pending_limit_across_batches(settings_factory) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    settings, database, session, service = await build_services(settings_factory)
    first = await service.upload(
        session.id,
        [upload(f"first-{index}.txt", b"x") for index in range(4)],
    )

    with pytest.raises(AppError) as exc_info:
        await service.upload(
            session.id,
            [upload("overflow-1.txt", b"x"), upload("overflow-2.txt", b"x")],
        )

    assert exc_info.value.code == "attachment_invalid"
    assert exc_info.value.message == "At most 5 pending attachments are allowed per session."
    async with database.session() as db:
        count = await db.scalar(
            select(func.count()).select_from(AttachmentRecord).where(
                AttachmentRecord.session_id == session.id
            )
        )
    assert count == 4
    assert all(service.resolve_path(record).is_file() for record in first)
    await database.dispose()


@pytest.mark.asyncio
async def test_concurrent_uploads_cannot_bypass_pending_limit(settings_factory) -> None:
    from app.db.models import AttachmentRecord
    from app.errors import AppError

    _settings, database, session, service = await build_services(settings_factory)
    results = await asyncio.gather(
        service.upload(
            session.id,
            [upload(f"left-{index}.txt", b"x") for index in range(3)],
        ),
        service.upload(
            session.id,
            [upload(f"right-{index}.txt", b"x") for index in range(3)],
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, list) for result in results) == 1
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) == 1
    assert errors[0].code == "attachment_invalid"
    async with database.session() as db:
        count = await db.scalar(
            select(func.count()).select_from(AttachmentRecord).where(
                AttachmentRecord.session_id == session.id
            )
        )
    assert count == 3
    await database.dispose()


@pytest.mark.asyncio
async def test_list_pending_returns_only_session_drafts_in_stable_order(
    settings_factory,
) -> None:
    _settings, database, session, service = await build_services(settings_factory)
    records = await service.upload(
        session.id,
        [upload("first.txt", b"first"), upload("second.txt", b"second")],
    )

    pending = await service.list_pending(session.id)

    assert [record.id for record in pending] == [record.id for record in records]
    assert all(record.session_id == session.id for record in pending)
    assert all(record.status == "pending" for record in pending)
    await database.dispose()
```

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
uv run pytest \
  tests/test_attachments.py::test_upload_enforces_pending_limit_across_batches \
  tests/test_attachments.py::test_concurrent_uploads_cannot_bypass_pending_limit \
  tests/test_attachments.py::test_list_pending_returns_only_session_drafts_in_stable_order -q
```

Expected: both tests fail because separate batches are currently checked independently and can create 6 pending records.

- [ ] **Step 3: Implement the Session-serialized pending limit**

In `app/attachments/service.py`, import `func` and `SessionLockRegistry`:

```python
from sqlalchemy import delete, func, select

from app.sessions.locks import SessionLockRegistry
```

Initialize a private upload lock registry:

```python
class AttachmentService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.data_dir = settings.app_data_dir.resolve()
        self._upload_locks = SessionLockRegistry()
```

Replace `upload()` with:

```python
    async def upload(
        self, session_id: str, files: list[UploadFile]
    ) -> list[AttachmentRecord]:
        try:
            if not files or len(files) > self.settings.max_files_per_turn:
                raise AppError(
                    "attachment_invalid",
                    f"Upload between 1 and {self.settings.max_files_per_turn} files.",
                )
            async with self._upload_locks.acquire(session_id):
                session = await self._get_session(session_id)
                async with self.database.session() as db:
                    pending_count = int(
                        (
                            await db.scalar(
                                select(func.count())
                                .select_from(AttachmentRecord)
                                .where(
                                    AttachmentRecord.session_id == session_id,
                                    AttachmentRecord.status == "pending",
                                )
                            )
                        )
                        or 0
                    )
                if pending_count + len(files) > self.settings.max_files_per_turn:
                    raise AppError(
                        "attachment_invalid",
                        f"At most {self.settings.max_files_per_turn} pending "
                        "attachments are allowed per session.",
                    )

                attachment_dir = self._attachment_dir(session)
                attachment_dir.mkdir(parents=True, exist_ok=True)
                records: list[AttachmentRecord] = []
                created_paths: list[Path] = []
                try:
                    for file in files:
                        record, path = await self._stage_file(
                            session, attachment_dir, file
                        )
                        records.append(record)
                        created_paths.append(path)
                    async with self.database.session() as db:
                        db.add_all(records)
                        await db.commit()
                        for record in records:
                            await db.refresh(record)
                except Exception:
                    for path in created_paths:
                        path.unlink(missing_ok=True)
                    for temp_path in attachment_dir.glob(".upload-*.tmp"):
                        temp_path.unlink(missing_ok=True)
                    raise
                return records
        finally:
            for file in files:
                await file.close()
```

Add this method to `AttachmentService`:

```python
    async def list_pending(self, session_id: str) -> list[AttachmentRecord]:
        await self._get_session(session_id)
        async with self.database.session() as db:
            return list(
                (
                    await db.scalars(
                        select(AttachmentRecord)
                        .where(
                            AttachmentRecord.session_id == session_id,
                            AttachmentRecord.status == "pending",
                        )
                        .order_by(
                            AttachmentRecord.created_at,
                            AttachmentRecord.id,
                        )
                    )
                ).all()
            )
```

- [ ] **Step 4: Run attachment tests and verify green**

Run:

```bash
uv run pytest tests/test_attachments.py -q
```

Expected: all attachment tests pass; the two new tests prove a maximum of 5 pending records across sequential and concurrent batches.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add app/attachments/service.py tests/test_attachments.py
git diff --cached --check
git commit -m "fix: enforce pending attachment limit"
```

---

### Task 2: Add the Final Turn Attachment Limit

**Files:**
- Modify: `app/turns/service.py:57-70`
- Test: `tests/test_turns.py:441-470`

**Interfaces:**
- Consumes: `AttachmentService.settings.max_files_per_turn`.
- Produces: an early `attachment_invalid` error before attachment lookup or Turn creation when too many IDs are submitted.

- [ ] **Step 1: Write the failing Turn limit test**

Add to `tests/test_turns.py`:

```python
@pytest.mark.asyncio
async def test_turn_rejects_more_than_configured_attachments(settings_factory) -> None:
    from app.errors import AppError

    (
        settings,
        database,
        session,
        _sessions,
        _attachments,
        _runtime,
        _broker,
        turns,
    ) = await build_turn_services(settings_factory)
    attachment_ids = [
        str(uuid.uuid4()) for _ in range(settings.max_files_per_turn + 1)
    ]

    with pytest.raises(AppError) as exc_info:
        await turns.start(session.id, "too many", attachment_ids, "request-limit")

    assert exc_info.value.code == "attachment_invalid"
    assert exc_info.value.message == "At most 5 attachments are allowed per turn."
    await turns.shutdown()
    await database.dispose()
```

- [ ] **Step 2: Run the test and verify red**

Run:

```bash
uv run pytest tests/test_turns.py::test_turn_rejects_more_than_configured_attachments -q
```

Expected: FAIL because the current error is the later “pending and belong to this session” validation rather than the configured count limit.

- [ ] **Step 3: Add the early Turn limit check**

Insert after duplicate-ID validation in `TurnService.start()`:

```python
        max_attachments = self.attachments.settings.max_files_per_turn
        if len(attachment_ids) > max_attachments:
            raise AppError(
                "attachment_invalid",
                f"At most {max_attachments} attachments are allowed per turn.",
            )
```

- [ ] **Step 4: Run focused and full Turn tests**

Run:

```bash
uv run pytest tests/test_turns.py -q
```

Expected: all Turn tests pass, including the new early limit test and existing binding/isolation tests.

- [ ] **Step 5: Commit Task 2**

```bash
git add app/turns/service.py tests/test_turns.py
git diff --cached --check
git commit -m "fix: enforce turn attachment limit"
```

---

### Task 3: Build the Clipboard Image Controller

**Files:**
- Create: `app/web/static/composer-paste.js`
- Create: `tests/js/test_composer_paste.cjs`

**Interfaces:**
- Produces: `ComposerPaste.extractImageFiles(clipboardData, now) -> File[]`.
- Produces: `ComposerPaste.createController(options) -> {handlePaste(event), destroy()}`.
- Controller options: `input`, `canUpload() -> boolean`, `getRemainingSlots() -> number`, `uploadFiles(files) -> Promise`, and `onError(message) -> void`.

- [ ] **Step 1: Write Node tests for text, mixed content, naming, limit, and lifecycle**

Create `tests/js/test_composer_paste.cjs`:

```javascript
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {extractImageFiles, createController} = require(
  "../../app/web/static/composer-paste.js"
);

class FakeInput {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
  }
}

function item(file) {
  return {kind: "file", type: file.type, getAsFile: () => file};
}

function pasteEvent(items) {
  return {
    clipboardData: {items},
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
  };
}

test("text-only clipboard data keeps native paste behavior", async () => {
  const input = new FakeInput();
  const uploaded = [];
  const controller = createController({
    input,
    canUpload: () => true,
    getRemainingSlots: () => 5,
    uploadFiles: async (files) => uploaded.push(files),
    onError: assert.fail,
  });
  const event = pasteEvent([{kind: "string", type: "text/plain"}]);

  assert.equal(await controller.handlePaste(event), false);
  assert.equal(event.defaultPrevented, false);
  assert.deepEqual(uploaded, []);
});

test("mixed clipboard content consumes ordered images only", async () => {
  const input = new FakeInput();
  const uploaded = [];
  const png = new File(["png"], "image.png", {type: "image/png"});
  const jpeg = new File(["jpeg"], "photo-original.jpg", {type: "image/jpeg"});
  const controller = createController({
    input,
    canUpload: () => true,
    getRemainingSlots: () => 5,
    uploadFiles: async (files) => uploaded.push(files),
    onError: assert.fail,
    now: () => new Date(2026, 6, 14, 1, 2, 3),
  });
  const event = pasteEvent([
    {kind: "string", type: "text/html"},
    item(png),
    {kind: "string", type: "text/plain"},
    item(jpeg),
  ]);

  assert.equal(await controller.handlePaste(event), true);
  assert.equal(event.defaultPrevented, true);
  assert.equal(uploaded.length, 1);
  assert.equal(uploaded[0][0].name, "clipboard-20260714-010203-1.png");
  assert.equal(uploaded[0][1].name, "photo-original.jpg");
});

test("controller rejects image paste when unavailable or over limit", async () => {
  const image = new File(["png"], "image.png", {type: "image/png"});
  const errors = [];
  const input = new FakeInput();
  let available = false;
  let remaining = 0;
  const controller = createController({
    input,
    canUpload: () => available,
    getRemainingSlots: () => remaining,
    uploadFiles: async () => assert.fail("upload must not run"),
    onError: (message) => errors.push(message),
  });

  const unavailable = pasteEvent([item(image)]);
  assert.equal(await controller.handlePaste(unavailable), true);
  assert.equal(unavailable.defaultPrevented, true);
  assert.deepEqual(errors, ["当前不能上传图片。"]);

  available = true;
  remaining = 1;
  const overLimit = pasteEvent([item(image), item(image)]);
  assert.equal(await controller.handlePaste(overLimit), true);
  assert.equal(overLimit.defaultPrevented, true);
  assert.deepEqual(errors, ["当前不能上传图片。", "最多还能添加 1 个附件。"]);

  controller.destroy();
  assert.equal(input.listeners.has("paste"), false);
});

test("extractImageFiles ignores null and non-image file items", () => {
  const text = new File(["text"], "notes.txt", {type: "text/plain"});
  assert.deepEqual(
    extractImageFiles({items: [item(text), {kind: "file", type: "image/png", getAsFile: () => null}]}),
    [],
  );
});
```

- [ ] **Step 2: Run the Node test and verify red**

Run:

```bash
node --test tests/js/test_composer_paste.cjs
```

Expected: FAIL with `MODULE_NOT_FOUND` for `composer-paste.js`.

- [ ] **Step 3: Implement the focused UMD controller**

Create `app/web/static/composer-paste.js`:

```javascript
"use strict";

(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ComposerPaste = api;
})(typeof window === "undefined" ? null : window, function buildModule() {
  const extensionByMime = new Map([
    ["image/png", "png"],
    ["image/jpeg", "jpg"],
    ["image/gif", "gif"],
    ["image/webp", "webp"],
  ]);

  function pad(value) { return String(value).padStart(2, "0"); }

  function timestamp(date) {
    return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}`
      + `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  }

  function hasMeaningfulName(file) {
    const name = String(file.name || "").trim();
    return Boolean(name) && !/^(?:image|blob)(?:\.[a-z0-9]+)?$/iu.test(name);
  }

  function clipboardName(file, index, date) {
    if (hasMeaningfulName(file)) return file;
    const extension = extensionByMime.get(file.type) || "img";
    return new File(
      [file],
      `clipboard-${timestamp(date)}-${index + 1}.${extension}`,
      {type: file.type, lastModified: file.lastModified || date.getTime()},
    );
  }

  function extractImageFiles(clipboardData, now = new Date()) {
    const files = [];
    for (const candidate of Array.from(clipboardData?.items || [])) {
      if (candidate.kind !== "file" || !candidate.type.startsWith("image/")) continue;
      const file = candidate.getAsFile();
      if (file) files.push(clipboardName(file, files.length, now));
    }
    return files;
  }

  function createController({
    input,
    canUpload,
    getRemainingSlots,
    uploadFiles,
    onError,
    now = () => new Date(),
  }) {
    async function handlePaste(event) {
      const files = extractImageFiles(event.clipboardData, now());
      if (!files.length) return false;
      event.preventDefault();
      if (!canUpload()) {
        onError("当前不能上传图片。");
        return true;
      }
      const remaining = Math.max(0, Number(getRemainingSlots()) || 0);
      if (files.length > remaining) {
        onError(`最多还能添加 ${remaining} 个附件。`);
        return true;
      }
      try {
        await uploadFiles(files);
      } catch (error) {
        onError(error?.message || "图片上传失败。");
      }
      return true;
    }

    const listener = (event) => { void handlePaste(event); };
    input.addEventListener("paste", listener);
    return {
      handlePaste,
      destroy() { input.removeEventListener("paste", listener); },
    };
  }

  return {extractImageFiles, createController};
});
```

- [ ] **Step 4: Run all JavaScript unit tests**

Run:

```bash
node --test tests/js/*.cjs
```

Expected: all existing autocomplete tests and all new paste-controller tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add app/web/static/composer-paste.js tests/js/test_composer_paste.cjs
git diff --cached --check
git commit -m "feat: parse pasted composer images"
```

---

### Task 4: Connect Paste to the Existing Upload and Turn Flow

**Files:**
- Modify: `app/api/routes.py:147-161`
- Modify: `app/web/routes.py:13-19`
- Modify: `app/web/templates/index.html:10,78-105,142-143`
- Modify: `app/web/static/app.js:49-71,120-190,345-464`
- Modify: `tests/test_web_page.py:7-60`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes: `ComposerPaste.createController`, `POST /api/sessions/{id}/attachments`, and `POST /api/sessions/{id}/turns`.
- Consumes: `AttachmentService.list_pending(session_id)` through `GET /api/sessions/{id}/attachments`.
- Produces: immediate Session-bound upload, Session draft restoration, `state.uploadingAttachments`, configured remaining-slot checks, and unchanged Turn JSON containing attachment IDs only.

- [ ] **Step 1: Add failing page-contract and browser paste tests**

Update `tests/test_web_page.py` to assert:

```python
    assert 'data-max-files-per-turn="5"' in html
    assert html.index('/static/composer-autocomplete.js') < html.index(
        '/static/composer-paste.js'
    ) < html.index('/static/app.js')
```

Extend the static asset test:

```python
        paste = await client.get("/static/composer-paste.js")

    assert paste.status_code == 200
    assert "extractImageFiles" in paste.text
```

Add this helper and test to `tests/browser/test_workbench.py`:

```python
async def paste_png(page) -> None:
    await page.locator("#messageInput").evaluate(
        """element => {
          const bytes = Uint8Array.from(
            atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII='),
            character => character.charCodeAt(0),
          );
          const transfer = new DataTransfer();
          transfer.items.add(new File([bytes], 'image.png', {type: 'image/png'}));
          const event = new Event('paste', {bubbles: true, cancelable: true});
          Object.defineProperty(event, 'clipboardData', {value: transfer});
          element.dispatchEvent(event);
        }"""
    )


@pytest.mark.asyncio
async def test_composer_pastes_image_and_submits_attachment_id(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        submitted = []

        async def capture_turn(route) -> None:
            submitted.append(route.request.post_data_json)
            await route.continue_()

        await page.route(re.compile(r".*/api/sessions/[^/]+/turns$"), capture_turn)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)

        await expect(page.locator("#attachmentTray")).to_contain_text(
            re.compile(r"clipboard-\d{8}-\d{6}-1\.png")
        )
        await page.locator("#messageInput").fill("描述图片")
        await page.locator("#sendButton").click()
        await expect(page.locator(".message-assistant .message-body").last).to_have_text(
            "hello world"
        )

        assert len(submitted) == 1
        assert len(submitted[0]["attachment_ids"]) == 1
        assert "base64" not in str(submitted[0]).lower()
        await browser.close()


@pytest.mark.asyncio
async def test_pending_pasted_image_does_not_leak_to_new_session(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)

        await page.locator("#newSessionButton").click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)

        await page.locator("#sessionList .session-row").nth(1).click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await browser.close()


@pytest.mark.asyncio
async def test_pasted_image_upload_state_and_removal(live_url: str) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})

        async def delay_upload(route) -> None:
            await asyncio.sleep(0.2)
            await route.continue_()

        await page.route(
            re.compile(r".*/api/sessions/[^/]+/attachments$"),
            delay_upload,
        )
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        await paste_png(page)

        await expect(page.locator("#sendButton")).to_be_disabled()
        await expect(page.locator("#messageInput")).to_be_enabled()
        await page.locator("#messageInput").fill("输入内容不会因上传而丢失")
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(1)
        await expect(page.locator("#sendButton")).to_be_enabled()
        await expect(page.locator("#messageInput")).to_have_value(
            "输入内容不会因上传而丢失"
        )

        await page.locator("#attachmentTray .attachment-chip button").click()
        await expect(page.locator("#attachmentTray .attachment-chip")).to_have_count(0)
        await browser.close()
```

- [ ] **Step 2: Run focused page/browser tests and verify red**

Run:

```bash
uv run pytest \
  tests/test_web_page.py \
  tests/browser/test_workbench.py::test_composer_pastes_image_and_submits_attachment_id \
  tests/browser/test_workbench.py::test_pending_pasted_image_does_not_leak_to_new_session \
  tests/browser/test_workbench.py::test_pasted_image_upload_state_and_removal -q
```

Expected: page contract fails because the script/config are absent; browser paste tests fail because no paste listener uploads the image.

- [ ] **Step 3: Expose pending drafts, the configured limit, and the controller**

Add this route above the existing attachment POST route in `app/api/routes.py`:

```python
@router.get(
    "/sessions/{session_id}/attachments",
    response_model=list[AttachmentOut],
)
async def list_pending_attachments(
    session_id: str,
    services: Services,
) -> list[AttachmentOut]:
    return [
        _attachment_out(record)
        for record in await services.attachments.list_pending(session_id)
    ]
```

Change `app/web/routes.py` context to:

```python
        context={
            "app_name": "Workspace Agent",
            "max_files_per_turn": request.app.state.services.settings.max_files_per_turn,
        },
```

Change the opening body tag in `index.html`:

```html
  <body data-max-files-per-turn="{{ max_files_per_turn }}">
```

Load scripts in this order:

```html
    <script src="/static/composer-autocomplete.js" defer></script>
    <script src="/static/composer-paste.js" defer></script>
    <script src="/static/app.js" defer></script>
```

- [ ] **Step 4: Add upload state and paste wiring in `app.js`**

After `elements`, read the configured limit:

```javascript
const maxFilesPerTurn = Number(document.body.dataset.maxFilesPerTurn);
```

Add these state fields:

```javascript
  uploadingAttachments: false,
  uploadLabel: "正在上传附件",
  paste: null,
```

In `initialize()`, after the autocomplete controller is created, create the paste controller:

```javascript
  state.paste = ComposerPaste.createController({
    input: elements.messageInput,
    canUpload: () => Boolean(state.session)
      && !state.running
      && !state.uploadingAttachments
      && state.serviceState !== "disconnected",
    getRemainingSlots: () => maxFilesPerTurn - state.pendingAttachments.length,
    uploadFiles: (files) => uploadFiles(files, {label: "正在上传图片"}),
    onError: showToast,
  });
```

Add this loader and call it from `selectSession()` after `selectedId` is set and the empty pending tray is rendered:

```javascript
async function loadPendingAttachments(sessionId) {
  try {
    const pending = await api(
      `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
    );
    if (state.session?.id !== sessionId) return;
    state.pendingAttachments = pending;
    renderAttachmentTray();
  } catch (error) {
    if (state.session?.id === sessionId) showToast(error.message);
  }
}
```

```javascript
  await loadPendingAttachments(selectedId);
```

Replace `updateSessionHeader()` with:

```javascript
function updateSessionHeader() {
  elements.sessionWorkspace.textContent = state.workspace?.name || "Workspace";
  elements.sessionTitle.textContent = state.session?.title || "选择或新建会话";
  elements.sessionActions.hidden = !state.session;
  elements.sessionStatus.textContent = state.session?.status || "idle";
  elements.sessionStatus.className = `status-badge ${state.session?.status || "idle"}`;
  const enabled = Boolean(state.session)
    && !state.running
    && state.serviceState !== "disconnected";
  elements.attachmentButton.disabled = !enabled || state.uploadingAttachments;
  elements.messageInput.disabled = !enabled;
  elements.sendButton.disabled = !enabled || state.uploadingAttachments;
  elements.composerState.textContent = !state.session
    ? "未选择会话"
    : state.running
      ? "正在执行"
      : state.uploadingAttachments
        ? state.uploadLabel
        : "就绪";
}
```

Replace `uploadFiles()` with:

```javascript
async function uploadFiles(fileList, {label = "正在上传附件"} = {}) {
  const files = Array.from(fileList || []);
  elements.attachmentInput.value = "";
  if (
    !state.session
    || !files.length
    || state.running
    || state.uploadingAttachments
  ) return;
  if (state.pendingAttachments.length + files.length > maxFilesPerTurn) {
    showToast(`每个 Turn 最多添加 ${maxFilesPerTurn} 个附件。`);
    return;
  }

  const uploadingSessionId = state.session.id;
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  state.uploadingAttachments = true;
  state.uploadLabel = label;
  updateSessionHeader();
  try {
    const uploaded = await api(
      `/api/sessions/${encodeURIComponent(uploadingSessionId)}/attachments`,
      {method: "POST", body: form},
    );
    if (state.session?.id !== uploadingSessionId) {
      await Promise.allSettled(
        uploaded.map((attachment) => api(`/api/attachments/${attachment.id}`, {method: "DELETE"})),
      );
      return;
    }
    state.pendingAttachments.push(...uploaded);
    renderAttachmentTray();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.uploadingAttachments = false;
    state.uploadLabel = "正在上传附件";
    updateSessionHeader();
  }
}
```

Add the upload guard at the start of `sendMessage()`:

```javascript
  if (!state.session || state.running || state.uploadingAttachments) return;
```

- [ ] **Step 5: Re-run page/browser tests and the Node suite**

Run:

```bash
uv run pytest \
  tests/test_web_page.py \
  tests/browser/test_workbench.py::test_composer_pastes_image_and_submits_attachment_id \
  tests/browser/test_workbench.py::test_pending_pasted_image_does_not_leak_to_new_session \
  tests/browser/test_workbench.py::test_pasted_image_upload_state_and_removal -q
node --test tests/js/*.cjs
```

Expected: all commands pass; the captured Turn body contains exactly one attachment ID and no image Base64.

- [ ] **Step 6: Commit Task 4**

```bash
git add \
  app/web/routes.py \
  app/api/routes.py \
  app/web/templates/index.html \
  app/web/static/app.js \
  tests/test_web_page.py \
  tests/browser/test_workbench.py
git diff --cached --check
git commit -m "feat: upload pasted composer images"
```

---

### Task 5: Render Pending and Historical Image Previews

**Files:**
- Modify: `app/web/static/app.js:404-432,596-615`
- Modify: `app/web/static/app.css:398-426,622-642`
- Modify: `tests/browser/test_workbench.py`

**Interfaces:**
- Consumes pending attachment fields `original_filename`, `mime_type`, `size_bytes`, and `content_url`.
- Consumes historical attachment fields `id`, `filename`, `mime_type`, and `size_bytes`.
- Produces shared `createAttachmentElement(attachment, options) -> HTMLElement` rendering with image-error fallback.

- [ ] **Step 1: Strengthen browser tests for thumbnail, size, history, and mobile layout**

In `test_composer_pastes_image_and_submits_attachment_id`, add before sending:

```python
        await expect(page.locator("#attachmentTray .attachment-thumbnail")).to_be_visible()
        await expect(page.locator("#attachmentTray .attachment-size")).to_contain_text("B")
```

After the assistant response, add:

```python
        await expect(
            page.locator(".message-user .history-attachment .attachment-thumbnail").last
        ).to_be_visible()
        await page.reload()
        await page.locator("#sessionList .session-row").first.click()
        await expect(
            page.locator(".message-user .history-attachment .attachment-thumbnail").last
        ).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
```

- [ ] **Step 2: Run the browser test and verify red**

Run:

```bash
uv run pytest tests/browser/test_workbench.py::test_composer_pastes_image_and_submits_attachment_id -q
```

Expected: FAIL because pending and historical attachments do not yet contain `.attachment-thumbnail` or `.attachment-size`.

- [ ] **Step 3: Add shared attachment rendering helpers**

Add above `renderAttachmentTray()` in `app.js`:

```javascript
function attachmentFilename(attachment) {
  return attachment.original_filename || attachment.filename || "attachment";
}

function attachmentContentUrl(attachment) {
  return attachment.content_url || `/api/attachments/${attachment.id}/content`;
}

function formatFileSize(sizeBytes) {
  const bytes = Math.max(0, Number(sizeBytes) || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function createAttachmentElement(attachment, {history = false, onRemove = null} = {}) {
  const filename = attachmentFilename(attachment);
  const isImage = String(attachment.mime_type || "").startsWith("image/");
  const element = document.createElement(history ? "a" : "div");
  element.className = `${history ? "history-attachment" : "attachment-chip"}`
    + `${isImage ? " image-attachment" : ""}`;
  if (history) {
    element.href = attachmentContentUrl(attachment);
    element.target = "_blank";
    element.rel = "noopener";
  }

  const icon = document.createElement("b");
  icon.className = "attachment-fallback-icon";
  icon.textContent = isImage ? "IMG" : "FILE";
  icon.hidden = isImage;
  if (isImage) {
    const image = document.createElement("img");
    image.className = "attachment-thumbnail";
    image.src = attachmentContentUrl(attachment);
    image.alt = filename;
    image.addEventListener("error", () => {
      image.remove();
      icon.hidden = false;
    });
    element.append(image);
  }

  const metadata = document.createElement("span");
  metadata.className = "attachment-metadata";
  const name = document.createElement("span");
  name.className = "attachment-name";
  name.textContent = filename;
  const size = document.createElement("small");
  size.className = "attachment-size";
  size.textContent = formatFileSize(attachment.size_bytes);
  metadata.append(name, size);
  element.append(icon, metadata);

  if (onRemove) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = "移除";
    remove.setAttribute("aria-label", `移除 ${filename}`);
    remove.textContent = "×";
    remove.addEventListener("click", onRemove);
    element.append(remove);
  }
  return element;
}
```

Replace `renderAttachmentTray()` with:

```javascript
function renderAttachmentTray() {
  elements.attachmentTray.replaceChildren();
  state.pendingAttachments.forEach((attachment) => {
    elements.attachmentTray.append(
      createAttachmentElement(attachment, {
        onRemove: () => removeAttachment(attachment.id),
      }),
    );
  });
}
```

Replace the inner attachment loop in `appendUserMessage()` with:

```javascript
    payload.attachments.forEach((attachment) => {
      attachments.append(createAttachmentElement(attachment, {history: true}));
    });
```

- [ ] **Step 4: Add responsive preview styles**

Add to `app.css` after the existing attachment rules:

```css
.attachment-chip.image-attachment,
.history-attachment.image-attachment {
  align-items: stretch;
  min-width: 190px;
}

.attachment-thumbnail {
  width: 72px;
  height: 72px;
  flex: 0 0 72px;
  border-radius: 4px;
  object-fit: cover;
  background: var(--surface-muted);
}

.attachment-metadata {
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  flex-direction: column;
  justify-content: center;
}

.attachment-size {
  color: var(--muted);
  font-size: 11px;
}

.attachment-fallback-icon[hidden] {
  display: none;
}

@media (max-width: 560px) {
  .attachment-chip.image-attachment,
  .history-attachment.image-attachment {
    min-width: 170px;
    max-width: 220px;
  }
}
```

- [ ] **Step 5: Run browser and static regressions**

Run:

```bash
uv run pytest tests/test_web_page.py tests/browser/test_workbench.py -q
```

Expected: all page and browser tests pass, including reload persistence and mobile-width checks.

- [ ] **Step 6: Commit Task 5**

```bash
git add app/web/static/app.js app/web/static/app.css tests/browser/test_workbench.py
git diff --cached --check
git commit -m "feat: preview image attachments"
```

---

### Task 6: Verify Qwen Multimodal Compatibility and Switch the Running Service

**Files:**
- Create: `tests/live/test_qwen_multimodal_compat.py`
- Operational change: launchd service `com.codex.claude-workspace-agent`

**Interfaces:**
- Consumes: `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, and `CLAUDE_MODEL=qwen3.7-plus` from the login-shell environment.
- Produces: deterministic live verification for direct user-image and nested `tool_result.image` messages, plus a running service whose Workspace API reports `qwen3.7-plus`.

- [ ] **Step 1: Add the opt-in live provider compatibility test**

Create `tests/live/test_qwen_multimodal_compat.py`:

```python
import base64
import os

import httpx
import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_QWEN_TESTS") != "1",
    reason="Set RUN_LIVE_QWEN_TESTS=1 to call the configured Qwen proxy.",
)

PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
    "x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_qwen_plus_accepts_user_and_tool_result_images() -> None:
    from app.config import Settings

    settings = Settings()
    assert settings.claude_model == "qwen3.7-plus"
    image = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(base64.b64decode(PNG_BASE64)).decode("ascii"),
        },
    }
    headers = {
        "x-api-key": settings.anthropic_api_key.get_secret_value(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = f"{str(settings.anthropic_base_url).rstrip('/')}/v1/messages"
    async with httpx.AsyncClient(timeout=90) as client:
        direct = await client.post(
            url,
            headers=headers,
            json={
                "model": settings.claude_model,
                "max_tokens": 32,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Reply OK."},
                            image,
                        ],
                    }
                ],
            },
        )
        tool_result = await client.post(
            url,
            headers=headers,
            json={
                "model": settings.claude_model,
                "max_tokens": 32,
                "tools": [
                    {
                        "name": "inspect_image",
                        "description": "Returns an image.",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                "messages": [
                    {"role": "user", "content": "Inspect the tool image."},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_live_image",
                                "name": "inspect_image",
                                "input": {},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_live_image",
                                "content": [image],
                            }
                        ],
                    },
                ],
            },
        )

    assert direct.status_code == 200, direct.text
    assert tool_result.status_code == 200, tool_result.text
    assert direct.json()["model"] == "qwen3.7-plus"
    assert tool_result.json()["model"] == "qwen3.7-plus"
```

- [ ] **Step 2: Run the live compatibility and Agent SDK image tests**

Run from a login shell so the same DashScope base URL and key are loaded without printing them:

```bash
/bin/zsh -lic '
  cd /Users/a110356/work/code/claude_workspace_mvp &&
  CLAUDE_MODEL=qwen3.7-plus RUN_LIVE_QWEN_TESTS=1 \
    uv run pytest tests/live/test_qwen_multimodal_compat.py -q &&
  CLAUDE_MODEL=qwen3.7-plus RUN_LIVE_CLAUDE_TESTS=1 \
    uv run pytest tests/live/test_claude_smoke.py -q
'
```

Expected: both live suites pass. The first suite proves the exact nested image shape that failed on `qwen3.7-max`; the second proves the full Claude Agent SDK Session/resume/image path.

- [ ] **Step 3: Commit the reusable live compatibility test**

```bash
git add tests/live/test_qwen_multimodal_compat.py
git diff --cached --check
git commit -m "test: cover qwen multimodal compatibility"
```

- [ ] **Step 4: Run complete non-live verification**

Run:

```bash
node --test tests/js/*.cjs
uv run pytest -q
```

Expected: Node reports zero failures and pytest reports zero failures; opt-in live tests remain skipped in the normal pytest run.

- [ ] **Step 5: Replace the dynamic launchd service with `qwen3.7-plus`**

Run:

```bash
launchctl remove com.codex.claude-workspace-agent 2>/dev/null || true
launchctl submit -l com.codex.claude-workspace-agent -- /bin/zsh -lic '
  export WORKSPACES_ROOT=/Users/a110356/work/code/claude_workspace_mvp/workspaces
  export CLAUDE_SKILLS_ROOT=/Users/a110356/.claude/skills
  export ANTHROPIC_DOCS_MCP_URL=https://platform.claude.com/docs/mcp
  export OPENAI_DOCS_MCP_URL=https://developers.openai.com/mcp
  export CODEX_SECURITY_MCP_ENTRYPOINT=/Users/a110356/.codex/plugins/cache/openai-curated-remote/codex-security/0.1.11/mcp/server.mjs
  export DATA_ANALYTICS_MCP_ENTRYPOINT=/Users/a110356/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.8-13ceeea1f599/mcp/server.cjs
  export AHS_HIVE_QUERY_MCP_ENTRYPOINT=/Users/a110356/work/code/luxury_data/hive_query_mcp_server/server.py
  export CODEX_HOME=/Users/a110356/.codex
  export CLAUDE_MODEL=qwen3.7-plus
  export APP_HOST=127.0.0.1
  export APP_PORT=8765
  export APP_DATA_DIR=/Users/a110356/work/code/claude_workspace_mvp/data
  cd /Users/a110356/work/code/claude_workspace_mvp
  exec /opt/homebrew/bin/uv run workspace-agent
'
```

This command relies on the login shell for the existing `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`; it does not print or persist the key in the repository.

- [ ] **Step 6: Verify health and effective model after restart**

Run:

```bash
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:8765/api/health; then
    break
  fi
  sleep 1
done
curl -fsS http://127.0.0.1:8765/api/workspaces | \
  /Users/a110356/work/code/claude_workspace_mvp/.venv/bin/python -c '
import json, sys
items = json.load(sys.stdin)
print([(item["id"], item["model"], item["available"]) for item in items])
'
```

Expected:

```text
{"status":"ok","database":"ok","workspace_count":1,"valid_workspace_count":1}
[('example', 'qwen3.7-plus', True)]
```

- [ ] **Step 7: Perform the visible acceptance flow**

Open `http://127.0.0.1:8765`, then perform these exact actions:

1. Confirm the capability pill displays `qwen3.7-plus`.
2. Create a new Session.
3. Paste `/tmp/snake-game.png` through the system clipboard into the message input.
4. Confirm a thumbnail, generated clipboard filename, byte size, and remove button appear.
5. Send `描述这张图片，只用一句话。`.
6. Confirm the reply describes the snake-game screenshot.
7. Reload the page and confirm the historical user message still displays the image preview.
8. Ask the Agent to use `Read` on `/tmp/snake-game.png`; confirm the Turn completes without `Unexpected item type in content`.

Expected: both direct pasted-image and tool-returned-image paths complete on `qwen3.7-plus`.

---

## Final Verification Checklist

- [ ] `node --test tests/js/*.cjs` reports zero failures.
- [ ] `uv run pytest -q` reports zero failures.
- [ ] The opt-in Qwen compatibility test reports two HTTP 200 responses.
- [ ] The opt-in Claude Agent SDK smoke test completes Session, resume, and image Turns.
- [ ] `/api/health` is healthy after restart.
- [ ] `/api/workspaces` reports `qwen3.7-plus`.
- [ ] Text paste remains native and unchanged.
- [ ] Clipboard image paste uploads immediately and Turn JSON contains only attachment IDs.
- [ ] More than 5 mixed attachments are rejected by frontend, upload service, and Turn service.
- [ ] Switching Session clears pending previews and never shows them in another Session.
- [ ] Pending and historical image previews survive the expected lifecycle and mobile layout.
- [ ] `git status --short` contains no new uncommitted implementation files; unrelated pre-existing modifications remain untouched.
