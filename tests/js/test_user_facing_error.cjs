const assert = require('node:assert/strict')
const test = require('node:test')

const formatter = import('../../web/embed/user-facing-error.js')

test('nested receipt issues explain the actual card, not the generic outer code', async () => {
  const { formatToolReceipt } = await formatter
  const receipt = {status: 'error', error: {code: 'INVALID_ARGUMENT', message: 'SQL secret'}, issues: [
    {code: 'LAYOUT_NO_READABLE_CANDIDATES', widgetIds: ['card-7'],
      constraints: {chartType: 11001, widgetTitle: '区域营收'}}
  ]}
  const before = JSON.stringify(receipt)
  const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error', outputPreview: before})
  assert.match(text, /调整看板布局/)
  assert.match(text, /排行榜.*区域营收/)
  assert.match(text, /布局.*技术支持/)
  assert.match(text, /保存.*确认|确认.*保存/)
  assert.doesNotMatch(text, /INVALID_ARGUMENT|SQL|secret|已回滚|未保存|未做任何修改/)
  assert.equal(JSON.stringify(receipt), before)
})

test('unknown issues, truncated previews and loading do not invent causes or cards', async () => {
  const { formatToolReceipt } = await formatter
  for (const receipt of [
    {status: 'error', issues: [{code: 'FUTURE_CODE', message: '排行榜2073没有数据', widgetIds: ['7']}]},
    {status: 'error', data: {loadingWidgetIds: ['7']}, error: {code: 'LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE'}},
    '{"status":"error","data":{"persisted":true},"issues":['
  ]) {
    const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error',
      inputPreview: '{"widgetTitle":"假标题","chartType":11001}',
      outputPreview: typeof receipt === 'string' ? receipt : JSON.stringify(receipt)})
    assert.match(text, /核对|检查/)
    assert.doesNotMatch(text, /没有数据|排行榜|2073|假标题|FUTURE_CODE|未保存|已保存/)
  }
})

test('write status comes only from a complete receipt, not success or false alone', async () => {
  const { formatToolReceipt } = await formatter
  const show = (data, options) => formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done',
    outputPreview: JSON.stringify({status: 'success', data, issues: []})}, options)
  assert.match(show({persisted: true}), /已保存/)
  assert.match(show({persisted: false, preview: true}), /预览.*未保存/)
  assert.match(show({persisted: false}), /未确认保存/)
  assert.doesNotMatch(show({persisted: false}), /未做任何修改|已回滚/)
  assert.match(show({}), /保存.*确认|确认.*保存/)
  assert.doesNotMatch(show({persisted: true}, {outcomeUnknown: true}), /已保存/)
})

test('success warnings are limitations, not failed operations', async () => {
  const { formatToolReceipt, businessToolLabel } = await formatter
  const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done', outputPreview: JSON.stringify({
    status: 'success', data: {persisted: true}, issues: [{code: 'LAYOUT_REMAINING_SPACE', message: 'raw'}]
  })})
  assert.match(text, /已保存/)
  assert.match(text, /留白/)
  assert.doesNotMatch(text, /失败|未完成|raw|LAYOUT_/)
  assert.equal(businessToolLabel('dashboard.set_widget_layout'), '调整看板布局')
  assert.equal(businessToolLabel('Read'), '读取文件')
  assert.doesNotMatch(businessToolLabel('unknown.private_tool'), /unknown|private_tool/)
})

test('conflicting or incomplete receipts override persisted true with unknown outcome', async () => {
  const {formatToolReceipt} = await formatter
  const base = {status: 'success', data: {persisted: true}, issues: []}
  const conflicts = [
    {...base, issues: [{code: 'LAYOUT_NO_READABLE_CANDIDATES', constraints: {writeDispatched: false}}]},
    {...base, issues: [{code: 'UNKNOWN', constraints: {committed: false}}]},
    {...base, data: {persisted: true, preview: true}},
    {...base, constraints: {persisted: false}},
    {...base, error: {details: {committed: false}}},
    {...base, writeDispatched: false},
    {...base, issuesTruncated: true},
    {...base, issueCount: 1},
    {...base, returnedIssueCount: 1},
  ]
  for (const receipt of conflicts) {
    const original = JSON.stringify(receipt)
    const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done', outputPreview: original})
    assert.match(text, /保存结果尚待确认.*核对.*不要重复提交/s, original)
    assert.doesNotMatch(text, /已保存|未保存|未发起写入|已回滚/, original)
    assert.equal(JSON.stringify(receipt), original)
  }
  assert.match(formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done', outputPreview: JSON.stringify(base)}), /已保存/)
})

test('terminal no-write evidence and native widget type are shown without guessing save state', async () => {
  const { formatToolReceipt } = await formatter
  const receipt = {status: 'error', issues: [{code: 'LAYOUT_NO_READABLE_CANDIDATES', widgetIds: ['7'],
    constraints: {widgetType: 'Metric', widgetTitle: '总营收', solverFinal: true, writeDispatched: false}}]}
  const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error', outputPreview: JSON.stringify(receipt)})
  assert.match(text, /指标卡.*总营收/)
  assert.match(text, /本次未发起写入/)
  assert.doesNotMatch(text, /未保存|已回滚|保存结果尚待确认/)
  receipt.issues[0].constraints.writeDispatched = true
  assert.match(formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error', outputPreview: JSON.stringify(receipt)}), /保存结果尚待确认/)
})

test('empty canvas is a successful no-op notice, not a failure or save confirmation', async () => {
  const { formatToolReceipt } = await formatter
  const text = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done', outputPreview: JSON.stringify({
    status: 'success', data: {persisted: false}, issues: [{code: 'LAYOUT_EMPTY_CANVAS', message: 'raw'}]
  })})
  assert.match(text, /没有.*卡片.*无需调整/)
  assert.doesNotMatch(text, /失败|有限制|技术支持|已保存|保存结果尚待确认/)
})

