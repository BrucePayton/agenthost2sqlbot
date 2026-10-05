import { createMessageFeedback, completedQuestionFeedbackTargets } from "./message-feedback.js";
import { callLabel, createCallIndex } from "./activity-tools.js";
import { HttpAgent } from "@ag-ui/client";
import { requireAgUiResponse, isUnacceptedAgUiRequest } from "./agui-http.js";
import { formatUserFacingError, formatToolReceipt, businessToolLabel } from "./user-facing-error.js";
import {
  MESSAGE_TYPES,
  PROTOCOL,
  PROTOCOL_VERSION,
  createDavinciEnvelope,
  toolsForHostContext,
  validateDavinciEnvelope,
  validateHostContext,
} from "../shared/davinci-protocol.js";
import { createFrontendToolRunner, nativeToolMessage } from "./frontend-tool-runner.js";
import { createLayoutDiagnostics } from "./layout-diagnostics.js";

const layoutDiagnostics = createLayoutDiagnostics({ api });
import { createHostBridge } from "./host-bridge.js";
import { createHostBridgeV2 } from "./host-bridge-v2.js";
import {
  PUBLIC_TOOL_CONTRACTS,
} from "../shared/generated/davinci-contracts.js";
import { createRuntimeId } from "../shared/runtime-id.js";
import { createSubscriptionStageForwarder, subscriptionMessageId } from "./subscription-progress.js";
import { renderMessageContent } from "./assistant-markdown.mjs";
import {
  createIdentityHeaders,
  createLegacyIdentityHeaders,
  sessionStorageKey,
} from "./identity.js";
import { copyTextWithFallback } from "./clipboard.js";
import {
  requestHeaders,
  selectEmbedWorkspaces,
} from "./skill-management.js";
import { createPanelUi } from "./panel-ui.js";
import { legacyPageState } from "./panel-model.js";
import {
  createTraceReducer,
  traceEntryFromRecord,
  tickRunningTurn,
} from "./turn-activity.js";

const elements = {
  panel: document.querySelector("#assistantPanel"),
  timeline: document.querySelector("#agentTimeline"),
  status: document.querySelector("#agentStatus"),
  recovery: document.querySelector("#agentRecovery"),
  reauthenticate: document.querySelector("#agentReauthenticate"),
  checkRun: document.querySelector("#agentCheckRun"),
  form: document.querySelector("#agentComposer"),
  input: document.querySelector("#agentMessageInput"),
  send: document.querySelector("#agentSendButton"),
  stop: document.querySelector("#agentStopButton"),
  newSession: document.querySelector("#agentNewSessionButton"),
  skills: document.querySelector("#agentSkillsButton"),
  skillCount: document.querySelector("#agentSkillCount"),
  skillManagementView: document.querySelector("#skillManagementView"),
  skillManagementBackButton: document.querySelector("#skillManagementBackButton"),
  skillManagementWorkspaceName: document.querySelector("#skillManagementWorkspaceName"),
  skillChangesNotice: document.querySelector("#skillChangesNotice"),
  globalSkillList: document.querySelector("#globalSkillList"),
  globalSkillLoading: document.querySelector("#globalSkillLoading"),
  globalSkillEmpty: document.querySelector("#globalSkillEmpty"),
  globalSkillImportRoot: document.querySelector("#globalSkillImportRoot"),
  globalSkillImportButton: document.querySelector("#globalSkillImportButton"),
  globalSkillArchiveButton: document.querySelector("#globalSkillArchiveButton"),
  globalSkillDirectoryInput: document.querySelector("#globalSkillDirectoryInput"),
  globalSkillArchiveInput: document.querySelector("#globalSkillArchiveInput"),
  personalSkillList: document.querySelector("#personalSkillList"),
  personalSkillLoading: document.querySelector("#personalSkillLoading"),
  personalSkillEmpty: document.querySelector("#personalSkillEmpty"),
  workspaceInstructionsSection: document.querySelector("#workspaceInstructionsSection"),
  workspaceInstructionsEditor: document.querySelector("#workspaceInstructionsEditor"),
  workspaceInstructionsSave: document.querySelector("#workspaceInstructionsSave"),
  workspaceInstructionsReset: document.querySelector("#workspaceInstructionsReset"),
  workspaceInstructionsStatus: document.querySelector("#workspaceInstructionsStatus"),
  workspaceInstructionsLoading: document.querySelector("#workspaceInstructionsLoading"),
  personalSkillImportRoot: document.querySelector("#personalSkillImportRoot"),
  personalSkillImportButton: document.querySelector("#personalSkillImportButton"),
  personalSkillArchiveButton: document.querySelector("#personalSkillArchiveButton"),
  personalSkillDirectoryInput: document.querySelector("#personalSkillDirectoryInput"),
  personalSkillArchiveInput: document.querySelector("#personalSkillArchiveInput"),
  skillDetailPanel: document.querySelector("#skillDetailPanel"),
  skillDetailName: document.querySelector("#skillDetailName"),
  skillDetailDescription: document.querySelector("#skillDetailDescription"),
  skillDetailVersion: document.querySelector("#skillDetailVersion"),
  skillDetailHash: document.querySelector("#skillDetailHash"),
  skillDetailSource: document.querySelector("#skillDetailSource"),
  skillDetailUpdatedAt: document.querySelector("#skillDetailUpdatedAt"),
  skillDetailContent: document.querySelector("#skillDetailContent"),
  skillDetailManifest: document.querySelector("#skillDetailManifest"),
  skillImportConflictDialog: document.querySelector("#skillImportConflictDialog"),
  skillImportConflictName: document.querySelector("#skillImportConflictName"),
  skillImportConflictExistingHash: document.querySelector("#skillImportConflictExistingHash"),
  skillImportConflictIncomingHash: document.querySelector("#skillImportConflictIncomingHash"),
  skillImportRenameInput: document.querySelector("#skillImportRenameInput"),
  skillImportOverwriteButton: document.querySelector("#skillImportOverwriteButton"),
  skillImportRenameButton: document.querySelector("#skillImportRenameButton"),
  skillImportCancelButton: document.querySelector("#skillImportCancelButton"),
  skillImportConflictError: document.querySelector("#skillImportConflictError"),
  sessionIdentity: document.querySelector("#agentSessionIdentity"),
  sessionId: document.querySelector("#agentSessionId"),
  copySessionId: document.querySelector("#agentCopySessionIdButton"),
  runOptions: document.querySelector("#agentRunOptions"),
  modelSelect: document.querySelector("#agentModelSelect"),
  effortSelect: document.querySelector("#agentEffortSelect"),
};

