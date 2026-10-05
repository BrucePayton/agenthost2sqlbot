// Synthetic fixtures, production rendering/planning, local in-memory persistence only.
import React, { useState } from 'react'
import ReactDOM from 'react-dom'
import { Responsive, WidthProvider } from 'react-grid-layout'
import MetricWidget from 'components/DashboardWidgets/MetricWidget'
import Chart from 'components/DashboardWidgets/Chart'
import LeaderboardWidget from 'components/DashboardWidgets/LeaderboardWidget'
import TableWidget from 'components/DashboardWidgets/Table'
import LayoutWidget from 'components/DashboardWidgets/LayoutWidget'
import WidgetNoDataFallback from 'components/DashboardWidgets/WidgetNoDataFallback'
import { TagLibraryContext } from 'components/DashboardPanel/TagLibraryContext'
import { DashboardLayoutController } from '@share/containers/WorkBenchNew/DashboardV2/agent/edit/DashboardLayoutController'
import { collectDashboardContentMeasurements } from '@share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardContentSizing'
import { hasCompleteMetricText } from '@share/containers/WorkBenchNew/DashboardV2/agent/edit/metricCompactProbe'
import { createConstraintMeasurement } from '@share/containers/WorkBenchNew/DashboardV2/agent/edit/dashboardConstraintMeasurement'
import { renderedRootWidgets, getRootCanvasCompactType } from '@share/containers/WorkBenchNew/DashboardV2/utils/rootCanvasLayout'
import { toWidgetBackgroundColor } from '@share/containers/WorkBenchNew/DashboardV2/utils/widgetBackgroundStyle'
import { computeFlatLayoutWidgetH, getWidgetParentId } from '@share/containers/WorkBenchNew/DashboardV2/utils/layoutUtils'
import { DASHBOARD_GRID_COLS, DASHBOARD_GRID_MARGIN, DASHBOARD_GRID_PADDING,
  DASHBOARD_GRID_ROW_HEIGHT, DASHBOARD_GRID_ITEM_BORDER_WIDTH } from '@share/containers/WorkBenchNew/DashboardV2/constants'
import canvasStyles from '@share/containers/WorkBenchNew/DashboardV2/components/Canvas/index.less'
import 'antd/dist/reset.css'

const api = window as any
const ResponsiveGrid = WidthProvider(Responsive)
const copy = value => JSON.parse(JSON.stringify(value))
const rows = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'].map((month, index) => ({
  month, revenue: [120, 180, 145, 260, 220, 310][index], orders: 10 + index * 3,
  city: ['A', 'B', 'C'][index % 3]
}))
const tags = { tags: [{ id: 160, name: '月度', color: '#047857' }], ready: true }
let revision = 1, dataRevision = 1, saves = 0, calls = 0, widgets = [], updateItems
let fixture, controller, generation = 0

function card(id, type, chartType, w, h, index) {
  return { id, name: id, type, x: 0, y: index * 22, w, h, order: index,
    data: copy(rows), datasetSnapshot: { nullable: null, rows: copy(rows) }, roles: [7],
    config: { chartType, cardUid: 'uid-' + id, rootLayoutMode: 'rows',
      filterList: [{ field: 'city', values: ['A', 'B', 'C'] }],
      datasets: [{ datasetUid: 'synthetic-' + id, nullable: null }], format: { nullable: null },
      cols: [{ key: 'month', name: 'Month' }],
      metrics: [{ key: 'revenue', name: 'Revenue', format: { decimal: 0, type: 'thousands' } }],
      xAxes: { label: { visible: true } }, yAxesLeft: { label: { visible: true } } }
  }
}

function metric(id, index, width, height) {
  const item = card(id, 'metric', 2001, width, height, index)
  item.name = ['收入', '订单', '客户', '成交', '回款', '转化', '客单', '访问', '留存'][index % 9]
  item.data = { value: 80 + index * 7, comparisons: [] }
  item.config = { ...item.config, scorecardTitle: item.name, scorecardFontSize: 'auto' }
  return item
}

