const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

const root = path.resolve(__dirname, '../..')

class FakeWindow {
  constructor() { this.listeners = new Set() }
  addEventListener(type, listener) { if (type === 'message') this.listeners.add(listener) }
  removeEventListener(type, listener) { if (type === 'message') this.listeners.delete(listener) }
  emit(event) { for (const listener of this.listeners) listener(event) }
}

test('subscription presentation requires negotiated support and forwards only bounded live identity', async () => {
  const { createHostBridgeV2 } = await import(pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js')))
  const win = new FakeWindow()
  const sent = []
  const parent = { postMessage: m => sent.push(m) }
  const bridge = createHostBridgeV2({ parentOrigin: 'https://parent.test',
    parentWindow: parent, windowObject: win })
  const notice = { taskId: 'draft-1', runId: 'run-1', noticeId: 'notice-1',
    revision: 2, step: 'datasets', text: '正在查找昨日成交量' }
  assert.equal(bridge.sendSubscriptionStage(notice), false)
  const ready = bridge.start()
  const hello = sent[0]
  win.emit({ origin: 'https://parent.test', source: parent, data: { ...hello,
    type: 'DAVINCI_AGENT_BRIDGE_READY', bridgeNonce: 'nonce', pageInstanceId: 'page',
    subscriptionStages: true } })
  await ready
  assert.equal(bridge.sendSubscriptionStage({ ...notice, taskId: '' }), false)
  assert.equal(bridge.sendSubscriptionStage({ ...notice, revision: 0 }), false)
  assert.equal(bridge.sendSubscriptionStage({ ...notice, step: 'save' }), false)
  assert.equal(bridge.sendSubscriptionStage(notice), true)
  assert.equal(bridge.sendSubscriptionStage({ ...notice, noticeId: 'notice-2' }), true)
  const stages = sent.filter(m => m.type === 'DAVINCI_AGENT_SUBSCRIPTION_STAGE')
  assert.equal(stages.length, 2)
  assert.deepEqual(stages[0].stage, { taskId: 'draft-1', runId: 'run-1',
    noticeId: 'notice-1', revision: 2, step: 'datasets', sequence: 1 })
  assert.equal(stages[1].stage.sequence, 2)
  assert.equal(stages[0].bridgeNonce, 'nonce')
  assert.equal(stages[0].pageInstanceId, 'page')
  bridge.destroy()
  assert.equal(bridge.sendSubscriptionStage(notice), false)
})

test('private layout computation requires an active bound call and allows two sequential attempts', async () => {
  const { createHostBridgeV2 } = await import(pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js')))
  const win = new FakeWindow()
  const sent = []
  const parent = { postMessage: m => sent.push(m) }
  const calls = []
  const remoteResult = { version: 'constraint-v1', status: 'feasible', placements: [],
    alternatives: [{ planKey: 'best', placements: [], quality: { gapCells: 0 } }],
    candidateCount: 1 }
  const bridge = createHostBridgeV2({ parentOrigin: 'https://parent.test', parentWindow: parent,
    windowObject: win, onLayoutSolve: async (...args) => {
      calls.push(args); return remoteResult
    } })
  const started = bridge.start()
  const base = sent[0]
  const emit = (data, origin = 'https://parent.test') => win.emit({ origin, source: parent,
    data: { ...base, bridgeNonce: 'nonce', pageInstanceId: 'page', ...data } })
  assert.equal(base.layoutSolverVersion, 'constraint-v1')
  emit({ type: 'DAVINCI_AGENT_BRIDGE_READY', helloId: base.helloId })
  await started
  const context = bridge.requestContext()
  emit({ type: 'DAVINCI_AGENT_CONTEXT_RESPONSE', runtimeContext: {
    state: { schemaVersion: 'davinci-page-state-v1', page: { instanceId: 'page' }, revisions: {} },
    profileId: 'dashboard', catalogDigest: 'd', toolSetId: 'set', tools: [] } })
  await context
  const message = { type: 'DAVINCI_AGENT_LAYOUT_REQUEST', toolCallId: 'layout',
    layoutAttempt: 1,
    problem: { version: 'constraint-v1', budgetMs: 1000, nodes: [] } }
  emit(message)
  const completed = bridge.executeToolCall('layout', 'dashboard.set_widget_layout', {}, { sessionId: 's' })
  emit(message, 'https://wrong.test')
  emit({ ...message, bridgeNonce: 'wrong' })
  emit({ ...message, pageInstanceId: 'wrong' })
  await new Promise(r => setTimeout(r, 0))
  assert.equal(calls.length, 0)
  emit(message)
  emit(message)
  await new Promise(r => setTimeout(r, 0))
  assert.equal(calls.length, 1)
  assert.equal(calls[0][1].sessionId, 's')
  assert.equal(calls[0][1].layoutAttempt, 1)
  const layoutResults = sent.filter(m => m.type === 'DAVINCI_AGENT_LAYOUT_RESULT')
  assert.equal(layoutResults.length, 1)
  assert.equal(layoutResults[0].layoutAttempt, 1)
  assert.deepEqual(layoutResults[0].result, remoteResult)
  emit({ ...message, layoutAttempt: 2 })
  emit({ ...message, layoutAttempt: 2 })
  await new Promise(r => setTimeout(r, 0))
  assert.equal(calls.length, 2)
  assert.equal(calls[1][1].layoutAttempt, 2)
  assert.equal(sent.filter(m => m.type === 'DAVINCI_AGENT_LAYOUT_RESULT').length, 2)
  emit({ type: 'DAVINCI_AGENT_UI_ACK', toolCallId: 'layout', result: { status: 'success' } })
  await completed
  assert.equal(calls[0][2].aborted, false)
  assert.equal(calls[1][2].aborted, false)
  emit(message)
  assert.equal(calls.length, 2)
  bridge.destroy()
})

test('V2 HostBridge transports PageState, Registry tools and ToolResultEnvelope', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    now: () => 1000
  })

  const ready = bridge.start()
  const hello = sent.shift().value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      helloId: hello.helloId,
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready

  const contextPromise = bridge.requestContext()
  const runtimeContext = {
    profileId: 'dashboard',
    catalogDigest: 'contract-digest:dashboard',
    toolSetId: 'contract-digest:dashboard:2',
    state: {
      schemaVersion: 'davinci-page-state-v1',
      page: {
        instanceId: 'page-1',
        kind: 'dashboard',
        route: '/share/workbench-new?dashboard=88',
        resource: { type: 'dashboard', id: '88' }
      },
      permissions: { canRead: true, canOperate: true, canPersist: false },
      ui: { busy: false, activeFilters: [] },
      revisions: { routeRevision: 2, dataRevision: 4 },
      dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] }
    },
    tools: [{ name: 'dashboard.get_structure', description: 'Read', parameters: { type: 'object' } }]
  }
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      runtimeContext
    }
  })
  assert.deepEqual(await contextPromise, runtimeContext)

  const resultPromise = bridge.executeToolCall('tool-1', 'dashboard.get_structure', {})
  const command = sent.at(-1).value.command
  assert.deepEqual(command.preconditions, { resourceId: '88', dataRevision: 4 })
  assert.equal(command.toolCallId, 'tool-1')
  assert.equal(command.toolSetId, runtimeContext.toolSetId)
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_UI_ACK',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      toolCallId: 'tool-1',
      result: { status: 'success', data: { summary: 'ok' }, issues: [] }
    }
  })
  assert.deepEqual(await resultPromise, {
    status: 'success', data: { summary: 'ok' }, issues: []
  })
  bridge.destroy()
})

