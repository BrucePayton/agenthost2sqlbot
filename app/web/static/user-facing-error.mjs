const SUPPORT = "请联系技术支持，并提供当前页面名称、操作时间和这条提示。";
const CHECK_RESULT = "这次操作的结果仍待确认。请先核对业务页面；若显示“检查执行状态”，请点击核对，不要重复提交。";
const LAYOUT_FOLLOWUP_FAILURE = "布局结果以上方业务摘要为准。后续模型会话未能完成，不要重复提交布局操作；如需排查，请联系管理员并提供操作时间。";

// Code/status select copy; raw backend messages are never a display fallback.
const MESSAGES = {
  login: ["登录状态已失效，暂时无法继续。", "请点击“重新认证”；若没有该按钮，请重新登录后打开助手。"],
  permission: ["当前账号没有完成这项操作所需的权限。", "请联系看板或数据管理员，确认你的查看、编辑或保存权限。"],
  serviceAuth: ["助手的服务配置异常，暂时无法继续。", "请联系系统管理员检查助手服务配置，反复登录无法解决这个问题。"],
  busy: ["助手服务当前繁忙。", "请稍后再试；若刚才涉及修改或保存，请先核对业务页面，避免重复操作。"],
  sessionBusy: ["当前会话还有任务正在执行。", "请等待任务结束；需要中止时可点击“停止”，确认结束后再继续。"],
  session: ["暂时无法打开原会话。", "请从会话列表重新打开；若仍不可用，先核对之前的操作结果，再新建对话。"],
  workspace: ["当前无法访问助手工作区。", "请联系管理员确认工作区是否可用，以及你的访问权限。"],
  request: ["系统未能处理这次请求，具体原因尚未查明。", SUPPORT],
  version: ["助手与当前页面的版本不匹配，暂时无法协同操作。", "请联系系统管理员检查页面与助手是否已更新到配套版本。"],
  origin: ["助手无法从当前页面建立可信连接。", "请从系统的正式入口打开助手；若仍失败，请联系系统管理员。"],
  page: ["暂时无法连接当前业务页面。", "请回到要操作的看板或数据页面，等待页面显示完整后重新打开助手；若仍失败，请联系技术支持。"],
  unsaved: ["暂时无法确认页面状态，不能继续分组或调整。", "请检查页面是否有未保存的修改，先保存要保留的内容；若页面已保存仍失败，请联系技术支持。"],
  content: ["暂时无法读取卡片的显示内容，本次布局未完成，具体原因尚未查明。", "请检查要调整的卡片是否正常显示；若已正常显示仍失败，请联系技术支持，并提供看板名称、卡片标题和操作时间。"],
  layout: ["本次尚未找到满足尺寸要求且能完整显示内容的布局。", "请检查是否限定了卡片或所在布局的宽高；没有尺寸限制时，请联系技术支持排查尺寸检查功能。"],
  layoutInput: ["布局参数未通过系统校验。", "请将下方诊断信息提供给技术支持；本次未进入保存流程，不要重复提交。"],
  semanticPlan: ["语义分组方案未能生成有效布局参数。", "请将下方诊断信息提供给技术支持；本次未进入保存流程，不要重复提交。"],
  layoutDuplicate: ["本轮已经发起一次布局调整。", "请等待本次回执；系统已阻止重复写入，不要再次提交。"],
  solverInfeasible: ["布局求解器没有找到满足约束的候选布局。", "请将下方诊断信息提供给技术支持，排查尺寸和分组约束。"],
  layoutCancelled: ["本次布局调整已停止。", "请先核对页面上的执行状态；确认结束后再决定是否继续，不要重复提交。"],
  canvasChanged: ["检查期间页面大小发生变化。", "请保持窗口大小和页面缩放不变，先核对本次结果；仍无法调整时请联系技术支持。"],
  contentChanged: ["检查期间卡片内容发生变化。", "请等待查询或筛选更新结束，再核对本次结果，不要重复提交。"],
  contentOverflow: ["当前尺寸可能无法完整显示卡片内容。", "请检查是否限制了卡片宽高；需要放宽时先确认允许调整的范围，不要删除卡片或改动数据。"],
  measurementLimit: ["本次尺寸检查达到处理上限，尚不能确定合适尺寸。", "请联系技术支持排查尺寸检查范围，不必反复刷新。"],
  canvasUnavailable: ["暂时无法读取看板显示区域。", "请打开目标看板，等待全屏切换或缩放结束，再核对本次结果。"],
  hiddenContent: ["暂时无法读取未打开标签中的卡片内容。", "请打开对应标签查看卡片；若正常显示仍无法调整，请联系技术支持。"],
  geometry: ["本次布局存在尺寸、边界或重叠问题。", "请核对指定的宽高和位置；没有手动限制时，请联系技术支持排查布局计算，不要删除卡片。"],
  projection: ["页面排布与检查通过的方案不一致。", "请联系技术支持排查布局显示，不要重复提交相同操作。"],
  recovery: ["上次操作的结果还需要核对。", "请先检查业务页面，并使用“检查执行状态”核对结果，不要重复提交；仍无法确认时请联系技术支持。"],
  network: ["助手连接中断，暂时无法确认执行结果。", "请先检查网络连接并核对业务页面，不要重复提交；连接恢复后可使用“检查执行状态”，仍无法确认时请联系技术支持。"],
  timeout: ["等待操作结果的时间较长，目前无法确认是否完成。", "请先核对业务页面，不要重复提交；仍无法确认时请联系技术支持，并提供操作时间。"],
  unavailable: ["助手服务暂时不可用，原因尚未查明。", SUPPORT],
  unsupported: ["当前页面暂不支持完成这项操作。", "请联系系统管理员确认该功能是否已开通。"],
  fileLarge: ["文件过大，无法处理。", "请缩小文件或拆分内容后重新上传。"],
  file: ["附件无法读取或已不可用。", "请检查文件能否正常打开，并重新选择文件上传。"],
  skill: ["技能文件无法导入。", "请确认选择的是完整的技能文件；不确定文件是否正确时，请联系技能提供者。"],
  skillStorage: ["系统暂时无法读取或准备技能内容。", "请联系系统管理员检查技能存储和配置；不需要反复上传文件。"],
  skillConflict: ["已存在同名技能，暂时不能按当前方式导入。", "请核对已有技能，选择重命名；只有确认要替换时才选择覆盖。"],
  changed: ["内容已被他人或其他页面修改。", "请先查看最新内容，再确认需要保留的修改，避免覆盖他人的编辑。"],
  missing: ["暂时无法访问这项内容。", "请重新打开内容列表，确认它仍存在且你有访问权限；仍不可用时请联系管理员。"],
  instructionsLarge: ["操作说明内容过长，无法保存。", "请缩短说明后再保存。"],
  instructionsText: ["系统无法识别操作说明中的部分字符。", "如果是刚编辑的说明，请改用普通文本输入；若未编辑也出现此提示，请联系管理员检查原有说明文件。"],
  instructionsUnknown: ["暂时无法确认操作说明是否已保存成功。", "请先核对当前已保存的说明内容，不要重复保存；仍无法确认时请联系管理员。"],
  unknown: ["这次操作遇到问题，具体原因尚未查明。", `请先核对业务页面，确认刚才的修改是否生效，不要重复提交。${SUPPORT}`],
};

