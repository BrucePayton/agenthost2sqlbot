# Skill Autocomplete Scroll and Descriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show live `SKILL.md` descriptions for copied and external Session Skills in a compact single-row autocomplete, with browser coverage that reaches the last item in a 77-Skill list.

**Architecture:** Keep the Session Skills API unchanged. Extend the catalog's bounded frontmatter reader with an explicit external-link mode derived from the persisted Session snapshot, then render Skill name and description as safe, separate DOM nodes. Preserve the existing listbox scrolling behavior and cover its full range with Chromium tests.

**Tech Stack:** Python 3.12, FastAPI service layer, PyYAML, browser-native JavaScript and CSS, Node `node:test`, pytest, Playwright Chromium.

## Global Constraints

- Skill names and ordering continue to come from the persisted Session snapshot.
- Only Sessions whose snapshot has a non-empty `skills_root_env` may follow materialized external Skill links.
- Frontmatter reads remain bounded by `MAX_SKILL_FRONTMATTER_BYTES = 64 * 1024`.
- Invalid, missing, non-regular, malformed, or non-string descriptions fall back to `""`.
- Resolved external paths and Skill contents are never returned; the API remains `{name, description}`.
- Skill name and description are inserted with `textContent`, never `innerHTML`.
- Pointer, keyboard, filtering, ordering, and `/skill-name ` insertion behavior remain unchanged.
- Existing unrelated working-tree changes must not be staged or modified.

---

### Task 1: Read descriptions from deliberately linked external Skills

**Files:**
- Modify: `tests/test_session_catalog.py:6-53`
- Modify: `app/sessions/catalog.py:28-175`

**Interfaces:**
- Consumes: `list_skills(workspace_dir: Path, snapshot: dict[str, Any]) -> tuple[SkillCatalogItem, ...]` and `snapshot["skills_root_env"]`.
- Produces: `_read_skill_description(root: Path, skill_file: Path, *, allow_external_links: bool) -> str`.

- [ ] **Step 1: Write failing external-link catalog tests**

Add these tests after `test_list_skills_uses_snapshot_order_and_safe_frontmatter`:

```python
def test_list_skills_reads_external_materialized_link_description(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "external-skills" / "summary"
    external_skill.mkdir(parents=True)
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Summarize linked files\n---\n",
        encoding="utf-8",
    )
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)
    external_snapshot = snapshot()
    external_snapshot["skills_root_env"] = "CLAUDE_SKILLS_ROOT"

    assert list_skills(workspace, external_snapshot)[0].description == (
        "Summarize linked files"
    )


def test_list_skills_rejects_unexpected_link_for_copied_skills(
    tmp_path: Path,
) -> None:
    from app.sessions.catalog import list_skills

    workspace = tmp_path / "workspace"
    skill_root = workspace / ".claude" / "skills"
    skill_root.mkdir(parents=True)
    external_skill = tmp_path / "unexpected-summary"
    external_skill.mkdir()
    (external_skill / "SKILL.md").write_text(
        "---\nname: summary\ndescription: Must stay hidden\n---\n",
        encoding="utf-8",
    )
    (skill_root / "summary").symlink_to(external_skill, target_is_directory=True)

    assert list_skills(workspace, snapshot())[0].description == ""
```

- [ ] **Step 2: Run the focused tests and verify the external case fails**

Run:

```bash
uv run pytest \
  tests/test_session_catalog.py::test_list_skills_reads_external_materialized_link_description \
  tests/test_session_catalog.py::test_list_skills_rejects_unexpected_link_for_copied_skills \
  -q
```

Expected: one failure because the external description is `""`; the ordinary Session link rejection passes.

- [ ] **Step 3: Pass explicit link policy into the bounded reader**

Change `list_skills` to derive the policy once and pass it into each read:

```python
def list_skills(
    workspace_dir: Path, snapshot: dict[str, Any]
) -> tuple[SkillCatalogItem, ...]:
    root = _resolve_workspace_root(workspace_dir)
    allow_external_links = bool(snapshot.get("skills_root_env"))
    items = [
        SkillCatalogItem(
            name=str(name),
            description=_read_skill_description(
                root,
                root / ".claude" / "skills" / str(name) / "SKILL.md",
                allow_external_links=allow_external_links,
            ),
        )
        for name in snapshot.get("skills", [])
    ]
    return tuple(sorted(items, key=lambda item: item.name.casefold()))
```

Update the reader so copied Skills retain the existing link rejection, while an external Session resolves the linked file before opening it:

