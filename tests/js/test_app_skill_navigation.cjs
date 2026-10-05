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
    this.style = {};
    this.files = [];
    this.open = false;
    this._textContent = "";
    this.classList = {
      add: (...names) => this.updateClasses(names, true),
      remove: (...names) => this.updateClasses(names, false),
    };
  }

  updateClasses(names, add) {
    const classes = new Set(this.className.split(/\s+/u).filter(Boolean));
    names.forEach((name) => add ? classes.add(name) : classes.delete(name));
    this.className = Array.from(classes).join(" ");
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

  async dispatch(type) {
    await Promise.all((this.listeners.get(type) || []).map((listener) => (
      listener({type, target: this, preventDefault() {}})
    )));
  }

  click() {
    for (const listener of this.listeners.get("click") || []) {
      listener({type: "click", target: this, preventDefault() {}});
    }
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  showModal() {
    this.open = true;
  }

  close() {
    this.open = false;
    for (const listener of this.listeners.get("close") || []) {
      listener({type: "close", target: this});
    }
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }
}

class FakeDocument {
  constructor() {
    this.activeElement = null;
    this.body = new FakeElement("body", this);
    this.body.dataset.maxFilesPerTurn = "5";
    this.elements = new Map();
    this.appShell = new FakeElement("div", this);
  }

  getElementById(id) {
    if (id === "debugIdentityPanel") return null;
    if (!this.elements.has(id)) {
      const element = new FakeElement("div", this);
      element.id = id;
      this.elements.set(id, element);
    }
    return this.elements.get(id);
  }

  querySelector(selector) {
    return selector === ".app-shell" ? this.appShell : null;
  }

  querySelectorAll() {
    return [];
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
}

function response(body) {
  return {
    ok: true,
    status: 200,
    headers: {get: () => "application/json"},
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function catalog() {
  return {
    global: [],
    personal: [],
    effective_count: 0,
    changes_apply_to: "new_sessions",
  };
}

function workspace(id, kind) {
  return {
    id,
    name: kind === "personal" ? "Personal" : "Team",
    kind,
    available: true,
    model: "claude-sonnet-4-5",
    effort: "medium",
    skill_count: 0,
    mcp_server_count: 0,
    personal_memory_enabled: false,
    can_manage_skills: true,
    can_manage_global_skills: false,
  };
}

function installBrowserHarness() {
  const document = new FakeDocument();
  const calls = [];
  const workspaces = [workspace("personal", "personal"), workspace("team", "team")];
  const saved = new Map();
  const globals = {
    document,
    window: {
      setInterval: () => 0,
      setTimeout: () => 0,
      clearTimeout() {},
    },
    localStorage: {
      getItem: (key) => saved.get(key) || null,
      setItem: (key, value) => saved.set(key, value),
    },
    MutationObserver: class {
      observe() {}
    },
    SkillManager,
    ComposerAutocomplete: {
      createController: () => ({
        handleInput() {},
        reset() {},
        setSession() {},
        setSkills() {},
      }),
    },
    ComposerPaste: {createController: () => ({})},
    ServiceHealth: {
      canExecute: (state) => state === "ready",
      deriveState: (transport, execution) => (
        transport === "ready" ? execution : transport
      ),
    },
    SessionInspector: {createToolTimelineState: () => new Map()},
    fetch: async (path) => {
      calls.push(path);
      if (path === "/api/health") return response({status: "ready"});
      if (path === "/api/workspaces") return response(workspaces);
      if (path.endsWith("/sessions")) return response([]);
      if (path.endsWith("/skills")) return response(catalog());
      throw new Error(`Unexpected request: ${path}`);
    },
  };
  const previous = new Map();
  for (const [name, value] of Object.entries(globals)) {
    previous.set(name, global[name]);
    global[name] = value;
  }
  return {
    calls,
    document,
    restore() {
      for (const [name, value] of previous) {
        if (value === undefined) delete global[name];
        else global[name] = value;
      }
    },
  };
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

async function initializeApp(t) {
  const harness = installBrowserHarness();
  const modulePath = require.resolve("../../app/web/static/app.js");
  t.after(() => {
    delete require.cache[modulePath];
    harness.restore();
  });
  const app = require(modulePath);

  await flush();
  assert.equal(typeof app.initialize, "function");
  await app.initialize();
  await flush();
  return {app, harness};
}

test("app.js initialization preserves Session identity across Skill navigation", async (t) => {
  const {app, harness} = await initializeApp(t);

  const selectedSession = {id: "session-1", title: "Selected", status: "idle"};
  const selectedSessionState = {...selectedSession};
  app.state.session = selectedSession;
  const skillsButton = harness.document.getElementById("skillsButton");
  const skillView = harness.document.getElementById("skillManagementView");
  const backButton = harness.document.getElementById("skillManagementBackButton");

  assert.equal(skillsButton.hidden, false);
  assert.equal(skillsButton.disabled, false);
  const personalCatalogReads = harness.calls.filter(
    (path) => path === "/api/workspaces/personal/skills",
  ).length;
  skillsButton.click();
  await flush();
  assert.equal(
    harness.calls.filter((path) => path === "/api/workspaces/personal/skills").length,
    personalCatalogReads + 1,
  );
  assert.equal(app.state.activeView, "skills");
  assert.equal(harness.document.appShell.hidden, true);
  assert.equal(skillView.hidden, false);
  assert.equal(app.state.session, selectedSession);
  assert.deepEqual(app.state.session, selectedSessionState);

  backButton.click();
  assert.equal(app.state.activeView, "chat");
  assert.equal(harness.document.appShell.hidden, false);
  assert.equal(skillView.hidden, true);
  assert.equal(app.state.session, selectedSession);
  assert.deepEqual(app.state.session, selectedSessionState);
});

test("app.js initialization hides and guards the team Skill entry", async (t) => {
  const {app, harness} = await initializeApp(t);
  const skillsButton = harness.document.getElementById("skillsButton");
  const skillView = harness.document.getElementById("skillManagementView");

  const workspaceSelect = harness.document.getElementById("workspaceSelect");
  workspaceSelect.value = "team";
  await workspaceSelect.dispatch("change");
  await flush();
  assert.equal(app.state.workspace.id, "team");
  assert.equal(skillsButton.hidden, true);
  assert.equal(skillsButton.disabled, true);

  const teamCatalogReads = harness.calls.filter(
    (path) => path === "/api/workspaces/team/skills",
  ).length;
  skillsButton.click();
  await flush();
  assert.equal(app.state.activeView, "chat");
  assert.equal(skillView.hidden, true);
  assert.equal(
    harness.calls.filter((path) => path === "/api/workspaces/team/skills").length,
    teamCatalogReads,
  );
});