test('V2 HostBridge retries HELLO until the parent bridge is ready', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    handshakeRetryMs: 5,
    handshakeTimeoutMs: 100
  })

  const ready = bridge.start()
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.ok(sent.length >= 2)
  assert.equal(new Set(sent.map(item => item.value.helloId)).size, 1)

  const hello = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready
  const sentAfterReady = sent.length
  await new Promise(resolve => setTimeout(resolve, 20))
  assert.equal(sent.length, sentAfterReady)
  bridge.destroy()
})

test('V2 HostBridge advances to a newer trusted page context', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    now: () => 1000
  })

  const ready = bridge.start()
  const hello = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready

  const contextPromise = bridge.requestContext()
  const runtimeContext = {
    profileId: 'dashboard',
    catalogDigest: 'contract-digest:dashboard',
    toolSetId: 'contract-digest:dashboard:3',
    state: {
      schemaVersion: 'davinci-page-state-v1',
      page: {
        instanceId: 'page-2',
        kind: 'dashboard',
        route: '/share/workbench-new?dashboard=207',
        resource: { type: 'dashboard', id: '207' }
      },
      permissions: { canRead: true, canOperate: true, canPersist: false },
      ui: { busy: false, activeFilters: [] },
      revisions: { routeRevision: 3, dataRevision: 5 },
      dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] }
    },
    tools: [{ name: 'dashboard.get_structure', description: 'Read', parameters: { type: 'object' } }]
  }
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-2',
      runtimeContext
    }
  })
  assert.deepEqual(await contextPromise, runtimeContext)

  const resultPromise = bridge.executeToolCall(
    'tool-2',
    'dashboard.get_structure',
    {}
  )
  const commandMessage = sent.at(-1).value
  assert.equal(commandMessage.pageInstanceId, 'page-2')
  assert.equal(commandMessage.command.pageInstanceId, 'page-2')
  assert.deepEqual(commandMessage.command.preconditions, {
    resourceId: '207',
    dataRevision: 5
  })
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_UI_ACK',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-2',
      toolCallId: 'tool-2',
      result: { status: 'success', data: { summary: 'ok' }, issues: [] }
    }
  })
  await resultPromise
  bridge.destroy()
})

