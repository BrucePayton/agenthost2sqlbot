const assert = require("node:assert/strict");
const test = require("node:test");
const { pathToFileURL } = require("node:url");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");

async function loadRunner() {
  return import(pathToFileURL(path.join(root, "web/embed/frontend-tool-runner.js")));
}

class FakeAgent {
  constructor(toolCalls) {
    this.messages = [];
    this.state = null;
    this.runs = [];
    this.toolCalls = [...toolCalls];
    this.timeline = [];
  }

  addMessage(message) {
    this.messages.push(structuredClone(message));
  }

  setState(state) {
    this.state = structuredClone(state);
  }

  async runAgent(options, callbacks) {
    this.timeline.push(`run:${options.runId}:start`);
    this.runs.push({
      options: structuredClone(options),
      messages: structuredClone(this.messages),
      state: structuredClone(this.state),
    });
    const toolCall = this.toolCalls.shift();
    if (toolCall) {
      callbacks.onToolCallEndEvent({
        event: { toolCallId: toolCall.id },
        toolCallName: toolCall.name,
        toolCallArgs: toolCall.args,
      });
    }
    this.timeline.push(`run:${options.runId}:end`);
  }
}

test("a user Run refreshes PageState before exposing state and tools", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([]);
  const staleContext = {
    state: {
      schemaVersion: "davinci-page-state-v1",
      permissions: { canRead: true, canOperate: false, canPersist: false },
    },
    tools: [{ name: "page.get_context", description: "Read", parameters: {} }],
  };
  const writableContext = {
    profileId: "dashboard",
    catalogDigest: "contract-digest:dashboard",
    toolSetId: "contract-digest:dashboard:7",
    state: {
      schemaVersion: "davinci-page-state-v1",
      permissions: { canRead: true, canOperate: true, canPersist: true },
    },
    tools: [{ name: "dashboard.add_widget", description: "Create", parameters: {} }],
  };
  let contextRequests = 0;
  const bridge = {
    async requestContext() {
      contextRequests += 1;
      return writableContext;
    },
  };
  let nextId = 0;
  agent.setState(staleContext.state);
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `run-${++nextId}`,
    createMessageId: () => `message-${nextId}`,
    initialContext: staleContext,
    getTools: (context) => context.tools,
    buildAgentState: (context) => context.state,
  });

  await runner.runUserMessage("添加一个指标卡");

  assert.equal(contextRequests, 1);
  assert.equal(agent.runs[0].state.permissions.canPersist, true);
  assert.equal(agent.runs[0].options.tools[0].name, "dashboard.add_widget");
  assert.deepEqual(agent.runs[0].options.forwardedProps, {
    profile: "davinci-agui-native-v2",
    profileId: writableContext.profileId,
    catalogDigest: writableContext.catalogDigest,
    toolSetId: writableContext.toolSetId,
    toolSetChanges: 0,
    catalogDigestChanges: 0,
  });
});

test("frontend tools resume as new Runs with fresh state and tools", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-1", name: "ui.open_dashboard", args: { dashboardId: "88" } },
    { id: "tool-2", name: "dashboard.get_structure", args: {} },
  ]);
  const contexts = [
    { schemaVersion: "davinci-page-state-v1", routeRevision: "route-0-fresh" },
    { schemaVersion: "davinci-page-state-v1", routeRevision: "route-1" },
    { schemaVersion: "davinci-page-state-v1", routeRevision: "route-2" },
  ];
  const bridge = {
    async execute(name) {
      agent.timeline.push(`execute:${name}`);
      return { status: "executed", result: { ok: true, action: name } };
    },
    async requestContext() {
      return contexts.shift();
    },
  };
  let nextId = 0;
  let nextMessageId = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `00000000-0000-4000-8000-${String(++nextId).padStart(12, "0")}`,
    createMessageId: () => `message-${++nextMessageId}`,
    initialContext: { schemaVersion: "davinci-page-state-v1", routeRevision: "route-0" },
    getTools: (state) => [
      { name: `tool-for-${state.routeRevision}`, description: "tool", parameters: {} },
    ],
  });

  await runner.runUserMessage("打开海外数据并查看结构");

  assert.equal(agent.runs.length, 3);
  assert.notEqual(agent.runs[0].options.runId, agent.runs[1].options.runId);
  assert.notEqual(agent.runs[1].options.runId, agent.runs[2].options.runId);
  assert.equal(agent.runs[0].messages.at(-1).role, "user");
  assert.equal(agent.runs[0].state.routeRevision, "route-0-fresh");
  assert.equal(agent.runs[0].options.tools[0].name, "tool-for-route-0-fresh");
  assert.deepEqual(agent.runs[1].messages.at(-1), {
    id: "message-2",
    role: "tool",
    toolCallId: "tool-1",
    content: '{"ok":true,"action":"ui.open_dashboard"}',
  });
  assert.equal(agent.runs[1].state.routeRevision, "route-1");
  assert.equal(agent.runs[1].options.tools[0].name, "tool-for-route-1");
  assert.equal(agent.runs[2].messages.at(-1).toolCallId, "tool-2");
  assert.equal(agent.runs[2].state.routeRevision, "route-2");
  assert.deepEqual(agent.timeline.slice(0, 4), [
    `run:${agent.runs[0].options.runId}:start`,
    `run:${agent.runs[0].options.runId}:end`,
    "execute:ui.open_dashboard",
    `run:${agent.runs[1].options.runId}:start`,
  ]);
  assert.equal(agent.runs[0].options.forwardedProps.profile, "davinci-agui-native-v2");
});

