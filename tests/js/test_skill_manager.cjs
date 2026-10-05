"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const SkillManager = require("../../app/web/static/skill-manager.js");

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.className = "";
    this.dataset = {};
    this._textContent = "";
    this.open = false;
    this.files = [];
    this.clickCount = 0;
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  emit(type, init = {}) {
    const event = {
      type,
      target: this,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      ...init,
    };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
    this.emit("close");
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  click() {
    this.clickCount += 1;
    this.focus();
    this.emit("click");
  }
}

class FakeDocument {
  constructor() {
    this.activeElement = null;
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
}

function createSkillManagerElements() {
  const document = new FakeDocument();
  const element = (tagName = "div") => document.createElement(tagName);
  return {
    document,
    backButton: element("button"),
    workspaceName: element(),
    changesNotice: element(),
    globalList: element(),
    globalLoading: element(),
    globalEmpty: element(),
    globalImportRoot: element(),
    globalImportButton: element("button"),
    globalArchiveButton: element("button"),
    globalDirectoryInput: element("input"),
    globalArchiveInput: element("input"),
    personalList: element(),
    personalLoading: element(),
    personalEmpty: element(),
    personalImportRoot: element(),
    personalImportButton: element("button"),
    personalArchiveButton: element("button"),
    personalDirectoryInput: element("input"),
    personalArchiveInput: element("input"),
    detailPanel: element("aside"),
    detailName: element(),
    detailDescription: element(),
    detailVersion: element(),
    detailHash: element(),
    detailSource: element(),
    detailUpdatedAt: element(),
    detailContent: element("pre"),
    detailManifest: element("ul"),
    importConflictDialog: element("dialog"),
    importConflictName: element(),
    importConflictExistingHash: element(),
    importConflictIncomingHash: element(),
    importRenameInput: element("input"),
    importOverwriteButton: element("button"),
    importRenameButton: element("button"),
    importCancelButton: element("button"),
    importConflictError: element(),
  };
}

function skill(overrides = {}) {
  const result = {
    id: "personal-1",
    scope: "workspace",
    workspace_id: "personal",
    name: "review",
    description: "Review changes",
    enabled: true,
    version_id: "version-1",
    version_no: 1,
    bundle_hash: "sha256:" + "a".repeat(64),
    origin: {type: "browser_directory", source_name: "review"},
    updated_at: "2026-08-19T12:00:00Z",
    ...overrides,
  };
  if (result.scope === "global" && !("workspace_id" in overrides)) {
    result.workspace_id = null;
  }
  return result;
}

const catalog = {
  global: [skill({id: "global-1", scope: "global", enabled: true})],
  personal: [skill({id: "personal-1", scope: "workspace", enabled: true})],
  effective_count: 2,
  changes_apply_to: "new_sessions",
};

function workspace(overrides = {}) {
  return {
    id: "personal",
    name: "Personal",
    kind: "personal",
    can_manage_skills: true,
    can_manage_global_skills: false,
    ...overrides,
  };
}

function directoryFiles(rootName = "brainstorming") {
  const root = new File(["root"], "SKILL.md", {type: "text/markdown"});
  Object.defineProperty(root, "webkitRelativePath", {value: `${rootName}/SKILL.md`});
  const script = new File(["script"], "run.py");
  Object.defineProperty(script, "webkitRelativePath", {
    value: `${rootName}/scripts/run.py`,
  });
  return [root, script];
}

function deferred() {
  let resolve;
  const promise = new Promise((onResolve) => { resolve = onResolve; });
  return {promise, resolve};
}

function createController({
  api = async () => catalog,
  elements = createSkillManagerElements(),
  getWorkspace = () => workspace(),
  onChanged = () => {},
  onEnter = () => {},
  onLeave = () => {},
  onError = () => {},
} = {}) {
  return {
    controller: SkillManager.createController({
      api,
      elements,
      getWorkspace,
      onChanged,
      onEnter,
      onLeave,
      onError,
    }),
    elements,
  };
}

test("open renders grouped catalog", async () => {
  const {controller, elements} = createController();
  assert.equal(await controller.open(), true);
  assert.equal(elements.workspaceName.textContent, "Personal");
  assert.equal(elements.globalList.children.length, 1);
  assert.equal(elements.personalList.children.length, 1);
  assert.match(elements.globalList.textContent, /review/u);
  assert.match(elements.personalList.textContent, /Review changes/u);
});

test("missing global management capability hides import controls", async () => {
  const {controller, elements} = createController();
  await controller.open();
  assert.equal(elements.globalImportRoot.hidden, true);
  assert.equal(elements.globalImportButton.hidden, true);
  assert.equal(elements.globalArchiveButton.hidden, true);
  assert.equal(elements.globalDirectoryInput.hidden, true);
  assert.equal(elements.globalArchiveInput.hidden, true);
});

test("global contributor sees folder and ZIP import controls", async () => {
  const {controller, elements} = createController({
    getWorkspace: () => workspace({can_manage_global_skills: true}),
  });
  await controller.open();
  assert.equal(elements.globalImportRoot.hidden, false);
  assert.equal(elements.globalImportButton.hidden, false);
  assert.equal(elements.globalArchiveButton.hidden, false);
  elements.globalImportButton.click();
  elements.globalArchiveButton.click();
  assert.equal(elements.globalDirectoryInput.clickCount, 1);
  assert.equal(elements.globalArchiveInput.clickCount, 1);
});

test("global toggle calls workspace setting endpoint", async () => {
  const calls = [];
  let currentCatalog = catalog;
  const {controller} = createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "PUT") {
        currentCatalog = {
          ...catalog,
          global: [{...catalog.global[0], enabled: false}],
          effective_count: 1,
        };
        return currentCatalog.global[0];
      }
      return currentCatalog;
    },
  });
  await controller.open();
  assert.equal(await controller.setGlobalEnabled("global-1", false), true);
  const request = calls.find((call) => call.options.method === "PUT");
  assert.equal(request.path, "/api/workspaces/personal/global-skills/global-1/setting");
  assert.deepEqual(JSON.parse(request.options.body), {enabled: false});
});