const panelElements = {
  header: document.querySelector(".assistant-head"),
  panel: elements.panel,
  form: elements.form,
  input: elements.input,
  modelSelect: elements.modelSelect,
  effortSelect: elements.effortSelect,
  historyTrigger: document.querySelector("#headerTaskHistory"),
  historyPopover: document.querySelector("#historyPopover"),
  historyList: document.querySelector("#historyList"),
  historyEmpty: document.querySelector("#historyEmpty"),
  railTaskList: document.querySelector("#railTaskList"),
  railEmpty: document.querySelector("#railEmpty"),
  sizeMenu: document.querySelector("#sizeMenu"),
  sizeMenuTrigger: document.querySelector("#sizeMenuTrigger"),
  sizeMenuPopover: document.querySelector("#sizeMenuPopover"),
  currentSizeLabel: document.querySelector("#currentSizeLabel"),
  sizeOptions: document.querySelectorAll("[data-size-option]"),
  closeButton: document.querySelector("#closeAssistant"),
  contextBar: document.querySelector("#compactContextBar"),
  contextArea: document.querySelector("#compactContextArea"),
  contextName: document.querySelector("#compactContextName"),
  contextSep: document.querySelector("#compactContextSep"),
  contextSuggestionCard: document.querySelector("#contextSuggestionCard"),
  sessionContextName: document.querySelector("#sessionContextName"),
  contextSuggestionHint: document.querySelector("#contextSuggestionHint"),
  suggestedQuestionList: document.querySelector("#suggestedQuestionList"),
  idleView: document.querySelector("#idleView"),
  taskView: document.querySelector("#taskView"),
  abilityClusters: document.querySelectorAll("[data-ability]"),
  abilityDetail: document.querySelector("#abilityDetail"),
  abilityDetailTitle: document.querySelector("#abilityDetailTitle"),
  abilityDetailTag: document.querySelector("#abilityDetailTag"),
  abilityDetailDesc: document.querySelector("#abilityDetailDesc"),
  abilityDetailScope: document.querySelector("#abilityDetailScope"),
  abilityDetailExample: document.querySelector("#abilityDetailExample"),
  capabilityDrawer: document.querySelector("#capabilityDrawer"),
  closeCapabilityDrawer: document.querySelector("#closeCapabilityDrawer"),
  promptButtons: document.querySelectorAll("[data-prompt], [data-inspector-prompt]"),
  inspectorContext: document.querySelector("#inspectorContext"),
  inspectorNav: document.querySelector("#inspectorNav"),
  inspectorObject: document.querySelector("#inspectorObject"),
  inspectorStatus: document.querySelector("#inspectorStatus"),
  composerModelTrigger: document.querySelector("#composerModelTrigger"),
  composerModelPopover: document.querySelector("#composerModelPopover"),
  composerModelLabel: document.querySelector("#composerModelLabel"),
  composerReasoningLabel: document.querySelector("#composerReasoningLabel"),
  composerResizeHandle: document.querySelector("#composerResizeHandle"),
  quickMenuRows: document.querySelectorAll("[data-quick-menu]"),
  quickModelValue: document.querySelector("#quickModelValue"),
  quickReasoningValue: document.querySelector("#quickReasoningValue"),
  quickModelSubmenu: document.querySelector("#quickModelSubmenu"),
  quickReasoningSubmenu: document.querySelector("#quickReasoningSubmenu"),
  sendPanelCommand: (panel) => state.v2Bridge?.sendPanelCommand(panel),
};

const query = new URLSearchParams(location.search);
const embedConfigElement = document.querySelector("#davinciEmbedConfig");
const embedConfig = embedConfigElement
  ? JSON.parse(embedConfigElement.textContent || "null")
  : null;

function initRunOptions() {
  const models = embedConfig?.selectableModels ?? [];
  if (!models.length) return;                 // 无配置时整行保持 hidden
  elements.modelSelect.replaceChildren(
    ...models.map((name) => {
      const option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      return option;
    })
  );
  const defaultModel = embedConfig?.defaultModel;
  elements.modelSelect.value = models.includes(defaultModel) ? defaultModel : models[0];
  const defaultEffort = embedConfig?.defaultEffort;
  if (defaultEffort) elements.effortSelect.value = defaultEffort;
  // 两个 select 只作为运行参数的状态源，界面由 composer 的快捷菜单呈现。
}
initRunOptions();

const configuredParentOrigin = query.get("parentOrigin");
function resolveParentOrigin(value) {
  try {
    const parsed = new URL(value);
    if (
      parsed.protocol === "http:" &&
      ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname) &&
      parsed.origin === value
    ) return parsed.origin;
  } catch {}
  return "http://127.0.0.1:4173";
}
const expectedParentOrigin = embedConfig?.parentOrigin || resolveParentOrigin(configuredParentOrigin);
const identityHeaders = embedConfig?.sessionToken
  ? createIdentityHeaders(embedConfig.sessionToken)
  : embedConfig
    ? createLegacyIdentityHeaders(embedConfig.obId)
    : {};
const nonce = query.get("nonce") || "";
const state = {
  workspaceId: null,
  skillWorkspace: null,
  sessionId: null,
  agent: null,
  hostContext: null,
  running: false,
  creatingSession: false,
  authRequired: false,
  unresolvedRunId: null,
  pendingInput: null,
  pendingParent: new Map(),
  v1Bridge: null,
  v2Bridge: null,
  runtimeContext: null,
  toolRunner: null,
  toolRecovery: null,
  trace: createTraceReducer(),
  activityTimer: null,
  expandedTools: new Set(),
  expandedTurns: new Set(),
};
const skillViewState = { workspace: null, activeView: "chat" };
const observedEventTypes = [];
const SkillManager = window.SkillManager;

const panelUi = createPanelUi({
  elements: panelElements,
  onSelectSession: (sessionId) => {
    switchSession(sessionId).catch((error) => renderError(error));
  },
  initialSize: embedConfig?.panelSize,
  onSizeChange: (size) => savePreferences({ panelSize: size }),
});

/** Persist a UI choice for this user; a failed save only means the next visit starts from the old value. */
function savePreferences(patch) {
  return api("/api/me/preferences", { method: "PUT", body: JSON.stringify(patch) })
    .catch(() => {});
}
panelUi.syncComposerLabels();

function setStatus(message, showDuringAuth = false) {
  if (state.authRequired && !showDuringAuth) message = "登录状态已失效，请重新认证后继续";
  elements.status.textContent = message;
  panelUi.setInspectorStatus(message);
}

/** Keep recovery controls and sending eligibility in sync with observed state. */
function syncRecoveryControls() {
  elements.recovery.hidden = !state.authRequired && !state.unresolvedRunId;
  elements.reauthenticate.hidden = !state.authRequired;
  elements.checkRun.textContent = state.toolRecovery ? "恢复上次操作" : "检查执行状态";
  elements.checkRun.hidden = state.authRequired || !state.unresolvedRunId;
  elements.send.disabled = state.running || state.creatingSession || state.authRequired || Boolean(state.unresolvedRunId);
}

/** Preserve HTTP authentication status for both REST and the AG-UI client. */
async function authenticatedFetch(input, init) {
  const response = await fetch(input, init);
  if (response.status === 401) {
    state.authRequired = true;
    setStatus("登录状态已失效，请重新认证后继续");
    syncRecoveryControls();
    throw Object.assign(new Error("登录状态已失效，请重新认证后继续"), { status: 401 });
  }
  return response;
}

/** Store only this actor/session's composer recovery data, never credentials. */
function recoveryKey() {
  return `${sessionStorageKey(embedConfig?.obId || "embed", state.workspaceId)}:${state.sessionId}:draft`;
}

/** Persist identifiers only; business receipts and credentials stay out of storage. */
function rememberToolRecovery(value) {
  state.toolRecovery = value;
  if (!state.sessionId) return;
  const key = `${recoveryKey()}:tools`;
  if (value) sessionStorage.setItem(key, JSON.stringify(value));
  else sessionStorage.removeItem(key);
}

