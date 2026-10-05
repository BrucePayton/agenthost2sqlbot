const assert = require("node:assert/strict");
const test = require("node:test");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

test("real HttpAgent preserves 422 rejection metadata", async () => {
  const { HttpAgent } = await import("@ag-ui/client");
  const { requireAgUiResponse } = await import("../../web/embed/agui-http.js");
  const agent = new HttpAgent({
    url: "/api/ag-ui", threadId: "test-thread",
    fetch: async () => requireAgUiResponse(new Response(JSON.stringify({ error: {
      code: "invalid_request", message: "AG-UI input validation failed (state).",
      request_id: "request-1", details: { stage: "state" },
    } }), { status: 422 })),
  });
  await assert.rejects(agent.runAgent({ runId: "test-run" }), error => {
    assert.equal(error.status, 422);
    assert.equal(error.code, "invalid_request");
    assert.equal(error.requestId, "request-1");
    return true;
  });
});

test("422 restores the draft and history; continuation and transport errors require reconciliation", async () => {
  const { isUnacceptedAgUiRequest } = await import("../../web/embed/agui-http.js");
  const source = readFileSync("web/embed/main.js", "utf8");
  const sendSource = source.slice(source.indexOf("async function sendMessage(text)"), source.indexOf('window.addEventListener("message",'));
  for (const scenario of ["rejected", "skills-changed", "accepted", "network", "turn"]) {
    const unaccepted = scenario === "rejected" || scenario === "skills-changed";
    const error = Object.assign(new Error("failure"), {
      runId: "run-1", status: scenario === "network" ? undefined : scenario === "skills-changed" ? 409 : 422,
      code: scenario === "skills-changed" ? "session_skills_changed" : "invalid_request",
      details: { stage: scenario === "turn" ? "turn" : "state" },
    });
    const previous = [{ role: "assistant", content: "Earlier reply" }];
    const state = { sessionId: "session-1", agent: { messages: previous, setState() {} },
      runtimeContext: { state: {} }, toolRunner: {
        setCurrentContext() {}, async runUserMessage() {
          state.agent.messages.push({ role: "user", content: "new question" });
          if (scenario === "accepted") state.pendingInput.accepted = true;
          throw error;
        },
      } };
    let restored = false;
    let configured;
    const reported = [];
    const context = vm.createContext({ state, structuredClone, isUnacceptedAgUiRequest,
      skillController: {async waitForPendingChanges() {}},
      embedConfig: { protocolVersion: "agui-native-v2" }, setRunningUi() {}, setStatus() {},
      renderError() {}, markConnectionLost(id) { state.unresolvedRunId = id; },
      restorePendingInput() { restored = true; state.pendingInput = null; },
      configureAgent(messages) { configured = messages; },
      reportRunState(run) { reported.push(run); },
    });
    await vm.runInContext(`${sendSource}\nsendMessage("new question")`, context);
    assert.equal(restored, unaccepted);
    // 启动器气泡只在结果确实不可知时说「待确认」，被拒绝的请求是干脆的失败。
    assert.deepEqual(reported,
      ["running", unaccepted ? "failed" : "unknown"]);
    if (unaccepted) {
      assert.deepEqual(configured, [{ role: "assistant", content: "Earlier reply" }]);
      assert.equal(state.unresolvedRunId, undefined);
    } else assert.equal(state.unresolvedRunId, "run-1");
  }
});

test("successful response remains available for SSE consumption", async () => {
  const { requireAgUiResponse } = await import("../../web/embed/agui-http.js");
  const response = new Response("data: test\n\n");
  assert.equal(await requireAgUiResponse(response), response);
  assert.equal(await response.text(), "data: test\n\n");
});

test("stopping while Skill settings save prevents the queued message from running", async () => {
  const source = readFileSync("web/embed/main.js", "utf8");
  const sendSource = source.slice(source.indexOf("async function stopRun()"), source.indexOf('window.addEventListener("message",'));
  let finishSave;
  const saving = new Promise(resolve => { finishSave = resolve; });
  let runs = 0;
  const state = { sessionId: "session-1", workspaceId: "workspace-1",
    runtimeContext: { state: {} }, agent: { messages: [], setState() {} },
    toolRunner: { setCurrentContext() {}, abort() {}, getCurrentRunId() { return null; },
      async runUserMessage() { runs++; return { aborted: true }; } },
  };
  const context = vm.createContext({ state, structuredClone,
    skillController: { waitForPendingChanges() { return saving; } },
    embedConfig: { protocolVersion: "agui-native-v2" },
    setRunningUi(value) { state.running = value; }, setStatus() {}, reportRunState() {},
    markConnectionLost() {}, restorePendingInput() { state.pendingInput = null; },
  });
  const sending = vm.runInContext(`${sendSource}\nsendMessage("new question")`, context);
  await vm.runInContext("stopRun()", context);
  finishSave();
  await sending;
  assert.equal(runs, 0);
  assert.equal(state.running, false);
});
