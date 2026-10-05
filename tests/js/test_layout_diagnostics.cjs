const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')

async function moduleUnderTest() {
  const source = await fs.readFile('web/embed/layout-diagnostics.js', 'utf8')
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
}

test('forwards preparation sidecar separately without mutating geometry or metadata', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const signal = new AbortController().signal
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push({ url, options, body: JSON.parse(options.body) })
    return { status: 'feasible' }
  } })
  const preparationDiagnostics = Object.freeze({ version: 'preparation-v1', elapsedMs: 12,
    truncated: false, events: [] })
  const nodes = Object.freeze([{ id: 'a', shapes: [{ w: 6, h: 4 }] }])
  const problem = Object.freeze({ version: 'constraint-v1', nodes, preparationDiagnostics })
  await diagnostics.solve(problem, { sessionId: 's /', toolCallId: 'call' }, signal)
  assert.equal(requests[0].url, '/api/sessions/s%20%2F/dashboardLayoutSolve')
  assert.equal(requests[0].options.signal, signal)
  assert.deepEqual(requests[0].body, { toolCallId: 'call',
    problem: { version: 'constraint-v1', nodes }, preparationDiagnostics })
  assert.equal(Object.hasOwn(requests[0].body.problem, 'preparationDiagnostics'), false)
  assert.equal(problem.preparationDiagnostics, preparationDiagnostics)
})

test('omits undefined sidecar and forwards defined malformed values for server sanitization', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (_url, options) => {
    requests.push(JSON.parse(options.body))
    return {}
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  for (const value of [undefined, null, false, 'invalid']) {
    await diagnostics.solve({ version: 'constraint-v1', preparationDiagnostics: value }, scope)
    const body = requests.at(-1)
    assert.deepEqual(body.problem, { version: 'constraint-v1' })
    assert.equal(Object.hasOwn(body, 'preparationDiagnostics'), value !== undefined)
    if (value !== undefined) assert.deepEqual(body.preparationDiagnostics, value)
  }
  await diagnostics.solve({ version: 'constraint-v1' }, scope)
  assert.deepEqual(requests.at(-1), { toolCallId: 'c', problem: { version: 'constraint-v1' } })
})

test('links multiple solves to one receipt without sending business text', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  let count = 0
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return { layoutRunId: `00000000-0000-4000-8000-${String(++count).padStart(12, '0')}` }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, { status: 'success', data: { persisted: true,
    summary: { executionMode: 'remote' },
    layoutChanges: [{ widgetId: 'a', after: { x: 0, y: 0, width: 6, height: 15 } }],
    title: 'secret' }, issues: [{ message: 'secret' }] })
  assert.equal(requests.length, 4)
  assert.equal(requests[2][1].persisted, true)
  assert.deepEqual(requests[2][1].changes, [{ id: 'a', x: 0, y: 0, w: 6, h: 15 }])
  assert.equal(JSON.stringify(requests.slice(2)).includes('secret'), false)
})

test('extracts bounded failure evidence from the first issue without business text', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error',
    error: {
      code: 'PERSISTENCE_OUTCOME_UNKNOWN',
      message: 'private server response',
      correlationId: 'private-request-id'
    },
    issues: [{
      code: 'PERSISTENCE_OUTCOME_UNKNOWN',
      message: 'private card title',
      widgetIds: ['private-widget-id'],
      constraints: {
        writeDispatched: true,
        persistenceStage: 'receipt_validation',
        persistenceReason: 'receipt_incomplete',
        widgetTitle: 'private title'
      }
    }, {
      code: 'IGNORED_SECOND_ISSUE',
      constraints: { writeDispatched: false }
    }]
  })

  const outcome = requests[1][1]
  assert.deepEqual(outcome, {
    toolCallId: 'c',
    status: 'error',
    persisted: false,
    executionMode: null,
    fallbackReason: null,
    errorCode: 'PERSISTENCE_OUTCOME_UNKNOWN',
    firstIssueCode: 'PERSISTENCE_OUTCOME_UNKNOWN',
    writeDispatched: true,
    committed: null,
    persistenceStage: 'receipt_validation',
    persistenceReason: 'receipt_incomplete',
    changes: [],
    changesTruncated: false
  })
  assert.equal(JSON.stringify(outcome).includes('private'), false)
  assert.equal(JSON.stringify(outcome).includes('IGNORED_SECOND_ISSUE'), false)
})

