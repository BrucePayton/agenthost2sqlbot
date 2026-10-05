process.env.TZ = 'Asia/Shanghai'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

const root = path.resolve(__dirname, '../..')

function load() {
  return import(pathToFileURL(path.join(root, 'web/embed/turn-activity.js')))
}

function entry(eventType, payload, at) {
  return { event_type: eventType, turn_id: 't1', at, payload }
}

function feed(reducer, entries) {
  for (const item of entries) reducer.push(item)
  return reducer.snapshot().get('t1')
}

test('a completed turn reports total, tool and model time', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', { started_at: '2026-08-30T01:00:00+00:00' }, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'a', name: 'catalog.search_datasets', input_preview: '{"query":"结算"}' }, '2026-08-30T01:00:01+00:00'),
    entry('tool.completed', { tool_use_id: 'a', name: 'catalog.search_datasets', output_preview: '3 行', is_error: false, duration_ms: 1200 }, '2026-08-30T01:00:02+00:00'),
    entry('usage.updated', { input_tokens: 12000, output_tokens: 400, cost_usd: 0.08 }, '2026-08-30T01:00:09+00:00'),
    entry('turn.completed', { completed_at: '2026-08-30T01:00:10+00:00' }, '2026-08-30T01:00:10+00:00')
  ])

  assert.equal(turn.status, 'completed')
  assert.equal(turn.stats.totalMs, 10000)
  assert.equal(turn.stats.toolMs, 1200)
  assert.equal(turn.stats.modelMs, null)
  assert.equal(turn.stats.inputTokens, 12000)
  assert.equal(turn.stats.costUsd, 0.08)
  assert.equal(turn.items.length, 1)
  assert.deepEqual(
    { kind: turn.items[0].kind, name: turn.items[0].name, state: turn.items[0].state },
    { kind: 'tool', name: 'catalog.search_datasets', state: 'done' }
  )
  assert.equal(turn.items[0].inputPreview, '{"query":"结算"}')
  assert.equal(turn.items[0].outputPreview, '3 行')
})

test('thinking deltas concatenate and the completion event supplies the duration', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('message.assistant.thinking.delta', { index: 0, text: '先确认 ' }, '2026-08-30T01:00:01+00:00'),
    entry('message.assistant.thinking.delta', { index: 0, text: 'query 是否必填' }, '2026-08-30T01:00:02+00:00'),
    entry('message.assistant.thinking', { index: 0, duration_ms: 12000, chars: 14, truncated: false }, '2026-08-30T01:00:12+00:00')
  ])

  assert.equal(turn.items.length, 1)
  assert.equal(turn.items[0].kind, 'thinking')
  assert.equal(turn.items[0].text, '先确认 query 是否必填')
  assert.equal(turn.items[0].durationMs, 12000)
  assert.equal(turn.items[0].done, true)
  assert.equal(turn.stats.thinkingMs, 12000)
  assert.equal(turn.stats.thinkingCount, 1)
})

test('a thinking delta with no preceding start still opens an item', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('message.assistant.thinking.delta', { index: 3, text: '孤儿片段' }, '2026-08-30T01:00:01+00:00')
  ])

  assert.equal(turn.items.length, 1)
  assert.equal(turn.items[0].text, '孤儿片段')
  assert.equal(turn.items[0].done, false)
})

test('two thinking blocks stay separate items', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('message.assistant.thinking.delta', { index: 0, text: '第一段' }, '2026-08-30T01:00:01+00:00'),
    entry('message.assistant.thinking', { index: 0, duration_ms: 1000, truncated: false }, '2026-08-30T01:00:02+00:00'),
    entry('message.assistant.thinking.delta', { index: 2, text: '第二段' }, '2026-08-30T01:00:03+00:00'),
    entry('message.assistant.thinking', { index: 2, duration_ms: 2000, truncated: false }, '2026-08-30T01:00:04+00:00')
  ])

  assert.equal(turn.items.length, 2)
  assert.deepEqual(turn.items.map((item) => item.text), ['第一段', '第二段'])
  assert.equal(turn.stats.thinkingMs, 3000)
})

test('a tool that never reports back is marked no-receipt once the turn ends', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'b', name: 'ui.open_space_page' }, '2026-08-30T01:00:01+00:00'),
    entry('turn.completed', {}, '2026-08-30T01:00:04+00:00')
  ])

  assert.equal(turn.items[0].state, 'no-receipt')
})

