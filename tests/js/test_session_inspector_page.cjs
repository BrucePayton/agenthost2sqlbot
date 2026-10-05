const assert = require("node:assert/strict");
const test = require("node:test");

const InspectorPage = require("../../app/web/static/session-inspector-page.js");


test("key event filter removes streaming deltas but preserves auditable actions", () => {
  const records = [
    {event_type: "message.assistant.delta"},
    {event_type: "turn.progress"},
    {event_type: "message.user"},
    {event_type: "tool.started"},
    {event_type: "tool.completed"},
    {event_type: "message.assistant.completed"},
    {event_type: "turn.failed"},
  ];

  assert.deepEqual(
    InspectorPage.filterEvents(records, "key").map((item) => item.event_type),
    [
      "message.user",
      "tool.started",
      "tool.completed",
      "message.assistant.completed",
      "turn.failed",
    ],
  );
  assert.equal(InspectorPage.filterEvents(records, "all").length, records.length);
});


test("event summary uses persisted human and tool fields", () => {
  assert.deepEqual(
    InspectorPage.summarizeEvent({
      event_type: "tool.completed",
      payload: {
        name: "mcp__davinci_ui__dashboard__get_widget_data",
        output_preview: "state=ready",
        is_error: false,
      },
    }),
    {
      title: "工具完成 · dashboard.get_widget_data",
      text: "state=ready",
      tone: "tool",
    },
  );
  assert.equal(
    InspectorPage.summarizeEvent({
      event_type: "message.user",
      payload: {text: "分析当前仪表盘"},
    }).text,
    "分析当前仪表盘",
  );
});


test("session search query is bounded and encoded", () => {
  assert.equal(
    InspectorPage.catalogPath({query: " user 12901 ", limit: 50, offset: 100}),
    "/api/inspector/sessions?query=user+12901&limit=50&offset=100",
  );
});


test("workspace filter is only included in the query when a workspace is selected", () => {
  assert.equal(
    InspectorPage.catalogPath({query: "", limit: 100, offset: 0, workspaceId: "ws-1"}),
    "/api/inspector/sessions?limit=100&offset=0&workspace_id=ws-1",
  );
  assert.equal(
    InspectorPage.catalogPath({query: "", limit: 100, offset: 0, workspaceId: ""}),
    "/api/inspector/sessions?limit=100&offset=0",
  );
});


test("export links and the curl command address the same unauthenticated endpoint", () => {
  const sessionId = "d8d9d1f1-a8b7-4b93-81d2-518891ee3fb3";

  assert.equal(
    InspectorPage.exportPath(sessionId),
    `/api/inspector/sessions/${sessionId}/export?format=md`,
  );
  assert.equal(
    InspectorPage.exportPath(sessionId, {turnId: "turn-1", format: "json"}),
    `/api/inspector/sessions/${sessionId}/export?format=json&turn=turn-1`,
  );
  assert.equal(
    InspectorPage.curlCommand("https://uat-agent.aihuishou.com", sessionId),
    'curl -sS "https://uat-agent.aihuishou.com/api/inspector/sessions/' +
      `${sessionId}/export?format=md" -o session-d8d9d1f1.md`,
  );
});