test("personal toggle sends expected hash", async () => {
  const calls = [];
  let currentCatalog = catalog;
  const {controller} = createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "PATCH") {
        currentCatalog = {
          ...catalog,
          personal: [{...catalog.personal[0], enabled: false}],
          effective_count: 1,
        };
        return currentCatalog.personal[0];
      }
      return currentCatalog;
    },
  });
  await controller.open();
  assert.equal(await controller.setPersonalEnabled(catalog.personal[0], false), true);
  const request = calls.find((call) => call.options.method === "PATCH");
  assert.equal(request.path, "/api/workspaces/personal/skills/personal-1/enabled");
  assert.deepEqual(JSON.parse(request.options.body), {
    expected_hash: catalog.personal[0].bundle_hash,
    enabled: false,
  });
});

test("personal archive sends expected hash and refreshes the personal list", async () => {
  const calls = [];
  let currentCatalog = catalog;
  const {controller, elements} = createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "DELETE") {
        currentCatalog = {...catalog, personal: [], effective_count: 1};
        return null;
      }
      return currentCatalog;
    },
  });
  await controller.open();
  assert.equal(await controller.archivePersonal(catalog.personal[0]), true);
  const request = calls.find((call) => call.options.method === "DELETE");
  assert.equal(request.path, "/api/workspaces/personal/skills/personal-1");
  assert.deepEqual(JSON.parse(request.options.body), {
    expected_hash: catalog.personal[0].bundle_hash,
  });
  assert.equal(elements.personalList.children.length, 0);
  assert.equal(elements.personalEmpty.hidden, false);
});

