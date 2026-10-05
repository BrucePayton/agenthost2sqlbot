"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const {
  findTrigger,
  formatFileToken,
  replaceTrigger,
  syncReferences,
  createController,
} = require("../../app/web/static/composer-autocomplete.js");

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.attributes = new Map();
    this.children = [];
    this.listeners = new Map();
    this.hidden = true;
    this.id = "";
    this.className = "";
    this._textContent = "";
    this.value = "";
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.focusCount = 0;
    this.dispatchedEvents = [];
    this.scrollIntoViewCalls = [];
  }

  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  setAttribute(name, value) {
    const normalized = String(value);
    this.attributes.set(name, normalized);
    if (name === "id") this.id = normalized;
  }

  getAttribute(name) {
    if (name === "id" && this.id) return this.id;
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = children;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type, init = {}) {
    const event = {
      type,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      ...init,
    };
    for (const listener of this.listeners.get(type) ?? []) listener(event);
    return event;
  }

  dispatchEvent(event) {
    this.dispatchedEvents.push(event);
    return this.emit(event.type, event);
  }

  setSelectionRange(start, end) {
    this.selectionStart = start;
    this.selectionEnd = end;
  }

  focus() {
    this.focusCount += 1;
  }

  scrollIntoView(options) {
    this.scrollIntoViewCalls.push(options);
  }
}

class FakeDocument {
  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
}

function createElements() {
  const document = new FakeDocument();
  const input = document.createElement("textarea");
  input.id = "prompt";
  const menu = document.createElement("div");
  const status = document.createElement("div");
  return {input, menu, status};
}