const CODE_KIND = new Map([
  ["login", ["identity_missing", "unauthorized", "authentication_required"]],
  ["permission", ["forbidden", "permission_denied", "access_denied"]],
  ["serviceAuth", ["claude_auth_failed"]],
  ["busy", ["claude_rate_limited", "rate_limited"]],
  ["sessionBusy", ["session_busy"]],
  ["session", ["session_not_found", "claude_resume_failed", "session_mismatch"]],
  ["workspace", ["workspace_not_found", "workspace_invalid", "invalid_workspace", "membership_unavailable"]],
  ["request", ["invalid_request"]],
  ["version", ["contract_mismatch"]],
  ["origin", ["origin_rejected"]],
  ["unsaved", ["grouping_unsaved_changes"]],
  ["content", ["layout_content_not_ready_before_deadline"]],
  ["layout", ["layout_no_readable_candidates"]],
  ["layoutInput", ["invalid_argument"]],
  ["semanticPlan", ["semantic_planning_failed", "semantic_planner_required", "semantic_planner_strategy_mismatch"]],
  ["layoutDuplicate", ["layout_write_already_dispatched"]],
  ["solverInfeasible", ["layout_solver_infeasible_candidates"]],
  ["layoutCancelled", ["layout_cancelled"]],
  ["canvasChanged", ["layout_canvas_changed"]],
  ["contentChanged", ["layout_content_changed"]],
  ["contentOverflow", ["layout_content_overflow"]],
  ["measurementLimit", ["layout_measurement_limit"]],
  ["canvasUnavailable", ["layout_canvas_unavailable"]],
  ["hiddenContent", ["layout_hidden_content_unavailable"]],
  ["content", ["layout_content_unavailable", "layout_measurement_timeout"]],
  ["geometry", ["layout_invalid_bounds", "layout_out_of_bounds", "layout_below_minimum", "layout_invalid_shape",
    "layout_child_overflow", "layout_overlap", "layout_conflict", "layout_incomplete"]],
  ["projection", ["layout_native_projection_changed"]],
  ["recovery", ["tool_continuation_required", "tool_result_conflict", "turn_state_conflict"]],
  ["timeout", ["turn_timeout"]],
  ["unavailable", ["claude_unavailable", "execution_unavailable", "mcp_unavailable", "memory_unavailable", "upstream_unavailable"]],
  ["unsupported", ["capability_unavailable"]],
  ["fileLarge", ["attachment_too_large", "skill_bundle_too_large", "snapshot_too_large"]],
  ["file", ["attachment_invalid", "attachment_not_found", "attachment_bound", "file_reference_invalid", "file_reference_unavailable"]],
  ["skill", ["invalid_skill_bundle"]],
  ["skillStorage", ["skill_artifact_corrupt", "skill_artifact_unavailable", "skill_materialization_failed"]],
  ["skillConflict", ["skill_import_conflict", "skill_name_conflict"]],
  ["changed", ["skill_changed", "instructions_changed"]],
  ["missing", ["skill_not_found", "message_not_found", "invalid_feedback_target"]],
  ["instructionsLarge", ["instructions_too_large"]],
  ["instructionsUnknown", ["instructions_invalid"]],
].flatMap(([kind, codes]) => codes.map(code => [code, kind])));