test('a deferred frontend tool keeps its own state', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'c', name: 'dashboard.apply_widget_spec' }, '2026-08-30T01:00:01+00:00'),
    entry('frontend_tool.deferred', { tool_use_id: 'c', name: 'dashboard.apply_widget_spec' }, '2026-08-30T01:00:02+00:00'),
    entry('turn.completed', {}, '2026-08-30T01:00:03+00:00')
  ])

  assert.equal(turn.items.length, 1)
  assert.equal(turn.items[0].state, 'deferred')
})

test('an errored tool is distinguished from a successful one', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'd', name: 'catalog.resolve_field' }, '2026-08-30T01:00:01+00:00'),
    entry('tool.completed', { tool_use_id: 'd', name: 'catalog.resolve_field', output_preview: '字段不存在', is_error: true, duration_ms: 300 }, '2026-08-30T01:00:02+00:00')
  ])

  assert.equal(turn.items[0].state, 'error')
  assert.equal(turn.stats.toolMs, 300)
})

test('a failed turn carries its status', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('turn.failed', { code: 'claude_timeout', message: '超时' }, '2026-08-30T01:00:30+00:00')
  ])

  assert.equal(turn.status, 'failed')
  assert.equal(turn.stats.totalMs, 30000)
  assert.equal(turn.items.at(-1).kind, 'error')
  assert.equal(turn.items.at(-1).code, 'claude_timeout')
})

test('live and replay inputs produce the same snapshot', async () => {
  const { createTraceReducer, traceEntryFromRecord } = await load()

  const live = [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'a', name: 'Read' }, '2026-08-30T01:00:01+00:00'),
    entry('tool.completed', { tool_use_id: 'a', name: 'Read', output_preview: 'ok', is_error: false, duration_ms: 500 }, '2026-08-30T01:00:02+00:00'),
    entry('turn.completed', {}, '2026-08-30T01:00:03+00:00')
  ]
  const records = live.map((item) => ({
    event_type: item.event_type,
    turn_id: item.turn_id,
    created_at: item.at,
    payload: item.payload
  }))

  const liveTurn = feed(createTraceReducer(), live)
  const replayTurn = feed(createTraceReducer(), records.map(traceEntryFromRecord))

  assert.deepEqual(replayTurn, liveTurn)
})

test('unknown event types are ignored', async () => {
  const { createTraceReducer } = await load()

  const reducer = createTraceReducer()
  reducer.push(entry('message.assistant.delta', { text: '正文' }, '2026-08-30T01:00:00+00:00'))

  assert.equal(reducer.snapshot().size, 0)
})

test('a running turn measures against now until it ends', async () => {
  const { createTraceReducer, tickRunningTurn } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('tool.started', { tool_use_id: 'a', name: 'Read' }, '2026-08-30T01:00:01+00:00')
  ])

  assert.equal(turn.status, 'running')
  tickRunningTurn(turn, '2026-08-30T01:00:07+00:00')
  assert.equal(turn.stats.totalMs, 7000)
  assert.equal(turn.stats.modelMs, null)
})

test('progress phases and compaction land on the timeline', async () => {
  const { createTraceReducer } = await load()

  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00+00:00'),
    entry('turn.progress', { phase: 'connecting_mcp', message: '正在连接 2 个 MCP 服务' }, '2026-08-30T01:00:01+00:00'),
    entry('context.compacted', { trigger: 'auto', pre_tokens: 180000 }, '2026-08-30T01:00:02+00:00')
  ])

  assert.deepEqual(turn.items.map((item) => item.kind), ['phase', 'compaction'])
  assert.equal(turn.items[0].message, '正在连接 2 个 MCP 服务')
  assert.equal(turn.items[1].preTokens, 180000)
})

function crossEntry(eventType, payload, at, turnId) {
  return { event_type: eventType, turn_id: turnId, at, payload }
}