function keyEvent(key, extra = {}) {
  return {
    key,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    ...extra,
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return {promise, resolve, reject};
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

test("Skill autocomplete CSS overrides generic role option styling", () => {
  const css = readFileSync(
    join(__dirname, "../../app/web/static/app.css"),
    "utf8",
  );
  const skillRule = css.match(
    /\.composer-autocomplete \[role="option"\]\.composer-autocomplete-skill\s*\{([^}]*)\}/u,
  );

  assert.ok(skillRule, "expected a Skill rule with higher specificity");
  assert.match(skillRule[1], /display:\s*flex;/u);
  assert.match(skillRule[1], /min-height:\s*38px;/u);
  assert.match(skillRule[1], /white-space:\s*nowrap;/u);
  assert.match(skillRule[1], /overflow:\s*hidden;/u);
});

test("skill trigger is limited to the leading token", () => {
  assert.deepEqual(findTrigger("  /bra", 6), {
    kind: "skill", query: "bra", start: 2, end: 6,
  });
  assert.equal(findTrigger("explain /bra", 12), null);
  assert.equal(findTrigger("https://example.test/", 21), null);
});

test("file trigger works after whitespace", () => {
  assert.deepEqual(findTrigger("review @out", 11), {
    kind: "file", query: "out", start: 7, end: 11,
  });
  assert.equal(findTrigger("mail@example.com", 16), null);
});

test("file token quotes whitespace and special characters", () => {
  assert.equal(formatFileToken("outputs/report.html"), "@outputs/report.html");
  assert.equal(formatFileToken("从0到1 Agent.md"), '@"从0到1 Agent.md"');
  assert.equal(formatFileToken('say"hi.txt'), '@"say\\"hi.txt"');
});

test("replacement preserves surrounding text and caret", () => {
  assert.deepEqual(
    replaceTrigger("review @out please", {start: 7, end: 11}, "@outputs/report.html"),
    {value: "review @outputs/report.html please", caret: 27},
  );
});

test("edited or deleted tokens remove structured references", () => {
  const selected = [
    {token: "@outputs/report.html", path: "outputs/report.html"},
    {token: "@notes.txt", path: "notes.txt"},
  ];
  assert.deepEqual(
    syncReferences("review @outputs/report.html", selected),
    [{token: "@outputs/report.html", path: "outputs/report.html"}],
  );
});

test("structured references require complete unquoted token boundaries", () => {
  const selected = [{token: "@foo", path: "foo"}];
  assert.deepEqual(syncReferences("@foobar", selected), []);
  assert.deepEqual(syncReferences("x@foo", selected), []);
  assert.deepEqual(syncReferences("@fooX", selected), []);
  assert.deepEqual(syncReferences("(@foo)", selected), []);
  assert.deepEqual(syncReferences("before @foo after", selected), selected);
});

test("short file tokens are not kept alive by longer selected paths", () => {
  const selected = [
    {token: "@foo", path: "foo"},
    {token: "@foo/bar", path: "foo/bar"},
  ];
  assert.deepEqual(syncReferences("review @foo/bar", selected), [selected[1]]);
  assert.deepEqual(syncReferences("review @foo", selected), [selected[0]]);
});

test("quoted file tokens preserve escapes but reject content edits", () => {
  const path = 'notes/say"hi\\there.txt';
  const selected = [{token: formatFileToken(path), path}];
  assert.deepEqual(
    syncReferences(`review ${selected[0].token} now`, selected),
    selected,
  );
  assert.deepEqual(
    syncReferences(`review ${selected[0].token.replace("hi", "bye")} now`, selected),
    [],
  );
  assert.deepEqual(syncReferences(`x${selected[0].token}`, selected), []);
  assert.deepEqual(syncReferences(`${selected[0].token}x`, selected), []);
});

test("controller filters Skills, renders ARIA options, and selects with the keyboard", () => {
  const {input, menu, status} = createElements();
  const skills = Array.from({length: 12}, (_, index) => ({
    name: `skill-${index}`,
    description: `NEEDLE description ${index}`,
  }));
  const controller = createController({
    input,
    menu,
    status,
    searchFiles: async () => [],
    onError: () => {},
  });
  controller.setSession({sessionId: "session-a", skills});
  input.value = "/needle";
  input.setSelectionRange(input.value.length, input.value.length);

  controller.handleInput();

  assert.equal(menu.hidden, false);
  assert.equal(menu.children.length, skills.length);
  assert.equal(menu.children[0].getAttribute("role"), "option");
  assert.match(menu.children[0].textContent, /skill-0/u);
  assert.match(menu.children[0].textContent, /NEEDLE description 0/u);
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
  assert.equal(input.getAttribute("aria-controls"), menu.id);
  assert.equal(input.getAttribute("aria-activedescendant"), menu.children[0].id);
  assert.equal(menu.children[0].getAttribute("aria-selected"), "true");
  const stableIds = menu.children.map((child) => child.id);

  const down = keyEvent("ArrowDown");
  assert.equal(controller.handleKeydown(down), true);
  assert.equal(down.defaultPrevented, true);
  assert.deepEqual(menu.children.map((child) => child.id), stableIds);
  assert.equal(menu.children[1].getAttribute("aria-selected"), "true");

  const enter = keyEvent("Enter");
  assert.equal(controller.handleKeydown(enter), true);
  assert.equal(enter.defaultPrevented, true);
  assert.equal(input.value, "/skill-1 ");
  assert.equal(input.selectionStart, input.value.length);
  assert.equal(input.focusCount, 1);
  assert.deepEqual(input.dispatchedEvents, []);
  assert.equal(menu.hidden, true);
  assert.equal(input.getAttribute("aria-controls"), null);

  const closedArrow = keyEvent("ArrowDown");
  assert.equal(controller.handleKeydown(closedArrow), false);
  assert.equal(closedArrow.defaultPrevented, false);
  assert.equal(controller.handleKeydown(keyEvent("a")), false);
});

test("keyboard navigation keeps the active Skill visible", () => {
  const {input, menu, status} = createElements();
  const controller = createController({
    input,
    menu,
    status,
    searchFiles: async () => [],
    onError: () => {},
  });
  controller.setSession({
    sessionId: "session-a",
    skills: Array.from({length: 20}, (_, index) => ({name: `skill-${index}`})),
  });
  input.value = "/";
  input.setSelectionRange(1, 1);
  controller.handleInput();
  for (const option of menu.children) option.scrollIntoViewCalls = [];

  controller.handleKeydown(keyEvent("ArrowDown"));

  assert.deepEqual(menu.children[1].scrollIntoViewCalls, [{block: "nearest"}]);
});

test("controller selects quoted file tokens by pointer and synchronizes ordered references", async () => {
  const {input, menu, status} = createElements();
  const results = [
    {path: "从0到1 Agent.md", description: "guide"},
    {path: "outputs/report.html"},
  ];
  const controller = createController({
    input,
    menu,
    status,
    searchFiles: async () => results,
    onError: () => {},
  });
  controller.setSession({sessionId: "session-a", skills: []});
  input.value = "review @out please";
  input.setSelectionRange(11, 11);

  controller.handleInput();
  assert.equal(status.textContent, "正在搜索");
  await delay(180);

  assert.equal(menu.children.length, 2);
  assert.match(menu.children[0].textContent, /从0到1 Agent\.md/u);
  const pointer = menu.children[0].emit("pointerdown");
  assert.equal(pointer.defaultPrevented, true);
  assert.equal(input.value, 'review @"从0到1 Agent.md" please');
  assert.deepEqual(controller.getFileReferences(), ["从0到1 Agent.md"]);

  input.value += " @out";
  input.setSelectionRange(input.value.length, input.value.length);
  controller.handleInput();
  await delay(180);
  controller.handleKeydown(keyEvent("ArrowDown"));
  controller.handleKeydown(keyEvent("Tab"));
  assert.deepEqual(controller.getFileReferences(), [
    "从0到1 Agent.md",
    "outputs/report.html",
  ]);

  input.value = "keep @outputs/report.html";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.deepEqual(controller.getFileReferences(), ["outputs/report.html"]);
});

test("controller ignores stale file responses and announces an empty current response", async () => {
  const {input, menu, status} = createElements();
  const requests = new Map();
  const controller = createController({
    input,
    menu,
    status,
    searchFiles(query) {
      const request = deferred();
      requests.set(query, request);
      return request.promise;
    },
    onError: () => {},
  });
  controller.setSession({sessionId: "session-a", skills: []});

  input.value = "@old";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  input.value = "@new";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);

  requests.get("new").resolve([{path: "new.txt"}]);
  await settle();
  assert.match(menu.children[0].textContent, /new\.txt/u);
  requests.get("old").resolve([{path: "old.txt"}]);
  await settle();
  assert.match(menu.children[0].textContent, /new\.txt/u);

  input.value = "@none";
  input.setSelectionRange(5, 5);
  controller.handleInput();
  await delay(180);
  requests.get("none").resolve([]);
  await settle();
  assert.equal(menu.hidden, true);
  assert.equal(status.textContent, "没有匹配文件");
});

