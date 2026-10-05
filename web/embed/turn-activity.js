// Turn 执行时间线的归约逻辑。纯函数、无 DOM——实时（AG-UI 的 workspace.trace）
// 与回放（/api/sessions/{id}/messages）喂进来的形状一致，所以只有这一份实现。
//
// 一次提问不等于一个 Turn：模型每调一次前端工具，当前 Turn 就结束，前端执行完
// 再开一个新 Turn 续跑。所以这里按 tool_use_id 把这些 Turn 链回一条活动记录，
// 让计时条量的是"这次提问"而不是"其中一段"。

const TERMINAL = {
  "turn.completed": "completed",
  "turn.failed": "failed",
  "turn.outcome_unknown": "outcome_unknown",
  "turn.recovery_required": "recovery_required",
  "turn.failed_before_execution": "failed",
  "turn.cancelled_before_execution": "cancelled",
  "turn.cancelled": "cancelled",
  "turn.interrupted": "cancelled",
};

export function traceEntryFromRecord(record) {
  return {
    event_type: record.event_type,
    turn_id: record.turn_id,
    at: record.created_at,
    payload: record.payload || {},
  };
}

function span(from, to) {
  if (!from || !to) return null;
  const value = Date.parse(to) - Date.parse(from);
  return Number.isFinite(value) ? Math.max(0, value) : null;
}

function emptySegment(turnId) {
  return {
    turnId,
    startedAt: null,
    endedAt: null,
    status: "running",
    disconnectedAt: null,
    items: [],
    tokens: { input: 0, output: 0, cost: null },
  };
}

function sumDurations(items, kind) {
  return items
    .filter((item) => item.kind === kind && Number.isFinite(item.durationMs))
    .reduce((total, item) => total + item.durationMs, 0);
}

/** Union observed intervals so parallel Tool calls do not inflate wall-clock time. */
function intervalDuration(items) {
  const ranges = items.filter(item => Number.isFinite(item.durationMs)).map(item => {
    const end = Date.parse(item.completedAt || item.at);
    return [end - item.durationMs, end];
  }).filter(range => range.every(Number.isFinite)).sort((a, b) => a[0] - b[0]);
  if (!ranges.length) return items.length ? null : 0;
  let total = 0, start = ranges[0][0], end = ranges[0][1];
  for (const [nextStart, nextEnd] of ranges.slice(1)) {
    if (nextStart > end) { total += end - start; start = nextStart; }
    end = Math.max(end, nextEnd);
  }
  return total + end - start;
}

function recomputeStats(turn, now) {
  const stats = turn.stats;
  stats.totalMs = span(turn.startedAt, turn.endedAt || now || null);
  stats.toolMs = intervalDuration(turn.items.filter(item => item.kind === "tool"));
  stats.thinkingMs = sumDurations(turn.items, "thinking");
  stats.thinkingCount = turn.items.filter((item) => item.kind === "thinking").length;
  stats.toolCount = turn.items.filter((item) => item.kind === "tool").length;
  stats.toolRoundTripMs = intervalDuration(turn.items.filter(item => item.timingKind === "frontend_round_trip"));
  // Runtime, transport and user confirmation overlap; subtraction cannot isolate the model.
  stats.modelMs = null;
}

function newToolItem(payload, at, state) {
  return {
    kind: "tool",
    at,
    toolUseId: String(payload.tool_use_id),
    name: String(payload.name || "tool"),
    inputPreview: payload.input_preview == null ? "" : String(payload.input_preview),
    outputPreview: "",
    isError: false,
    durationMs: null,
    state,
  };
}

function newThinkingItem(index, at) {
  return {
    kind: "thinking",
    at,
    index,
    text: "",
    durationMs: null,
    truncated: false,
    done: false,
  };
}