const LOCAL_MESSAGES = new Map([
  ...[
    "Davinci V2 Bridge 不可用", "Davinci 宿主上下文不可用", "正在等待 Davinci 页面状态",
    "正在等待 Davinci 页面上下文", "Davinci HostBridge V2 handshake timed out",
    "Davinci HostBridge V2 context request timed out", "Davinci HostBridge V2 is not ready",
    "Davinci HostBridge is not ready", "Davinci HostBridge V2 has no PageState",
    "Davinci HostBridge V2 closed", "Davinci HostBridge closed",
  ].map(message => [message, "page"]),
  ...["Davinci 页面响应超时", "Davinci frontend Tool timed out", "Davinci frontend command timed out"]
    .map(message => [message, "timeout"]),
  ...["Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource."]
    .map(message => [message, "network"]),
  ["请先新建 Session", "session"],
  ["没有可用 Workspace", "workspace"],
  ["Workspace 尚未就绪", "workspace"],
  ["请先重新认证", "login"],
  ["请先恢复认证或核对执行状态", "recovery"],
  ["当前页面不支持认证恢复，请重新打开 Agent", "page"],
]);

const LOCAL_NOTICES = new Set([
  "评价加载失败，重新打开会话后可重试",
  "回复已完成，历史同步失败；重新打开会话后可评价",
  "当前不能上传图片。",
]);