// Independently rendered packing witnesses never replace controller output.
function makeFixture(name) {
  if (name === 'organize-metric-trend-rank' || name === 'organize-metrics-charts-table') {
    const first = metric('m1', 0, 4, 6)
    first.name = '近30天成交订单金额'; first.config.scorecardTitle = first.name
    first.data = { value: 1159598223, comparisons: [] }
    let items
    if (name === 'organize-metric-trend-rank') {
      const rank = card('rank', 'leaderboard', 11001, 6, 24, 2)
      rank.config = { ...rank.config, cols: [{ key: 'city', name: 'City' }],
        leaderboardStyle: 'podium', leaderboardLimit: 10 }
      rank.data = { rows: Array.from({ length: 10 }, (_, j) => ({ city: 'City ' + (j + 1), revenue: 980-j*61 })) }
      items = [first, card('trend', 'line', 4001, 14, 12, 1), rank]
      items.forEach((w, i) => { w.x = [0, 4, 18][i]; w.y = 0 })
    } else {
      const second = metric('m2', 1, 4, 6)
      const detail = card('detail', 'table', 1001, 12, 12, 4)
      detail.config.metrics.push({ key: 'orders', name: 'Orders', format: { decimal: 0, type: 'thousands' } })
      items = [first, second, card('share', 'pie', 5001, 12, 12, 2),
        card('distribution', 'bar', 3001, 12, 12, 3), detail]
      items.forEach((w, i) => { w.x = [0, 4, 8, 0, 12][i]; w.y = i < 3 ? 0 : 12 })
    }
    return { name, items, witness: [], automatic: true,
      request: { preset: { mode: 'organize', sizing: 'content' } } }
  }
  if (name === 'adjacent-hole' || name === 'tall-side-stack') {
    const ranks = ['rank-a', 'rank-b'].map((id, i) => {
      const w = card(id, 'leaderboard', 11001, 8, 18, i)
      w.config = { ...w.config, cols: [{ key: 'city', name: 'City' }],
        leaderboardStyle: 'podium', leaderboardLimit: 10 }
      w.data = { rows: Array.from({ length: 10 }, (_, j) => ({ city: 'City ' + (j + 1), revenue: 980 - j * 61 })) }
      return w
    })
    const items = [...(name === 'adjacent-hole'
      ? [card('trend', 'line', 4001, 12, 6, 0), card('share', 'pie', 5001, 8, 6, 1)] : []),
      ...ranks, metric('duration', 2, 8, 3), card('distribution', 'bar', 3001, 8, 6, 3),
      metric('average', 4, 8, 3), card('comparison', 'line', 4001, 8, 6, 5)]
    items.forEach((w, i) => { w.order = i; w.y = i * 22 })
    return { name, items, witness: [], automatic: true,
      request: { preset: { mode: 'compact', sizing: 'content', sizeOverrides:
        items.filter(w => w.type === 'metric' || w.type === 'leaderboard')
          .map(w => ({ widgetId: w.id, width: w.w, height: w.h })) } } }
  }
  if (name === 'metric-only-bottoms') {
    const metrics = [4, 5, 4, 4, 7].map((width, i) => metric('m' + i, i, width, 8))
    metrics.forEach((w, i) => {
      w.name = ['经营总览：近30天成交订单量', '经营总览：近30天成交订单金额',
        '经营总览：近30天成交率', '经营总览：近30天成交用户数', '经营总览：近30天取消订单量'][i]
      w.config.scorecardTitle = w.name
      w.data = { value: [927576, 1159598223, 0, 780357, 0][i], comparisons: [] }
    })
    const items = [...metrics, card('trend', 'line', 4001, 24, 6, 5)]
    return { name, items, witness: [], automatic: true,
      request: { preset: { mode: 'compact', sizing: 'content',
        sizeOverrides: [{ widgetId: 'trend', width: 24, height: 6 }] } } }
  }
  if (name.startsWith('group-background-')) {
    const cards = [card('amount', 'line', 4001, 12, 8, 0), card('orders', 'bar', 3001, 12, 8, 1)]
    cards.forEach((w, i) => { w.config.bgColor = name.endsWith('white') ? '#FFFFFF' : ['#EAF3FF', '#F2EEFF'][i] })
    const existing = card('existing', 'flatLayout', 19001, 24, 8, 2)
    existing.config.bgColor = '#FCE8E6'
    const child = metric('existing-child', 0, 12, 4)
    child.parentId = existing.config.cardUid; child.config.layoutParentId = existing.config.cardUid
    child.config.bgColor = '#DCE2E9'; child.config.bgOpacity = 0.5; child.y = 0
    const items = [...cards, existing, child]
    return { name, items, witness: [], automatic: true,
      request: { expectedResourceRevision: revision, preset: { mode: 'reorder', sizing: 'content',
        orderedWidgetIds: [...cards.map(w => w.id), existing.id], groupingConfirmed: true,
        groups: [{ title: 'Business summary', widgetIds: cards.map(w => w.id) }] } } }
  }
  if (name === 'column-balance' || name === 'column-balance-locked') {
    const ranks = ['rank-a', 'rank-b'].map((id, i) => {
      const w = card(id, 'leaderboard', 11001, 8, 14, i)
      w.config = { ...w.config, cols: [{ key: 'city', name: 'City' }],
        leaderboardStyle: 'podium', leaderboardLimit: 10 }
      w.data = { rows: Array.from({ length: 10 }, (_, j) => ({ city: 'City ' + (j + 1), revenue: 980 - j * 61 })) }
      return w
    })
    const items = [...ranks, card('trend', 'line', 4001, 8, 6, 2), card('share', 'pie', 5001, 8, 6, 3)]
    return { name, items, witness: [], automatic: true,
      request: { preset: { mode: 'reorder', sizing: 'content', orderedWidgetIds: items.map(w => w.id),
        sizeOverrides: items.map(w => ({ widgetId: w.id, width: w.w,
          ...(w.type === 'leaderboard' || name.endsWith('-locked') ? { height: w.h } : {}) })) } } }
  }
  if (name === 'mixed-metric-rows') {
    const metrics = Array.from({ length: 4 }, (_, i) => metric('m' + i, i, 4, 6))
    metrics.forEach((w, i) => {
      w.name = ['近30天成交订单量', '近30天成交用户数', '近30天成交率', '近30天成交订单金额'][i]
      w.config.scorecardTitle = w.name
      w.data = { value: [927576, 780357, 0, 1159598223][i], comparisons: [] }
    })
    const items = [metrics[0], metrics[1], card('share', 'pie', 5001, 8, 6, 2),
      card('trend', 'line', 4001, 8, 6, 3), metrics[2], metrics[3],
      card('empty-trend', 'line', 4001, 16, 6, 6)]
    items[6].data = []; items[6].datasetSnapshot.rows = []
    items.forEach((w, i) => { w.order = i; w.y = i * 8 })
    return { name, items, witness: [], automatic: true,
      request: { preset: { mode: 'compact', sizing: 'content', sizeOverrides:
        items.filter(w => w.type !== 'metric').map(w => ({ widgetId: w.id, width: w.w, height: w.h })) } } }
  }
  if (name === 'chart-group' || name === 'chart-group-preserve' || name === 'chart-pairs' || name === 'chart-pairs-offset') {
    const paired = name.startsWith('chart-pairs')
    const items = paired
      ? [card('amount', 'bar', 3001, 12, 8, 0), card('amount-share', 'pie', 5001, 8, 8, 1),
        card('orders', 'bar', 3001, 12, 8, 2), card('orders-share', 'pie', 5001, 8, 8, 3),
        card('empty-detail', 'bar', 3001, 12, 8, 4)]
      : [card('submitted', 'line', 4001, 12, 8, 0), card('completed', 'line', 4001, 12, 8, 1),
        card('empty-rate', 'line', 4001, 12, 8, 2)]
    items[items.length - 1].data = []
    items[items.length - 1].datasetSnapshot.rows = []
    if (name === 'chart-pairs-offset') {
      items.unshift(card('intro', 'line', 4001, 12, 8, 0))
      items.forEach((w, index) => { w.y = index * 22; w.order = index })
      items.find(w => w.id === 'amount').name = 'Monthly completed order amount by service region'
      items.find(w => w.id === 'amount-share').name = 'Monthly completed order amount share by service region'
    }
    return { name, items, witness: [], automatic: true,
      request: { expectedResourceRevision: revision, preset: { mode: 'reorder',
        sizing: name === 'chart-group-preserve' ? 'preserve' : 'content',
        orderedWidgetIds: items.map(w => w.id), groupingConfirmed: true,
        groups: [{ title: paired ? 'Comparisons' : 'Trends', widgetIds: items.map(w => w.id),
          comparisonPairs: paired ? [['amount', 'amount-share'], ['orders', 'orders-share']]
            : [['submitted', 'completed']] }] } } }
  }
  if (name === 'table-expand' || name === 'table-width-lock') {
    const detail = card('detail', 'table', 1001, 8, 10, 1)
    detail.name = 'Monthly settlement detail'
    detail.config.cols = [
      { key: 'month', name: 'Statement month' }, { key: 'city', name: 'Service region' }
    ]
    detail.config.metrics = [
      { key: 'revenue', name: 'Invoice revenue' }, { key: 'orders', name: 'Completed orders' },
      { key: 'settlement', name: 'Net settlement' }, { key: 'margin', name: 'Net margin' }
    ].map(column => ({ ...column, format: { decimal: 0, type: 'thousands' } }))
    detail.data = rows.map((row, i) => ({ ...row, month: '2026-' + ('0' + (i + 1)).slice(-2),
      city: ['North district', 'Central district', 'South district'][i % 3],
      revenue: 120000 + i * 23000, settlement: 98000 + i * 19000, margin: 18000 + i * 1300 }))
    detail.datasetSnapshot.rows = copy(detail.data)
    return { name, items: [detail], witness: [], automatic: false,
      request: { preset: { mode: 'compact', sizing: 'content',
        ...(name === 'table-width-lock' ? { sizeOverrides: [{ widgetId: 'detail', width: 8 }] } : {}) } } }
  }
  const nine = name === 'shelves-9'
  const shapes = nine ? [[4, 3], [4, 3], [4, 3], [5, 4], [3, 4], [4, 4], [3, 3], [4, 3], [5, 3]]
    : [[5, 4], [3, 4], [4, 4], [3, 3], [3, 3], [3, 3], [3, 3]]
  const automatic = name === 'automatic' || name === 'grouping' || name === 'grouping-widths'
  const metrics = shapes.map(([w, h], i) => metric('m' + i, i, automatic ? 6 : w, automatic ? 8 : h))
  const chartHeight = nine || automatic ? 10 : 7
  const trend = card('trend', 'line', 4001, 12, chartHeight, 3)
  let items = [...metrics.slice(0, 3), trend, ...metrics.slice(3)]
  const detail = card('detail', 'table', 1001, 24, 8, items.length)
  detail.config.cols = [{ key: 'month', name: 'Month' }, { key: 'city', name: 'City' }]
  detail.config.metrics.push({ key: 'orders', name: 'Orders', format: { decimal: 0, type: 'thousands' } })
  items.push(detail)
  let sizes = items.filter(w => !automatic || w.type !== 'metric')
    .map(w => ({ widgetId: w.id, width: w.w, height: w.h }))
  let witness = []
  if (name !== 'grouping') {
    let y = 0
    for (let i = 0; i < shapes.length;) {
      const count = i === 0 || nine ? 3 : 4
      let x = 0
      for (const [w, h] of shapes.slice(i, i + count)) {
        witness.push({ id: 'm' + i++, x, y, w, h }); x += w
      }
      y += shapes[i - 1][1]
    }
    witness.push({ id: 'trend', x: 12, y: 0, w: 12, h: chartHeight },
      { id: 'detail', x: 0, y: chartHeight, w: 24, h: 8 })
  }
  if (name === 'automatic') {
    witness = metrics.map((w, i) => i < 3
      ? { id: w.id, x: i * 4, y: 0, w: 4, h: 3 }
      : { id: w.id, x: ((i - 3) % 2) * 6,
        y: 3 + Math.floor((i - 3) / 2) * 3, w: 6, h: 3 })
    witness.push({ id: 'trend', x: 12, y: 0, w: 12, h: chartHeight },
      { id: 'detail', x: 0, y: chartHeight, w: 24, h: 8 })
  }
  if (name === 'rank-stack') {
    const rank = card('rank', 'leaderboard', 11001, 8, 20, 0)
    rank.config = { ...rank.config, cols: [{ key: 'city', name: 'City' }], leaderboardStyle: 'podium', leaderboardLimit: 8 }
    rank.data = { rows: Array.from({ length: 8 }, (_, i) => ({ city: 'C' + (i + 1), revenue: 980 - i * 61 })) }
    items = [rank, card('trend-a', 'line', 4001, 16, 10, 1),
      card('trend-b', 'bar', 3001, 16, 10, 2), detail]
    sizes = items.filter(w => w.id !== 'rank').map(w => ({ widgetId: w.id, width: w.w, height: w.h }))
    witness = [{ id: 'rank', x: 0, y: 0, w: 8, h: 20 },
      { id: 'trend-a', x: 8, y: 0, w: 16, h: 10 },
      { id: 'trend-b', x: 8, y: 10, w: 16, h: 10 },
      { id: 'detail', x: 0, y: 20, w: 24, h: 8 }]
  }
  if (name === 'grouping-widths') items = [...metrics.slice(0, 6), detail]
  items.forEach((w, i) => { w.order = i; w.y = i * 22 })
  let request = { preset: { mode: 'compact', sizing: 'content', sizeOverrides: sizes } } as any
  if (name === 'grouping') {
    const oldGroup = card('existing', 'flatLayout', 19001, 24, 9, items.length)
    oldGroup.config.title = 'Existing protected group'
    const child = metric('existing-child', 0, 12, 4)
    child.x = 0; child.y = 0; child.parentId = oldGroup.config.cardUid
    child.config.layoutParentId = oldGroup.config.cardUid
    items.push(oldGroup, child)
    request = { expectedResourceRevision: revision, preset: { mode: 'reorder', sizing: 'content',
      orderedWidgetIds: items.filter(w => !getWidgetParentId(w)).map(w => w.id),
      groups: [{ title: 'Metrics and trend', widgetIds: items.slice(0, -3).map(w => w.id) }],
      groupingConfirmed: true } }
  }
  if (name === 'grouping-widths') {
    request = { expectedResourceRevision: revision, preset: { mode: 'reorder', sizing: 'content',
      orderedWidgetIds: items.map(w => w.id), groupingConfirmed: true,
      groups: [{ title: 'Metrics A', widgetIds: ['m0', 'm1', 'm2'] },
        { title: 'Metrics B', widgetIds: ['m3', 'm4', 'm5'] }] } }
  }
  return { name, items, request, witness, automatic }
}