export function createTraceReducer() {
  const segments = new Map();
  const toolOwner = new Map(); // tool_use_id -> 拥有该调用的 turn
  const parent = new Map(); // 续跑 turn -> 它接续的 turn

  function rootOf(turnId) {
    const seen = new Set();
    let current = turnId;
    while (parent.has(current) && !seen.has(current)) {
      seen.add(current);
      current = parent.get(current);
    }
    return current;
  }

  function segmentFor(entry) {
    const id = entry.turn_id;
    if (!id) return null;
    if (!segments.has(id)) segments.set(id, emptySegment(id));
    return segments.get(id);
  }

  function toolItem(segment, toolUseId) {
    return segment.items.find(
      (item) => item.kind === "tool" && item.toolUseId === toolUseId,
    );
  }

  // 只匹配未结束的块：同一个 turn 里 index 可以复用，收口后必须开新条目。
  function openThinkingItem(segment, index) {
    return segment.items.find(
      (item) => item.kind === "thinking" && item.index === index && !item.done,
    );
  }

  function push(entry) {
    const type = entry.event_type;
    const payload = entry.payload || {};
    const at = entry.at || null;

    if (type === "turn.started") {
      const segment = segmentFor(entry);
      if (!segment) return;
      segment.startedAt = payload.started_at || at;
      return;
    }

    if (TERMINAL[type]) {
      const segment = segmentFor(entry);
      if (!segment) return;
      segment.status = TERMINAL[type];
      segment.disconnectedAt = null;
      segment.endedAt = payload.completed_at || at;
      for (const item of segment.items) {
        // 有 started 无 completed 的调用是真实存在的一类缺陷，如实标出来，
        // 不要让它永远停在"执行中"。交给前端执行的除外——它的出参会在
        // 下一段回来。
        if (item.kind === "tool" && item.state === "running") item.state = "no-receipt";
        if (item.kind === "thinking") item.done = true;
      }
      if (segment.status === "failed" && payload.code) {
        segment.items.push({
          kind: "error",
          at,
          code: String(payload.code),
          message: String(payload.message || ""),
        });
      }
      return;
    }

    if (type === "tool.started") {
      const segment = segmentFor(entry);
      if (!segment || !payload.tool_use_id) return;
      if (toolItem(segment, payload.tool_use_id)) return;
      segment.items.push(newToolItem(payload, at, "running"));
      toolOwner.set(String(payload.tool_use_id), segment.turnId);
      return;
    }

    if (type === "tool.completed") {
      const segment = segmentFor(entry);
      if (!segment || !payload.tool_use_id) return;
      const toolUseId = String(payload.tool_use_id);
      const ownerId = toolOwner.get(toolUseId);
      // 前端工具的出参是在下一段补发的：写回原来那段，并把两段链起来。
      const owner = ownerId ? segments.get(ownerId) : segment;
      if (ownerId && ownerId !== segment.turnId) {
        const ownerRoot = rootOf(ownerId);
        if (ownerRoot !== segment.turnId) parent.set(segment.turnId, ownerId);
      }
      let item = toolItem(owner, toolUseId);
      if (!item) {
        item = newToolItem(payload, at, "running");
        owner.items.push(item);
        toolOwner.set(toolUseId, owner.turnId);
      }
      // 补发事件带的是占位名，别覆盖 tool.started 记下的真名。
      if (payload.name && payload.name !== "tool") item.name = String(payload.name);
      item.outputPreview =
        payload.output_preview == null ? "" : String(payload.output_preview);
      item.isError = Boolean(payload.is_error);
      item.completedAt = payload.completed_at || at;
      item.timingKind = payload.timing_kind || "tool";
      item.durationMs = Number.isFinite(payload.duration_ms) ? payload.duration_ms : null;
      item.state = item.isError ? "error" : "done";
      return;
    }

    if (type === "frontend_tool.deferred") {
      const segment = segmentFor(entry);
      if (!segment || !payload.tool_use_id) return;
      const item = toolItem(segment, payload.tool_use_id);
      if (item) {
        item.state = "deferred";
        // deferred 带的是契约里的公开名（dashboard.get_widget_config），比
        // tool.started 记下的 SDK 名（mcp__davinci_ui__dashboard__…）好读。
        if (payload.name) item.name = String(payload.name);
      } else {
        segment.items.push(newToolItem(payload, at, "deferred"));
        toolOwner.set(String(payload.tool_use_id), segment.turnId);
      }
      return;
    }

    if (type === "message.assistant.thinking.delta") {
      const segment = segmentFor(entry);
      if (!segment) return;
      const index = Number(payload.index || 0);
      let item = openThinkingItem(segment, index);
      if (!item) {
        item = newThinkingItem(index, at);
        segment.items.push(item);
      }
      item.text += String(payload.text || "");
      return;
    }

    if (type === "message.assistant.thinking") {
      const segment = segmentFor(entry);
      if (!segment) return;
      const index = Number(payload.index || 0);
      let item = openThinkingItem(segment, index);
      if (!item) {
        item = newThinkingItem(index, at);
        segment.items.push(item);
      }
      item.durationMs = Number.isFinite(payload.duration_ms) ? payload.duration_ms : null;
      item.truncated = Boolean(payload.truncated);
      item.done = true;
      return;
    }

    if (type === "turn.progress") {
      const segment = segmentFor(entry);
      if (!segment) return;
      segment.items.push({
        kind: "phase",
        at,
        phase: String(payload.phase || ""),
        message: String(payload.message || ""),
      });
      return;
    }

    if (type === "context.compacted") {
      const segment = segmentFor(entry);
      if (!segment) return;
      segment.items.push({
        kind: "compaction",
        at,
        trigger: String(payload.trigger || "auto"),
        preTokens: Number.isFinite(payload.pre_tokens) ? payload.pre_tokens : null,
      });
      return;
    }

    if (type === "usage.updated") {
      const segment = segmentFor(entry);
      if (!segment) return;
      segment.tokens = {
        input: Number(payload.input_tokens || 0),
        output: Number(payload.output_tokens || 0),
        cost: Number.isFinite(payload.cost_usd) ? payload.cost_usd : null,
      };
    }
  }

  /** 把链在一起的各段合成一条活动记录，键是链首 turn。 */
  function snapshot() {
    const chains = new Map();
    for (const segment of segments.values()) {
      const root = rootOf(segment.turnId);
      if (!chains.has(root)) chains.set(root, []);
      chains.get(root).push(segment);
    }

    const merged = new Map();
    for (const [rootId, parts] of chains) {
      const last = parts[parts.length - 1];
      const items = [];
      let inputTokens = 0;
      let outputTokens = 0;
      let costUsd = null;
      parts.forEach((part, index) => {
        if (index > 0) items.push({ kind: "segment", at: part.startedAt, index });
        items.push(...part.items);
        inputTokens += part.tokens.input;
        outputTokens += part.tokens.output;
        if (Number.isFinite(part.tokens.cost)) {
          costUsd = (costUsd || 0) + part.tokens.cost;
        }
      });

      const turn = {
        turnId: rootId,
        startedAt: parts[0].startedAt,
        endedAt: last.disconnectedAt || (last.status === "running" ? null : last.endedAt),
        status: last.disconnectedAt ? "disconnected" : last.status,
        lastTurnId: last.turnId,
        segments: parts.length,
        items,
        stats: {
          totalMs: null,
          toolMs: 0,
          thinkingMs: 0,
          modelMs: null,
          thinkingCount: 0,
          toolCount: 0,
          inputTokens,
          outputTokens,
          costUsd,
        },
      };
      recomputeStats(turn);
      merged.set(rootId, turn);
    }
    return merged;
  }

  /** Freeze local display without inventing a server-side terminal event. */
  function disconnect(turnId, at) {
    const segment = segments.get(turnId);
    if (segment?.status === "running" && !segment.disconnectedAt) {
      segment.disconnectedAt = at;
    }
  }

  return { push, snapshot, disconnect };
}

/** 运行中的 turn 要按"现在"重算总时长；渲染层每秒调用一次。 */
export function tickRunningTurn(turn, nowIso) {
  if (!turn || turn.status !== "running") return turn;
  recomputeStats(turn, nowIso);
  return turn;
}