test('reports the direct preflight stage and error when no write was dispatched', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error',
    error: { code: 'EXECUTION_FAILED' },
    issues: [{
      code: 'GROUPING_PENDING_WRITES_FAILED',
      constraints: {
        writeDispatched: false,
        preflightStage: 'pending_writes',
        preflightError: 'GROUPING_PENDING_WRITES_FAILED'
      }
    }]
  })

  assert.equal(requests[1][1].preflightStage, 'pending_writes')
  assert.equal(requests[1][1].preflightError, 'GROUPING_PENDING_WRITES_FAILED')
  assert.equal(requests[1][1].writeDispatched, false)
  assert.equal(requests[1][1].firstIssueCode, 'GROUPING_PENDING_WRITES_FAILED')
})

test('drops malformed or unsupported failure evidence before reporting', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error',
    error: { code: 'PRIVATE_CUSTOMER_ACME' },
    issues: [{
      code: 'PRIVATE_CUSTOMER_ACME',
      constraints: {
        writeDispatched: 'true',
        persistenceStage: 'database_private_stage',
        persistenceReason: 'secret backend response'
      }
    }]
  })
  const outcome = requests[1][1]
  assert.equal(outcome.errorCode, null)
  assert.equal(outcome.firstIssueCode, null)
  assert.equal(outcome.writeDispatched, null)
  assert.equal(outcome.persistenceStage, null)
  assert.equal(outcome.persistenceReason, null)
  assert.equal(JSON.stringify(outcome).includes('private'), false)
  assert.equal(JSON.stringify(outcome).includes('secret'), false)
})

test('preserves stable terminal layout issue codes but drops configuration limits', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  let run = 0
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: `00000000-0000-4000-8000-${String(++run).padStart(12, '0')}` }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  const stableCodes = [
    'LAYOUT_CANCELLED', 'LAYOUT_CANVAS_UNAVAILABLE', 'LAYOUT_CHILD_OVERFLOW',
    'LAYOUT_CONTAINER_CYCLE', 'LAYOUT_CONTAINER_WHITESPACE', 'LAYOUT_INCOMPLETE',
    'LAYOUT_INPUT_UNAVAILABLE', 'LAYOUT_INVALID_BOUNDS', 'LAYOUT_INVALID_IDENTITY',
    'LAYOUT_INVALID_OVERRIDE', 'LAYOUT_INVALID_SHAPE', 'LAYOUT_NESTED_CONTAINER_UNSUPPORTED',
    'LAYOUT_NO_READABLE_CANDIDATES', 'LAYOUT_OVERLAP', 'LAYOUT_RULE_INCOMPLETE'
  ]

  for (const code of stableCodes) {
    await diagnostics.solve({}, scope)
    await diagnostics.report(scope, { status: 'error', issues: [{ code }] })
    assert.equal(requests.at(-1)[1].firstIssueCode, code)
  }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error', issues: [{ code: 'LAYOUT_ORDER_RELATION_LIMIT' }]
  })
  assert.equal(requests.at(-1)[1].firstIssueCode, null)
})

test('does not report persistence failures on a successful saved receipt', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'success',
    data: { persisted: true },
    error: { code: 'PERSISTENCE_OUTCOME_UNKNOWN' },
    issues: [{
      code: 'PERSISTENCE_OUTCOME_UNKNOWN',
      constraints: {
        writeDispatched: true,
        persistenceStage: 'receipt_validation',
        persistenceReason: 'receipt_incomplete'
      }
    }]
  })
  assert.deepEqual(requests[1][1], {
    toolCallId: 'c', status: 'success', persisted: true,
    executionMode: null, fallbackReason: null,
    errorCode: null, firstIssueCode: null, writeDispatched: null,
    committed: null,
    persistenceStage: null, persistenceReason: null,
    changes: [], changesTruncated: false
  })
})