/** Reconcile the server's pending calls with this actor/session's receipt index. */
async function readToolRecovery() {
  if (!state.v2Bridge || !state.sessionId) return [];
  const sessionId = state.sessionId;
  const recovery = await api(`/api/sessions/${encodeURIComponent(sessionId)}/frontendToolRecovery`);
  if (state.sessionId !== sessionId) return [];
  const calls = recovery?.calls || [];
  if (!calls.length) { rememberToolRecovery(null); return []; }
  let saved = state.toolRecovery;
  try { saved ||= JSON.parse(sessionStorage.getItem(`${recoveryKey()}:tools`)); } catch {}
  const ids = calls.map(call => call.toolCallId);
  const same = saved?.toolCallIds?.length === ids.length && ids.every(id => saved.toolCallIds.includes(id));
  rememberToolRecovery({
    originRunId: calls[0].originRunId,
    continuationRunId: calls[0].continuationRunId || (same && saved.continuationRunId) || createRuntimeId(),
    toolCallIds: ids,
  });
  state.unresolvedRunId = calls[0].continuationRunId || calls[0].originRunId;
  syncRecoveryControls();
  return calls;
}

/** Deliver the original receipts once, without ever dispatching the original tools. */
async function recoverToolResults(calls) {
  if (new Set(calls.map(call => call.originRunId)).size !== 1) {
    setStatus("原任务存在多批待核对操作，请先检查业务页面");
    return;
  }
  const accepted = calls.find(call => call.continuationRunId);
  if (accepted) {
    setStatus(["queued", "waiting_for_memory", "assigned", "running", "finalizing"].includes(accepted.continuationStatus)
      ? "原任务仍在执行，请稍后检查状态" : "原任务已停止，操作结果待核对；请先检查业务页面");
    return;
  }
  const pending = state.toolRunner?.getPendingToolResults();
  let messages = pending?.messages?.length === calls.length &&
    calls.every(call => pending.messages.some(message => message.toolCallId === call.toolCallId))
    ? pending.messages : null;
  if (!messages) {
    await state.v2Bridge.requestContext();
    const receipts = await Promise.all(calls.map(call => state.v2Bridge.lookupNativeReceipt(call.toolCallId, {
      sessionId: state.sessionId, workspaceId: state.workspaceId, pageInstanceId: call.page?.instanceId,
    })));
    if (receipts.some(receipt => receipt.state !== "completed")) {
      setStatus(receipts.some(receipt => receipt.state === "pending")
        ? "原操作仍在执行，请稍后检查" : "原操作回执已不可用，结果待核对；请检查业务页面后新建任务");
      return;
    }
    messages = receipts.map((receipt, index) => nativeToolMessage(calls[index].toolCallId, receipt.result));
  }
  const recovery = state.toolRecovery;
  setRunningUi(true);
  try {
    await state.toolRunner.resumeToolResults(messages, recovery.continuationRunId, recovery.originRunId);
    state.unresolvedRunId = null;
    rememberToolRecovery(null);
    await refreshCompletedHistory();
    setStatus("原操作结果已恢复");
  } catch (error) {
    markConnectionLost(error.runId || recovery.continuationRunId);
    if (!state.authRequired) setStatus("恢复未完成，请检查原任务状态");
  } finally { setRunningUi(false); }
}

/** Freeze the current activity without claiming the server failed. */
function markConnectionLost(runId) {
  state.unresolvedRunId = runId || state.unresolvedRunId;
  if (runId) state.trace.disconnect(runId, new Date().toISOString());
  renderActivityOnly();
  syncRecoveryControls();
}

/** Recover a draft only when doing so cannot overwrite newer user input. */
function restorePendingInput() {
  if (state.pendingInput && !state.pendingInput.accepted && !elements.input.value) {
    elements.input.value = state.pendingInput.text;
  }
  state.pendingInput = null;
}

/** Restore the latest unresolved execution; older turns cannot block a later completed task. */
function restoreRunCheck() {
  const latest = [...state.trace.snapshot().values()].at(-1);
  if (!latest) return;
  if (latest.status === "running") markConnectionLost(latest.lastTurnId);
  if (["outcome_unknown", "recovery_required"].includes(latest.status)) {
    state.unresolvedRunId = latest.lastTurnId;
  }
}

/** Read the existing Turn and messages; never replay a business request. */
async function checkRunStatus({ recoverTools = true } = {}) {
  const runId = state.unresolvedRunId;
  const sessionId = state.sessionId;
  if (!runId || state.running) return;
  elements.checkRun.disabled = true;
  try {
    const calls = await readToolRecovery();
    if (calls.length) {
      if (recoverTools) await recoverToolResults(calls);
      else setStatus("已请求停止，原操作结果待核对；请检查业务页面");
      return;
    }
    const turn = await api(`/api/turns/${encodeURIComponent(runId)}`);
    const events = await api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
    if (state.sessionId !== sessionId || state.unresolvedRunId !== runId) return;
    seedTraceFromHistory(events);
    const active = ["queued", "waiting_for_memory", "assigned", "running", "finalizing"].includes(turn.status);
    const unknown = ["outcome_unknown", "recovery_required"].includes(turn.status);
    if (active) state.trace.disconnect(runId, new Date().toISOString());
    else {
      const terminal = events.find(event => event.turn_id === runId && event.event_type === `turn.${turn.status}`);
      state.trace.push({
        event_type: `turn.${turn.status}`, turn_id: runId,
        at: turn.completed_at || terminal?.payload?.completed_at || terminal?.created_at || new Date().toISOString(), payload: {},
      });
    }
    state.unresolvedRunId = active || unknown ? runId : null;
    state.pendingInput = null; // The server owns this request, including a continuation.
    await loadFeedback(events);
    if (state.sessionId !== sessionId) return;
    configureAgent(historyMessages(events));
    setStatus(active ? "服务端仍在执行，可稍后检查状态" : unknown ? "操作结果待核对，请先在业务页面确认" :
      turn.status === "completed" ? "执行已完成" : turn.status.includes("cancel") || turn.status === "interrupted" ? "已停止" : "执行失败");
    return active;
  } catch (error) {
    if (state.sessionId !== sessionId || state.unresolvedRunId !== runId) return;
    if (error.status === 404) {
      state.unresolvedRunId = null;
      restorePendingInput();
      await refreshCompletedHistory();
      setStatus("该次请求未被接收，可继续操作");
    } else if (!state.authRequired) {
      setStatus("暂时无法确认执行结果，请稍后检查");
    }
  } finally {
    elements.checkRun.disabled = false;
    syncRecoveryControls();
  }
}

/** Reconcile an intentional stop after stream teardown, without resuming tools. */
async function reconcileStoppedRun(sessionId, runId) {
  const deadline = Date.now() + 30_000;
  while (state.sessionId === sessionId && state.unresolvedRunId === runId &&
    !state.authRequired && Date.now() < deadline) {
    // Cancellation is asynchronous: the 202 response can still say "running".
    // Wait for the old runner to finish before replacing it from server history.
    if (!state.running && !elements.checkRun.disabled) {
      const active = await checkRunStatus({ recoverTools: false });
      if (!active) return;
    }
    await new Promise(resolve => window.setTimeout(resolve, 500));
  }
}

/** Rebootstrap the same iframe through the trusted parent control channel. */
async function reauthenticate() {
  elements.reauthenticate.disabled = true;
  try {
    if (!state.v2Bridge?.getPanelCommands().includes("reauthenticate")) {
      throw new Error("当前页面不支持认证恢复，请重新打开 Agent");
    }
    if (state.workspaceId && state.sessionId) {
      sessionStorage.setItem(recoveryKey(), JSON.stringify({
        input: elements.input.value, pendingInput: state.pendingInput,
        runId: state.unresolvedRunId,
      }));
    }
    await state.v2Bridge.requestContext();
    state.v2Bridge.sendPanelCommand({ action: "reauthenticate" });
    setStatus("正在重新认证；失败时可重试", true);
  } catch (error) {
    setStatus(formatUserFacingError(error), true);
  } finally {
    elements.reauthenticate.disabled = false;
  }
}