function renderCard(current, ready, items) {
  const group = current.type === 'flatLayout'
  return <div key={current.id} data-widget-card data-widget-id={current.id}
    data-testid={'dashboard-v2-widget-card-' + current.id} data-content-measure-ready={ready}
    style={{ width: '100%', height: '100%', position: 'relative',
      background: toWidgetBackgroundColor(current.config.bgColor, current.config.bgOpacity), boxSizing: 'border-box',
      paddingTop: group ? 50 : 32, overflow: 'hidden' }}>
    {current.type !== 'metric' && <header data-card-heading style={{ position: 'absolute',
      top: 6, left: 12, right: 12, fontSize: 14 }}>{current.config.title || current.name}</header>}
    {group ? <LayoutWidget chartType={19001} layoutWidget={current} allWidgets={items} readonly
      renderChild={child => renderCard(child, ready, items)}/>
      : current.type === 'metric' ? <MetricWidget widget={current} value={current.data.value}
        contrastResults={current.data.comparisons} isPending={!ready}/>
        : current.type === 'leaderboard' ? <LeaderboardWidget widget={current} data={current.data} isPending={!ready}/>
          : current.type === 'table' ? <TableWidget config={current.config} data={current.data}/>
            : Array.isArray(current.data) && current.data.length === 0 ? <WidgetNoDataFallback/>
            : <Chart config={current.config} data={current.data} isPending={!ready}/>}
  </div>
}