test('ordinary copy uses trusted Chinese type and title, never internal IDs or invented multi-card identity', async () => {
  const {formatToolReceipt} = await formatter
  const show = issue => formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error',
    outputPreview: JSON.stringify({status: 'error', issues: [issue]})})
  const issue = {code: 'LAYOUT_NO_READABLE_CANDIDATES', widgetIds: ['15666'],
    constraints: {widgetType: '指标卡', widgetTitle: '业务营收', chartType: 999999}}
  assert.match(show(issue), /指标卡“业务营收”/)
  assert.doesNotMatch(show(issue), /15666|编号|999999/)
  assert.doesNotMatch(show({...issue, widgetIds: ['15666', '15667']}), /业务营收|15666|15667|指标卡/)
  assert.doesNotMatch(show({...issue, constraints: {widgetType: 999999, widgetTitle: '业务营收'}}), /指标卡|排行榜|999999/)
  assert.match(show({...issue, constraints: {widgetType: 11001, widgetTitle: '业务营收'}}), /排行榜“业务营收”/)
})

test('no-write statement requires complete unanimous prewrite evidence with no contradictory receipt state', async () => {
  const {formatToolReceipt} = await formatter
  const issue = {code: 'LAYOUT_NO_READABLE_CANDIDATES', constraints: {solverFinal: true, writeDispatched: false}}
  const base = {status: 'error', issues: [issue]}
  const show = receipt => formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'error', outputPreview: JSON.stringify(receipt)})
  assert.match(show(base), /本次未发起写入/)
  const ambiguous = [
    {...base, issuesTruncated: true}, {...base, issueCount: 2},
    {...base, issues: [issue, {code:'UNKNOWN'}]}, {...base, status: 'partial'},
    {...base, error: {details: {unknown: true}}}, {...base, data: {constraints: {committed:true}}},
    {...base, issues:[issue, {...issue, constraints:{solverFinal:true,writeDispatched:true}}]},
  ]
  for (const layer of ['', 'error', 'data', 'constraints']) {
    for (const state of [{committed:true}, {unknown:true}, {writeDispatched:true}, {persisted:true}, {writeStatus:'unknown'}, {writeStatus:'committed'}]) {
      ambiguous.push(layer ? {...base, [layer]: state} : {...base, ...state})
    }
  }
  for (const receipt of ambiguous) assert.doesNotMatch(show(receipt), /本次未发起写入/, JSON.stringify(receipt))
  const partial = formatToolReceipt({name: 'dashboard.set_widget_layout', state: 'done', outputPreview: JSON.stringify({status:'partial', issues:[issue]})})
  assert.match(partial, /部分完成/)
  assert.doesNotMatch(partial, /未保存|未发起写入/)
})

test('local attachment validation remains actionable without accepting arbitrary prose', async () => {
  const {formatUserFacingError} = await formatter
  assert.equal(formatUserFacingError('当前不能上传图片。'), '当前不能上传图片。')
  assert.equal(formatUserFacingError('最多还能添加 2 个附件。'), '最多还能添加 2 个附件。')
  assert.doesNotMatch(formatUserFacingError('最多还能添加 2 个附件。secret'), /secret/)
})

test('native chart identities match the actual Lark type dictionary', async () => {
  const {formatToolReceipt} = await formatter
  for (const [type, label] of [[1001,'表格'], [2001,'指标卡'], [3001,'基础柱状图'], [13001,'透视表'],
    [19001,'平铺布局'], [19002,'标签布局'], ['堆积柱状图','堆积柱状图']]) {
    const text = formatToolReceipt({name:'dashboard.set_widget_layout', state:'error', outputPreview: JSON.stringify({
      status:'error', issues:[{code:'LAYOUT_CONTENT_OVERFLOW',widgetIds:['15666'],constraints:{widgetType:type,widgetTitle:'业务卡片'}}]
    })})
    assert.match(text, new RegExp(`${label}“业务卡片”`))
    assert.doesNotMatch(text, /15666|编号/)
  }
})