test("each Run reports only real catalog identity transitions", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-1", name: "space.open", args: { spaceRef: "space:1" } },
    { id: "tool-2", name: "space.member.get_context", args: {} },
  ]);
  const contexts = [
    {
      profileId: "space",
      catalogDigest: "contract:space",
      toolSetId: "contract:space:1",
      state: {},
      tools: [],
    },
    {
      profileId: "space-dashboard",
      catalogDigest: "contract:space-dashboard",
      toolSetId: "contract:space-dashboard:2",
      state: {},
      tools: [],
    },
    {
      profileId: "space-dashboard",
      catalogDigest: "contract:space-dashboard",
      toolSetId: "contract:space-dashboard:2",
      state: {},
      tools: [],
    },
  ];
  const bridge = {
    async executeToolCall() {
      return { status: "success", data: {}, issues: [] };
    },
    async requestContext() {
      return contexts.shift();
    },
  };
  let id = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `run-${++id}`,
    createMessageId: () => `message-${id}`,
    initialContext: null,
    getTools: (context) => context.tools,
    buildAgentState: (context) => context.state,
  });

  await runner.runUserMessage("进入空间并读取成员");

  assert.deepEqual(
    agent.runs.map((run) => ({
      toolSetChanges: run.options.forwardedProps.toolSetChanges,
      catalogDigestChanges: run.options.forwardedProps.catalogDigestChanges,
    })),
    [
      { toolSetChanges: 0, catalogDigestChanges: 0 },
      { toolSetChanges: 1, catalogDigestChanges: 1 },
      { toolSetChanges: 0, catalogDigestChanges: 0 },
    ],
  );
});

test("frontend tool errors become ToolMessage errors and still resume", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-1", name: "dashboard.refresh_all", args: {} },
  ]);
  const bridge = {
    async execute() {
      const error = new Error("Dashboard is busy");
      error.code = "TOOL_NOT_READY";
      throw error;
    },
    async requestContext() {
      return { schemaVersion: "davinci-page-state-v1", dataRevision: "data-1" };
    },
  };
  let nextId = 0;
  let nextMessageId = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `00000000-0000-4000-8000-${String(++nextId).padStart(12, "0")}`,
    createMessageId: () => `message-${++nextMessageId}`,
    initialContext: { schemaVersion: "davinci-page-state-v1", dataRevision: "data-0" },
    getTools: () => [],
  });

  await runner.runUserMessage("刷新");

  const result = agent.runs[1].messages.at(-1);
  assert.equal(result.role, "tool");
  assert.equal(result.toolCallId, "tool-1");
  assert.equal(result.error, "TOOL_NOT_READY");
  assert.equal(JSON.parse(result.content).code, "TOOL_NOT_READY");
  assert.equal(agent.runs.length, 2);
});