test('V2 HostBridge carries pre-injected context items and ignores a malformed context', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    now: () => 1000
  })

  const ready = bridge.start()
  const hello = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready

  const state = {
    schemaVersion: 'davinci-page-state-v1',
    page: {
      instanceId: 'page-1',
      kind: 'dashboard',
      route: '/share/workbench-new?dashboard=88',
      resource: { type: 'dashboard', id: '88' }
    },
    permissions: { canRead: true, canOperate: true, canPersist: false },
    ui: { busy: false, activeFilters: [] },
    revisions: { routeRevision: 2, dataRevision: 4 },
    dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] }
  }
  const contextPromise = bridge.requestContext()
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      runtimeContext: { state, tools: [], context: 'not-an-array' }
    }
  })
  const pending = Symbol('pending')
  assert.equal(
    await Promise.race([
      contextPromise,
      new Promise(resolve => setTimeout(() => resolve(pending), 10))
    ]),
    pending
  )

  const runtimeContext = {
    profileId: 'dashboard',
    catalogDigest: 'contract-digest:dashboard',
    toolSetId: 'contract-digest:dashboard:2',
    state,
    tools: [],
    context: [{ description: 'dashboard_structure', value: '仪表盘：海外数据' }]
  }
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      runtimeContext
    }
  })
  assert.deepEqual(await contextPromise, runtimeContext)
  bridge.destroy()
})

test('V2 HostBridge times out a context request instead of hanging forever', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    contextTimeoutMs: 5
  })

  const ready = bridge.start()
  const hello = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready
  await assert.rejects(
    bridge.requestContext(),
    /Davinci HostBridge V2 context request timed out/
  )
  bridge.destroy()
})

async function readyBridge({ readyExtras = {}, sent, iframeWindow, parentWindow, origin }) {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    now: () => 1000
  })
  const ready = bridge.start()
  const hello = sent.shift().value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      helloId: hello.helloId,
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      ...readyExtras
    }
  })
  await ready
  return bridge
}

test('V2 editor preparation uses canonical two-minute transport deadlines', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({ sent, iframeWindow, parentWindow, origin })
  const context = bridge.requestContext()
  const request = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...request,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      runtimeContext: {
        profileId: 'dataset-editor',
        catalogDigest: 'editor-digest',
        toolSetId: 'editor-tools',
        state: {
          schemaVersion: 'davinci-page-state-v1',
          page: { instanceId: 'page-1', kind: 'dataset-editor', route: '/dataset' },
          revisions: { routeRevision: 1 }
        },
        tools: []
      }
    }
  })
  await context
  try {
    for (const suffix of ['apply_draft', 'validate', 'preview', 'save_draft', 'get_join_candidates']) {
      const toolCallId = `editor-${suffix}`
      const pending = bridge.executeToolCall(
        toolCallId, `dataset.editor.${suffix}`, { expectedDraftRevision: 'draft-1' }
      )
      const message = sent.at(-1).value
      assert.equal(message.command.expiresAt, 1000 + 120_000)
      iframeWindow.emit({
        origin,
        source: parentWindow,
        data: {
          ...message,
          type: 'DAVINCI_AGENT_UI_ACK',
          toolCallId,
          result: { status: 'success', data: {}, issues: [] }
        }
      })
      await pending
    }
  } finally {
    bridge.destroy()
  }
})