test("global archive is capability-gated and uses the compatibility admin endpoint", async () => {
  const withoutCapability = createController();
  await withoutCapability.controller.open();
  assert.doesNotMatch(withoutCapability.elements.globalList.textContent, /归档/u);

  const calls = [];
  let currentCatalog = catalog;
  const contributor = createController({
    getWorkspace: () => workspace({can_manage_global_skills: true}),
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "DELETE") {
        currentCatalog = {...catalog, global: [], effective_count: 1};
        return null;
      }
      return currentCatalog;
    },
  });
  await contributor.controller.open();
  assert.match(contributor.elements.globalList.textContent, /归档/u);
  assert.equal(await contributor.controller.archiveGlobal(catalog.global[0]), true);
  const request = calls.find((call) => call.options.method === "DELETE");
  assert.equal(request.path, "/api/admin/global-skills/global-1");
  assert.deepEqual(JSON.parse(request.options.body), {
    expected_hash: catalog.global[0].bundle_hash,
  });
});

test("personal folder upload defaults to the personal route", async () => {
  const calls = [];
  const imported = skill({id: "personal-new", name: "brainstorming"});
  let currentCatalog = catalog;
  const {controller, elements} = createController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "POST") {
        currentCatalog = {
          ...catalog,
          personal: [...catalog.personal, imported],
          effective_count: 3,
        };
        return {status: "created", skill: imported};
      }
      return currentCatalog;
    },
  });
  await controller.open();
  elements.personalDirectoryInput.value = "picked";
  assert.equal(await controller.importDirectory("personal", directoryFiles()), true);
  const request = calls.find((call) => call.options.method === "POST");
  assert.equal(request.path, "/api/workspaces/personal/skills/import-directory");
  assert.equal(request.options.body.get("on_conflict"), "fail");
  assert.deepEqual(
    request.options.body.getAll("files").map((file) => file.name),
    ["brainstorming/SKILL.md", "brainstorming/scripts/run.py"],
  );
  assert.match(elements.personalList.textContent, /brainstorming/u);
  assert.equal(elements.personalDirectoryInput.value, "");
});

test("global ZIP upload uses the admin route", async () => {
  const calls = [];
  const {controller, elements} = createController({
    getWorkspace: () => workspace({can_manage_global_skills: true}),
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "POST") {
        return {status: "already_imported", skill: catalog.global[0]};
      }
      return catalog;
    },
  });
  await controller.open();
  elements.globalArchiveInput.value = "picked";
  const archive = new Blob(["archive"], {type: "application/zip"});
  assert.equal(await controller.importArchive("global", archive), true);
  const request = calls.find((call) => call.options.method === "POST");
  assert.equal(request.path, "/api/admin/global-skills/import");
  assert.equal(await request.options.body.get("archive").text(), "archive");
  assert.equal(elements.globalArchiveInput.value, "");
});

test("same-name conflict retains files and supports overwrite/rename/cancel", async () => {
  const requests = [];
  const existingHash = catalog.personal[0].bundle_hash;
  const conflict = Object.assign(new Error("Skill already exists"), {
    code: "skill_import_conflict",
    details: {
      incoming_name: "brainstorming",
      existing_hash: existingHash,
      incoming_hash: "sha256:" + "b".repeat(64),
    },
  });
  const {controller, elements} = createController({
    api: async (_path, options = {}) => {
      if (!options.method) return catalog;
      const fields = {
        onConflict: options.body.get("on_conflict"),
        expectedHash: options.body.get("expected_hash"),
        targetName: options.body.get("target_name"),
        names: options.body.getAll("files").map((file) => file.name),
      };
      requests.push(fields);
      if (fields.onConflict === "fail") throw conflict;
      return {
        status: fields.onConflict === "rename" ? "renamed" : "overwritten",
        skill: catalog.personal[0],
      };
    },
  });
  await controller.open();

  elements.personalDirectoryInput.value = "first-pick";
  assert.equal(await controller.importDirectory("personal", directoryFiles()), false);
  assert.equal(elements.importConflictDialog.open, true);
  assert.equal(elements.personalDirectoryInput.value, "first-pick");
  assert.equal(await controller.overwriteImport(), true);
  assert.equal(requests[1].onConflict, "overwrite");
  assert.equal(requests[1].expectedHash, existingHash);
  assert.deepEqual(requests[1].names, requests[0].names);
  assert.equal(elements.personalDirectoryInput.value, "");
  assert.equal(elements.document.activeElement, elements.personalImportButton);

  elements.personalDirectoryInput.value = "second-pick";
  await controller.importDirectory("personal", directoryFiles("renamed"));
  assert.equal(await controller.renameImport("brainstorming-copy"), true);
  assert.equal(requests[3].onConflict, "rename");
  assert.equal(requests[3].targetName, "brainstorming-copy");
  assert.deepEqual(requests[3].names, requests[2].names);

  elements.personalDirectoryInput.value = "third-pick";
  await controller.importDirectory("personal", directoryFiles("cancelled"));
  assert.equal(controller.cancelImport(), true);
  assert.equal(requests.length, 5);
  assert.equal(elements.importConflictDialog.open, false);
  assert.equal(elements.personalDirectoryInput.value, "");
  assert.equal(elements.document.activeElement, elements.personalImportButton);
});

