const assert = require("node:assert/strict");
const test = require("node:test");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const moduleUrl = pathToFileURL(path.join(root, "web/embed/clipboard.js")).href;

function fakeDoc(execResult) {
  const el = { value: "", style: {}, focus() {}, select() {}, setAttribute() {} };
  return {
    created: [],
    body: { appendChild(node) { this.appended = node; }, removeChild() { this.removed = true; } },
    createElement(tag) { this.created.push(tag); return el; },
    execCommand(cmd) { this.command = cmd; return execResult; },
    _el: el,
  };
}

test("uses the Clipboard API when it is available", async () => {
  const { copyTextWithFallback } = await import(moduleUrl);
  const written = [];
  const how = await copyTextWithFallback("abc", {
    clipboard: { writeText: async (t) => { written.push(t); } },
    doc: fakeDoc(true),
  });
  assert.equal(how, "clipboard");
  assert.deepEqual(written, ["abc"]);
});

test("falls back to execCommand when the Clipboard API is missing", async () => {
  const { copyTextWithFallback } = await import(moduleUrl);
  const doc = fakeDoc(true);
  const how = await copyTextWithFallback("abc", { clipboard: undefined, doc });
  assert.equal(how, "exec-command");
  assert.equal(doc.command, "copy");
  assert.equal(doc._el.value, "abc");
});

test("keeps the original failure name and message when both paths fail", async () => {
  const { copyTextWithFallback } = await import(moduleUrl);
  const error = new Error("Document is not focused");
  error.name = "NotAllowedError";
  await assert.rejects(
    copyTextWithFallback("abc", {
      clipboard: { writeText: async () => { throw error; } },
      doc: fakeDoc(false),
    }),
    /NotAllowedError.*Document is not focused/
  );
});

test("reports a usable reason when the Clipboard API is absent and execCommand fails", async () => {
  const { copyTextWithFallback } = await import(moduleUrl);
  await assert.rejects(
    copyTextWithFallback("abc", { clipboard: undefined, doc: fakeDoc(false) }),
    /NotSupportedError.*HTTPS/
  );
});