test("controller aborts a superseded file request without surfacing an error", async () => {
  const {input, menu, status} = createElements();
  const requests = new Map();
  const errors = [];
  const controller = createController({
    input,
    menu,
    status,
    searchFiles(query, signal) {
      const request = deferred();
      requests.set(query, {request, signal});
      signal.addEventListener("abort", () => {
        request.reject(Object.assign(new Error("aborted"), {
          name: "AbortError",
          code: "file_reference_unavailable",
        }));
      }, {once: true});
      return request.promise;
    },
    onError(error) { errors.push(error); },
  });
  controller.setSession({sessionId: "session-a", skills: []});

  input.value = "@old";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  assert.ok(requests.get("old").signal, "search receives an abort signal");
  assert.equal(requests.get("old").signal.aborted, false);

  input.value = "@new";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.equal(requests.get("old").signal.aborted, true);
  await settle();
  assert.deepEqual(errors, []);
  assert.equal(status.textContent, "正在搜索");

  await delay(180);
  requests.get("new").request.resolve([{path: "new.txt"}]);
  await settle();
  assert.match(menu.children[0].textContent, /new\.txt/u);
  assert.deepEqual(errors, []);
});

test("controller aborts active file requests on reset, session change, and destroy", async () => {
  const actions = [
    ["reset", "reset", (controller) => controller.reset({keepSkills: true})],
    ["session change", "session-change", (controller) => (
      controller.setSession({sessionId: "session-b", skills: []})
    )],
    ["destroy", "destroy", (controller) => controller.destroy()],
  ];

  for (const [name, query, act] of actions) {
    const {input, menu, status} = createElements();
    let signal;
    const controller = createController({
      input,
      menu,
      status,
      searchFiles(_query, nextSignal) {
        signal = nextSignal;
        return new Promise(() => {});
      },
      onError: () => {},
    });
    controller.setSession({sessionId: "session-a", skills: []});
    input.value = `@${query}`;
    input.setSelectionRange(input.value.length, input.value.length);
    controller.handleInput();
    await delay(180);
    assert.ok(signal, `${name} search receives an abort signal`);
    assert.equal(signal.aborted, false, `${name} request starts active`);

    act(controller);

    assert.equal(signal.aborted, true, `${name} aborts the active request`);
  }
});