test('layout reasons have distinct actions and successful local rules are informational', async () => {
  const {formatToolReceipt, formatUserFacingError} = await formatter
  for (const [code, reason, action] of [
    ['LAYOUT_CANCELLED', /停止/, /执行状态/],
    ['LAYOUT_CANVAS_CHANGED', /页面大小/, /窗口.*缩放/],
    ['LAYOUT_CONTENT_CHANGED', /内容.*变化/, /查询.*筛选/],
    ['LAYOUT_CONTENT_OVERFLOW', /完整显示/, /宽高.*范围/],
  ]) {
    const text = formatUserFacingError({code})
    assert.match(text, reason, code); assert.match(text, action, code)
    assert.doesNotMatch(text, /检查.*是否正常显示|还在加载|没有数据/)
  }
  const text = formatToolReceipt({name:'dashboard.set_widget_layout', state:'done', outputPreview: JSON.stringify({
    status:'success',data:{persisted:true},issues:[{code:'LAYOUT_LOCAL_RULE',message:'raw'}]
  })})
  assert.match(text, /已保存/)
  assert.doesNotMatch(text, /限制|失败|技术支持|raw|LAYOUT/)
})

test('layout failures expose trusted correlation diagnostics without raw backend prose', async () => {
  const {formatToolReceipt} = await formatter
  const text = formatToolReceipt({name:'dashboard.set_widget_layout', state:'error', outputPreview: JSON.stringify({
    status:'error',
    error:{code:'LAYOUT_SOLVER_infeasible_candidates', message:'secret card-15666 payload'},
    diagnostics:{
      code:'LAYOUT_SOLVER_infeasible_candidates', stage:'candidate_search', retryable:false,
      writeDispatched:false, sessionId:'session-123', toolCallId:'tool-456', layoutRunId:'layout-789',
      privateCardId:'15666'
    }
  })})
  assert.match(text, /没有找到满足约束的候选布局/)
  assert.match(text, /code=LAYOUT_SOLVER_infeasible_candidates/)
  assert.match(text, /stage=candidate_search/)
  assert.match(text, /retryable=false/)
  assert.match(text, /writeDispatched=false/)
  assert.match(text, /sessionId=session-123/)
  assert.match(text, /toolCallId=tool-456/)
  assert.match(text, /layoutRunId=layout-789/)
  assert.doesNotMatch(text, /secret|15666|privateCardId/)

  const bridgeText = formatToolReceipt({name:'dashboard.set_widget_layout', state:'error', outputPreview: JSON.stringify({
    schemaVersion:'davinci-tool-error-v1', ok:false, code:'INVALID_ARGUMENT', message:'raw schema internals',
    diagnostics:{code:'INVALID_ARGUMENT', stage:'argument_validation', retryable:false,
      writeDispatched:false, sessionId:'session-123', toolCallId:'tool-456', layoutRunId:null}
  })})
  assert.match(bridgeText, /布局参数未通过系统校验/)
  assert.match(bridgeText, /code=INVALID_ARGUMENT/)
  assert.match(bridgeText, /本次未发起写入/)
  assert.doesNotMatch(bridgeText, /raw schema internals/)
})

test('exact trusted post-layout model-failure notice overrides retry advice without trusting arbitrary prose', async () => {
  const {formatUserFacingError} = await formatter
  const message = '布局结果以上方业务摘要为准。后续模型会话未能完成，不要重复提交布局操作；如需排查，请联系管理员并提供操作时间。'
  for (const code of ['claude_rate_limited','claude_auth_failed','claude_unavailable']) {
    assert.equal(formatUserFacingError({code,message}), message)
    assert.equal(formatUserFacingError({code,message}, {outcomeUnknown:true}), message)
  }
  assert.doesNotMatch(formatUserFacingError({code:'claude_rate_limited',message:message+'secret'}), /secret|以上方业务摘要/)
})