function App() {
  const [state, setState] = useState({ items: [], generation: 0 })
  updateItems = items => setState({ items, generation })
  const roots = renderedRootWidgets(state.items)
  return <TagLibraryContext.Provider value={tags}>
    <div key={state.generation} data-testid="dashboard-v2-canvas" style={{ position: 'relative',
      width: 'calc(100vw - 16px)', height: Math.max(1, ...roots.map(w => w.y + w.h)) * 40 }}>
      <ResponsiveGrid className={canvasStyles.grid}
        layouts={{ lg: roots.map(w => ({ i: String(w.id), x: w.x, y: w.y, w: w.w, h: w.h })) }}
        compactType={getRootCanvasCompactType(state.items)} breakpoints={{ lg: 0 }}
        cols={{ lg: DASHBOARD_GRID_COLS }} rowHeight={DASHBOARD_GRID_ROW_HEIGHT}
        margin={DASHBOARD_GRID_MARGIN} containerPadding={DASHBOARD_GRID_PADDING}
        isDraggable={false} isResizable={false}>
        {roots.map(w => <div key={String(w.id)}
          className={`${canvasStyles.gridItem} ${canvasStyles.gridItemReadonly}`}
          style={{ borderWidth: DASHBOARD_GRID_ITEM_BORDER_WIDTH }}
          data-testid={'dashboard-v2-canvas-widget-' + w.id} data-root-frame>
          {renderCard(w, true, state.items)}
        </div>)}
      </ResponsiveGrid>
    </div>
  </TagLibraryContext.Provider>
}