test('reports an unconfirmed HTTP response without claiming the write was rejected', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error',
    error: { code: 'PERSISTENCE_OUTCOME_UNKNOWN' },
    issues: [{ code: 'PERSISTENCE_OUTCOME_UNKNOWN', constraints: {
      writeDispatched: true,
      persistenceStage: 'backend_response',
      persistenceReason: 'response_unconfirmed'
    } }]
  })
  assert.equal(requests[1][1].persistenceStage, 'backend_response')
  assert.equal(requests[1][1].persistenceReason, 'response_unconfirmed')
})

test('reports a committed local conflict as bounded persistence evidence', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    requests.push([url, JSON.parse(options.body)])
    return url.endsWith('dashboardLayoutSolve')
      ? { layoutRunId: '00000000-0000-4000-8000-000000000001' }
      : { stored: true }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await diagnostics.report(scope, {
    status: 'error',
    error: { code: 'EXECUTION_FAILED', committed: true },
    issues: [{ code: 'GROUPING_COMMITTED_CONFLICT', constraints: {
      writeDispatched: true,
      persistenceStage: 'receipt_validation',
      persistenceReason: 'local_state_conflict'
    } }]
  })
  assert.equal(requests[1][1].committed, true)
  assert.equal(requests[1][1].firstIssueCode, 'GROUPING_COMMITTED_CONFLICT')
  assert.equal(requests[1][1].persistenceReason, 'local_state_conflict')
})

test('report failure does not reject the completed tool', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const diagnostics = createLayoutDiagnostics({ api: async url => {
    if (url.endsWith('dashboardLayoutSolve')) return { layoutRunId: '00000000-0000-4000-8000-000000000001' }
    throw new Error('offline')
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  await diagnostics.solve({}, scope)
  await assert.doesNotReject(diagnostics.report(scope, { status: 'error' }))
})

test('negotiates reading support and retains explicit relationships', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const calls = []
  const problem = { version: 'constraint-v1', nodes: [], orders: [['a', 'b']],
    orderSources: ['derived_geometry'], requiredBefore: [['a', 'b']] }
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    calls.push([url, options])
    if (url.endsWith('Capabilities')) return { readingContract: 'local-regions-v1' }
    return { status: 'feasible' }
  } })
  await diagnostics.solve(problem, { sessionId: 's', toolCallId: 'c' })
  assert.equal(calls.length, 2)
  assert.deepEqual(JSON.parse(calls[1][1].body).problem, problem)
})

test('old server uses strict seed order but cannot drop requiredBefore', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const requests = []
  const diagnostics = createLayoutDiagnostics({ api: async (url, options) => {
    if (url.endsWith('Capabilities')) throw Object.assign(new Error('missing'), { status: 404 })
    requests.push(JSON.parse(options.body))
    return { status: 'feasible' }
  } })
  const scope = { sessionId: 's', toolCallId: 'c' }
  const problem = { version: 'constraint-v1', nodes: [], orders: [['a', 'b']],
    orderSources: ['derived_geometry'], requiredBefore: [] }
  const result = await diagnostics.solve(problem, scope)
  assert.deepEqual(requests[0].problem, { version: 'constraint-v1', nodes: [], orders: [['a', 'b']] })
  assert.equal(result.readingContract, 'legacy-strict')
  await assert.rejects(diagnostics.solve({ ...problem, requiredBefore: [['b', 'a']] }, scope),
    /reading constraints/)
  assert.equal(requests.length, 1)
})

test('does not downgrade on authorization failure or dispatch after cancellation', async () => {
  const { createLayoutDiagnostics } = await moduleUnderTest()
  const abort = new AbortController()
  const problem = { orderSources: ['derived_geometry'], requiredBefore: [] }
  let solves = 0
  const diagnostics = createLayoutDiagnostics({ api: async (url) => {
    if (url.endsWith('Capabilities')) {
      abort.abort()
      return { readingContract: 'local-regions-v1' }
    }
    solves++
  } })
  await assert.rejects(diagnostics.solve(problem, { sessionId: 's', toolCallId: 'c' }, abort.signal))
  assert.equal(solves, 0)
  const denied = createLayoutDiagnostics({ api: async () => {
    throw Object.assign(new Error('denied'), { status: 403 })
  } })
  await assert.rejects(denied.solve(problem, { sessionId: 's', toolCallId: 'c' }), /denied/)
})