function renderSessionIdentity() {
  elements.sessionIdentity.hidden = !state.sessionId;
  elements.sessionId.textContent = state.sessionId || "";
}

async function copySessionId() {
  if (!state.sessionId) return;
  await copyTextWithFallback(state.sessionId);
  const original = elements.copySessionId.textContent;
  elements.copySessionId.textContent = "已复制";
  window.setTimeout(() => {
    elements.copySessionId.textContent = original;
  }, 1200);
}

function formatDuration(msValue) {
  if (!Number.isFinite(msValue)) return "—";
  if (msValue < 1000) return `${msValue}ms`;
  const total = Math.round(msValue / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes > 0) return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  return `${seconds}s`;
}

const TOOL_STATE_LABEL = {
  running: "执行中",
  done: "",
  error: "报错",
  deferred: "已交前端执行",
  "no-receipt": "未回执",
};

function renderToolIo(row, item) {
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "ti-toggle";
  toggle.textContent = "技术详情";
  toggle.setAttribute("aria-expanded", "false");
  const io = document.createElement("div");
  io.className = "ti-io";
  io.hidden = true;
  for (const [label, body] of [
    ["工具", callLabel(item)],
    ["入参", item.inputPreview],
    ["出参", item.outputPreview || (['running', 'deferred'].includes(item.state) ? '等待工具返回' : '未记录出参')],
  ]) {
    if (!body) continue;
    const heading = document.createElement("b");
    heading.textContent = label;
    if (label === "出参") heading.className = "ti-output-label";
    const pre = document.createElement("pre");
    pre.textContent = body;
    io.append(heading, pre);
  }
  const open = state.expandedTools.has(item.toolUseId);
  io.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.addEventListener("click", () => {
    // 运行中每来一个 trace 事件就重建 body，展开状态必须记在 DOM 之外。
    if (state.expandedTools.has(item.toolUseId)) {
      state.expandedTools.delete(item.toolUseId);
    } else {
      state.expandedTools.add(item.toolUseId);
    }
    io.hidden = !state.expandedTools.has(item.toolUseId);
    toggle.setAttribute("aria-expanded", String(!io.hidden));
  });
  row.append(toggle, io);
}

function renderTimelineItem(item, outcomeUnknown = false) {
  if (item.kind === "segment") {
    const divider = document.createElement("div");
    divider.className = "ti segment";
    const label = document.createElement("span");
    // 模型每调一次前端工具，后端就重开一个 Turn 续跑——这里是那个接缝。
    label.textContent = `第 ${item.index + 1} 段 · 前端工具执行后续跑`;
    divider.append(label);
    return divider;
  }
  const row = document.createElement("div");
  row.className = `ti ${item.kind}`;
  const glyph = document.createElement("span");
  glyph.className = "glyph";
  glyph.textContent =
    item.kind === "thinking" ? "◇" : item.kind === "tool" ? "▸" : "·";
  const name = document.createElement("span");
  name.className = "name";
  const dur = document.createElement("span");
  dur.className = "dur";

  if (item.kind === "thinking") {
    name.textContent = item.done ? "思考" : "思考中";
    dur.textContent = formatDuration(item.durationMs);
    row.append(glyph, name, dur);
    if (item.text) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = item.truncated ? `${item.text}…（已截断）` : item.text;
      row.append(note);
    }
    return row;
  }

  if (item.kind === "tool") {
    row.dataset.toolUseId = item.toolUseId;
    row.dataset.state = item.state;
    name.textContent = businessToolLabel(item.name);
    const stateLabel = TOOL_STATE_LABEL[item.state] || "";
    dur.textContent = [item.timingKind === "frontend_round_trip" ? `往返 ${formatDuration(item.durationMs)}` : formatDuration(item.durationMs), stateLabel]
      .filter(Boolean)
      .join(" · ");
    row.append(glyph, name, dur);
    const summary = document.createElement("p");
    summary.className = "note tool-business-summary";
    summary.style.overflowWrap = "anywhere";
    summary.textContent = formatToolReceipt(item, { outcomeUnknown });
    row.append(summary);
    renderToolIo(row, item);
    return row;
  }

  name.textContent =
    item.kind === "compaction"
      ? `上下文已压缩（${item.trigger}）`
      : item.kind === "error"
        ? formatUserFacingError(item, { outcomeUnknown })
        : item.message;
  row.append(glyph, name, dur);
  return row;
}

function paintTurnActivity(section, turn) {
  // 运行中的 turn 总时长按"现在"算，否则首次绘制到第一次跳秒之间会显示 "—"。
  if (turn.status === "running") tickRunningTurn(turn, new Date().toISOString());
  const title = section.querySelector(".ta-title");
  const sub = section.querySelector(".ta-sub");
  const foot = section.querySelector(".ta-foot");
  const running = turn.status === "running";
  section.dataset.state = turn.status;

  const outcome = turn.status === "disconnected" ? "连接中断，结果待确认" :
    ["outcome_unknown", "recovery_required"].includes(turn.status) ? "操作结果待核对" :
      turn.status === "failed" ? "执行失败" : turn.status === "cancelled" ? "已停止" : running ? "工作中" : "已工作";
  title.textContent = turn.stats.totalMs === 0 && !turn.stats.toolCount && !turn.stats.thinkingCount
    ? outcome : `${outcome} ${formatDuration(turn.stats.totalMs)}`;

  if (running) {
    const last = turn.items[turn.items.length - 1];
    sub.textContent =
      last && last.kind === "tool" && last.state === "running"
        ? `正在${businessToolLabel(last.name)}…`
        : last && last.kind === "thinking" && !last.done
          ? "思考中…"
          : "运行中…";
  } else {
    const parts = [
      `思考 ${turn.stats.thinkingCount}`,
    ];
    if (turn.segments > 1) parts.push(`${turn.segments} 段`);
    sub.textContent = parts.join(" · ");
  }

  section.callIndex.update(turn.items);

  const total = turn.stats.totalMs;
  const parts = [`总 ${formatDuration(total)}`];
  if (Number.isFinite(total) && total > 0) {
    parts.push(`模型 ${formatDuration(turn.stats.modelMs)}`);
    parts.push(`工具已观测 ${formatDuration(turn.stats.toolMs)}`);
    if (turn.stats.toolRoundTripMs > 0) parts.push(`前端往返 ${formatDuration(turn.stats.toolRoundTripMs)}`);
  }
  const tokens = turn.stats.inputTokens + turn.stats.outputTokens;
  if (tokens) parts.push(`${(tokens / 1000).toFixed(1)}k tok`);
  if (Number.isFinite(turn.stats.costUsd)) {
    parts.push(`$${turn.stats.costUsd.toFixed(2)}`);
  }
  foot.textContent = parts.join(" │ ");
}

function fillActivityBody(body, turn, open) {
  body.replaceChildren();
  const outcomeUnknown = state.unresolvedRunId === turn.lastTurnId ||
    ["disconnected", "outcome_unknown", "recovery_required"].includes(turn.status);
  for (const item of turn.items) body.append(renderTimelineItem(item, outcomeUnknown));
  const foot = document.createElement("div");
  foot.className = "ta-foot";
  body.append(foot);
  body.hidden = !open;
}