ReactDOM.render(<App/>, document.getElementById('root'))

async function settle() {
  await document.fonts.ready
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  await new Promise(resolve => setTimeout(resolve, 600))
}

function rect(element) {
  const r = element.getBoundingClientRect()
  return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom }
}

function inspect() {
  const rendered = widgets.map(w => {
    const element = document.querySelector('[data-widget-id="' + w.id + '"]') as HTMLElement
    const ink = Array.from(element.querySelectorAll('canvas')).map((canvas: HTMLCanvasElement) => {
      const context = canvas.getContext('2d')
      if (!context) return 0
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
      let count = 0
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] > 0 && Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) < 220) count++
      }
      return count
    })
    return { id: w.id, pixels: rect(element), text: element.innerText, ink,
      metricComplete: w.type === 'metric' ? hasCompleteMetricText(element) : null,
      tableRows: element.querySelectorAll('tbody tr[data-row-key]').length,
      tableHeaders: Array.from(element.querySelectorAll('thead th')).map((e: HTMLElement) => e.innerText) }
  })
  const rootFrames = renderedRootWidgets(widgets).map(w => {
    const frame = document.querySelector('[data-testid="dashboard-v2-canvas-widget-' + w.id + '"]') as HTMLElement
    const card = frame.querySelector('[data-widget-card]') as HTMLElement
    const grid = card.querySelector('.react-grid-layout') as HTMLElement
    const style = getComputedStyle(frame)
    return { id: w.id, frame: rect(frame), card: rect(card), innerCanvas: grid ? rect(grid) : null,
      boxSizing: style.boxSizing,
      borderWidths: [style.borderLeftWidth, style.borderRightWidth, style.borderTopWidth,
        style.borderBottomWidth].map(parseFloat),
      borderX: parseFloat(style.borderLeftWidth) + parseFloat(style.borderRightWidth),
      borderY: parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth) }
  })
  return { widgets: copy(widgets), roots: renderedRootWidgets(copy(widgets)), rendered, rootFrames,
    canvas: rect(document.querySelector('[data-testid="dashboard-v2-canvas"]')) }
}