test('V2 HostBridge exposes panel capabilities from BRIDGE_READY and sends panel commands', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({
    readyExtras: { panelCommands: ['resize', 'close'] },
    sent, iframeWindow, parentWindow, origin
  })

  assert.deepEqual(bridge.getPanelCommands(), ['resize', 'close'])

  bridge.sendPanelCommand({ action: 'resize', size: 'large' })
  const resizeMessage = sent.at(-1)
  assert.equal(resizeMessage.target, origin)
  assert.equal(resizeMessage.value.type, 'DAVINCI_AGENT_PANEL_COMMAND')
  assert.equal(resizeMessage.value.bridgeNonce, 'nonce-v2')
  assert.equal(resizeMessage.value.pageInstanceId, 'page-1')
  assert.deepEqual(resizeMessage.value.panel, { action: 'resize', size: 'large' })

  bridge.sendPanelCommand({ action: 'close' })
  assert.deepEqual(sent.at(-1).value.panel, { action: 'close' })
  bridge.destroy()
})

test('V2 HostBridge reports no panel commands for a legacy parent and refuses to send', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({ sent, iframeWindow, parentWindow, origin })

  assert.deepEqual(bridge.getPanelCommands(), [])
  const sentBefore = sent.length
  assert.throws(
    () => bridge.sendPanelCommand({ action: 'resize', size: 'large' }),
    /panel commands/i
  )
  assert.equal(sent.length, sentBefore)
  bridge.destroy()
})

test('V2 HostBridge sends bounded drag coordinates only when the parent advertises support', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({ readyExtras: { panelCommands: ['drag'] },
    sent, iframeWindow, parentWindow, origin })
  const drag = { action: 'drag', phase: 'move', pointerId: 1, screenX: -100, screenY: 300 }
  for (const invalid of [{ screenX: NaN }, { screenY: Infinity }, { screenX: 100001 },
    { phase: 'drop' }, { pointerId: -1 }]) {
    assert.throws(() => bridge.sendPanelCommand({ ...drag, ...invalid }), /Invalid panel drag/)
  }
  bridge.sendPanelCommand({ ...drag, unexpected: 'ignored' })
  assert.deepEqual(sent.at(-1).value.panel, drag)
  assert.equal(sent.at(-1).target, origin)
  bridge.destroy()
})

test('V2 HostBridge sanitizes malformed panel capabilities and rejects invalid commands', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({
    readyExtras: { panelCommands: 'resize' },
    sent, iframeWindow, parentWindow, origin
  })
  assert.deepEqual(bridge.getPanelCommands(), [])
  bridge.destroy()

  const sent2 = []
  const iframeWindow2 = new FakeWindow()
  const parentWindow2 = { postMessage: (value, target) => sent2.push({ value, target }) }
  const bridge2 = await readyBridge({
    readyExtras: { panelCommands: ['resize', 'close', 42, 'unknown'] },
    sent: sent2, iframeWindow: iframeWindow2, parentWindow: parentWindow2, origin
  })
  assert.deepEqual(bridge2.getPanelCommands(), ['resize', 'close'])
  const sentBefore = sent2.length
  assert.throws(
    () => bridge2.sendPanelCommand({ action: 'resize', size: 'huge' }),
    /size/i
  )
  assert.throws(
    () => bridge2.sendPanelCommand({ action: 'open' }),
    /action/i
  )
  assert.equal(sent2.length, sentBefore)
  bridge2.destroy()
})

test('V2 HostBridge leaves transport grace for a page timeout ACK', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    toolTimeoutFor: () => 5,
    toolAckGraceMs: 20,
    now: () => 1000
  })

  const ready = bridge.start()
  const hello = sent.at(-1).value
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1'
    }
  })
  await ready

  const contextPromise = bridge.requestContext()
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      runtimeContext: {
        profileId: 'workspace',
        catalogDigest: 'contract-digest:workspace',
        toolSetId: 'contract-digest:workspace:1',
        state: {
          schemaVersion: 'davinci-page-state-v1',
          page: { instanceId: 'page-1', kind: 'other', route: '/share' },
          permissions: { canRead: true, canOperate: true, canPersist: true },
          ui: { busy: false, activeFilters: [] },
          revisions: { routeRevision: 1 },
          dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] }
        },
        tools: []
      }
    }
  })
  await contextPromise

  const resultPromise = bridge.executeToolCall('tool-timeout', 'test.tool', {})
  await new Promise(resolve => setTimeout(resolve, 10))
  iframeWindow.emit({
    origin,
    source: parentWindow,
    data: {
      ...hello,
      type: 'DAVINCI_AGENT_UI_ACK',
      bridgeNonce: 'nonce-v2',
      pageInstanceId: 'page-1',
      toolCallId: 'tool-timeout',
      result: {
        status: 'error',
        error: { code: 'TOOL_TIMEOUT', layer: 'page' },
        issues: []
      }
    }
  })
  assert.deepEqual(await resultPromise, {
    status: 'error',
    error: { code: 'TOOL_TIMEOUT', layer: 'page' },
    issues: []
  })
  bridge.destroy()
})