```python
def _read_skill_description(
    root: Path,
    skill_file: Path,
    *,
    allow_external_links: bool,
) -> str:
    try:
        if allow_external_links:
            readable_skill_file = skill_file.resolve(strict=True)
            if not readable_skill_file.is_file():
                return ""
        else:
            cursor = root
            for part in skill_file.relative_to(root).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    return ""
            readable_skill_file = skill_file
        with readable_skill_file.open("rb") as handle:
            raw = handle.read(MAX_SKILL_FRONTMATTER_BYTES + 1)
        if not raw.startswith(b"---\n"):
            return ""
        closing = raw.find(b"\n---\n", 4, MAX_SKILL_FRONTMATTER_BYTES + 1)
        if (
            closing < 0
            and len(raw) <= MAX_SKILL_FRONTMATTER_BYTES
            and raw.endswith(b"\n---")
        ):
            closing = len(raw) - len(b"\n---")
        if closing < 0:
            return ""
        frontmatter = yaml.safe_load(raw[4:closing].decode("utf-8"))
    except (OSError, RuntimeError, UnicodeError, yaml.YAMLError):
        return ""
    if not isinstance(frontmatter, dict):
        return ""
    description = frontmatter.get("description")
    return description.strip() if isinstance(description, str) else ""
```

- [ ] **Step 4: Run all catalog tests**

Run:

```bash
uv run pytest tests/test_session_catalog.py -q
```

Expected: all catalog tests pass.

- [ ] **Step 5: Commit the backend behavior**

```bash
git add app/sessions/catalog.py tests/test_session_catalog.py
git commit -m "fix: read linked skill descriptions"
```

---

### Task 2: Render compact Skill name and description elements

**Files:**
- Modify: `tests/js/test_composer_autocomplete.cjs:8-280`
- Modify: `app/web/static/composer-autocomplete.js:132-169`
- Modify: `app/web/static/app.css:608-648`

**Interfaces:**
- Consumes: autocomplete items shaped as `{kind: "skill", name: string, description: string}`.
- Produces: `.composer-autocomplete-skill`, `.composer-autocomplete-name`, and `.composer-autocomplete-description` elements; the description element has a `title` attribute with the full text.

- [ ] **Step 1: Make the fake DOM aggregate child text**

In the `FakeElement` constructor, replace:

```javascript
    this.id = "";
    this.textContent = "";
    this.value = "";
```

with:

```javascript
    this.id = "";
    this.className = "";
    this._textContent = "";
    this.value = "";
```

Immediately after the constructor, add:

```javascript
  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }
```

- [ ] **Step 2: Write a failing structured-rendering assertion**

In `controller filters Skills, renders ARIA options, and selects with the keyboard`, add these assertions after checking the first option text:

```javascript
  const skillOption = menu.children[0];
  assert.equal(
    skillOption.className,
    "composer-autocomplete-option composer-autocomplete-skill",
  );
  assert.equal(skillOption.children.length, 2);
  assert.equal(skillOption.children[0].className, "composer-autocomplete-name");
  assert.equal(skillOption.children[0].textContent, "skill-0");
  assert.equal(
    skillOption.children[1].className,
    "composer-autocomplete-description",
  );
  assert.equal(skillOption.children[1].textContent, "NEEDLE description 0");
  assert.equal(
    skillOption.children[1].getAttribute("title"),
    "NEEDLE description 0",
  );
```

- [ ] **Step 3: Run the Node test and verify it fails on missing child elements**

Run:

```bash
node --test tests/js/test_composer_autocomplete.cjs
```

Expected: failure because the current Skill option has no class or child elements.

- [ ] **Step 4: Render Skill text as safe, separate nodes**

In `renderItems`, replace the current label/description `textContent` assignment with:

```javascript
        const label = item.kind === "skill" ? item.name : item.path;
        if (item.kind === "skill") {
          option.className = (
            "composer-autocomplete-option composer-autocomplete-skill"
          );
          const name = menu.ownerDocument.createElement("span");
          name.className = "composer-autocomplete-name";
          name.textContent = label;
          option.appendChild(name);
          if (item.description) {
            const description = menu.ownerDocument.createElement("span");
            description.className = "composer-autocomplete-description";
            description.textContent = item.description;
            description.setAttribute("title", item.description);
            option.appendChild(description);
          }
        } else {
          option.className = "composer-autocomplete-option";
          option.textContent = item.description
            ? `${label}\n${item.description}`
            : label;
        }
```

- [ ] **Step 5: Add the compact one-row CSS**

After the generic option rule, add:

```css
.composer-autocomplete-skill {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-height: 38px;
  white-space: nowrap;
  overflow: hidden;
}

.composer-autocomplete-name {
  flex: 0 0 auto;
}

.composer-autocomplete-skill .composer-autocomplete-description {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

- [ ] **Step 6: Run the Node autocomplete tests**

Run:

```bash
node --test tests/js/test_composer_autocomplete.cjs
```

Expected: all autocomplete controller tests pass.

- [ ] **Step 7: Commit the frontend rendering**

```bash
git add app/web/static/composer-autocomplete.js app/web/static/app.css \
  tests/js/test_composer_autocomplete.cjs