api.prepareRegion = async name => {
  generation++; revision++; dataRevision++; saves = 0; calls = 0
  fixture = makeFixture(name); widgets = copy(fixture.items); updateItems(widgets)
  controller = new DashboardLayoutController({
    getSnapshot: () => ({ widgets, pageState: { schemaVersion: 'davinci-page-state-v1',
      page: { kind: 'dashboard', instanceId: 'synthetic-region', route: '/', resource: { type: 'dashboard', id: 'local-fixture' } },
      revisions: { resourceRevision: revision, dataRevision, routeRevision: 1 },
      permissions: { canRead: true, canOperate: true, canPersist: true },
      ui: { busy: false, activeFilters: [] }, dataStatus: { loadingWidgetIds: [], errorWidgetIds: [] } } }),
    persist: async (before, next, guard) => { guard?.(); saves++; revision++; widgets = next; updateItems(next); return next },
    persistGroups: async (before, next, guard) => {
      guard(); saves++; revision++
      widgets = next.map((w, i) => w._isNew ? { ...w, id: 'saved-group-' + i, _isNew: undefined } : w)
      updateItems(widgets); return widgets
    },
    getResourceRevision: () => revision, now: () => new Date().toISOString()
  })
  await settle()
  return { ...inspect(), request: fixture.request, witness: fixture.witness }
}

api.runRegion = async () => {
  const before = copy(widgets), started = performance.now()
  calls++
  const result = await controller.apply(fixture.request,
    { signal: new AbortController().signal, expiresAt: Date.now() + 30000,
      ...(api.solveRegion ? { solveLayout: api.solveRegion } : {}) })
  await settle()
  return { ...inspect(), before, result, saves, calls, localToolAndRenderMs: performance.now() - started }
}

// Diagnostic only: never supplied to the controller's own measurement dependency.
api.inspectMeasurements = () => {
  const state = () => ({ widgets: copy(widgets), revision, dataRevision, calls, saves,
    metricDom: widgets.filter(w => w.type === 'metric').map(w =>
      document.querySelector('[data-widget-id="' + w.id + '"]').outerHTML) })
  const before = state()
  const measurement = copy(collectDashboardContentMeasurements(widgets))
  return { measurement, before, after: state() }
}