test("skill_changed disables stale overwrite and requires a fresh import", async () => {
  let importCalls = 0;
  const conflict = Object.assign(new Error("Skill already exists"), {
    code: "skill_import_conflict",
    details: {
      incoming_name: "brainstorming",
      existing_hash: catalog.personal[0].bundle_hash,
      incoming_hash: "sha256:" + "b".repeat(64),
    },
  });
  const changed = Object.assign(new Error("Skill changed"), {
    code: "skill_changed",
  });
  const {controller, elements} = createController({
    api: async (_path, options = {}) => {
      if (!options.method) return catalog;
      importCalls += 1;
      if (options.body.get("on_conflict") === "fail") throw conflict;
      throw changed;
    },
  });
  await controller.open();
  elements.personalDirectoryInput.value = "picked";
  await controller.importDirectory("personal", directoryFiles());

  assert.equal(await controller.overwriteImport(), false);
  assert.equal(importCalls, 2);
  assert.equal(elements.importOverwriteButton.disabled, true);
  assert.equal(elements.importRenameButton.disabled, false);
  assert.equal(elements.importCancelButton.disabled, false);
  assert.match(elements.importConflictError.textContent, /覆盖已禁用/u);
  assert.match(elements.importConflictError.textContent, /取消后重新选择/u);

  assert.equal(await controller.overwriteImport(), false);
  assert.equal(importCalls, 2);
  assert.equal(elements.personalDirectoryInput.value, "picked");
});

test("workspace switch invalidates late catalog/import/toggle responses", async (t) => {
  await t.test("catalog", async () => {
    const catalogRequest = deferred();
    const elements = createSkillManagerElements();
    let currentWorkspace = workspace();
    let leaves = 0;
    const {controller} = createController({
      elements,
      getWorkspace: () => currentWorkspace,
      api: async () => catalogRequest.promise,
      onLeave: () => { leaves += 1; },
    });
    const opening = controller.open();
    await Promise.resolve();
    currentWorkspace = workspace({id: "other", name: "Other"});
    assert.equal(await controller.refresh(), false);
    catalogRequest.resolve(catalog);
    assert.equal(await opening, false);
    assert.equal(leaves, 1);
    assert.equal(elements.globalList.children.length, 0);
  });

  for (const kind of ["import", "toggle"]) {
    await t.test(kind, async () => {
      const mutation = deferred();
      const elements = createSkillManagerElements();
      const changed = [];
      let currentWorkspace = workspace();
      const {controller} = createController({
        elements,
        getWorkspace: () => currentWorkspace,
        api: async (_path, options = {}) => options.method ? mutation.promise : catalog,
        onChanged: (...args) => changed.push(args),
      });
      await controller.open();
      elements.personalDirectoryInput.value = "picked";
      const pending = kind === "import"
        ? controller.importDirectory("personal", directoryFiles())
        : controller.setPersonalEnabled(catalog.personal[0], false);
      currentWorkspace = workspace({id: "other", name: "Other"});
      assert.equal(await controller.refresh(), false);
      mutation.resolve(kind === "import"
        ? {status: "created", skill: catalog.personal[0]}
        : {...catalog.personal[0], enabled: false});
      assert.equal(await pending, false);
      assert.deepEqual(changed, []);
      assert.equal(elements.changesNotice.hidden, true);
      if (kind === "import") {
        assert.equal(elements.personalDirectoryInput.value, "picked");
      }
    });
  }
});

