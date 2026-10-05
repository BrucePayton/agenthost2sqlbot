// 数巢智能体面板的纯展示逻辑：把 Davinci PageState、Session 列表和工具名
// 翻译成面板上直接可渲染的中文文案，不触碰 DOM。
import { parseServerTime } from './server-time.js';

const PAGE_AREAS = {
  workspace: '个人工作台',
  dashboard: '仪表盘',
  'dataset-marketplace': '数据集市',
  'dataset-editor': '数据集',
};
const FALLBACK_AREA = '数巢页面';

const SUGGESTIONS = {
  dashboard: [
    '分析当前看板的指标异常和主要下降原因',
    '解释当前看板核心指标的统计口径',
    '给当前看板补一个按品类下钻的组件',
  ],
  'dataset-marketplace': [
    '查找回收订单从提交到成交的转化指标数据集',
    '对比候选数据集的字段覆盖和数据粒度',
    '说明这个数据集的更新频率和负责人',
  ],
  'dataset-editor': [
    '解释当前数据集各字段的业务口径',
    '检查当前数据集的关联键和数据粒度',
    '基于当前数据集搭一个日报看板',
  ],
  workspace: [
    '找到华东回收转化相关的报表',
    '汇总我最近常看的核心指标',
    '基于回收订单数据集搭一个日报看板',
  ],
};
const FALLBACK_SUGGESTIONS = [
  '帮我找到相关的报表或数据集',
  '解释这个指标的统计口径',
  '分析最近的指标异常和原因',
];

const EFFORT_LABELS = {
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '极高',
  max: '最大',
};

const TOOL_DOMAINS = {
  dashboard: '仪表盘',
  dataset: '数据集',
  workspace: '工作台',
  navigation: '页面',
  catalog: '数据目录',
};
const TOOL_VERBS = {
  get: '读取',
  read: '读取',
  list: '读取',
  describe: '读取',
  apply: '更新',
  update: '更新',
  set: '更新',
  edit: '更新',
  add: '新建',
  create: '新建',
  delete: '删除',
  remove: '删除',
  clear: '清除',
  capture: '截取',
  open: '打开',
  close: '关闭',
  publish: '发布',
  focus: '定位',
  copy: '复制',
  enter: '切换',
  exit: '切换',
  navigate: '跳转',
  query: '查询',
  search: '查询',
};

const DEFAULT_SESSION_LIMIT = 8;
const DEFAULT_SESSION_TITLE = '未命名会话';

/** 当前 Davinci 页面属于哪个区域、正在看哪个对象。 */
export function describePageContext(state) {
  const page = state?.page;
  if (!page || typeof page.kind !== 'string') {
    return { available: false, kind: '', area: '', name: '' };
  }
  const resource = page.resource;
  const name = resource?.name || (resource?.id ? `#${resource.id}` : '');
  return {
    available: true,
    kind: page.kind,
    area: PAGE_AREAS[page.kind] || FALLBACK_AREA,
    name,
  };
}

const LEGACY_PAGE_KINDS = {
  dashboard: "dashboard",
  dataset: "dataset-marketplace",
};

/** 把 v1 协议的 hostContext 抬成 v2 PageState 的形状，让上下文条对两条链路一致。 */
export function legacyPageState(hostContext) {
  const kind = LEGACY_PAGE_KINDS[hostContext?.pageType];
  if (!kind) return null;
  const resourceId = hostContext.resourceId;
  return {
    page: {
      instanceId: "legacy",
      kind,
      route: "",
      ...(resourceId
        ? { resource: { type: hostContext.pageType, id: resourceId } }
        : {}),
    },
  };
}

/** 空闲态「猜你想问」的三条候选，按页面类型给。 */
export function suggestionsFor(kind) {
  return [...(SUGGESTIONS[kind] || FALLBACK_SUGGESTIONS)];
}

/** 推理强度在输入框上的中文标签。 */
export function effortLabel(value) {
  return EFFORT_LABELS[value] || value;
}

/** 把 `domain.verb_object` 形式的工具名翻译成执行态文案。 */
export function describeToolCall(name) {
  const hint = typeof name === 'string' ? name : '';
  const [domain, rest] = hint.split('.');
  const verb = TOOL_VERBS[(rest || '').split('_')[0]];
  const domainLabel = TOOL_DOMAINS[domain];
  if (!verb || !domainLabel) {
    return { runningTitle: '正在调用工具', doneTitle: '已调用工具', hint };
  }
  return {
    runningTitle: `正在${verb}${domainLabel}`,
    doneTitle: `已${verb}${domainLabel}`,
    hint,
  };
}

/** Compare dates in the viewer's local timezone after decoding server UTC. */
function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function timeLabelFor(value, now) {
  if (!value) return '';
  const date = parseServerTime(value);
  if (Number.isNaN(date.getTime())) return '';
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) {
    const pad = (part) => String(part).padStart(2, '0');
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }
  if (days === 1) return '昨天';
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function timestampOf(session) {
  const value = parseServerTime(session?.updated_at ?? 0).getTime();
  return Number.isNaN(value) ? 0 : value;
}

/** 会话列表（rail 与头部历史共用）：按最近更新排序并标出当前会话。 */
export function sessionListModel(sessions, activeId, options = {}) {
  if (!Array.isArray(sessions)) return [];
  const now = options.now instanceof Date ? options.now : new Date();
  const limit = Number.isInteger(options.limit) ? options.limit : DEFAULT_SESSION_LIMIT;
  return [...sessions]
    .sort((left, right) => timestampOf(right) - timestampOf(left))
    .slice(0, limit)
    .map((session) => ({
      id: session.id,
      title: session.title || DEFAULT_SESSION_TITLE,
      active: Boolean(activeId) && session.id === activeId,
      timeLabel: timeLabelFor(session.updated_at, now),
    }));
}