test('known errors explain the next step without exposing backend details', async () => {
  const { formatUserFacingError } = await formatter
  const cases = [
    ['identity_missing', 401, /登录/, /重新认证/],
    ['forbidden', 403, /权限/, /管理员/],
    ['claude_auth_failed', 502, /服务配置/, /管理员/],
    ['claude_rate_limited', 503, /繁忙/, /稍后/],
    ['invalid_request', 422, /请求/, /技术支持/],
    ['session_not_found', 404, /会话/, /重新打开/],
    ['workspace_not_found', 404, /权限|访问/, /管理员/],
    ['session_busy', 409, /执行/, /等待/],
    ['GROUPING_UNSAVED_CHANGES', 409, /页面状态/, /保存/],
    ['LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE', undefined, /显示内容/, /技术支持/],
    ['LAYOUT_NO_READABLE_CANDIDATES', undefined, /布局/, /技术支持/],
    ['CONTRACT_MISMATCH', 409, /版本/, /管理员/],
    ['attachment_too_large', 413, /文件/, /缩小/],
    ['invalid_skill_bundle', 400, /技能/, /文件/],
    ['instructions_changed', 409, /他人/, /最新/],
  ]
  for (const [code, status, reason, action] of cases) {
    const error = Object.assign(new Error('SQL trace retryable:false token=secret'), { code, status })
    const result = formatUserFacingError(error)
    assert.match(result, reason, code)
    assert.match(result, action, code)
    assert.doesNotMatch(result, /SQL|trace|retryable|secret|\b[A-Z_]{4,}\b/, code)
    assert.equal(error.message, 'SQL trace retryable:false token=secret')
    assert.equal(error.code, code)
  }
})

test('unknown errors never guess a cause, a card or a successful rollback', async () => {
  const { formatUserFacingError } = await formatter
  for (const error of [null, 'unknown raw text', new Error('接口失败：2073 secret'), { code: 'NEW_ERROR', message: 'empty data' }]) {
    const text = formatUserFacingError(error)
    assert.match(text, /原因.*确认|原因.*查明/)
    assert.match(text, /技术支持/)
    assert.doesNotMatch(text, /2073|secret|NEW_ERROR|raw|没有保存|未保存|排行榜|没有数据|刷新.*重试/)
  }
})

test('unresolved writes keep their uncertainty even when the cause is known', async () => {
  const { formatUserFacingError } = await formatter
  for (const code of ['claude_rate_limited', 'forbidden', 'TOOL_RESULT_CONFLICT', 'turn_timeout']) {
    const text = formatUserFacingError({ code }, { outcomeUnknown: true })
    assert.match(text, /结果.*确认|结果.*核对/)
    assert.match(text, /不要重复提交/)
    assert.doesNotMatch(text, /没有保存|未做任何修改|已回滚|稍后再试/)
  }
})

test('measurement failures alone never claim empty data or instruct skipping cards', async () => {
  const { formatUserFacingError } = await formatter
  for (const code of ['LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE', 'LAYOUT_NO_READABLE_CANDIDATES']) {
    const text = formatUserFacingError({ code, message: 'widget 2073' })
    assert.doesNotMatch(text, /没有数据|还在加载|未找到符合条件|删除|跳过|2073/)
  }
})

test('local failures get contextual recovery, not a generic retry', async () => {
  const { formatUserFacingError } = await formatter
  assert.match(formatUserFacingError(new Error('NotAllowedError'), { context: 'clipboard' }), /手动复制/)
  assert.match(formatUserFacingError(new Error('host timeout'), { context: 'stop' }), /停止.*确认/)
  assert.match(formatUserFacingError(new Error('Davinci HostBridge V2 handshake timed out')), /重新打开.*助手/)
  assert.match(formatUserFacingError(new Error('没有可用 Workspace')), /管理员/)
  assert.match(formatUserFacingError(new TypeError('Failed to fetch')), /网络/)
  assert.doesNotMatch(formatUserFacingError(new Error('NetworkError: SQL secret')), /SQL|secret/)
})

test('trusted local notices are preserved but arbitrary backend messages are not', async () => {
  const { formatUserFacingError } = await formatter
  const notice = '回复已完成，历史同步失败；重新打开会话后可评价'
  assert.equal(formatUserFacingError(notice), notice)
  assert.notEqual(formatUserFacingError({ code: 'unknown', message: notice }), notice)
})

test('skill storage problems are not blamed on the uploaded file', async () => {
  const { formatUserFacingError } = await formatter
  for (const code of ['skill_artifact_unavailable', 'skill_artifact_corrupt', 'skill_materialization_failed']) {
    const text = formatUserFacingError({ code, status: 500 })
    assert.match(text, /管理员/)
    assert.doesNotMatch(text, /重新上传|文件无法导入|确认选择|文件完整/)
  }
})

test('instruction readback failure never claims a rejected save or requests another write', async () => {
  const { formatUserFacingError } = await formatter
  for (const status of [500, undefined]) {
    const text = formatUserFacingError({ code: 'instructions_invalid', status })
    assert.match(text, /保存.*确认|确认.*保存/)
    assert.match(text, /不要重复保存/)
    assert.doesNotMatch(text, /无法保存|过长|为空|再保存/)
  }
  assert.match(formatUserFacingError({ code: 'instructions_invalid', status: 422 }), /字符/)
  assert.match(formatUserFacingError({ code: 'instructions_too_large', status: 413 }), /过长|缩短/)
})
