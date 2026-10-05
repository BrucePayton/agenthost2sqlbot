const assert = require("node:assert/strict");
const test = require("node:test");

const DebugIdentity = require("../../app/web/static/debug-identity.js");

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test("selected OBID is scoped to session storage and injected into headers", async () => {
  const storage = memoryStorage();
  const calls = [];
  const controller = DebugIdentity.createController({
    storage,
    isRunning: () => false,
    fetchJson: async (path) => {
      calls.push(path);
      return [{ob_id: "12901", display_name: "Davinci 12901", workspace_count: 1, session_count: 2}];
    },
  });

  const users = await controller.loadUsers();
  assert.equal(users.length, 1);
  assert.equal(controller.getObId(), "12901");
  assert.equal(storage.getItem("workspace-agent.debug-obid"), "12901");
  assert.deepEqual(controller.headers({Accept: "application/json"}), {
    Accept: "application/json",
    "X-Davinci-ObId": "12901",
  });
  assert.deepEqual(calls, ["/api/debug/users"]);
});

test("active Turn refuses identity switching", async () => {
  const controller = DebugIdentity.createController({
    storage: memoryStorage({"workspace-agent.debug-obid": "12901"}),
    isRunning: () => true,
    fetchJson: async () => [],
  });

  await assert.rejects(controller.select("other"), /Turn/);
  assert.equal(controller.getObId(), "12901");
});

test("Session lookup switches identity and returns its Workspace target", async () => {
  const selected = [];
  const controller = DebugIdentity.createController({
    storage: memoryStorage({"workspace-agent.debug-obid": "old"}),
    isRunning: () => false,
    fetchJson: async (path) => {
      assert.equal(path, "/api/debug/sessions/session-1");
      return {session_id: "session-1", workspace_id: "workspace-1", ob_id: "12901", title: "分析"};
    },
    onIdentityChanged: async (obId) => selected.push(obId),
  });

  const located = await controller.locateSession("session-1");
  assert.equal(controller.getObId(), "12901");
  assert.deepEqual(selected, ["12901"]);
  assert.equal(located.workspace_id, "workspace-1");
  assert.equal(located.identity_changed, true);
});