test("detail is read-only and renders version/hash/Manifest", async () => {
  const detail = {
    ...catalog.personal[0],
    version_id: "version-3",
    version_no: 3,
    content: "# Read only",
    files: [{
      path: "references/policy.txt",
      mime_type: "text/plain",
      size_bytes: 6,
      sha256: "sha256:" + "c".repeat(64),
    }],
  };
  const {controller, elements} = createController({
    api: async (path) => path.includes("/skills/personal-1") ? detail : catalog,
  });
  await controller.open();
  assert.equal(await controller.selectDetail(catalog.personal[0]), true);
  assert.equal(elements.detailPanel.hidden, false);
  assert.equal(elements.detailContent.tagName, "PRE");
  assert.equal(elements.detailContent.textContent, "# Read only");
  assert.equal(elements.detailVersion.textContent, "v3 · version-3");
  assert.equal(elements.detailHash.textContent, detail.bundle_hash);
  assert.match(elements.detailSource.textContent, /browser directory/u);
  assert.match(elements.detailManifest.textContent, /references\/policy\.txt/u);
  assert.match(elements.detailManifest.textContent, new RegExp(detail.files[0].sha256, "u"));
});

test("successful mutation refreshes counts and displays new-session notice", async () => {
  let currentCatalog = catalog;
  let catalogReads = 0;
  const changed = [];
  const {controller, elements} = createController({
    api: async (_path, options = {}) => {
      if (options.method === "PUT") {
        currentCatalog = {
          ...catalog,
          global: [{...catalog.global[0], enabled: false}],
          effective_count: 1,
        };
        return currentCatalog.global[0];
      }
      catalogReads += 1;
      return currentCatalog;
    },
    onChanged: (workspaceId, _lifecycleIsCurrent, effectiveCount) => {
      changed.push({workspaceId, effectiveCount});
    },
  });
  await controller.open();
  assert.equal(await controller.setGlobalEnabled("global-1", false), true);
  assert.equal(catalogReads, 2);
  assert.deepEqual(changed, [{workspaceId: "personal", effectiveCount: 1}]);
  assert.equal(elements.changesNotice.hidden, false);
  assert.equal(elements.changesNotice.textContent, "仅对新会话生效");
});

test("Session creation rejects deferred A-B and A-B-A responses", async (t) => {
  for (const selections of [["A", "B"], ["A", "B", "A"]]) {
    await t.test(selections.join("-"), async () => {
      const response = deferred();
      const committed = [];
      let selection = {workspaceId: "A", epoch: 0};
      const coordinator = SkillManager.createSessionCreationCoordinator({
        api: async () => response.promise,
        getSelection: () => selection,
        commit: async (session) => committed.push(session),
      });
      const pending = coordinator.create();
      for (const [index, workspaceId] of selections.slice(1).entries()) {
        selection = {workspaceId, epoch: index + 1};
      }
      response.resolve({id: "session-from-A", workspace_id: "A"});
      assert.equal(await pending, false);
      assert.deepEqual(committed, []);
    });
  }
});

