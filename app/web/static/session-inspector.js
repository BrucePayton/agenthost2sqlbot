(function sessionInspectorModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SessionInspector = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function factory() {
  const secretKeyPattern = /(?:authorization|api[_-]?key|token|secret|password|credential)/i;

  function isVisibleUserMessage(payload = {}) {
    return Boolean(
      String(payload.text || "").trim()
      || payload.attachments?.length
      || payload.file_references?.length
    );
  }

  function createToolTimelineState() {
    return {tools: new Map()};
  }

  function reduceEvent(state, eventType, payload = {}) {
    if (eventType === "frontend_tool.deferred") {
      const tool = {
        kind: "tool",
        id: payload.tool_use_id,
        name: payload.name || "Tool",
        status: "running",
        input: payload.arguments || {},
        output: null,
        isError: false,
      };
      state.tools.set(tool.id, tool);
      return {...tool};
    }
    if (eventType !== "message.user") return null;
    const results = Array.isArray(payload.tool_results) ? payload.tool_results : [];
    if (!results.length) return null;
    const result = results[0];
    const tool = state.tools.get(result.tool_call_id);
    if (tool) {
      tool.status = result.is_error ? "failed" : "completed";
      tool.output = result.content;
      tool.isError = Boolean(result.is_error);
    }
    return {
      kind: "tool-update",
      id: result.tool_call_id,
      status: result.is_error ? "failed" : "completed",
      output: result.content,
      isError: Boolean(result.is_error),
    };
  }

  function redactContext(value) {
    if (Array.isArray(value)) return value.map(redactContext);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key,
      secretKeyPattern.test(key) ? "[REDACTED]" : redactContext(item),
    ]));
  }

  function buildPerformanceTraces(records = []) {
    const events = records
      .map((record, index) => ({
        ...record,
        _index: index,
        _at: parseTimestamp(record.created_at),
      }))
      .filter((record) => Number.isFinite(record._at))
      .sort((left, right) => left._at - right._at || left._index - right._index);
    const traces = [];
    const turnTraces = new Map();
    const deferredTools = new Map();

    for (const event of events) {
      const payload = event.payload || {};
      let trace = turnTraces.get(event.turn_id);
      if (event.event_type === "message.user" && isVisibleUserMessage(payload)) {
        trace = createTrace(payload.text, event._at, payload.model, payload.effort);
        traces.push(trace);
        turnTraces.set(event.turn_id, trace);
      } else if (event.event_type === "message.user" && payload.tool_results?.length) {
        trace = deferredTools.get(payload.tool_results[0].tool_call_id)?.trace || trace;
        if (trace) turnTraces.set(event.turn_id, trace);
      }
      if (!trace) continue;
      registerRun(trace, event.turn_id);

      const steps = timingSteps(event, trace, deferredTools);
      for (const step of steps) appendTimingStep(trace, step, event._at);
      if (event.event_type === "frontend_tool.deferred" && payload.tool_use_id) {
        deferredTools.set(payload.tool_use_id, {
          trace,
          name: normalizeToolName(payload.name || "Tool"),
          startedAt: event._at,
        });
      }
    }

    for (const trace of traces) {
      trace.totalMs = Math.max(0, (trace.endedAt || trace.startedAt) - trace.startedAt);
      trace.slowestStep = trace.steps.reduce(
        (slowest, step) => !slowest || step.deltaMs > slowest.deltaMs ? step : slowest,
        null,
      );
    }
    return traces;
  }

  function createTrace(title, startedAt, model = null, effort = null) {
    return {
      title: String(title || "新请求").trim() || "新请求",
      startedAt,
      endedAt: startedAt,
      totalMs: 0,
      runIds: [],
      steps: [],
      slowestStep: null,
      model: model ?? null,
      effort: effort ?? null,
      _firstOutputRuns: new Set(),
    };
  }

  function registerRun(trace, turnId) {
    if (turnId && !trace.runIds.includes(turnId)) trace.runIds.push(turnId);
  }

  function appendTimingStep(trace, step, at) {
    const previousAt = trace.steps.at(-1)?.at ?? trace.startedAt;
    const item = {
      ...step,
      at,
      deltaMs: Math.max(0, at - previousAt),
      elapsedMs: Math.max(0, at - trace.startedAt),
    };
    trace.steps.push(item);
    trace.endedAt = Math.max(trace.endedAt, at);
  }

  function timingSteps(event, trace, deferredTools) {
    const payload = event.payload || {};
    const run = trace.runIds.indexOf(event.turn_id) + 1;
    switch (event.event_type) {
      case "message.user": {
        if (isVisibleUserMessage(payload)) {
          return [{kind: "user-request", label: "收到用户请求", run}];
        }
        return (payload.tool_results || []).map((result) => {
          const tool = deferredTools.get(result.tool_call_id);
          return {
            kind: "frontend-tool-result",
            label: `前端工具返回 · ${tool?.name || "Tool"}`,
            run,
            operationMs: tool ? Math.max(0, event._at - tool.startedAt) : null,
          };
        });
      }
      case "turn.started":
        return [{kind: "run-started", label: `Run ${run} 开始`, run}];
      case "turn.progress":
        return [{
          kind: `progress-${payload.phase || "unknown"}`,
          label: payload.message || payload.phase || "执行进度更新",
          run,
        }];
      case "tool.started":
        return [{
          kind: "tool-started",
          label: `模型调用工具 · ${normalizeToolName(payload.name || "Tool")}`,
          run,
        }];
      case "tool.completed":
        return [{
          kind: payload.is_error ? "tool-failed" : "tool-completed",
          label: `${payload.is_error ? "工具失败" : "工具完成"} · ${normalizeToolName(payload.name || "Tool")}`,
          run,
          operationMs: payload.duration_ms ?? null,
        }];
      case "frontend_tool.deferred":
        return [{
          kind: "frontend-tool-started",
          label: `下发前端工具 · ${normalizeToolName(payload.name || "Tool")}`,
          run,
        }];
      case "message.assistant.delta":
        if (trace._firstOutputRuns.has(event.turn_id)) return [];
        trace._firstOutputRuns.add(event.turn_id);
        return [{kind: "assistant-first-output", label: "收到模型首个输出", run}];
      case "message.assistant.completed":
        return [{kind: "assistant-completed", label: "模型回复生成完成", run}];
      case "context.compacted":
        return [{kind: "context-compacted", label: "上下文压缩完成", run}];
      case "usage.updated":
        return [{kind: "usage-updated", label: "用量统计已记录", run}];
      case "turn.completed":
        return [{kind: "run-completed", label: `Run ${run} 完成并落库`, run}];
      case "turn.failed":
      case "turn.cancelled":
      case "turn.interrupted":
        return [{kind: event.event_type, label: payload.message || event.event_type, run}];
      default:
        return [];
    }
  }

  function normalizeToolName(value) {
    const name = String(value || "Tool");
    const prefix = "mcp__davinci_ui__";
    return name.startsWith(prefix)
      ? name.slice(prefix.length).replaceAll("__", ".")
      : name;
  }

  function parseTimestamp(value) {
    if (typeof value !== "string" || !value.trim()) return NaN;
    const text = value.trim();
    const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text}Z`;
    return Date.parse(normalized);
  }

  return {
    buildPerformanceTraces,
    createToolTimelineState,
    isVisibleUserMessage,
    parseTimestamp,
    redactContext,
    reduceEvent,
  };
});