test("controller invalidates searches across sessions and disables unavailable file references once", async () => {
  const {input, menu, status} = createElements();
  const first = deferred();
  const calls = [];
  const errors = [];
  let mode = "deferred";
  const controller = createController({
    input,
    menu,
    status,
    searchFiles(query) {
      calls.push(query);
      if (mode === "deferred") return first.promise;
      return Promise.reject(Object.assign(new Error("unavailable"), {
        code: "file_reference_unavailable",
      }));
    },
    onError(error) { errors.push(error); },
  });
  controller.setSession({sessionId: "session-a", skills: []});
  input.value = "@old";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  controller.setSession({sessionId: "session-b", skills: []});
  first.resolve([{path: "old.txt"}]);
  await settle();
  assert.equal(menu.hidden, true);
  assert.equal(menu.children.length, 0);

  mode = "unavailable";
  input.value = "@one";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  assert.equal(status.textContent, "文件引用不可用");
  assert.equal(errors.length, 1);
  assert.equal(calls.length, 2);

  controller.reset({keepSkills: true});
  input.value = "@two";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  assert.equal(errors.length, 1);
  assert.equal(calls.length, 2);

  controller.reset();
  input.value = "@three";
  input.setSelectionRange(6, 6);
  controller.handleInput();
  await delay(180);
  assert.equal(errors.length, 1);
  assert.equal(calls.length, 2);

  controller.setSession({sessionId: "session-c", skills: []});
  input.value = "@four";
  input.setSelectionRange(5, 5);
  controller.handleInput();
  await delay(180);
  assert.equal(errors.length, 2);
  assert.equal(calls.length, 3);
});

test("controller updates current Skills without resetting file state", async () => {
  const {input, menu, status} = createElements();
  let unavailable = false;
  let searchCount = 0;
  const controller = createController({
    input,
    menu,
    status,
    searchFiles: async () => {
      searchCount += 1;
      if (unavailable) {
        throw Object.assign(new Error("unavailable"), {
          code: "file_reference_unavailable",
        });
      }
      return [{path: "Agent.md"}];
    },
    onError: () => {},
  });
  controller.setSession({sessionId: "session-a", skills: []});
  assert.equal(typeof controller.setSkills, "function");
  input.value = "@Age";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  await delay(180);
  assert.match(menu.children[0].textContent, /Agent\.md/u);

  controller.setSkills({
    sessionId: "session-a",
    skills: [{name: "summary", description: "Summarize"}],
  });
  assert.match(menu.children[0].textContent, /Agent\.md/u);
  controller.handleKeydown(keyEvent("Tab"));
  assert.deepEqual(controller.getFileReferences(), ["Agent.md"]);

  controller.setSkills({
    sessionId: "session-b",
    skills: [{name: "stale", description: "Wrong session"}],
  });
  input.value = "/stale";
  input.setSelectionRange(6, 6);
  controller.handleInput();
  assert.equal(menu.hidden, true);

  unavailable = true;
  input.value = "@missing";
  input.setSelectionRange(8, 8);
  controller.handleInput();
  await delay(180);
  assert.equal(searchCount, 2);
  assert.equal(status.textContent, "文件引用不可用");
  controller.setSkills({
    sessionId: "session-a",
    skills: [{name: "summary", description: "Summarize"}],
  });
  input.value = "@again";
  input.setSelectionRange(6, 6);
  controller.handleInput();
  await delay(180);
  assert.equal(searchCount, 2);

  input.value = "/sum";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.match(menu.children[0].textContent, /summary/u);
});

test("controller avoids IME selection and reset or destroy cancels pending work", async () => {
  const {input, menu, status} = createElements();
  let searchCount = 0;
  const controller = createController({
    input,
    menu,
    status,
    searchFiles: async () => {
      searchCount += 1;
      return [];
    },
    onError: () => {},
  });
  controller.setSession({
    sessionId: "session-a",
    skills: [{name: "alpha", description: "first"}],
  });

  input.emit("compositionstart");
  input.value = "/alp";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.equal(menu.hidden, true);
  assert.equal(controller.handleKeydown(keyEvent("Enter", {isComposing: true})), false);
  input.emit("compositionend");
  assert.equal(menu.hidden, false);

  controller.reset({keepSkills: true});
  assert.equal(menu.hidden, true);
  input.value = "/alp";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.equal(menu.children.length, 1);

  controller.reset();
  input.value = "/alp";
  input.setSelectionRange(4, 4);
  controller.handleInput();
  assert.equal(menu.hidden, true);

  input.value = "@pending";
  input.setSelectionRange(8, 8);
  controller.handleInput();
  controller.destroy();
  await delay(180);
  assert.equal(searchCount, 0);
});