git commit -m "feat: show skill autocomplete descriptions"
```

---

### Task 3: Cover full-list scrolling and final visual behavior in Chromium

**Files:**
- Modify: `tests/browser/test_workbench.py:1790-2000`

**Interfaces:**
- Consumes: `GET /api/sessions/{session_id}/skills` and the existing `live_url` browser fixture.
- Produces: browser regression coverage for 77 options, wheel scrolling, ArrowDown scrolling, one-row overflow styling, and full-description tooltip text.

- [ ] **Step 1: Add the large-catalog browser regression**

Add this test after `test_composer_autocompletes_skill_and_session_files`:

```python
@pytest.mark.asyncio
async def test_skill_autocomplete_reaches_last_item_and_compacts_descriptions(
    live_url: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        skills = [
            {
                "name": f"skill-{index:02d}",
                "description": f"Usage introduction for skill {index} " + ("x" * 80),
            }
            for index in range(77)
        ]

        async def route_skills(route) -> None:
            await route.fulfill(json={"items": skills})

        await page.route(re.compile(r".*/api/sessions/[^/]+/skills$"), route_skills)
        await page.goto(live_url)
        await page.locator("#newSessionButton").click()
        message = page.locator("#messageInput")
        menu = page.locator("#composerAutocomplete")
        options = menu.locator('[role="option"]')
        await message.fill("/")
        await expect(options).to_have_count(77)

        first_description = options.first.locator(
            ".composer-autocomplete-description"
        )
        await expect(first_description).to_have_attribute(
            "title", skills[0]["description"]
        )
        styles = await first_description.evaluate(
            """element => ({
              whiteSpace: getComputedStyle(element).whiteSpace,
              overflow: getComputedStyle(element).overflow,
              textOverflow: getComputedStyle(element).textOverflow,
            })"""
        )
        assert styles == {
            "whiteSpace": "nowrap",
            "overflow": "hidden",
            "textOverflow": "ellipsis",
        }

        await menu.hover()
        await page.mouse.wheel(0, 10_000)
        await page.wait_for_function(
            """menu => Math.abs(
              menu.scrollTop - (menu.scrollHeight - menu.clientHeight)
            ) <= 1""",
            arg=await menu.element_handle(),
        )
        wheel_position = await menu.evaluate(
            "menu => menu.scrollTop + menu.clientHeight"
        )
        scroll_height = await menu.evaluate("menu => menu.scrollHeight")
        assert abs(wheel_position - scroll_height) <= 1

        await message.fill("/")
        await message.focus()
        for _ in range(76):
            await message.press("ArrowDown")
        await expect(options.last).to_have_attribute("aria-selected", "true")
        bounds = await menu.evaluate(
            """menu => ({
              menuBottom: menu.getBoundingClientRect().bottom,
              lastBottom: menu.lastElementChild.getBoundingClientRect().bottom,
            })"""
        )
        assert bounds["lastBottom"] <= bounds["menuBottom"] + 1
        await browser.close()
```

- [ ] **Step 2: Run the browser regression**

Run:

```bash
uv run pytest \
  tests/browser/test_workbench.py::test_skill_autocomplete_reaches_last_item_and_compacts_descriptions \
  -q
```

Expected after Tasks 1 and 2: pass, with all 77 options rendered and both scrolling paths reaching the final option.

- [ ] **Step 3: Run all autocomplete-focused tests together**

Run:

```bash
uv run pytest tests/test_session_catalog.py -q
node --test tests/js/test_composer_autocomplete.cjs
uv run pytest tests/browser/test_workbench.py -q
```

Expected: all focused Python, Node, and Chromium tests pass without warnings or errors.

- [ ] **Step 4: Commit the browser regression**

```bash
git add tests/browser/test_workbench.py
git commit -m "test: cover full skill autocomplete scrolling"
```

---

### Task 4: Complete verification and handoff

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: all implementation commits from Tasks 1-3.
- Produces: fresh full-suite evidence and a clean task-scoped diff.

- [ ] **Step 1: Run the full automated suite**

Run:

```bash
uv run pytest -q
```

Expected: every non-live test passes.

- [ ] **Step 2: Verify JavaScript tests explicitly**

Run:

```bash
node --test tests/js/test_composer_autocomplete.cjs tests/js/test_composer_paste.cjs
```

Expected: all Node tests pass.

- [ ] **Step 3: Inspect only the task's commits and preserve unrelated edits**

Run:

```bash
git status --short
git log --oneline -5
git diff ac71d09..HEAD --check
git diff ac71d09..HEAD --stat
```

Expected: commits after `ac71d09` contain only the catalog, autocomplete
JavaScript/CSS, tests, and this plan/spec documentation. Pre-existing modifications to
`.env.example`, `.gitignore`, `README.md`, `tests/test_workspaces.py`, and
`workspaces/example/workspace.yaml` remain unstaged and unchanged by this work.
