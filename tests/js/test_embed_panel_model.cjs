process.env.TZ = 'Asia/Shanghai'

const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

const root = path.resolve(__dirname, '../..')

test('session timestamps without an offset are UTC, including midnight boundaries', async () => {
  const { sessionListModel } = await load()
  const sessions = [
    { id: 'naive', updated_at: '2026-09-07T03:49:00' },
    { id: 'offset', updated_at: '2026-09-07T11:49:00+08:00' },
    { id: 'midnight', updated_at: '2026-09-06T17:30:00' }
  ]
  const rows = sessionListModel(sessions, null, { now: new Date('2026-09-07T12:00:00+08:00') })
  assert.deepEqual(rows.map(row => row.timeLabel), ['11:49', '11:49', '01:30'])
})

function load() {
  return import(pathToFileURL(path.join(root, 'web/embed/panel-model.js')))
}

function pageState(page) {
  return {
    schemaVersion: 'davinci-page-state-v1',
    page,
    permissions: { canRead: true, canOperate: true, canPersist: true },
    ui: { busy: false, activeFilters: [] },
    revisions: { routeRevision: 1 },
    dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] }
  }
}

test('describePageContext names the Davinci page area and resource', async () => {
  const { describePageContext } = await load()

  assert.deepEqual(
    describePageContext(pageState({
      instanceId: 'p1',
      kind: 'dashboard',
      route: '/share/workbench-new?dashboard=88',
      resource: { type: 'dashboard', id: '88', name: '出口利润' }
    })),
    { available: true, kind: 'dashboard', area: '仪表盘', name: '出口利润' }
  )

  assert.deepEqual(
    describePageContext(pageState({
      instanceId: 'p2',
      kind: 'dataset-marketplace',
      route: '/share/dataset-marketplace'
    })),
    { available: true, kind: 'dataset-marketplace', area: '数据集市', name: '' }
  )
})

test('describePageContext falls back to the resource id and reports unusable state', async () => {
  const { describePageContext } = await load()

  assert.equal(
    describePageContext(pageState({
      instanceId: 'p3',
      kind: 'dashboard',
      route: '/share/workbench-new?dashboard=207',
      resource: { type: 'dashboard', id: '207' }
    })).name,
    '#207'
  )

  assert.equal(describePageContext(null).available, false)
  assert.equal(describePageContext({}).available, false)
  assert.equal(
    describePageContext(pageState({ instanceId: 'p4', kind: 'other', route: '/x' })).area,
    '数巢页面'
  )
})

test('legacyPageState lifts a v1 host context into the v2 page shape', async () => {
  const { legacyPageState, describePageContext } = await load()

  const dashboard = legacyPageState({
    contextVersion: 3,
    pageType: 'dashboard',
    resourceId: '1024'
  })
  assert.deepEqual(dashboard.page, {
    instanceId: 'legacy',
    kind: 'dashboard',
    route: '',
    resource: { type: 'dashboard', id: '1024' }
  })
  assert.deepEqual(describePageContext(dashboard), {
    available: true, kind: 'dashboard', area: '仪表盘', name: '#1024'
  })

  const dataset = legacyPageState({ pageType: 'dataset', resourceId: null })
  assert.equal(dataset.page.kind, 'dataset-marketplace')
  assert.equal(dataset.page.resource, undefined)

  assert.equal(legacyPageState(null), null)
  assert.equal(legacyPageState({}), null)
})

test('suggestionsFor gives three page-appropriate prompts', async () => {
  const { suggestionsFor } = await load()

  const dashboard = suggestionsFor('dashboard')
  assert.equal(dashboard.length, 3)
  assert.ok(dashboard.every(item => typeof item === 'string' && item.length > 0))

  assert.notDeepEqual(suggestionsFor('dataset-marketplace'), dashboard)
  assert.equal(suggestionsFor('unknown-kind').length, 3)
})

test('effortLabel maps run effort onto the Chinese composer labels', async () => {
  const { effortLabel } = await load()

  assert.equal(effortLabel('low'), '低')
  assert.equal(effortLabel('medium'), '中')
  assert.equal(effortLabel('high'), '高')
  assert.equal(effortLabel('xhigh'), '极高')
  assert.equal(effortLabel('max'), '最大')
  assert.equal(effortLabel('weird'), 'weird')
})

test('describeToolCall turns a dotted tool action into running and done titles', async () => {
  const { describeToolCall } = await load()

  assert.deepEqual(describeToolCall('dashboard.get_structure'), {
    runningTitle: '正在读取仪表盘',
    doneTitle: '已读取仪表盘',
    hint: 'dashboard.get_structure'
  })
  assert.equal(describeToolCall('dashboard.apply_widget_spec').runningTitle, '正在更新仪表盘')
  assert.equal(describeToolCall('dashboard.publish').runningTitle, '正在发布仪表盘')
  assert.equal(describeToolCall('dataset.search_datasets').runningTitle, '正在查询数据集')
})

test('describeToolCall degrades gracefully for unknown tool names', async () => {
  const { describeToolCall } = await load()

  assert.deepEqual(describeToolCall('mystery'), {
    runningTitle: '正在调用工具',
    doneTitle: '已调用工具',
    hint: 'mystery'
  })
  assert.equal(describeToolCall('').runningTitle, '正在调用工具')
  assert.equal(describeToolCall(undefined).hint, '')
})

test('sessionListModel sorts sessions by recency and marks the active one', async () => {
  const { sessionListModel } = await load()
  const now = new Date('2026-08-25T10:00:00Z')
  const sessions = [
    { id: 'a', title: '成交订单日报', updated_at: '2026-08-25T09:30:00Z' },
    { id: 'b', title: '', updated_at: '2026-08-24T09:30:00Z' },
    { id: 'c', title: '华东订单异常分析', updated_at: '2026-08-25T09:55:00Z' }
  ]

  const model = sessionListModel(sessions, 'a', { now })
  assert.deepEqual(model.map(item => item.id), ['c', 'a', 'b'])
  assert.deepEqual(
    model.map(item => item.active),
    [false, true, false]
  )
  assert.equal(model[2].title, '未命名会话')
})

test('sessionListModel labels times by day and honours the limit', async () => {
  const { sessionListModel } = await load()
  const now = new Date('2026-08-25T10:00:00Z')
  const at = value => ({ id: value, title: value, updated_at: value })

  const [today, yesterday, older] = sessionListModel(
    [
      at('2026-08-25T09:30:00Z'),
      at('2026-08-24T10:00:00Z'),
      at('2026-08-11T08:00:00Z')
    ],
    null,
    { now }
  )
  assert.match(today.timeLabel, /^\d{2}:\d{2}$/u)
  assert.equal(yesterday.timeLabel, '昨天')
  assert.equal(older.timeLabel, '8月11日')

  assert.equal(
    sessionListModel(
      Array.from({ length: 12 }, (_, index) => ({
        id: String(index),
        title: `会话 ${index}`,
        updated_at: `2026-08-25T09:${String(index).padStart(2, '0')}:00Z`
      })),
      null,
      { now, limit: 8 }
    ).length,
    8
  )
})

test('sessionListModel tolerates missing or malformed input', async () => {
  const { sessionListModel } = await load()

  assert.deepEqual(sessionListModel(null, null), [])
  assert.deepEqual(sessionListModel(undefined, 'x'), [])
  const model = sessionListModel([{ id: 'a' }], null, { now: new Date('2026-08-25T10:00:00Z') })
  assert.equal(model.length, 1)
  assert.equal(model[0].timeLabel, '')
})