test("Session creation does not override a newer Session selection", async () => {
  const response = deferred();
  const committed = [];
  let selection = {workspaceId: "A", epoch: 0, sessionGeneration: 4};
  const coordinator = SkillManager.createSessionCreationCoordinator({
    api: async () => response.promise,
    getSelection: () => selection,
    commit: async (session) => committed.push(session),
  });
  const pending = coordinator.create();
  selection = {workspaceId: "A", epoch: 0, sessionGeneration: 5};
  response.resolve({id: "new-session", workspace_id: "A"});
  assert.equal(await pending, false);
  assert.deepEqual(committed, []);
});

test("candidate source drops previous and disabled Workspace Skills but pins Sessions", async () => {
  const applied = [];
  const calls = [];
  const first = deferred();
  const autocomplete = {
    setSession(payload) { applied.push({type: "session", ...payload}); },
    setSkills(payload) { applied.push({type: "skills", ...payload}); },
  };
  const candidates = SkillManager.createCandidateController({
    api: async (path) => {
      calls.push(path);
      if (path.includes("/workspaces/first/")) return first.promise;
      if (path.includes("/workspaces/second/")) {
        return {
          global: [skill({name: "global-enabled", scope: "global", enabled: true})],
          personal: [
            skill({name: "personal-enabled", enabled: true}),
            skill({name: "personal-disabled", enabled: false}),
          ],
          effective_count: 2,
          changes_apply_to: "new_sessions",
        };
      }
      return {items: [{name: "pinned", description: "Session snapshot"}]};
    },
    autocomplete,
    onError: () => {},
  });
  const oldLoad = candidates.selectWorkspace("first");
  await candidates.selectWorkspace("second");
  first.resolve({...catalog, personal: [skill({name: "stale", enabled: true})]});
  await oldLoad;
  await candidates.selectSession("session-1");
  const workspaceUpdates = applied.filter(
    (entry) => entry.type === "skills" && entry.sessionId.startsWith("workspace:"),
  );
  assert.deepEqual(
    workspaceUpdates.map((entry) => entry.skills.map((item) => item.name)),
    [["global-enabled", "personal-enabled"]],
  );
  assert.equal(calls.includes("/api/sessions/session-1/skills"), true);
  assert.deepEqual(applied.at(-1), {
    type: "skills",
    sessionId: "session-1",
    skills: [{name: "pinned", description: "Session snapshot"}],
  });
});

test("Workspace refresh coordinator rejects A-B and A-B-A stale responses", async () => {
  const first = deferred();
  const second = deferred();
  const third = deferred();
  const pending = [first, second, third];
  const applied = [];
  let selection = {workspaceId: "A", epoch: 0};
  const coordinator = SkillManager.createWorkspaceRefreshCoordinator({
    loadWorkspaces: async () => pending.shift().promise,
    getSelection: () => selection,
    apply: (workspaces, workspaceId) => applied.push({workspaces, workspaceId}),
  });
  const aToB = coordinator.refresh("A");
  selection = {workspaceId: "B", epoch: 1};
  first.resolve([{id: "A", skill_count: 2}]);
  assert.equal(await aToB, false);
  selection = {workspaceId: "A", epoch: 2};
  const aToBToA = coordinator.refresh("A");
  selection = {workspaceId: "B", epoch: 3};
  selection = {workspaceId: "A", epoch: 4};
  second.resolve([{id: "A", skill_count: 3}]);
  assert.equal(await aToBToA, false);
  let lifecycleCurrent = true;
  const closedManager = coordinator.refresh("A", () => lifecycleCurrent);
  lifecycleCurrent = false;
  third.resolve([{id: "A", skill_count: 4}]);
  assert.equal(await closedManager, false);
  assert.deepEqual(applied, []);
});

function createInstructionsElements() {
  const document = new FakeDocument();
  const element = (tagName = "div") => document.createElement(tagName);
  const resetButton = element("button");
  resetButton.textContent = "还原默认";
  return {
    section: element("section"),
    editor: element("textarea"),
    saveButton: element("button"),
    resetButton,
    status: element(),
    loading: element(),
  };
}