test("V2 envelopes are preserved for the Agent and execution is correlated by ToolCall ID", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-v2", name: "dashboard.get_widget_data", args: { maxRows: 20 } },
  ]);
  const executions = [];
  const bridge = {
    async executeToolCall(toolCallId, name, args) {
      executions.push({ toolCallId, name, args });
      return { status: "partial", data: { widgets: [{ widgetId: "763" }] }, issues: [{ code: "DATA_NOT_READY" }] };
    },
    async requestContext() {
      return {
        state: { schemaVersion: "davinci-page-state-v1" },
        tools: [{ name: "dashboard.get_widget_data", description: "Read", parameters: {} }],
      };
    },
  };
  let id = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `00000000-0000-4000-8000-${String(++id).padStart(12, "0")}`,
    createMessageId: () => `message-${id}`,
    initialContext: { state: {}, tools: [] },
    getTools: (context) => context.tools,
    buildAgentState: (context) => context.state,
  });

  await runner.runUserMessage("读组件数据");

  assert.deepEqual(executions, [{
    toolCallId: "tool-v2",
    name: "dashboard.get_widget_data",
    args: { maxRows: 20 },
  }]);
  assert.deepEqual(JSON.parse(agent.runs[1].messages.at(-1).content), {
    status: "partial",
    data: { widgets: [{ widgetId: "763" }] },
    issues: [{ code: "DATA_NOT_READY" }],
  });
});

test("V2 error envelopes stay generic when the Agent resumes", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-v2-error", name: "dashboard.capture_current_view", args: {} },
  ]);
  const errorEnvelope = {
    status: "error",
    error: {
      code: "DATA_NOT_READY",
      message: "Dashboard data is still loading.",
      retryable: true,
      layer: "page",
    },
    issues: [],
  };
  const bridge = {
    async executeToolCall() {
      return errorEnvelope;
    },
    async requestContext() {
      return { state: { schemaVersion: "davinci-page-state-v1" }, tools: [] };
    },
  };
  let id = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge,
    createRunId: () => `00000000-0000-4000-8000-${String(++id).padStart(12, "0")}`,
    createMessageId: () => `message-${id}`,
    initialContext: { state: {}, tools: [] },
    getTools: (context) => context.tools,
    buildAgentState: (context) => context.state,
  });

  await runner.runUserMessage("读取数据");

  const result = agent.runs[1].messages.at(-1);
  assert.equal(result.error, "DATA_NOT_READY");
  assert.deepEqual(JSON.parse(result.content), errorEnvelope);
});

test("每个 Run 都带上父页预注入的 AG-UI context，缺省时为空数组", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "tool-v2", name: "dashboard.get_structure", args: {} },
  ]);
  const contexts = [
    [{ description: "dashboard_structure", value: "仪表盘：海外数据；组件数：9" }],
    [{ description: "dashboard_structure", value: "仪表盘：海外数据；组件数：10" }],
  ];
  const bridge = {
    async executeToolCall() {
      return { status: "success", data: {}, issues: [] };
    },
    async requestContext() {
      return {
        state: { schemaVersion: "davinci-page-state-v1" },
        tools: [],
        context: contexts.shift(),
      };
    },
  };
  let id = 0;
  const runnerOptions = {
    agent,
    bridge,
    createRunId: () => `00000000-0000-4000-8000-${String(++id).padStart(12, "0")}`,
    createMessageId: () => `message-${id}`,
    initialContext: { state: {}, tools: [], context: [] },
    getTools: (context) => context.tools,
    buildAgentState: (context) => context.state,
  };
  const runner = createFrontendToolRunner({
    ...runnerOptions,
    getContextItems: (context) => context?.context || [],
  });

  await runner.runUserMessage("看看当前仪表盘");

  assert.deepEqual(agent.runs[0].options.context, [
    { description: "dashboard_structure", value: "仪表盘：海外数据；组件数：9" },
  ]);
  assert.deepEqual(agent.runs[1].options.context, [
    { description: "dashboard_structure", value: "仪表盘：海外数据；组件数：10" },
  ]);

  const plainAgent = new FakeAgent([]);
  id = 0;
  const plainRunner = createFrontendToolRunner({
    ...runnerOptions,
    agent: plainAgent,
  });
  await plainRunner.runUserMessage("看看当前仪表盘");
  assert.deepEqual(plainAgent.runs[0].options.context, []);
});