// Separate diagnostic page only; never injected into the controller's measurement path.
api.inspectSupplementalShapes = () => {
  const state = () => ({ widgets: copy(widgets), revision, dataRevision, calls, saves,
    metricDom: widgets.filter(w => w.type === 'metric').map(w =>
      document.querySelector('[data-widget-id="' + w.id + '"]').outerHTML) })
  const before = state()
  const metrics = widgets.filter(w => w.type === 'metric' && !getWidgetParentId(w))
  const measurement = createConstraintMeasurement(metrics, Date.now() + 10000)
  const samples = metrics.map(widget => ({ widgetId: String(widget.id),
    candidates: [4, 6, 8].map(width => ({ requested: { width, height: 3 },
      verified: measurement.measure(widget, measurement.canvasWidth, false, { width, height: 3 }) })) }))
  return { canvasWidth: measurement.canvasWidth, samples, before, after: state() }
}

// Verify the independent known-packing witness with the real renderers, without saving it.
api.inspectRegionWitness = async screenshotName => {
  const saved = widgets
  try {
    if (fixture.name === 'grouping-widths') {
      const groups = widgets.filter(w => w.type === 'flatLayout')
      if (groups.length !== 2) return null
      const height = computeFlatLayoutWidgetH({ contentBottom: 4 })
      widgets = widgets.map(w => {
        const groupIndex = groups.findIndex(group => group.id === w.id)
        if (groupIndex >= 0) return { ...w, x: groupIndex * 12, y: 0, w: 12, h: height }
        if (w.type === 'metric') return { ...w, x: (Number(w.id.slice(1)) % 3) * 8, y: 0, w: 8, h: 4 }
        return { ...w, x: 0, y: height, w: 24, h: 8 }
      })
    } else if (fixture.name === 'grouping') {
      const group = widgets.find(w => w.type === 'flatLayout' && w.id !== 'existing')
      if (!group) return null
      const height = computeFlatLayoutWidgetH({ contentBottom: 12 })
      const positions = [
        { id: 'm0', x: 0, y: 0, w: 8, h: 4 }, { id: 'm1', x: 8, y: 0, w: 8, h: 4 },
        { id: 'm2', x: 16, y: 0, w: 8, h: 4 }, { id: 'trend', x: 0, y: 4, w: 12, h: 8 },
        { id: 'm3', x: 12, y: 4, w: 6, h: 4 }, { id: 'm4', x: 18, y: 4, w: 6, h: 4 },
        { id: 'm5', x: 12, y: 8, w: 6, h: 4 }, { id: 'm6', x: 18, y: 8, w: 6, h: 4 },
        { id: group.id, x: 0, y: 0, w: 24, h: height },
        { id: 'detail', x: 0, y: height, w: 24, h: 8 },
        { id: 'existing', x: 0, y: height + 8, w: 24, h: 9 }
      ]
      widgets = widgets.map(w => ({ ...w, ...positions.find(item => item.id === w.id) }))
    } else {
      if (!fixture.witness.length) return null
      widgets = fixture.items.map(w => ({ ...w, ...fixture.witness.find(item => item.id === w.id) }))
    }
    updateItems(widgets); await settle()
    const result = inspect() as any
    if (screenshotName) await api.captureRegionWitness(screenshotName)
    if (fixture.name === 'grouping') {
      const tailIds = ['m3', 'm4', 'm5', 'm6']
      const tail = saved.filter(w => tailIds.includes(w.id))
      const parent = tail[0] && getWidgetParentId(tail[0])
      const others = saved.filter(w => getWidgetParentId(w) === parent && !tailIds.includes(w.id))
      const isolated = tail.length === 4 && tail.every(w => w.y === tail[0].y) &&
        others.every(w => w.y + w.h <= tail[0].y) && Math.max(...tail.map(w => w.h)) >= 4
      if (isolated) {
        widgets = saved.map(w => tailIds.includes(w.id)
          ? { ...w, x: tailIds.indexOf(w.id) * 6, w: 6, h: 4 } : w)
        updateItems(widgets); await settle()
        result.tailFill = inspect()
        if (screenshotName) await api.captureRegionWitness(screenshotName.replace('.png', '-tail-fill.png'))
      }
    }
    return result
  } finally { widgets = saved; updateItems(widgets); await settle() }
}