function createInstructionsController({
  api,
  elements = createInstructionsElements(),
  resetArmTimeoutMs,
} = {}) {
  return {
    controller: SkillManager.createInstructionsController({
      api,
      elements,
      getWorkspace: () => workspace(),
      isEditable: () => true,
      onChanged: () => {},
      onError: (error) => { throw error; },
      ...(resetArmTimeoutMs === undefined ? {} : {resetArmTimeoutMs}),
    }),
    elements,
  };
}

const customInstructions = {
  content: "# custom",
  source: "custom",
  content_hash: "sha256:" + "c".repeat(64),
  size_bytes: 8,
  max_bytes: 16384,
};

test("reset needs a second click and never relies on window.confirm", async () => {
  const calls = [];
  const {controller, elements} = createInstructionsController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "DELETE") return null;
      return customInstructions;
    },
  });
  // 沙箱 iframe 里 confirm() 直接返回 false；控制器不得依赖它。
  globalThis.confirm = () => { throw new Error("confirm must not be called"); };
  try {
    await controller.refresh();
    assert.equal(elements.resetButton.disabled, false);

    elements.resetButton.click();
    assert.equal(calls.filter((call) => call.options.method === "DELETE").length, 0);
    assert.equal(elements.resetButton.textContent, "确认还原");
    assert.match(elements.status.textContent, /再点一次/);

    elements.resetButton.click();
    await new Promise((resolve) => setImmediate(resolve));
    const request = calls.find((call) => call.options.method === "DELETE");
    assert.equal(request.path, "/api/workspaces/personal/instructions");
    assert.deepEqual(JSON.parse(request.options.body), {
      expected_hash: customInstructions.content_hash,
    });
    assert.equal(elements.resetButton.textContent, "还原默认");
    assert.match(elements.status.textContent, /已还原默认/);
  } finally {
    delete globalThis.confirm;
  }
});

test("editing the instructions disarms a pending reset", async () => {
  const calls = [];
  const {controller, elements} = createInstructionsController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "DELETE") return null;
      return customInstructions;
    },
  });
  await controller.refresh();
  elements.resetButton.click();
  assert.equal(elements.resetButton.textContent, "确认还原");

  elements.editor.value = "# custom edited";
  elements.editor.emit("input");
  assert.equal(elements.resetButton.textContent, "还原默认");

  elements.resetButton.click();
  assert.equal(calls.filter((call) => call.options.method === "DELETE").length, 0);
  assert.equal(elements.resetButton.textContent, "确认还原");
});

test("a pending reset times out back to the idle label", async () => {
  const calls = [];
  const {controller, elements} = createInstructionsController({
    api: async (path, options = {}) => {
      calls.push({path, options});
      if (options.method === "DELETE") return null;
      return customInstructions;
    },
    resetArmTimeoutMs: 10,
  });
  await controller.refresh();
  elements.resetButton.click();
  assert.equal(elements.resetButton.textContent, "确认还原");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(elements.resetButton.textContent, "还原默认");
  assert.equal(calls.filter((call) => call.options.method === "DELETE").length, 0);
});

test("new Session waits for a Skill save even after leaving management", async () => {
  const saved = deferred();
  const calls = [];
  const committed = [];
  const api = async (path) => {
    calls.push(path);
    if (path.endsWith("/setting")) return saved.promise;
    if (path.endsWith("/sessions")) return {id: "fresh", skills: []};
    return catalog;
  };
  const {controller} = createController({api});
  await controller.open();
  const saving = controller.setGlobalEnabled("global-1", false);
  controller.close();
  const coordinator = SkillManager.createSessionCreationCoordinator({
    api,
    getSelection: () => ({workspaceId: "personal", epoch: 0}),
    waitForSkills: (id) => controller.waitForPendingChanges(id),
    commit: async (session) => committed.push(session.id),
  });
  const creating = coordinator.create();
  await Promise.resolve();
  assert.equal(calls.some((path) => path.endsWith("/sessions")), false);
  saved.resolve({enabled: false});
  await saving;
  assert.equal(await creating, true);
  assert.deepEqual(committed, ["fresh"]);
});
