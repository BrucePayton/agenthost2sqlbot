const assert = require("node:assert/strict");
const test = require("node:test");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");

async function protocol() {
  return import(pathToFileURL(path.join(root, "web/shared/davinci-protocol.js")));
}

async function mockStore() {
  return import(pathToFileURL(path.join(root, "web/davinci-mock/main.js")));
}

function dashboardContext() {
  return {
    pageType: "dashboard",
    resourceId: "1024",
    contextVersion: 3,
    supportedCapabilities: ["dashboard.capture_current_view"],
    supportedCommands: ["navigateTo"],
  };
}

test("dataset advertises the bounded Run tool catalog", async () => {
  const { toolsForHostContext } = await protocol();
  const tools = toolsForHostContext({
    pageType: "dataset",
    resourceId: null,
    contextVersion: 4,
    supportedCapabilities: [],
    supportedCommands: ["navigateTo"],
  });
  assert.deepEqual(tools.map((tool) => tool.name), [
    "dashboard.capture_current_view",
    "navigateTo",
  ]);
});

test("validation rejects stale, expired, and wrong-source messages", async () => {
  const {
    MESSAGE_TYPES,
    createDavinciEnvelope,
    validateDavinciEnvelope,
  } = await protocol();
  const source = {};
  const now = 1_000;
  const envelope = createDavinciEnvelope({
    messageType: MESSAGE_TYPES.UI_ACK,
    requestId: "request-1",
    toolCallId: "tool-1",
    nonce: "nonce-1",
    contextVersion: 3,
    payload: { status: "executed" },
    now,
  });
  const event = { origin: "http://127.0.0.1:4173", source };
  const expected = {
    expectedOrigin: "http://127.0.0.1:4173",
    expectedSource: source,
    nonce: "nonce-1",
    contextVersion: 3,
    now: now + 1,
  };

  assert.equal(validateDavinciEnvelope(envelope, event, expected).ok, true);
  assert.equal(
    validateDavinciEnvelope(envelope, event, { ...expected, contextVersion: 4 }).code,
    "CONTEXT_STALE",
  );
  assert.equal(
    validateDavinciEnvelope(envelope, event, { ...expected, now: now + 20_000 }).code,
    "ORIGIN_REJECTED",
  );
  assert.equal(
    validateDavinciEnvelope(envelope, { ...event, source: {} }, expected).code,
    "ORIGIN_REJECTED",
  );
});

test("dashboard tool catalog is exact", async () => {
  const { toolsForHostContext } = await protocol();
  assert.deepEqual(
    toolsForHostContext(dashboardContext()).map((tool) => tool.name),
    ["dashboard.capture_current_view", "navigateTo"],
  );
});

test("snapshot follows the live Store and increments context version", async () => {
  const { createDavinciStore } = await mockStore();
  const store = createDavinciStore({
    route: "/dashboard/1024",
    now: () => "2026-08-06T16:00:00.000Z",
  });

  store.setRegion("华东区");
  const snapshot = store.captureCurrentView();

  assert.equal(store.state.contextVersion, 2);
  assert.equal(snapshot.page.contextVersion, 2);
  assert.equal(snapshot.filters[0].value, "华东区");
  assert.equal(snapshot.metrics.itemCount, 4734);
});

test("invalid dashboard navigation does not mutate route or version", async () => {
  const { createDavinciStore } = await mockStore();
  const store = createDavinciStore({ route: "/datasets" });
  const before = structuredClone(store.state);

  const result = store.navigateTo({ destination: "dashboard", resourceId: "9999" });

  assert.equal(result.ok, false);
  assert.equal(result.error, "TARGET_NOT_FOUND");
  assert.equal(store.state.route, before.route);
  assert.equal(store.state.contextVersion, before.contextVersion);
});

test("dashboard navigation defaults to the only Mock dashboard", async () => {
  const { createDavinciStore } = await mockStore();
  const store = createDavinciStore({ route: "/datasets" });

  const result = store.navigateTo({ destination: "dashboard" });

  assert.equal(result.ok, true);
  assert.equal(store.state.route, "/dashboard/1024");
  assert.equal(result.ack.path, "/dashboard/1024");
  assert.equal(result.ack.contextVersion, 2);
});

test("valid navigation mutates route before returning its ACK", async () => {
  const { createDavinciStore } = await mockStore();
  const store = createDavinciStore({ route: "/dashboard/1024" });

  const result = store.navigateTo({ destination: "datasets" });

  assert.equal(result.ok, true);
  assert.equal(store.state.route, "/datasets");
  assert.equal(result.ack.path, "/datasets");
  assert.equal(result.ack.contextVersion, 2);
});