test('a frontend tool resolved in the next turn chains both turns into one activity', async () => {
  const { createTraceReducer } = await load()

  const reducer = createTraceReducer()
  // 第一段：提问 → 思考 → 把工具交给前端 → turn 结束
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:16:19+00:00', 'A'))
  reducer.push(crossEntry('message.assistant.thinking.delta', { index: 0, text: '该列一下仪表盘' }, '2026-08-30T12:16:26+00:00', 'A'))
  reducer.push(crossEntry('message.assistant.thinking', { index: 0, duration_ms: 2000 }, '2026-08-30T12:16:28+00:00', 'A'))
  reducer.push(crossEntry('tool.started', { tool_use_id: 'toolu_57', name: 'workspace.list_dashboards', input_preview: '{}' }, '2026-08-30T12:16:28+00:00', 'A'))
  reducer.push(crossEntry('frontend_tool.deferred', { tool_use_id: 'toolu_57', name: 'workspace.list_dashboards' }, '2026-08-30T12:16:29+00:00', 'A'))
  reducer.push(crossEntry('usage.updated', { input_tokens: 1780, output_tokens: 81, cost_usd: 0.015 }, '2026-08-30T12:16:29+00:00', 'A'))
  reducer.push(crossEntry('turn.completed', {}, '2026-08-30T12:16:29+00:00', 'A'))

  // 第二段：带着出参续跑
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:16:30+00:00', 'B'))
  reducer.push(crossEntry('tool.completed', { tool_use_id: 'toolu_57', name: 'tool', output_preview: '共 9 个仪表盘', is_error: false, duration_ms: null }, '2026-08-30T12:16:30+00:00', 'B'))
  reducer.push(crossEntry('usage.updated', { input_tokens: 2400, output_tokens: 300, cost_usd: 0.02 }, '2026-08-30T12:16:37+00:00', 'B'))
  reducer.push(crossEntry('turn.completed', {}, '2026-08-30T12:16:37+00:00', 'B'))

  const snapshot = reducer.snapshot()
  assert.equal(snapshot.size, 1, '两段应该合成一条')
  const turn = snapshot.get('A')

  assert.equal(turn.status, 'completed')
  assert.equal(turn.stats.totalMs, 18000, '总时长要跨两段：12:16:19 → 12:16:37')
  assert.equal(turn.segments, 2)

  // 出参落回第一段那个工具行，工具名不被占位符覆盖
  const tool = turn.items.find((item) => item.kind === 'tool')
  assert.equal(tool.name, 'workspace.list_dashboards')
  assert.equal(tool.state, 'done')
  assert.equal(tool.inputPreview, '{}')
  assert.equal(tool.outputPreview, '共 9 个仪表盘')

  // token 与成本按整次提问累加
  assert.equal(turn.stats.inputTokens, 4180)
  assert.equal(turn.stats.outputTokens, 381)
  assert.equal(Math.round(turn.stats.costUsd * 1000) / 1000, 0.035)

  // 段落分隔可见
  assert.equal(turn.items.filter((item) => item.kind === 'segment').length, 1)
})

test('a chained turn still running keeps the whole activity running', async () => {
  const { createTraceReducer } = await load()

  const reducer = createTraceReducer()
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:16:19+00:00', 'A'))
  reducer.push(crossEntry('tool.started', { tool_use_id: 't1', name: 'ui.open_space_page' }, '2026-08-30T12:16:20+00:00', 'A'))
  reducer.push(crossEntry('frontend_tool.deferred', { tool_use_id: 't1', name: 'ui.open_space_page' }, '2026-08-30T12:16:21+00:00', 'A'))
  reducer.push(crossEntry('turn.completed', {}, '2026-08-30T12:16:21+00:00', 'A'))
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:16:22+00:00', 'B'))
  reducer.push(crossEntry('tool.completed', { tool_use_id: 't1', name: 'tool', output_preview: 'ok', is_error: false }, '2026-08-30T12:16:22+00:00', 'B'))

  const turn = reducer.snapshot().get('A')
  assert.equal(turn.status, 'running', '最后一段还在跑，整条就还在跑')
  assert.equal(turn.endedAt, null)
})

test('an unrelated turn does not get chained', async () => {
  const { createTraceReducer } = await load()

  const reducer = createTraceReducer()
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:16:19+00:00', 'A'))
  reducer.push(crossEntry('turn.completed', {}, '2026-08-30T12:16:21+00:00', 'A'))
  reducer.push(crossEntry('turn.started', {}, '2026-08-30T12:17:00+00:00', 'B'))
  reducer.push(crossEntry('turn.completed', {}, '2026-08-30T12:17:05+00:00', 'B'))

  const snapshot = reducer.snapshot()
  assert.equal(snapshot.size, 2)
  assert.equal(snapshot.get('B').stats.totalMs, 5000)
})