function renderTurnActivity(turn) {
  const section = document.createElement("section");
  section.className = "turn-activity";
  section.dataset.turnId = turn.turnId;
  section.dataset.state = turn.status;
  section.dataset.open = "0";

  const header = document.createElement("div");
  header.className = "ta-header";
  const bar = document.createElement("button");
  bar.type = "button";
  bar.className = "ta-bar";
  bar.setAttribute("aria-expanded", "false");
  const clock = document.createElement("span");
  clock.className = "ta-clock";
  clock.ariaHidden = "true";
  const title = document.createElement("span");
  title.className = "ta-title";
  const sub = document.createElement("span");
  sub.className = "ta-sub";
  const chev = document.createElement("span");
  chev.className = "ta-chev";
  chev.ariaHidden = "true";
  chev.textContent = "›";
  bar.append(clock, title, sub, chev);

  const body = document.createElement("div");
  body.className = "ta-body";
  const open = state.expandedTurns.has(turn.turnId);
  section.dataset.open = open ? "1" : "0";
  bar.setAttribute("aria-expanded", String(open));
  fillActivityBody(body, turn, open);

  bar.addEventListener("click", () => {
    const open = section.dataset.open !== "1";
    if (open) state.expandedTurns.add(turn.turnId);
    else state.expandedTurns.delete(turn.turnId);
    section.dataset.open = open ? "1" : "0";
    body.hidden = !open;
    bar.setAttribute("aria-expanded", String(open));
  });

  section.callIndex = createCallIndex(item => {
    state.expandedTurns.add(turn.turnId);
    section.dataset.open = "1";
    bar.setAttribute("aria-expanded", "true");
    const current = state.trace.snapshot().get(turn.turnId) || turn;
    fillActivityBody(body, current, true);
    paintTurnActivity(section, current);
    const row = body.querySelector(`[data-tool-use-id="${CSS.escape(item.toolUseId)}"]`);
    if (!row) return;
    row.tabIndex = -1;
    (row.querySelector(".tool-business-summary") || row).scrollIntoView({ block: "center" });
    row.focus({ preventScroll: true });
  });
  header.append(bar, section.callIndex.root);
  section.append(header, body);
  paintTurnActivity(section, turn);
  return section;
}

function findActivitySection(turnId) {
  return elements.timeline.querySelector(
    `.turn-activity[data-turn-id="${CSS.escape(turnId)}"]`,
  );
}

/** 只重画活动块，不重建整条时间线——thinking 与工具事件不改变 messages。 */
function renderActivityOnly() {
  const snapshot = state.trace.snapshot();
  // 续跑的 turn 先以自己为链首渲染，出参一到就被并进上一段。被并掉的那个
  // 块必须删掉，否则它会僵在原地变成第二条计时条。
  for (const section of elements.timeline.querySelectorAll(".turn-activity")) {
    if (!snapshot.has(section.dataset.turnId)) section.remove();
  }
  const atBottom =
    elements.timeline.scrollHeight -
      elements.timeline.scrollTop -
      elements.timeline.clientHeight <
    40;
  for (const turn of snapshot.values()) {
    const section = findActivitySection(turn.turnId);
    if (section) {
      const open = state.expandedTurns.has(turn.turnId);
      fillActivityBody(section.querySelector(".ta-body"), turn, open);
      paintTurnActivity(section, turn);
    } else {
      elements.timeline.append(renderTurnActivity(turn));
      panelUi.setHasConversation(true);
    }
  }
  syncActivityTimer();
  // 只在本来就贴着底的时候跟随，否则会把正在看出参的人一直拽走。
  if (atBottom) elements.timeline.scrollTop = elements.timeline.scrollHeight;
}

function syncActivityTimer() {
  const turns = [...state.trace.snapshot().values()];
  const hasRunning = turns.some((turn) => turn.status === "running");
  if (hasRunning && !state.activityTimer) {
    state.activityTimer = window.setInterval(() => {
      const now = new Date().toISOString();
      for (const turn of state.trace.snapshot().values()) {
        if (turn.status !== "running") continue;
        tickRunningTurn(turn, now);
        const section = findActivitySection(turn.turnId);
        if (section) paintTurnActivity(section, turn);
      }
    }, 1000);
  }
  if (!hasRunning && state.activityTimer) {
    window.clearInterval(state.activityTimer);
    state.activityTimer = null;
  }
}

function seedTraceFromHistory(events) {
  // 每次切换/新建会话都要重建，否则上一个会话的 turn 会残留在时间线里。
  state.trace = createTraceReducer();
  for (const event of events) state.trace.push(traceEntryFromRecord(event));
}

function renderMessages(messages) {
  elements.timeline.replaceChildren();
  const activities = [...state.trace.snapshot().values()];
  let activityCursor = 0;
  for (const message of messages) {
    if (!['user', 'assistant'].includes(message.role) || typeof message.content !== "string" || !message.content.trim()) continue;
    const item = document.createElement("article");
    item.className = `agent-message ${message.role}`;
    renderMessageContent(item, message.role, message.content);
    if (message.role === "assistant" && feedbackEligibleIds.has(message.id)) item.append(feedback.element(message.id, feedbackEligibleIds.get(message.id)));
    elements.timeline.append(item);
    // 一次提问一条活动块，紧贴在这条提问下面。续跑产生的空提问不占用一条。
    if (message.role === "user" && message.content.trim() && activityCursor < activities.length) {
      elements.timeline.append(renderTurnActivity(activities[activityCursor]));
      activityCursor += 1;
    }
  }
  // 尾部：极端情况下活动块比提问多（例如历史被截断），补在最后不丢。
  while (activityCursor < activities.length) {
    elements.timeline.append(renderTurnActivity(activities[activityCursor]));
    activityCursor += 1;
  }
  syncActivityTimer();
  panelUi.setHasConversation(messages.length > 0 || activities.length > 0);
  elements.timeline.scrollTop = elements.timeline.scrollHeight;
}

function renderError(error, options) {
  const item = document.createElement("article");
  item.className = "agent-message error";
  item.textContent = formatUserFacingError(error, options);
  panelUi.setHasConversation(true);
  elements.timeline.append(item);
  elements.timeline.scrollTop = elements.timeline.scrollHeight;
}