/** Translate only known failure evidence; never interpret arbitrary server prose. */
export function formatUserFacingError(error, { context, outcomeUnknown = false } = {}) {
  if (context === "clipboard") return "复制未成功。请选中会话编号后手动复制。";
  if (context === "stop") return `停止请求尚未得到确认。${CHECK_RESULT}${SUPPORT}`;
  // Exact Host-owned terminal notice, not a general permission to display backend prose.
  if (error?.message === LAYOUT_FOLLOWUP_FAILURE || error?.error?.message === LAYOUT_FOLLOWUP_FAILURE) return LAYOUT_FOLLOWUP_FAILURE;
  if (typeof error === "string" && (LOCAL_NOTICES.has(error) || /^最多还能添加 \d{1,3} 个附件。$/.test(error))) return error;
  const issue = Array.isArray(error?.issues)
    ? error.issues.find(item => typeof item?.code === "string" && CODE_KIND.has(item.code.toLowerCase())) : null;
  const evidence = issue || error?.error || error;
  const code = typeof evidence?.code === "string" ? evidence.code.toLowerCase() : "";
  let kind = CODE_KIND.get(code);
  if (code === "instructions_invalid" && error?.status === 422) kind = "instructionsText";
  if (!kind && error?.status === 401) kind = "login";
  if (!kind && error?.status === 403) kind = "permission";
  if (!kind && error?.status === 429) kind = "busy";
  if (!kind && !code) kind = LOCAL_MESSAGES.get(error?.message);
  const [reason, action] = MESSAGES[kind || "unknown"];
  // A retry/new-session instruction must not bypass an unresolved write.
  if (outcomeUnknown) {
    const safeAction = ["login", "permission", "serviceAuth", "version", "origin"].includes(kind)
      ? action : SUPPORT;
    return reason + CHECK_RESULT + safeAction;
  }
  return reason + action;
}

const OPERATIONS = {
  "dashboard.set_widget_layout": "调整看板布局",
  "dashboard.apply_widget_spec": "配置看板卡片",
  "dashboard.apply_widget_edits": "调整卡片样式",
  "dashboard.get_structure": "读取看板结构",
  "dashboard.get_widget_spec": "读取卡片配置",
  "Skill": "加载技能",
  "Read": "读取文件",
};

const DIAGNOSTIC_TOKEN = /^[A-Za-z0-9_.:-]{1,96}$/;

/** Show only Host-owned bounded fields; arbitrary backend prose and object keys stay hidden. */
function layoutDiagnosticLine(receipt) {
  const diagnostics = receipt?.diagnostics;
  if (!diagnostics || typeof diagnostics !== "object" || Array.isArray(diagnostics)) return "";
  const fields = [];
  for (const key of ["code", "stage", "sessionId", "toolCallId", "layoutRunId"]) {
    const value = diagnostics[key];
    if (typeof value === "string" && DIAGNOSTIC_TOKEN.test(value)) fields.push(`${key}=${value}`);
  }
  for (const key of ["retryable", "writeDispatched"]) {
    if (typeof diagnostics[key] === "boolean") fields.push(`${key}=${diagnostics[key]}`);
  }
  return fields.length ? `诊断：${fields.join("；")}` : "";
}
const DOMAINS = {dashboard: "看板", dataset: "数据集", workspace: "工作台", catalog: "数据目录", navigation: "页面"};
const VERBS = {get: "读取", read: "读取", list: "读取", describe: "读取", query: "查询", search: "查询",
  set: "更新", apply: "更新", update: "更新", edit: "更新", create: "新建", add: "新建", remove: "删除", delete: "删除",
  open: "打开", close: "关闭", capture: "截取", save: "保存", publish: "发布"};

export function businessToolLabel(name) {
  if (Object.hasOwn(OPERATIONS, name)) return OPERATIONS[name];
  const [domain, action] = typeof name === "string" ? name.split(".") : [];
  const verb = action?.split("_")[0];
  return Object.hasOwn(DOMAINS, domain) && Object.hasOwn(VERBS, verb)
    ? VERBS[verb] + DOMAINS[domain] : "执行操作";
}