test('three chained turns collapse into one root', async () => {
  const { createTraceReducer } = await load()

  const reducer = createTraceReducer()
  for (const [turnId, toolId, prevToolId, start, end] of [
    ['A', 'x1', null, '2026-08-30T12:00:00+00:00', '2026-08-30T12:00:05+00:00'],
    ['B', 'x2', 'x1', '2026-08-30T12:00:06+00:00', '2026-08-30T12:00:10+00:00'],
    ['C', null, 'x2', '2026-08-30T12:00:11+00:00', '2026-08-30T12:00:20+00:00']
  ]) {
    reducer.push(crossEntry('turn.started', {}, start, turnId))
    if (prevToolId) {
      reducer.push(crossEntry('tool.completed', { tool_use_id: prevToolId, name: 'tool', output_preview: 'ok', is_error: false }, start, turnId))
    }
    if (toolId) {
      reducer.push(crossEntry('tool.started', { tool_use_id: toolId, name: 'ui.thing' }, start, turnId))
      reducer.push(crossEntry('frontend_tool.deferred', { tool_use_id: toolId, name: 'ui.thing' }, start, turnId))
    }
    reducer.push(crossEntry('turn.completed', {}, end, turnId))
  }

  const snapshot = reducer.snapshot()
  assert.equal(snapshot.size, 1)
  const turn = snapshot.get('A')
  assert.equal(turn.segments, 3)
  assert.equal(turn.stats.totalMs, 20000)
})

test('a lost connection freezes the displayed time until a real terminal receipt', async () => {
  const { createTraceReducer, tickRunningTurn } = await load()
  const reducer = createTraceReducer()
  reducer.push(entry('turn.started', {}, '2026-09-05T00:00:00Z'))
  reducer.disconnect('t1', '2026-09-05T00:00:10Z')
  let turn = tickRunningTurn(reducer.snapshot().get('t1'), '2026-09-05T01:34:00Z')
  assert.equal(turn.status, 'disconnected')
  assert.equal(turn.stats.totalMs, 10000)
  reducer.push(entry('turn.completed', {}, '2026-09-05T00:00:20Z'))
  turn = reducer.snapshot().get('t1')
  assert.equal(turn.status, 'completed')
  assert.equal(turn.stats.totalMs, 20000)
})

test('unknown write outcomes are not reported as ordinary failures', async () => {
  const { createTraceReducer } = await load()
  const reducer = createTraceReducer()
  reducer.push(entry('turn.started', {}, '2026-09-05T00:00:00Z'))
  reducer.push(entry('turn.outcome_unknown', {}, '2026-09-05T00:00:10Z'))
  reducer.disconnect('t1', '2026-09-05T00:00:11Z')
  assert.equal(reducer.snapshot().get('t1').status, 'outcome_unknown')
})


test('frontend round trips are observed wall time and parallel intervals count once', async () => {
  const { createTraceReducer } = await load()
  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00Z'),
    entry('tool.started', { tool_use_id: 'a', name: 'page.read' }, '2026-08-30T01:00:01Z'),
    entry('tool.started', { tool_use_id: 'b', name: 'page.read' }, '2026-08-30T01:00:02Z'),
    entry('tool.completed', { tool_use_id: 'a', duration_ms: 10000, timing_kind: 'frontend_round_trip' }, '2026-08-30T01:00:11Z'),
    entry('tool.completed', { tool_use_id: 'b', duration_ms: 8000, timing_kind: 'frontend_round_trip' }, '2026-08-30T01:00:11Z'),
    entry('turn.completed', {}, '2026-08-30T01:00:12Z')
  ])
  assert.equal(turn.stats.totalMs, 12000)
  assert.equal(turn.stats.toolMs, 10000)
  assert.equal(turn.stats.toolRoundTripMs, 10000)
  assert.equal(turn.stats.modelMs, null)
})

test('a historical receipt without its monotonic clock has unknown tool duration', async () => {
  const { createTraceReducer } = await load()
  const turn = feed(createTraceReducer(), [
    entry('turn.started', {}, '2026-08-30T01:00:00Z'),
    entry('tool.started', { tool_use_id: 'a', name: 'page.read' }, '2026-08-30T01:00:01Z'),
    entry('tool.completed', { tool_use_id: 'a', duration_ms: null, timing_kind: 'frontend_round_trip' }, '2026-08-30T01:00:11Z'),
    entry('turn.completed', {}, '2026-08-30T01:00:12Z')
  ])
  assert.equal(turn.stats.toolMs, null)
  assert.equal(turn.stats.modelMs, null)
})
