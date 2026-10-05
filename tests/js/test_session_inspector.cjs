const assert = require("node:assert/strict");
const test = require("node:test");

const SessionInspector = require("../../app/web/static/session-inspector.js");

test("protocol-only continuation is hidden from the human timeline", () => {
  assert.equal(SessionInspector.isVisibleUserMessage({
    text: "",
    attachments: [],
    file_references: [],
    tool_results: [{tool_call_id: "tool-1", content: "ok", is_error: false}],
  }), false);
  assert.equal(SessionInspector.isVisibleUserMessage({
    text: "分析仪表盘",
    attachments: [],
    file_references: [],
    tool_results: [],
  }), true);
});

test("frontend tool is correlated with its continuation result", () => {
  const state = SessionInspector.createToolTimelineState();
  const deferred = SessionInspector.reduceEvent(state, "frontend_tool.deferred", {
    tool_use_id: "tool-1",
    name: "dashboard.get_widget_data",
    arguments: {widgetIds: ["88"]},
  });
  assert.deepEqual(deferred, {
    kind: "tool",
    id: "tool-1",
    name: "dashboard.get_widget_data",
    status: "running",
    input: {widgetIds: ["88"]},
    output: null,
    isError: false,
  });

  const completed = SessionInspector.reduceEvent(state, "message.user", {
    text: "",
    attachments: [],
    file_references: [],
    tool_results: [{tool_call_id: "tool-1", content: '{"status":"success"}', is_error: false}],
  });
  assert.deepEqual(completed, {
    kind: "tool-update",
    id: "tool-1",
    status: "completed",
    output: '{"status":"success"}',
    isError: false,
  });
});

test("persisted context is redacted recursively by secret-shaped keys", () => {
  assert.deepEqual(SessionInspector.redactContext({
    page_state: {resourceRevision: 7},
    authorization: "Bearer secret",
    nested: {api_key: "secret", token: "secret", url_env: "DAVINCI_URL"},
  }), {
    page_state: {resourceRevision: 7},
    authorization: "[REDACTED]",
    nested: {api_key: "[REDACTED]", token: "[REDACTED]", url_env: "DAVINCI_URL"},
  });
});

test("performance trace chains frontend tool continuations and computes observable timing", () => {
  const events = [
    event("run-1", 1, "2026-08-13T08:04:33.000Z", "message.user", {
      text: "分析当前仪表盘",
    }),
    event("run-1", 2, "2026-08-13T08:04:33.100Z", "turn.started"),
    event("run-1", 3, "2026-08-13T08:04:35.000Z", "turn.progress", {
      phase: "waiting_model",
      message: "已提交请求，等待模型响应",
    }),
    event("run-1", 4, "2026-08-13T08:04:37.000Z", "frontend_tool.deferred", {
      tool_use_id: "tool-1",
      name: "page.get_context",
    }),
    event("run-1", 5, "2026-08-13T08:04:37.100Z", "turn.completed"),
    event("run-2", 1, "2026-08-13T08:04:38.200Z", "message.user", {
      text: "",
      tool_results: [{tool_call_id: "tool-1", content: "ok", is_error: false}],
    }),
    event("run-2", 2, "2026-08-13T08:04:38.300Z", "turn.started"),
    event("run-2", 3, "2026-08-13T08:04:40.000Z", "turn.progress", {
      phase: "generating",
      message: "模型正在生成回复",
    }),
    event("run-2", 4, "2026-08-13T08:04:40.100Z", "message.assistant.delta", {text: "首"}),
    event("run-2", 5, "2026-08-13T08:04:40.200Z", "message.assistant.delta", {text: "段"}),
    event("run-2", 6, "2026-08-13T08:04:46.000Z", "message.assistant.completed", {
      text: "完整回复",
    }),
    event("run-2", 7, "2026-08-13T08:04:46.500Z", "turn.completed"),
  ];

  const [trace] = SessionInspector.buildPerformanceTraces(events);

  assert.equal(trace.title, "分析当前仪表盘");
  assert.deepEqual(trace.runIds, ["run-1", "run-2"]);
  assert.equal(trace.totalMs, 13_500);
  assert.equal(trace.steps.filter((step) => step.kind === "assistant-first-output").length, 1);
  const continuation = trace.steps.find((step) => step.kind === "frontend-tool-result");
  assert.equal(continuation.label, "前端工具返回 · page.get_context");
  assert.equal(continuation.deltaMs, 1_100);
  assert.equal(trace.slowestStep.label, "模型回复生成完成");
  assert.equal(trace.slowestStep.deltaMs, 5_900);
  assert.equal(trace.steps.at(-1).elapsedMs, 13_500);
});

test("performance traces keep independent user requests separate", () => {
  const traces = SessionInspector.buildPerformanceTraces([
    event("run-1", 1, "2026-08-13T08:00:00.000Z", "message.user", {text: "请求一"}),
    event("run-1", 2, "2026-08-13T08:00:01.000Z", "turn.completed"),
    event("run-2", 1, "2026-08-13T08:01:00.000Z", "message.user", {text: "请求二"}),
    event("run-2", 2, "2026-08-13T08:01:03.000Z", "turn.completed"),
  ]);

  assert.deepEqual(traces.map((trace) => trace.title), ["请求一", "请求二"]);
  assert.deepEqual(traces.map((trace) => trace.totalMs), [1_000, 3_000]);
});

test("naive database timestamps are interpreted as UTC before local display", () => {
  const [trace] = SessionInspector.buildPerformanceTraces([
    event("run-1", 1, "2026-08-13T10:02:51.000000", "message.user", {text: "本地时间"}),
    event("run-1", 2, "2026-08-13T10:02:52.000000", "turn.completed"),
  ]);

  assert.equal(trace.startedAt, Date.UTC(2026, 7, 13, 10, 2, 51));
  assert.equal(trace.totalMs, 1_000);
});

test("performance trace captures the model and effort used for the turn", () => {
  const [withOverrides] = SessionInspector.buildPerformanceTraces([
    event("run-1", 1, "2026-08-13T08:00:00.000Z", "message.user", {
      text: "请求一",
      model: "claude-sonnet-4-5",
      effort: "high",
    }),
    event("run-1", 2, "2026-08-13T08:00:01.000Z", "turn.completed"),
  ]);
  assert.equal(withOverrides.model, "claude-sonnet-4-5");
  assert.equal(withOverrides.effort, "high");

  const [withoutOverrides] = SessionInspector.buildPerformanceTraces([
    event("run-2", 1, "2026-08-13T08:01:00.000Z", "message.user", {text: "请求二"}),
    event("run-2", 2, "2026-08-13T08:01:01.000Z", "turn.completed"),
  ]);
  assert.equal(withoutOverrides.model, null);
  assert.equal(withoutOverrides.effort, null);
});

test("explicit timestamp offsets are preserved", () => {
  assert.equal(
    SessionInspector.parseTimestamp("2026-08-13T18:02:51+08:00"),
    Date.UTC(2026, 7, 13, 10, 2, 51),
  );
});

function event(turnId, sequence, createdAt, eventType, payload = {}) {
  return {
    turn_id: turnId,
    sequence,
    created_at: createdAt,
    event_type: eventType,
    payload,
  };
}