async function api(path, options = {}) {
  const response = await authenticatedFetch(path, {
    credentials: "same-origin",
    ...options,
    headers: requestHeaders(identityHeaders, options),
  });
  if (response.status === 204) return null;
  let payload = null;
  try {
    payload = await response.json();
  } catch {}
  if (!response.ok) {
    const details = payload?.error || {};
    const error = new Error(
      details.message || `Workspace API failed (${response.status})`,
    );
    error.code = details.code;
    error.details = details.details;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function syncSkillEntry() {
  skillViewState.workspace = state.skillWorkspace;
  const available = skillNavigation.syncEntry();
  elements.skillCount.textContent = String(state.skillWorkspace?.skill_count || 0);
  return available;
}

function showSkillError(error) {
  elements.skillChangesNotice.textContent = formatUserFacingError(error);
  elements.skillChangesNotice.hidden = false;
}

const skillNavigation = SkillManager.createViewNavigationController({
  state: skillViewState,
  elements: {
    skillsButton: elements.skills,
    appShell: elements.panel,
    skillManagementView: elements.skillManagementView,
  },
});
const skillController = SkillManager.createController({
  api,
  elements: {
    backButton: elements.skillManagementBackButton,
    workspaceName: elements.skillManagementWorkspaceName,
    changesNotice: elements.skillChangesNotice,
    globalList: elements.globalSkillList,
    globalLoading: elements.globalSkillLoading,
    globalEmpty: elements.globalSkillEmpty,
    globalImportRoot: elements.globalSkillImportRoot,
    globalImportButton: elements.globalSkillImportButton,
    globalArchiveButton: elements.globalSkillArchiveButton,
    globalDirectoryInput: elements.globalSkillDirectoryInput,
    globalArchiveInput: elements.globalSkillArchiveInput,
    personalList: elements.personalSkillList,
    personalLoading: elements.personalSkillLoading,
    personalEmpty: elements.personalSkillEmpty,
    personalImportRoot: elements.personalSkillImportRoot,
    personalImportButton: elements.personalSkillImportButton,
    personalArchiveButton: elements.personalSkillArchiveButton,
    personalDirectoryInput: elements.personalSkillDirectoryInput,
    personalArchiveInput: elements.personalSkillArchiveInput,
    detailPanel: elements.skillDetailPanel,
    detailName: elements.skillDetailName,
    detailDescription: elements.skillDetailDescription,
    detailVersion: elements.skillDetailVersion,
    detailHash: elements.skillDetailHash,
    detailSource: elements.skillDetailSource,
    detailUpdatedAt: elements.skillDetailUpdatedAt,
    detailContent: elements.skillDetailContent,
    detailManifest: elements.skillDetailManifest,
    importConflictDialog: elements.skillImportConflictDialog,
    importConflictName: elements.skillImportConflictName,
    importConflictExistingHash: elements.skillImportConflictExistingHash,
    importConflictIncomingHash: elements.skillImportConflictIncomingHash,
    importRenameInput: elements.skillImportRenameInput,
    importOverwriteButton: elements.skillImportOverwriteButton,
    importRenameButton: elements.skillImportRenameButton,
    importCancelButton: elements.skillImportCancelButton,
    importConflictError: elements.skillImportConflictError,
  },
  getWorkspace: () => state.skillWorkspace,
  onChanged: (_workspaceId, lifecycleIsCurrent, effectiveCount) => {
    if (!lifecycleIsCurrent() || !state.skillWorkspace) return;
    state.skillWorkspace = {
      ...state.skillWorkspace,
      skill_count: effectiveCount,
    };
    syncSkillEntry();
  },
  onEnter: skillNavigation.showSkillManagement,
  onLeave: skillNavigation.showChat,
  onError: showSkillError,
});
const instructionsEditor = SkillManager.createInstructionsController({
  api,
  elements: {
    section: elements.workspaceInstructionsSection,
    editor: elements.workspaceInstructionsEditor,
    saveButton: elements.workspaceInstructionsSave,
    resetButton: elements.workspaceInstructionsReset,
    status: elements.workspaceInstructionsStatus,
    loading: elements.workspaceInstructionsLoading,
  },
  getWorkspace: () => state.skillWorkspace,
  // skillWorkspace 已经是「personal + 可管理」筛出来的，与 Skill 区块同一口径。
  isEditable: () => Boolean(state.skillWorkspace),
  onError: showSkillError,
});
skillNavigation.setSkillManager(skillController);
skillNavigation.setInstructionsEditor(instructionsEditor);
window.__davinciMvp = {
  eventTypes: observedEventTypes,
  openSkillManagement: () => skillNavigation.open(),
  // 测试钩子：直接灌 trace 事件，验证渲染层（尤其是被并段后残留块的清理）。
  pushTrace: (entry) => {
    state.trace.push(entry);
    renderActivityOnly();
  },
};

const feedback = createMessageFeedback({
  save: (messageId, payload) => api(
    `/api/sessions/${encodeURIComponent(state.sessionId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    { method: "PUT", body: JSON.stringify(payload) },
  ),
});
let feedbackEligibleIds = new Map();
let feedbackSessionId = null;

/** Load feedback only against persisted message IDs in the current authorized session. */
async function loadFeedback(events) {
  const sessionId = state.sessionId;
  feedbackEligibleIds = new Map();
  if (feedbackSessionId !== sessionId) feedback.reset();
  feedbackSessionId = sessionId;
  try {
    const values = await api(`/api/sessions/${encodeURIComponent(sessionId)}/feedback`);
    if (state.sessionId !== sessionId) return;
    feedback.hydrate(values);
    feedbackEligibleIds = completedQuestionFeedbackTargets(events, state.trace.snapshot().values());
  } catch (error) {
    if (!state.authRequired) renderError("评价加载失败，重新打开会话后可重试");
  }
}

/** Reload persisted messages after the stream so temporary SDK IDs cannot be rated. */
async function refreshCompletedHistory() {
  const sessionId = state.sessionId;
  const events = await api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
  if (state.sessionId !== sessionId) return;
  seedTraceFromHistory(events);
  await loadFeedback(events);
  if (state.sessionId !== sessionId) return;
  configureAgent(historyMessages(events));
}

function historyMessages(events) {
  const messages = [];
  const subscriptionIds = new Set();
  for (const event of events) {
    if (event.event_type === "message.user") {
      messages.push({ id: event.id, role: "user", content: event.payload.text || "" });
    }
    if (["message.assistant.completed", "subscription.progress"].includes(event.event_type)) {
      const id = event.event_type === "subscription.progress" ? subscriptionMessageId(event) : event.id;
      if (event.event_type === "subscription.progress") {
        if (subscriptionIds.has(id)) continue;
        subscriptionIds.add(id);
      }
      messages.push({ id, role: "assistant", content: event.payload.text || "" });
    }
  }
  return messages;
}

function configureAgent(initialMessages = []) {
  const nativeV2 = embedConfig?.protocolVersion === "agui-native-v2";
  const initialRuntimeContext = nativeV2 ? state.runtimeContext : state.hostContext;
  const runnerBridge = nativeV2
    ? {
        async executeToolCall(toolCallId, toolName, toolArgs) {
          if (!state.v2Bridge) throw new Error("Davinci V2 Bridge 不可用");
          const scope = { sessionId: state.sessionId, workspaceId: state.workspaceId, toolCallId };
          try {
            const result = await state.v2Bridge.executeToolCall(toolCallId, toolName, toolArgs, scope);
            void layoutDiagnostics.report(scope, result).catch(() => {});
            return result;
          } catch (error) {
            void layoutDiagnostics.report(scope, { status: "error" }).catch(() => {});
            throw error;
          }
        },
        async requestContext() {
          state.runtimeContext = await state.v2Bridge.requestContext();
          panelUi.setPageContext(state.runtimeContext?.state);
          return state.runtimeContext;
        },
      }
    : {
        async execute(toolName, toolArgs) {
          if (state.v1Bridge) return state.v1Bridge.execute(toolName, toolArgs);
          const result = await waitForParent(null, toolName, toolArgs);
          return { status: "executed", result };
        },
        async requestContext() {
          if (state.v1Bridge) return state.v1Bridge.requestContext();
          if (!state.hostContext) throw new Error("Davinci 宿主上下文不可用");
          return state.hostContext;
        },
      };
  state.agent = new HttpAgent({
    url: "/api/ag-ui",
    threadId: state.sessionId,
    initialMessages,
    initialState: nativeV2
      ? initialRuntimeContext?.state || {}
      : { hostContext: state.hostContext },
    headers: identityHeaders,
    fetch: async (input, init) => requireAgUiResponse(await authenticatedFetch(input, init)),
  });
  const agent = state.agent;
  const agentSessionId = state.sessionId;
  let liveRunId = null;
  const forwardSubscriptionStage = createSubscriptionStageForwarder(
    notice => state.v2Bridge?.sendSubscriptionStage(notice) || false,
  );
  agent.subscribe({
    onEvent({ event }) {
      if (state.agent !== agent || state.sessionId !== agentSessionId) return;
      observedEventTypes.push(event.type);
      if (event.type === "RUN_STARTED") liveRunId = event.runId;
      if (event.type === "RUN_STARTED" && state.pendingInput) state.pendingInput.accepted = true;
      if (event.type === "CUSTOM" && event.name === "workspace.subscription_progress") {
        forwardSubscriptionStage(event.value, liveRunId);
      }
      if (event.type === "CUSTOM" && event.name === "workspace.trace") {
        state.trace.push(event.value);
        renderActivityOnly();
      }
      if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") liveRunId = null;
    },
    onMessagesChanged({ messages }) {
      if (state.agent !== agent) return;
      renderMessages(messages);
    },
  });
  state.toolRunner = createFrontendToolRunner({
    agent: state.agent,
    bridge: runnerBridge,
    createRunId: createRuntimeId,
    createMessageId: createRuntimeId,
    getTools: (context) => nativeV2 ? context?.tools || [] : toolsForCurrentContext(context),
    getContextItems: (context) => nativeV2 ? context?.context || [] : [],
    initialContext: initialRuntimeContext,
    buildAgentState: (context) => nativeV2 ? context.state : { hostContext: context },
    getForwardedProps: () => ({
      workspaceId: state.workspaceId,
      model: elements.modelSelect.value || undefined,
      effort: elements.effortSelect.value || undefined,
    }),
    onRecoveryChange: rememberToolRecovery,
    // RUN_ERROR is propagated by the runner and handled with its Run identity.
  });
  renderMessages(initialMessages);
}

function toolsForCurrentContext(hostContext = state.hostContext) {
  if (!embedConfig) return toolsForHostContext(hostContext);
  const supported = new Set(hostContext?.supportedActions || []);
  return PUBLIC_TOOL_CONTRACTS
    .filter((contract) => contract.executor === "frontend" && supported.has(contract.action))
    .map((contract) => ({
      name: contract.action,
      description: contract.description,
      parameters: structuredClone(contract.inputSchema),
    }));
}

async function refreshSessions() {
  if (!state.workspaceId) return;
  try {
    const sessions = await api(
      `/api/workspaces/${encodeURIComponent(state.workspaceId)}/sessions`,
    );
    panelUi.setSessions(sessions, state.sessionId);
  } catch {
    // 会话列表只是导航辅助，拉取失败不影响当前对话。
  }
}

async function switchSession(sessionId) {
  if (state.running) {
    setStatus("Agent 正在运行，请先点击停止");
    return;
  }
  const events = await api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`);
  seedTraceFromHistory(events);
  state.sessionId = sessionId;
  state.toolRecovery = null;
  state.unresolvedRunId = null;
  state.pendingInput = null;
  renderSessionIdentity();
  localStorage.setItem(
    sessionStorageKey(embedConfig?.obId || "embed", state.workspaceId),
    sessionId,
  );
  await loadFeedback(events);
  configureAgent(historyMessages(events));
  restoreRunCheck();
  await readToolRecovery();
  syncRecoveryControls();
  setStatus("已切换会话");
  await refreshSessions();
}

async function restoreSession() {
  const storageKey = sessionStorageKey(embedConfig?.obId || "embed", state.workspaceId);
  const saved = localStorage.getItem(storageKey);
  if (!saved) return;
  try {
    await api(`/api/sessions/${encodeURIComponent(saved)}`);
    const events = await api(`/api/sessions/${encodeURIComponent(saved)}/messages`);
    seedTraceFromHistory(events);
    state.sessionId = saved;
    renderSessionIdentity();
    await loadFeedback(events);
    configureAgent(historyMessages(events));
    const savedDraft = sessionStorage.getItem(recoveryKey());
    if (savedDraft) {
      try {
        const draft = JSON.parse(savedDraft);
        elements.input.value = typeof draft.input === "string" ? draft.input : "";
        state.pendingInput = typeof draft.pendingInput?.text === "string"
          ? { text: draft.pendingInput.text, accepted: draft.pendingInput.accepted === true } : null;
        state.unresolvedRunId = typeof draft.runId === "string" ? draft.runId : null;
      } catch {}
      sessionStorage.removeItem(recoveryKey());
    }
    restoreRunCheck();
    await readToolRecovery();
    syncRecoveryControls();
    setStatus("Session 已恢复");
    if (state.unresolvedRunId) await checkRunStatus();
  } catch (error) {
    if (error.status === 403 || error.status === 404) localStorage.removeItem(storageKey);
    else throw error;
  }
}

async function createSession() {
  if (state.authRequired) throw new Error("请先重新认证");
  if (!state.workspaceId) throw new Error("Workspace 尚未就绪");
  if (state.creatingSession) return;
  state.creatingSession = true;
  elements.newSession.disabled = true;
  syncRecoveryControls();
  try {
    const workspaceId = state.workspaceId;
    await skillController.waitForPendingChanges(workspaceId);
    if (state.authRequired || state.workspaceId !== workspaceId) return;
    const session = await api(`/api/workspaces/${encodeURIComponent(workspaceId)}/sessions`, { method: "POST" });
    if (state.authRequired || state.workspaceId !== workspaceId) return;
    state.sessionId = session.id;
    state.toolRecovery = null;
    state.unresolvedRunId = null;
    state.pendingInput = null;
    renderSessionIdentity();
    localStorage.setItem(
      sessionStorageKey(embedConfig?.obId || "embed", state.workspaceId),
      session.id,
    );
    seedTraceFromHistory([]);
    feedbackEligibleIds = new Map();
    feedback.reset();
    configureAgent([]);
    syncRecoveryControls();
    setStatus("新 Session 已创建");
    await refreshSessions();
  } finally {
    state.creatingSession = false;
    elements.newSession.disabled = state.running;
    syncRecoveryControls();
  }
}

function waitForParent(toolCallId, toolName, args) {
  if (!state.hostContext || !nonce) return Promise.reject(new Error("Davinci 宿主上下文不可用"));
  const requestId = createRuntimeId();
  const messageType = toolName === "navigateTo"
    ? MESSAGE_TYPES.UI_COMMAND
    : MESSAGE_TYPES.CAPABILITY_REQUEST;
  const envelope = createDavinciEnvelope({
    messageType,
    requestId,
    toolCallId,
    nonce,
    contextVersion: state.hostContext.contextVersion,
    payload: toolName === "navigateTo" ? args : { capability: toolName, arguments: args },
  });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      state.pendingParent.delete(requestId);
      reject(new Error("Davinci 页面响应超时"));
    }, 15_000);
    state.pendingParent.set(requestId, { resolve, reject, timer, toolCallId });
    window.parent.postMessage(envelope, expectedParentOrigin);
  });
}