// Parse complete recorded JSON only. Truncation and arbitrary prose are not evidence.
function receiptFrom(value, depth = 0) {
  if (depth > 3) return null;
  if (typeof value === "string") {
    try { return receiptFrom(JSON.parse(value), depth + 1); } catch { return null; }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (["success", "partial", "error"].includes(value.status)) return value;
  if (value.schemaVersion === "davinci-tool-error-v1" && value.ok === false &&
      typeof value.code === "string") return value;
  if (Array.isArray(value.content) && value.content.length === 1 && value.content[0]?.type === "text") {
    return receiptFrom(value.content[0].text, depth + 1);
  }
  return null;
}

// Davinci app/components/DashboardPanel/{types.ts,DashboardPanelContants.ts}:
// LarkChartTypes + CHART_TITLE_MAP. These are not the legacy Widget ChartTypes IDs.
const NATIVE_CARD_TYPES = {
  1001: "表格", 2001: "指标卡", 3001: "基础柱状图", 3002: "堆积柱状图", 3003: "百分比堆积柱状图",
  4001: "基础折线图", 4002: "平滑折线图", 4003: "阶梯折线图", 5001: "饼图", 5002: "环形图",
  6001: "基础条形图", 6002: "堆积条形图", 6003: "百分比堆积条形图", 7001: "基础面积图",
  7002: "堆积面积图", 7003: "百分比堆积面积图", 8001: "散点图", 9001: "雷达图", 10001: "漏斗图",
  11001: "排行榜", 12001: "进度条", 12002: "半环进度", 12003: "圆环进度", 12004: "组合图",
  13001: "透视表", 14001: "内嵌网页", 16001: "日历图", 17001: "甘特图", 18001: "文本",
  19001: "平铺布局", 19002: "标签布局", 20001: "整体概览", 20002: "消息指标卡", 20003: "消息列表",
  20004: "消息指标", 21001: "整体概览", 21002: "任务指标卡", 21003: "任务列表",
};
const CARD_TYPES = {...NATIVE_CARD_TYPES, metric: "指标卡", metriccard: "指标卡", leaderboard: "排行榜", rank: "排行榜",
  table: "表格", pivottable: "透视表", bar: "基础柱状图", line: "基础折线图", pie: "饼图"};
const CHINESE_CARD_TYPES = new Set(Object.values(CARD_TYPES));
function shortText(value) {
  return typeof value === "string" ? value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, 120) : "";
}

function issueTarget(issue) {
  const ids = Array.isArray(issue?.widgetIds) ? issue.widgetIds.filter(id => shortText(id)) : [];
  // A singular title/type cannot safely be assigned to several unrelated IDs.
  if (ids.length === 1) {
    const constraints = issue.constraints;
    const type = String(constraints?.widgetType ?? constraints?.chartType ?? "").toLowerCase();
    const label = CHINESE_CARD_TYPES.has(type) ? type : Object.hasOwn(CARD_TYPES, type) ? CARD_TYPES[type] : "卡片";
    const title = shortText(constraints?.widgetTitle);
    return `${label}${title ? `“${title}”` : "（标题未提供）"}`;
  }
  if (ids.length) return "相关卡片（回执未提供逐卡身份）";
  return "";
}

function writeEvidenceNodes(receipt) {
  const nodes = [];
  function visit(value, depth) {
    if (!value || typeof value !== "object" || depth > 3) return;
    nodes.push(value);
    for (const key of ["error", "data", "constraints", "details", "diagnostics"]) visit(value[key], depth + 1);
  }
  visit(receipt, 0);
  if (Array.isArray(receipt?.issues)) receipt.issues.forEach(issue => visit(issue, 0));
  return nodes;
}

function unknownWrite(node) {
  return node.unknown === true || node.outcomeUnknown === true ||
    ["state", "status", "writeStatus", "writeState", "saveStatus", "outcome", "writeOutcome"].some(key =>
      ["unknown", "outcome_unknown", "pending", "saving"].includes(node[key]));
}