test("abort stops the frontend tool loop and exposes the active runId", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([
    { id: "call-1", name: "dashboard.get_structure", args: {} },
    { id: "call-2", name: "dashboard.get_structure", args: {} },
  ]);
  let aborted = 0;
  agent.abortRun = () => { aborted += 1; };
  let runIdDuringRun = null;
  const runner = createFrontendToolRunner({
    agent,
    bridge: {
      requestContext: async () => ({ state: { schemaVersion: "davinci-page-state-v1" }, tools: [] }),
      executeToolCall: async () => {
        runIdDuringRun = runner.getCurrentRunId();
        runner.abort();
        return { status: "executed", result: {} };
      },
    },
    createRunId: (() => { let n = 0; return () => `run-${(n += 1)}`; })(),
    createMessageId: (() => { let n = 0; return () => `msg-${(n += 1)}`; })(),
    getTools: () => [],
    getContextItems: () => [],
  });

  assert.equal(runner.getCurrentRunId(), null);
  await runner.runUserMessage("hi");
  assert.equal(runIdDuringRun, "run-1");
  assert.equal(aborted, 1);
  assert.equal(agent.runs.length, 1, "aborting must stop before the second run");
  assert.equal(runner.getCurrentRunId(), null, "runId clears when the loop ends");
});

test("an aborted transport error resolves as an intentional stop", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([]);
  let rejectRun;
  let reportRunError;
  let markStarted;
  const started = new Promise((resolve) => { markStarted = resolve; });
  agent.runAgent = async (_, callbacks) => new Promise((_, reject) => {
    rejectRun = reject;
    reportRunError = callbacks.onRunErrorEvent;
    markStarted();
  });
  agent.abortRun = () => {
    reportRunError({ event: { message: "BodyStreamBuffer was aborted" } });
    rejectRun(new Error("BodyStreamBuffer was aborted"));
  };
  const runErrors = [];
  const runner = createFrontendToolRunner({
    agent,
    bridge: {
      requestContext: async () => ({ state: { schemaVersion: "davinci-page-state-v1" }, tools: [] }),
    },
    createRunId: () => "run-1",
    createMessageId: () => "msg-1",
    getTools: () => [],
    onRunError: (event) => runErrors.push(event.message),
  });

  const running = runner.runUserMessage("hi");
  await started;
  runner.abort();

  assert.deepEqual(await running, { aborted: true });
  assert.deepEqual(runErrors, []);
  assert.equal(runner.getCurrentRunId(), null);
});

test("executes several deferred read tool calls from one run in parallel", async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([]);
  agent.runAgent = async function (options, callbacks) {
    this.runs.push({
      options: structuredClone(options),
      messages: structuredClone(this.messages),
    });
    if (this.runs.length === 1) {
      callbacks.onToolCallEndEvent({
        event: { toolCallId: "c1" },
        toolCallName: "dashboard.get_widget_config",
        toolCallArgs: {},
      });
      callbacks.onToolCallEndEvent({
        event: { toolCallId: "c2" },
        toolCallName: "dashboard.get_errors",
        toolCallArgs: {},
      });
    }
  };
  const executed = [];
  let releaseExecutions;
  const executionGate = new Promise((resolve) => { releaseExecutions = resolve; });
  let markBothStarted;
  const bothStarted = new Promise((resolve) => { markBothStarted = resolve; });
  const runner = createFrontendToolRunner({
    agent,
    bridge: {
      requestContext: async () => ({ state: { schemaVersion: "davinci-page-state-v1" }, tools: [] }),
      executeToolCall: async (id) => {
        executed.push(id);
        if (executed.length === 2) markBothStarted();
        await executionGate;
        return { status: "executed", result: {} };
      },
    },
    createRunId: (() => { let n = 0; return () => `run-${(n += 1)}`; })(),
    createMessageId: (() => { let n = 0; return () => `msg-${(n += 1)}`; })(),
    getTools: () => [],
    getContextItems: () => [],
  });

  const running = runner.runUserMessage("hi");
  let startTimeout;
  await Promise.race([
    bothStarted,
    new Promise((_, reject) => {
      startTimeout = setTimeout(
        () => reject(new Error("deferred reads did not start concurrently")),
        1000,
      );
    }),
  ]);
  clearTimeout(startTimeout);
  releaseExecutions();
  await running;

  assert.deepEqual(executed.sort(), ["c1", "c2"]);
  assert.equal(agent.runs.length, 2, "both results are submitted in one continuation run");
  assert.deepEqual(
    agent.runs[1].messages.slice(-2).map((message) => message.toolCallId).sort(),
    ["c1", "c2"],
  );
});