/** 面板运行状态提示：父页没声明 status 能力就静默跳过，失败也不能影响运行本身。 */
function reportRunState(run) {
  try {
    if (!state.v2Bridge?.getPanelCommands().includes("status")) return;
    state.v2Bridge.sendPanelCommand({ action: "status", run });
  } catch {
    // 提示是锦上添花；父页拒绝或桥未就绪时保持静默。
  }
}

function setRunningUi(running) {
  state.running = running;
  elements.send.hidden = running;
  elements.stop.hidden = !running;
  elements.newSession.disabled = running || state.creatingSession;
  elements.modelSelect.disabled = running;
  elements.effortSelect.disabled = running;
  panelElements.composerModelTrigger.disabled = running;
  syncRecoveryControls();
}

async function stopRun() {
  const sessionId = state.sessionId;
  const runId = state.toolRunner?.getCurrentRunId?.();
  state.toolRunner?.abort?.();
  markConnectionLost(runId);
  setStatus("正在停止");
  if (!runId) {
    restorePendingInput();
    setStatus("已停止");
    reportRunState("stopped");
    return;
  }
  try {
    const result = await api(`/api/turns/${runId}/cancel`, { method: "POST" });
    if (state.sessionId !== sessionId || state.unresolvedRunId !== runId) return;
    setStatus(result.status.includes("cancel") ? "已停止" : "已请求停止，等待服务端确认");
    reportRunState(result.status.includes("cancel") ? "stopped" : "unknown");
  } catch (error) {
    if (state.sessionId !== sessionId || state.unresolvedRunId !== runId) return;
    setStatus(formatUserFacingError(error, { context: "stop" }));
  }
  await reconcileStoppedRun(sessionId, runId);
}