test('V2 reauthentication uses the bound panel command without exposing credentials', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({
    readyExtras: { panelCommands: ['resize', 'close', 'reauthenticate'] },
    sent, iframeWindow, parentWindow, origin
  })
  bridge.sendPanelCommand({ action: 'reauthenticate' })
  assert.deepEqual(sent.at(-1).value.panel, { action: 'reauthenticate' })
  assert.equal(typeof sent.at(-1).value.bridgeNonce, 'string')
  bridge.destroy()
})

test('V2 HostBridge forwards a trusted PANEL_STATE and drops untrusted or malformed ones', async () => {
  const { createHostBridgeV2 } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge-v2.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const received = []
  const bridge = createHostBridgeV2({
    parentOrigin: origin,
    parentWindow,
    windowObject: iframeWindow,
    onPanelState: state => received.push(state)
  })
  const ready = bridge.start()
  const hello = sent.shift().value
  const emit = (data, source = parentWindow, eventOrigin = origin) =>
    iframeWindow.emit({ origin: eventOrigin, source, data: { ...hello, ...data } })

  // Before READY nothing is bound, so the state has no trusted owner yet.
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 0.1, y: 0.1 } })
  emit({ type: 'DAVINCI_AGENT_BRIDGE_READY', helloId: hello.helloId, bridgeNonce: 'nonce-v2', pageInstanceId: 'page-1' })
  await ready

  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 0.25, y: 0.75 } })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: null })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'other-nonce', launcher: { x: 0.5, y: 0.5 } })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 2, y: 0.5 } })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 'a', y: 0.5 } })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2' })
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 0.5, y: 0.5 } }, {}, origin)
  emit({ type: 'DAVINCI_AGENT_PANEL_STATE', bridgeNonce: 'nonce-v2', launcher: { x: 0.5, y: 0.5 } }, parentWindow, 'http://evil.test')

  assert.deepEqual(received, [
    { launcher: { x: 0.25, y: 0.75 } },
    { launcher: null }
  ])
})

test('V2 HostBridge forwards run status only when the parent advertises it', async () => {
  const iframeWindow = new FakeWindow()
  const sent = []
  const origin = 'http://local.aihuishou.com:5002'
  const parentWindow = { postMessage: (value, target) => sent.push({ value, target }) }
  const bridge = await readyBridge({
    readyExtras: { panelCommands: ['resize', 'status'] },
    sent, iframeWindow, parentWindow, origin
  })

  assert.deepEqual(bridge.getPanelCommands(), ['resize', 'status'])

  bridge.sendPanelCommand({ action: 'status', run: 'running' })
  const message = sent.at(-1)
  assert.equal(message.target, origin)
  assert.equal(message.value.type, 'DAVINCI_AGENT_PANEL_COMMAND')
  assert.equal(message.value.bridgeNonce, 'nonce-v2')
  assert.equal(message.value.pageInstanceId, 'page-1')
  assert.deepEqual(message.value.panel, { action: 'status', run: 'running' })

  for (const run of ['idle', 'finished', 'failed', 'stopped', 'unknown']) {
    bridge.sendPanelCommand({ action: 'status', run })
    assert.deepEqual(sent.at(-1).value.panel, { action: 'status', run })
  }

  const sentBefore = sent.length
  for (const run of ['busy', '', null, undefined, 42]) {
    assert.throws(() => bridge.sendPanelCommand({ action: 'status', run }), /run state/i)
  }
  assert.equal(sent.length, sentBefore)
  bridge.destroy()

  const sent2 = []
  const iframeWindow2 = new FakeWindow()
  const parentWindow2 = { postMessage: (value, target) => sent2.push({ value, target }) }
  const legacy = await readyBridge({
    readyExtras: { panelCommands: ['resize'] },
    sent: sent2, iframeWindow: iframeWindow2, parentWindow: parentWindow2, origin
  })
  assert.throws(
    () => legacy.sendPanelCommand({ action: 'status', run: 'running' }),
    /action/i
  )
  legacy.destroy()
})