test("HttpAgent submits every deferred read result in one continuation", async () => {
  const { HttpAgent } = await import("@ag-ui/client");
  const { createFrontendToolRunner } = await loadRunner();
  const requestBodies = [];
  const toolCalls = [
    ["c1", "dashboard.get_errors", {}],
    ["c2", "dashboard.get_widget_data", { waitUntilSettled: true }],
    ["c3", "dashboard.get_publish_readiness", {}],
  ];
  const encodeEvents = (events) => events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join("");
  const agent = new HttpAgent({
    url: "http://agent.test/api/ag-ui",
    threadId: "thread-1",
    fetch: async (_url, init) => {
      const body = JSON.parse(init.body);
      requestBodies.push(body);
      const events = [{
        type: "RUN_STARTED",
        threadId: body.threadId,
        runId: body.runId,
      }];
      if (requestBodies.length === 1) {
        for (const [toolCallId, toolCallName, args] of toolCalls) {
          events.push(
            {
              type: "TOOL_CALL_START",
              toolCallId,
              toolCallName,
              parentMessageId: `${body.runId}:assistant`,
            },
            {
              type: "TOOL_CALL_ARGS",
              toolCallId,
              delta: JSON.stringify(args),
            },
            { type: "TOOL_CALL_END", toolCallId },
          );
        }
      }
      events.push({
        type: "RUN_FINISHED",
        threadId: body.threadId,
        runId: body.runId,
        outcome: { type: "success" },
      });
      return new Response(encodeEvents(events), {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    },
  });
  const executed = [];
  let id = 0;
  const runner = createFrontendToolRunner({
    agent,
    bridge: {
      requestContext: async () => ({
        state: { schemaVersion: "davinci-page-state-v1" },
        tools: [],
      }),
      executeToolCall: async (toolCallId, name, args) => {
        executed.push({ toolCallId, name, args });
        return { status: "success", data: {}, issues: [] };
      },
    },
    createRunId: () => `00000000-0000-4000-8000-${String(++id).padStart(12, "0")}`,
    createMessageId: () => `message-${id}-${Math.random()}`,
    getTools: () => [],
    getContextItems: () => [],
    buildAgentState: (context) => context.state,
  });

  await runner.runUserMessage("检查当前仪表盘");

  assert.deepEqual(
    executed.map(({ toolCallId }) => toolCallId),
    ["c1", "c2", "c3"],
  );
  assert.equal(requestBodies.length, 2);
  assert.deepEqual(
    requestBodies[1].messages.slice(-3).map((message) => message.toolCallId),
    ["c1", "c2", "c3"],
  );
});

test('a RUN_ERROR rejects the request with its run identity instead of reporting completion', async () => {
  const { createFrontendToolRunner } = await loadRunner()
  const agent = new FakeAgent([])
  agent.runAgent = async (_input, callbacks) => {
    callbacks.onRunErrorEvent({ event: { code: 'session_busy', message: 'Busy' } })
  }
  const runner = createFrontendToolRunner({
    agent, bridge: { requestContext: async () => ({}) },
    createRunId: () => 'failed-run', createMessageId: () => 'message',
    getTools: () => []
  })
  await assert.rejects(runner.runUserMessage('hello'), error => {
    assert.equal(error.code, 'session_busy')
    assert.equal(error.runId, 'failed-run')
    return true
  })
})

test('a 401 during result delivery reuses the receipt and continuation ID without executing again', async () => {
  const { createFrontendToolRunner } = await loadRunner();
  const agent = new FakeAgent([{ id: 'write-once', name: 'space.create', args: {} }]);
  const original = agent.runAgent.bind(agent);
  agent.runAgent = async (...args) => {
    await original(...args);
    if (agent.runs.length === 2) throw Object.assign(new Error('expired'), { status: 401 });
  };
  let writes = 0;
  let next = 0;
  const changes = [];
  const runner = createFrontendToolRunner({
    agent,
    bridge: {
      requestContext: async () => ({ state: {}, tools: [] }),
      executeToolCall: async () => { writes++; return { status: 'success', data: { id: 'created' }, issues: [] }; }
    },
    createRunId: () => `run-${++next}`,
    createMessageId: () => `message-${++next}`,
    getTools: context => context.tools,
    onRecoveryChange: value => changes.push(value)
  });
  await assert.rejects(runner.runUserMessage('create'), /expired/);
  const pending = runner.getPendingToolResults();
  assert.equal(writes, 1);
  await runner.resumeToolResults(pending.messages, pending.continuationRunId, pending.originRunId);
  assert.equal(writes, 1);
  assert.equal(agent.runs[1].options.runId, agent.runs[2].options.runId);
  assert.deepEqual(agent.runs[1].messages, agent.runs[2].messages);
  assert.equal(changes.at(-1), null);
});