function receiptWriteStatus(receipt, outcomeUnknown) {
  const data = receipt?.data;
  const nodes = writeEvidenceNodes(receipt);
  const issues = Array.isArray(receipt?.issues) ? receipt.issues : [];
  const complete = (receipt?.issuesTruncated == null || receipt.issuesTruncated === false) &&
    (receipt?.issueCount == null || receipt.issueCount === issues.length) &&
    (receipt?.returnedIssueCount == null || receipt.returnedIssueCount === issues.length);
  // Conflicting or incomplete evidence must win over either saved or no-write claims.
  const conflictingSave = nodes.some(node => node.persisted === true || node.committed === true) &&
    nodes.some(node => node.persisted === false || node.committed === false ||
      node.writeDispatched === false || node.preview === true);
  const uncertain = outcomeUnknown || !complete || conflictingSave || nodes.some(unknownWrite);
  if (uncertain) return "保存结果尚待确认；请先核对页面，不要重复提交。";
  if (data?.persisted === true && data?.preview !== true) return "回执确认已保存。";
  const mayHaveWritten = nodes.some(node => node.committed === true || node.persisted === true ||
    (node.writeDispatched != null && node.writeDispatched !== false) ||
    ["state", "status", "writeStatus", "writeState", "saveStatus", "outcome", "writeOutcome"].some(key =>
      ["committed", "dispatched", "persisted"].includes(node[key])));
  if (!mayHaveWritten && data?.persisted === false && data?.preview === true) return "本次为预览，未保存。";
  if (!mayHaveWritten && receipt?.diagnostics?.writeDispatched === false) return "本次未发起写入。";
  if (receipt?.status === "error" && !mayHaveWritten && issues.length &&
      issues.every(issue => issue?.constraints?.solverFinal === true && issue.constraints.writeDispatched === false)) {
    return "本次未发起写入。";
  }
  if (data?.persisted === false) return "回执未确认保存完成；请先核对页面，不要重复提交。";
  return "保存结果尚待确认；请先核对页面，不要重复提交。";
}

/** Display projection only: never mutate receipts or authorize another execution. */
export function formatToolReceipt(item, {outcomeUnknown = false} = {}) {
  const operation = businessToolLabel(item?.name);
  if (["running", "deferred"].includes(item?.state)) return `${operation}：正在执行，等待回执。`;
  const receipt = receiptFrom(item?.outputPreview);
  const issues = Array.isArray(receipt?.issues) ? receipt.issues.filter(issue => issue && typeof issue === "object") : [];
  const failed = item?.isError || item?.state === "error" || receipt?.status === "error";
  const partial = receipt?.status === "partial";
  const pending = outcomeUnknown || item?.state === "no-receipt";
  const empty = !failed && !pending && receipt?.status === "success" && issues.length === 1 &&
    issues[0].code === "LAYOUT_EMPTY_CANVAS" && receipt?.data?.persisted === false;
  if (empty) return `${operation}：当前看板没有可排列的卡片，无需调整。`;
  const headline = `${operation}：${pending ? "结果待核对" : failed ? "遇到问题" : partial ? "部分完成" : "已收到回执"}。`;
  const displayIssues = issues.filter(issue => failed || issue.code !== "LAYOUT_LOCAL_RULE");
  const reasons = displayIssues.slice(0, 4).map(issue => {
    const target = issueTarget(issue);
    let reason;
    if (failed) reason = formatUserFacingError(issue, {outcomeUnknown});
    else if (issue.code === "LAYOUT_REMAINING_SPACE") reason = "布局仍有留白；请核对显示效果。";
    else reason = "本次另有提示，具体影响尚待确认；请核对页面，必要时联系技术支持。";
    return (target ? `${target}：` : "") + reason;
  });
  if (displayIssues.length > 4 || receipt?.issuesTruncated === true) reasons.push("还有其他提示，请展开技术详情核对。");
  if (failed && !reasons.length) reasons.push(formatUserFacingError(receipt?.error || receipt, {outcomeUnknown}));
  const diagnostics = item?.name === "dashboard.set_widget_layout" && failed
    ? layoutDiagnosticLine(receipt) : "";
  if (pending && !failed) reasons.push(CHECK_RESULT);
  const isWrite = /\.(set|apply|update|edit|create|add|remove|delete|save|publish|clear)_?/.test(item?.name || "");
  const writeStatus = isWrite || typeof receipt?.data?.persisted === "boolean"
    ? receiptWriteStatus(receipt, pending) : "";
  return [headline, ...reasons, diagnostics, writeStatus].filter(Boolean).join("\n");
}
