const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { pathToFileURL } = require("node:url");
const test = require("node:test");

const moduleUrl = pathToFileURL(`${process.cwd()}/web/embed/identity.js`).href;

test("identity headers use only the opaque Host session token", async () => {
  const { createIdentityHeaders } = await import(moduleUrl);
  assert.deepEqual(createIdentityHeaders(" host-session-token "), {
    Authorization: "Bearer host-session-token",
  });
  assert.throws(() => createIdentityHeaders(""), /session token/);
});

test("session keys are scoped by actor and Workspace", async () => {
  const { sessionStorageKey } = await import(moduleUrl);
  assert.equal(
    sessionStorageKey("Actor-A", "workspace 1"),
    "davinci-agent:session:v1:Actor-A:workspace%201",
  );
  assert.notEqual(
    sessionStorageKey("Actor-A", "workspace 1"),
    sessionStorageKey("Actor-B", "workspace 1"),
  );
});

test("embed network and restoration paths use the scoped identity contract", () => {
  const source = readFileSync("web/embed/main.js", "utf8");
  assert.match(source, /headers: identityHeaders/);
  assert.match(source, /requestHeaders\(identityHeaders, options\)/);
  assert.match(source, /createIdentityHeaders\(embedConfig\.sessionToken\)/);
  assert.match(
    source,
    /sessionStorageKey\(embedConfig\?\.obId \|\| "embed", state\.workspaceId\)/,
  );
  assert.doesNotMatch(source, /davinci-mvp-session/);
});

test("embed exposes the active Session ID and copies that exact value", () => {
  const source = readFileSync("web/embed/main.js", "utf8");
  const template = readFileSync("app/web/templates/embed.html", "utf8");

  assert.match(template, /id="agentSessionIdentity"[^>]*hidden/);
  assert.match(template, /id="agentSessionId"/);
  assert.match(template, /id="agentCopySessionIdButton"/);
  assert.match(source, /elements\.sessionId\.textContent = state\.sessionId/);
  assert.match(source, /elements\.sessionIdentity\.hidden = !state\.sessionId/);
  assert.match(source, /copyTextWithFallback\(state\.sessionId\)/);
});