async function sendMessage(text) {
  if (state.authRequired || state.unresolvedRunId) throw new Error("请先恢复认证或核对执行状态");
  if (state.creatingSession) throw new Error("正在创建新会话，请稍后发送");
  if (!state.sessionId || !state.agent || !state.toolRunner) throw new Error("请先新建 Session");
  const nativeV2 = embedConfig?.protocolVersion === "agui-native-v2";
  if (nativeV2 && !state.runtimeContext) throw new Error("正在等待 Davinci 页面状态");
  if (!nativeV2 && !state.hostContext) throw new Error("正在等待 Davinci 页面上下文");
  const currentContext = nativeV2 ? state.runtimeContext : state.hostContext;
  state.toolRunner.setCurrentContext(currentContext);
  state.agent.setState(nativeV2 ? currentContext.state : { hostContext: currentContext });
  // Earlier questions stay independently rateable while this new question runs.
  setRunningUi(true);
  setStatus("Agent 正在运行");
  reportRunState("running");
  const pendingInput = { text, accepted: false };
  const sessionId = state.sessionId;
  state.pendingInput = pendingInput;
  const previousMessages = structuredClone(state.agent.messages);
  try {
    await skillController.waitForPendingChanges(state.workspaceId);
    // Stop or a session/auth change during the save must not dispatch later.
    if (state.pendingInput !== pendingInput || state.sessionId !== sessionId || state.authRequired) return;
    const result = await state.toolRunner.runUserMessage(text);
    if (!result?.aborted) {
      state.pendingInput = null;
      setStatus("就绪");
      reportRunState("finished");
      try { await refreshCompletedHistory(); } catch (error) {
        if (!state.authRequired) renderError("回复已完成，历史同步失败；重新打开会话后可评价");
      }
    }
  } catch (error) {
    if (isUnacceptedAgUiRequest(error, state.pendingInput)) {
      restorePendingInput();
      configureAgent(previousMessages);
    }
    else if (error.runId && error.status !== 401) markConnectionLost(error.runId);
    else if (error.status === 401 && state.pendingInput?.accepted) markConnectionLost(error.runId);
    else restorePendingInput();
    if (error.code === "TOOL_CONTINUATION_REQUIRED") {
      if (!elements.input.value && state.pendingInput?.text) elements.input.value = state.pendingInput.text;
      await readToolRecovery();
    }
    renderError(error, { outcomeUnknown: Boolean(state.unresolvedRunId) });
    if (!state.authRequired) setStatus(state.unresolvedRunId ? "连接中断，执行结果待确认" : "运行失败");
    reportRunState(state.unresolvedRunId ? "unknown" : "failed");
  } finally {
    setRunningUi(false);
  }
}

window.addEventListener("message", (event) => {
  if (embedConfig) return;
  const envelope = event.data;
  if (envelope?.protocol !== PROTOCOL || envelope?.protocolVersion !== PROTOCOL_VERSION) return;
  const expected = {
    expectedOrigin: expectedParentOrigin,
    expectedSource: window.parent,
    nonce,
    contextVersion: state.hostContext?.contextVersion ?? envelope.contextVersion,
    now: Date.now(),
  };
  const validation = validateDavinciEnvelope(envelope, event, expected);
  if (!validation.ok) return;
  if (envelope.messageType === MESSAGE_TYPES.HOST_CONTEXT) {
    if (!validateHostContext(envelope.payload)) return;
    state.hostContext = envelope.payload;
    panelUi.setPageContext(legacyPageState(state.hostContext));
    state.agent?.setState({ hostContext: state.hostContext });
    state.toolRunner?.setCurrentContext(state.hostContext);
    if (!state.running) {
      setStatus(state.sessionId ? "就绪" : "宿主已连接，请新建 Session");
    }
    return;
  }
  if (![MESSAGE_TYPES.CAPABILITY_RESULT, MESSAGE_TYPES.UI_ACK].includes(envelope.messageType)) return;
  const pending = state.pendingParent.get(envelope.requestId);
  if (!pending || pending.toolCallId !== envelope.toolCallId) return;
  clearTimeout(pending.timer);
  state.pendingParent.delete(envelope.requestId);
  if (envelope.payload?.error) {
    const error = new Error(envelope.payload.message || "Davinci frontend Tool failed.");
    error.code = envelope.payload.error;
    pending.reject(error);
  } else {
    pending.resolve(envelope.payload);
  }
});

elements.newSession.addEventListener("click", () => {
  if (state.running) {
    setStatus("Agent 正在运行，请先点击停止");
    return;
  }
  createSession().catch((error) => renderError(error));
});

elements.copySessionId.addEventListener("click", () => {
  copySessionId().catch((error) => {
    renderError(error, { context: "clipboard" });
    const range = document.createRange();
    range.selectNodeContents(elements.sessionId);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  });
});

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = elements.input.value.trim();
  if (!text || state.running || state.authRequired || state.unresolvedRunId) return;
  elements.input.value = "";
  sendMessage(text).catch((error) => {
    if (!elements.input.value.trim()) elements.input.value = text;
    renderError(error);
    setStatus("运行失败");
  });
});

async function initialize() {
  if (embedConfig) {
    if (embedConfig.protocolVersion === "agui-native-v2") {
      state.v2Bridge = createHostBridgeV2({
        parentOrigin: expectedParentOrigin,
        // 父页把启动器拖到哪只有它知道；iframe 持有 Host 会话，所以由这里落库。
        onPanelState: (panelState) => savePreferences(panelState),
        onLayoutSolve: (problem, scope, signal) => layoutDiagnostics.solve(problem, scope, signal),
      });
      await state.v2Bridge.start();
      panelUi.setPanelCommands(state.v2Bridge.getPanelCommands());
      reportRunState(state.running ? "running" : "idle");
      state.runtimeContext = await state.v2Bridge.requestContext();
      state.hostContext = state.runtimeContext.state;
      panelUi.setPageContext(state.runtimeContext.state);
    } else {
      state.v1Bridge = createHostBridge({ parentOrigin: expectedParentOrigin });
      await state.v1Bridge.start();
      state.hostContext = await state.v1Bridge.requestContext();
      panelUi.setPageContext(legacyPageState(state.hostContext));
    }
  }
  const workspaces = await api("/api/workspaces");
  const {agentWorkspace, skillWorkspace} = selectEmbedWorkspaces(workspaces);
  state.workspaceId = agentWorkspace?.id || null;
  state.skillWorkspace = skillWorkspace;
  syncSkillEntry();
  if (!state.workspaceId) throw new Error("没有可用 Workspace");
  await restoreSession();
  await refreshSessions();
  if (!state.hostContext && !state.runtimeContext) setStatus("等待 Davinci 宿主页面");
}

initialize().catch((error) => renderError(error));

elements.reauthenticate.addEventListener("click", () => void reauthenticate());
elements.checkRun.addEventListener("click", () => void checkRunStatus());

elements.stop.addEventListener("click", () => {
  stopRun().catch((error) => renderError(error, { context: "stop" }));
});
