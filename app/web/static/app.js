"use strict";

let userFacingUI;

const $ = (id) => document.getElementById(id);

const elements = {
  serviceStatus: $("serviceStatus"),
  serviceStatusText: $("serviceStatusText"),
  workspaceSelect: $("workspaceSelect"),
  workspaceKind: $("workspaceKind"),
  debugIdentityPanel: $("debugIdentityPanel"),
  debugObIdSelect: $("debugObIdSelect"),
  debugSessionLocatorInput: $("debugSessionLocatorInput"),
  debugSessionLocatorButton: $("debugSessionLocatorButton"),
  capabilitySummary: $("capabilitySummary"),
  skillsButton: $("skillsButton"),
  appShell: document.querySelector(".app-shell"),
  skillManagementView: $("skillManagementView"),
  skillManagementBackButton: $("skillManagementBackButton"),
  skillManagementWorkspaceName: $("skillManagementWorkspaceName"),
  skillChangesNotice: $("skillChangesNotice"),
  globalSkillList: $("globalSkillList"),
  globalSkillLoading: $("globalSkillLoading"),
  globalSkillEmpty: $("globalSkillEmpty"),
  globalSkillImportRoot: $("globalSkillImportRoot"),
  globalSkillImportButton: $("globalSkillImportButton"),
  globalSkillArchiveButton: $("globalSkillArchiveButton"),
  globalSkillDirectoryInput: $("globalSkillDirectoryInput"),
  globalSkillArchiveInput: $("globalSkillArchiveInput"),
  personalSkillList: $("personalSkillList"),
  personalSkillLoading: $("personalSkillLoading"),
  personalSkillEmpty: $("personalSkillEmpty"),
  workspaceInstructionsSection: $("workspaceInstructionsSection"),
  workspaceInstructionsEditor: $("workspaceInstructionsEditor"),
  workspaceInstructionsSave: $("workspaceInstructionsSave"),
  workspaceInstructionsReset: $("workspaceInstructionsReset"),
  workspaceInstructionsStatus: $("workspaceInstructionsStatus"),
  workspaceInstructionsLoading: $("workspaceInstructionsLoading"),
  personalSkillImportRoot: $("personalSkillImportRoot"),
  personalSkillImportButton: $("personalSkillImportButton"),
  personalSkillArchiveButton: $("personalSkillArchiveButton"),
  personalSkillDirectoryInput: $("personalSkillDirectoryInput"),
  personalSkillArchiveInput: $("personalSkillArchiveInput"),
  skillDetailPanel: $("skillDetailPanel"),
  skillDetailName: $("skillDetailName"),
  skillDetailDescription: $("skillDetailDescription"),
  skillDetailVersion: $("skillDetailVersion"),
  skillDetailHash: $("skillDetailHash"),
  skillDetailSource: $("skillDetailSource"),
  skillDetailUpdatedAt: $("skillDetailUpdatedAt"),
  skillDetailContent: $("skillDetailContent"),
  skillDetailManifest: $("skillDetailManifest"),
  newSessionButton: $("newSessionButton"),
  sessionList: $("sessionList"),
  sessionCount: $("sessionCount"),
  sessionTitle: $("sessionTitle"),
  sessionIdentity: $("sessionIdentity"),
  sessionIdValue: $("sessionIdValue"),
  copySessionIdButton: $("copySessionIdButton"),
  sessionContextButton: $("sessionContextButton"),
  sessionPerformanceButton: $("sessionPerformanceButton"),
  sessionWorkspace: $("sessionWorkspace"),
  sessionStatus: $("sessionStatus"),
  sessionActions: $("sessionActions"),
  messageTimeline: $("messageTimeline"),
  conversationEmpty: $("conversationEmpty"),
  attachmentInput: $("attachmentInput"),
  attachmentButton: $("attachmentButton"),
  attachmentTray: $("attachmentTray"),
  messageInput: $("messageInput"),
  composerAutocomplete: $("composerAutocomplete"),
  composerAutocompleteStatus: $("composerAutocompleteStatus"),
  sendButton: $("sendButton"),
  stopButton: $("stopButton"),
  composerState: $("composerState"),
  usageSummary: $("usageSummary"),
  executionPanel: $("executionPanel"),
  executionPhase: $("executionPhase"),
  executionElapsed: $("executionElapsed"),
  executionHeartbeat: $("executionHeartbeat"),
  executionConnection: $("executionConnection"),
  executionSteps: $("executionSteps"),
  dropZone: $("dropZone"),
  renameDialog: $("renameDialog"),
  renameForm: $("renameForm"),
  renameInput: $("renameInput"),
  renameSessionButton: $("renameSessionButton"),
  deleteDialog: $("deleteDialog"),
  deleteForm: $("deleteForm"),
  deleteSessionButton: $("deleteSessionButton"),
  sessionContextDialog: $("sessionContextDialog"),
  sessionContextContent: $("sessionContextContent"),
  sessionPerformanceDialog: $("sessionPerformanceDialog"),
  sessionPerformanceContent: $("sessionPerformanceContent"),
  toast: $("toast"),
  sidebarToggle: $("sidebarToggle"),
  sessionSidebar: $("sessionSidebar"),
  mobileScrim: $("mobileScrim"),
  skillImportConflictDialog: $("skillImportConflictDialog"),
  skillImportConflictName: $("skillImportConflictName"),
  skillImportConflictExistingHash: $("skillImportConflictExistingHash"),
  skillImportConflictIncomingHash: $("skillImportConflictIncomingHash"),
  skillImportRenameInput: $("skillImportRenameInput"),
  skillImportOverwriteButton: $("skillImportOverwriteButton"),
  skillImportRenameButton: $("skillImportRenameButton"),
  skillImportCancelButton: $("skillImportCancelButton"),
  skillImportConflictError: $("skillImportConflictError"),
};

const maxFilesPerTurn = Number(document.body.dataset.maxFilesPerTurn);

const state = {
  workspaces: [],
  workspace: null,
  sessions: [],
  session: null,
  pendingAttachments: [],
  deletedAttachmentIdsBySession: new Map(),
  sessionSelectionGeneration: 0,
  sessionListRequestGeneration: 0,
  eventSource: null,
  activeTurnId: null,
  seenEvents: new Set(),
  streamMessages: new Map(),
  toolElements: new Map(),
  running: false,
  transportState: "ready",
  executionState: "ready",
  serviceState: "ready",
  currentPhase: "准备执行",
  turnStartedAt: null,
  turnEndedAt: null,
  lastTransportActivityAt: null,
  lastBusinessActivityAt: null,
  disconnectEpisodeActive: false,
  connectionCheckPending: false,
  autocomplete: null,
  skillCandidates: null,
  skillManager: null,
  skillViewNavigation: null,
  activeView: "chat",
  workspaceEpoch: 0,
  workspaceRefreshCoordinator: null,
  sessionCreationCoordinator: null,
  creatingSession: false,
  composerComposing: false,
  loadingAttachments: false,
  uploadingAttachments: false,
  removingAttachments: new Map(),
  deletingSession: null,
  uploadOperationGeneration: 0,
  activeUploadOperationGeneration: null,
  uploadLabel: "正在上传附件",
  paste: null,
  debugIdentity: null,
  pendingLocatedSession: null,
  timelineTools: SessionInspector.createToolTimelineState(),
  timelineToolElements: new Map(),
};

const terminalEvents = ["turn.completed", "turn.failed", "turn.cancelled", "turn.interrupted"];
const eventTypes = [
  "turn.started",
  "turn.progress",
  "message.user",
  "message.assistant.delta",
  "message.assistant.completed",
  "subscription.progress",
  "tool.started",
  "tool.completed",
  "frontend_tool.deferred",
  "context.compacted",
  "usage.updated",
  "turn.failed",
  "turn.cancelled",
  "turn.interrupted",
  "turn.completed",
  "heartbeat",
];

async function api(path, options = {}) {
  const headers = state.debugIdentity
    ? state.debugIdentity.headers(options.headers || {})
    : options.headers;
  const response = await fetch(path, {...options, headers});
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = body?.error?.message || body?.detail || `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    error.code = body?.error?.code || "request_failed";
    error.details = body?.error?.details || null;
    throw error;
  }
  return body;
}

async function initialize() {
  userFacingUI = await import("./user-facing-error.mjs");
  bindEvents();
  if (elements.debugIdentityPanel) {
    state.debugIdentity = DebugIdentity.createController({
      storage: sessionStorage,
      isRunning: () => state.running,
      fetchJson: async (path) => {
        const response = await fetch(path);
        const body = await response.json();
        if (!response.ok) throw new Error(body?.error?.message || "调试请求失败");
        return body;
      },
    });
    const users = await state.debugIdentity.loadUsers();
    renderDebugUsers(users);
    if (!state.debugIdentity.getObId()) {
      setTransportState("disconnected");
      showToast("没有可调试的 OBID 用户");
      return;
    }
  }
  state.autocomplete = ComposerAutocomplete.createController({
    input: elements.messageInput,
    menu: elements.composerAutocomplete,
    status: elements.composerAutocompleteStatus,
    searchFiles: async (query, signal) => {
      if (!state.session) return { items: [], truncated: false };
      return api(
        `/api/sessions/${encodeURIComponent(state.session.id)}/files?q=${encodeURIComponent(query)}`,
        { signal },
      );
    },
    onError: showError,
  });
  state.skillCandidates = SkillManager.createCandidateController({
    api,
    autocomplete: state.autocomplete,
    onError: showError,
  });
  state.workspaceRefreshCoordinator = SkillManager.createWorkspaceRefreshCoordinator({
    loadWorkspaces: () => api("/api/workspaces"),
    getSelection: () => ({
      workspaceId: state.workspace?.id || null,
      epoch: state.workspaceEpoch,
      sessionGeneration: state.sessionSelectionGeneration,
    }),
    apply: (workspaces, workspaceId) => {
      state.workspaces = workspaces;
      state.workspace = workspaces.find((item) => item.id === workspaceId) || null;
      renderWorkspaceOptions();
      elements.workspaceSelect.value = workspaceId;
      renderCapabilitySummary();
      if (!state.session && state.workspace) {
        void state.skillCandidates.selectWorkspace(workspaceId);
      }
    },
  });
  state.sessionCreationCoordinator = SkillManager.createSessionCreationCoordinator({
    api,
    waitForSkills: (workspaceId) => state.skillManager.waitForPendingChanges(workspaceId),
    getSelection: () => ({
      workspaceId: state.workspace?.id || null,
      epoch: state.workspaceEpoch,
      sessionGeneration: state.sessionSelectionGeneration,
    }),
    commit: async (session) => {
      state.sessions.unshift(session);
      const selection = selectSession(session.id, {session});
      state.creatingSession = false;
      updateSessionHeader();
      await selection;
    },
  });
  state.skillViewNavigation = SkillManager.createViewNavigationController({
    state,
    elements: {
      skillsButton: elements.skillsButton,
      appShell: elements.appShell,
      skillManagementView: elements.skillManagementView,
    },
  });
  state.skillManager = SkillManager.createController({
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
    getWorkspace: () => state.workspace,
    onChanged: (workspaceId, lifecycleIsCurrent) => (
      state.workspaceRefreshCoordinator.refresh(workspaceId, lifecycleIsCurrent)
    ),
    onEnter: state.skillViewNavigation.showSkillManagement,
    onLeave: state.skillViewNavigation.showChat,
    onError: showError,
  });
  state.instructionsEditor = SkillManager.createInstructionsController({
    api,
    elements: {
      section: elements.workspaceInstructionsSection,
      editor: elements.workspaceInstructionsEditor,
      saveButton: elements.workspaceInstructionsSave,
      resetButton: elements.workspaceInstructionsReset,
      status: elements.workspaceInstructionsStatus,
      loading: elements.workspaceInstructionsLoading,
    },
    getWorkspace: () => state.workspace,
    // 与个人 Skill 区块同一个可见性口径。
    isEditable: () => Boolean(
      state.workspace
      && state.workspace.kind === "personal"
      && state.workspace.can_manage_skills,
    ),
    onChanged: () => state.skillManager.refresh(),
    onError: showError,
  });
  state.skillViewNavigation.setSkillManager(state.skillManager);
  state.skillViewNavigation.setInstructionsEditor(state.instructionsEditor);
  state.paste = ComposerPaste.createController({
    input: elements.messageInput,
    canUpload: () => Boolean(state.session)
      && !state.running
      && !state.loadingAttachments
      && !state.uploadingAttachments
      && state.removingAttachments.size === 0
      && !state.deletingSession
      && ServiceHealth.canExecute(state.serviceState),
    getRemainingSlots: () => maxFilesPerTurn - state.pendingAttachments.length,
    uploadFiles: (files) => uploadFiles(files, {label: "正在上传图片"}),
    onError: showError,
  });
  const syncAutocompleteExpanded = () => {
    elements.messageInput.setAttribute(
      "aria-expanded",
      elements.composerAutocomplete.hidden ? "false" : "true",
    );
  };
  new MutationObserver(syncAutocompleteExpanded).observe(
    elements.composerAutocomplete,
    { attributes: true, attributeFilter: ["hidden"] },
  );
  syncAutocompleteExpanded();
  try {
    await refreshServiceHealth();
    state.workspaces = await api("/api/workspaces");
    renderWorkspaceOptions();
    const available = state.workspaces.filter((workspace) => workspace.available);
    const query = new URLSearchParams(window.location?.search || "");
    const requestedWorkspace = query.get("workspace");
    const requestedSession = query.get("session");
    const remembered = localStorage.getItem("workspace-agent.workspace");
    const selected = available.find((workspace) => workspace.id === requestedWorkspace)
      || available.find((workspace) => workspace.id === remembered) || available[0];
    if (selected) {
      elements.workspaceSelect.value = selected.id;
      await selectWorkspace(selected.id);
      if (requestedSession) await selectSession(requestedSession);
    } else {
      showToast("没有可用的 Workspace");
    }
    setTransportState("ready");
  } catch (error) {
    setTransportState("disconnected");
    showError(error);
  }
  window.setInterval(refreshServiceHealth, 5000);
}

function bindEvents() {
  elements.workspaceSelect.addEventListener("change", () => selectWorkspace(elements.workspaceSelect.value));
  elements.newSessionButton.addEventListener("click", createSession);
  elements.attachmentButton.addEventListener("click", () => elements.attachmentInput.click());
  elements.attachmentInput.addEventListener("change", () => uploadFiles(elements.attachmentInput.files));
  elements.sendButton.addEventListener("click", sendMessage);
  elements.stopButton.addEventListener("click", stopTurn);
  elements.renameSessionButton.addEventListener("click", openRenameDialog);
  elements.deleteSessionButton.addEventListener("click", openDeleteDialog);
  elements.renameForm.addEventListener("submit", renameSession);
  elements.deleteForm.addEventListener("submit", deleteSession);
  elements.copySessionIdButton.addEventListener("click", copySessionId);
  elements.sessionContextButton.addEventListener("click", openSessionContext);
  elements.sessionPerformanceButton.addEventListener("click", openSessionPerformance);
  elements.debugObIdSelect?.addEventListener("change", async () => {
    try {
      const selection = await state.debugIdentity.select(
        elements.debugObIdSelect.value,
      );
      if (selection.changed) await reloadDebugIdentity();
    } catch (error) {
      elements.debugObIdSelect.value = state.debugIdentity.getObId() || "";
      showError(error);
    }
  });
  elements.debugSessionLocatorButton?.addEventListener("click", locateDebugSession);
  elements.debugSessionLocatorInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") locateDebugSession();
  });
  elements.messageInput.addEventListener("input", () => {
    resizeComposer();
    state.autocomplete?.handleInput();
  });
  elements.messageInput.addEventListener("compositionstart", () => {
    state.composerComposing = true;
  });
  elements.messageInput.addEventListener("compositionend", () => {
    state.composerComposing = false;
  });
  elements.messageInput.addEventListener("keydown", handleComposerKeydown);
  elements.sidebarToggle.addEventListener("click", openSidebar);
  elements.mobileScrim.addEventListener("click", closeSidebar);
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => $(button.dataset.closeDialog).close());
  });
  ["dragenter", "dragover"].forEach((eventName) => {
    elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropZone.classList.add("dragging");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropZone.classList.remove("dragging");
    });
  });
  elements.dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));
  window.setInterval(refreshSessions, 5000);
  window.setInterval(updateExecutionPanel, 1000);
}

function renderDebugUsers(users) {
  elements.debugObIdSelect.replaceChildren();
  users.forEach((user) => {
    const option = document.createElement("option");
    option.value = user.ob_id;
    option.textContent = `${user.ob_id} · ${user.workspace_count} Workspace · ${user.session_count} Session`;
    elements.debugObIdSelect.append(option);
  });
  elements.debugObIdSelect.disabled = !users.length;
  elements.debugObIdSelect.value = state.debugIdentity.getObId() || "";
}

async function reloadDebugIdentity() {
  closeEventSource();
  state.skillManager?.close();
  state.workspaceEpoch += 1;
  state.sessionListRequestGeneration += 1;
  state.workspaces = [];
  state.workspace = null;
  state.sessions = [];
  clearCurrentSessionSelection();
  state.workspaces = await api("/api/workspaces");
  renderWorkspaceOptions();
  const available = state.workspaces.filter((workspace) => workspace.available);
  const located = state.pendingLocatedSession;
  const selected = available.find(
    (workspace) => workspace.id === located?.workspace_id,
  ) || available[0];
  if (!selected) return;
  elements.workspaceSelect.value = selected.id;
  await selectWorkspace(selected.id);
  if (located && selected.id === located.workspace_id) {
    state.pendingLocatedSession = null;
    await selectSession(located.session_id);
  }
}

async function locateDebugSession() {
  try {
    const located = await state.debugIdentity.locateSession(
      elements.debugSessionLocatorInput.value,
    );
    state.pendingLocatedSession = located;
    await reloadDebugIdentity();
  } catch (error) {
    showError(error);
  }
}

function renderWorkspaceOptions() {
  elements.workspaceSelect.replaceChildren();
  state.workspaces.forEach((workspace) => {
    const option = document.createElement("option");
    option.value = workspace.id;
    option.textContent = workspace.available ? workspace.name : `${workspace.name}（不可用）`;
    option.disabled = !workspace.available;
    elements.workspaceSelect.append(option);
  });
}

async function selectWorkspace(workspaceId) {
  state.workspaceEpoch += 1;
  closeEventSource();
  state.skillManager?.close();
  state.sessionListRequestGeneration += 1;
  state.composerComposing = false;
  state.workspace = state.workspaces.find((workspace) => workspace.id === workspaceId) || null;
  state.session = null;
  void state.skillCandidates?.selectWorkspace(state.workspace?.id || null);
  state.pendingAttachments = [];
  state.loadingAttachments = false;
  localStorage.setItem("workspace-agent.workspace", workspaceId);
  renderCapabilitySummary();
  clearConversation();
  updateSessionHeader();
  renderAttachmentTray();
  await loadSessions();
}

function renderCapabilitySummary() {
  elements.capabilitySummary.replaceChildren();
  elements.workspaceKind.textContent = state.workspace?.kind || "Workspace";
  if (!state.workspace) {
    elements.skillsButton.textContent = "0 Skills";
    elements.capabilitySummary.append(elements.skillsButton);
    state.skillViewNavigation?.syncEntry();
    return;
  }
  [state.workspace.model].forEach((text) => {
    const span = document.createElement("span");
    span.className = "capability-pill";
    span.textContent = text;
    elements.capabilitySummary.append(span);
  });
  elements.skillsButton.textContent = `${state.workspace.skill_count} Skills`;
  elements.skillsButton.title = `管理 ${state.workspace.name} 的 Skills`;
  elements.capabilitySummary.append(elements.skillsButton);
  state.skillViewNavigation?.syncEntry();
  const mcp = document.createElement("span");
  mcp.className = "capability-pill";
  mcp.textContent = `${state.workspace.mcp_server_count} MCP`;
  elements.capabilitySummary.append(mcp);
  if (state.workspace.personal_memory_enabled) {
    const memory = document.createElement("span");
    memory.className = "capability-pill";
    memory.textContent = "个人记忆";
    memory.title = "仅当前用户和当前 Workspace 可用";
    elements.capabilitySummary.append(memory);
  }
}

async function loadSessions({ preserveSelection = false } = {}) {
  if (!state.workspace) return {applied: false};
  const workspaceId = state.workspace.id;
  const requestGeneration = ++state.sessionListRequestGeneration;
  const selectedId = preserveSelection ? (state.session?.id || null) : null;
  const selectionGeneration = state.sessionSelectionGeneration;
  const sessions = await api(
    `/api/workspaces/${encodeURIComponent(workspaceId)}/sessions`,
  );
  if (
    state.workspace?.id !== workspaceId
    || state.sessionListRequestGeneration !== requestGeneration
  ) {
    return {applied: false};
  }

  state.sessions = sessions;
  const selectionUnchanged = state.sessionSelectionGeneration === selectionGeneration
    && (state.session?.id || null) === selectedId;
  if (preserveSelection && selectedId && selectionUnchanged) {
    const freshSession = sessions.find((session) => session.id === selectedId);
    if (freshSession) {
      state.session = freshSession;
    } else {
      clearCurrentSessionSelection();
    }
  }
  renderSessions();
  updateSessionHeader();
  return {
    applied: true,
    preserveSelection,
    selectedId,
    selectionGeneration,
    selectionUnchanged,
    workspaceId,
    requestGeneration,
  };
}

async function refreshSessions() {
  if (!state.workspace) return;
  try {
    const result = await loadSessions({ preserveSelection: true });
    if (!result.applied) return;
    if (!state.disconnectEpisodeActive) setTransportState("ready");
    if (
      result.selectionUnchanged
      && result.selectedId
      && state.workspace?.id === result.workspaceId
      && state.sessionListRequestGeneration === result.requestGeneration
      && state.sessionSelectionGeneration === result.selectionGeneration
      && state.session?.id === result.selectedId
      && state.running
      && state.session?.status
      && state.session.status !== "running"
    ) {
      await selectSession(result.selectedId, { session: state.session });
    }
  } catch (_) {
    setTransportState("disconnected");
  }
}

function renderSessions() {
  elements.sessionList.replaceChildren();
  elements.sessionCount.textContent = String(state.sessions.length);
  state.sessions.forEach((session) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `session-row${state.session?.id === session.id ? " active" : ""}`;
    button.dataset.sessionId = session.id;
    button.setAttribute("role", "listitem");

    const title = document.createElement("span");
    title.className = "session-row-title";
    title.textContent = session.title;
    const dot = document.createElement("span");
    dot.className = `session-state-dot ${session.status}`;
    dot.setAttribute("aria-label", session.status);
    const time = document.createElement("span");
    time.className = "session-row-time";
    time.textContent = formatTime(session.updated_at);
    button.append(title, dot, time);
    button.addEventListener("click", () => selectSession(session.id));
    elements.sessionList.append(button);
  });
}

async function createSession() {
  if (
    !state.workspace
    || !state.sessionCreationCoordinator
    || state.creatingSession
    || state.serviceState === "disconnected"
  ) return;
  state.creatingSession = true;
  updateSessionHeader();
  try {
    await state.sessionCreationCoordinator.create();
  } catch (error) {
    showError(error);
  } finally {
    state.creatingSession = false;
    updateSessionHeader();
  }
}

async function selectSession(sessionId, { session = null } = {}) {
  closeEventSource();
  closeSidebar();
  state.session = session || state.sessions.find((item) => item.id === sessionId) || await api(`/api/sessions/${sessionId}`);
  const selectedId = state.session.id;
  void state.skillCandidates.selectSession(selectedId);
  state.composerComposing = false;
  state.pendingAttachments = [];
  state.loadingAttachments = true;
  const selectionGeneration = ++state.sessionSelectionGeneration;
  state.seenEvents.clear();
  state.streamMessages.clear();
  state.toolElements.clear();
  state.timelineTools = SessionInspector.createToolTimelineState();
  state.timelineToolElements.clear();
  clearConversation();
  renderSessions();
  renderAttachmentTray();
  updateSessionHeader();
  setRunning(state.session.status === "running");
  await loadPendingAttachments(selectedId, selectionGeneration);
  try {
    const history = await api(`/api/sessions/${encodeURIComponent(selectedId)}/messages`);
    if (state.session?.id !== selectedId) return;
    history.forEach((record) => handleEvent(record.event_type, record.payload, {
      key: `${record.turn_id}:${record.sequence}`,
      turnId: record.turn_id,
      history: true,
      occurredAt: record.created_at,
    }));
    if (history.length === 0) showConversationEmpty("开始新的对话");
    if (state.session.status === "running" && history.length) {
      connectEvents(history[history.length - 1].turn_id);
    } else {
      resetExecutionPanel();
    }
    scrollTimeline();
  } catch (error) {
    if (state.session?.id === selectedId) showError(error);
  }
}

async function loadPendingAttachments(sessionId, selectionGeneration) {
  try {
    const pending = await api(
      `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
    );
    if (
      state.session?.id !== sessionId
      || state.sessionSelectionGeneration !== selectionGeneration
    ) return;
    state.pendingAttachments = withoutDeletedAttachments(
      sessionId,
      mergeAttachments(pending, state.pendingAttachments),
    );
    renderAttachmentTray();
  } catch (error) {
    if (
      state.session?.id === sessionId
      && state.sessionSelectionGeneration === selectionGeneration
    ) showError(error);
  } finally {
    if (
      state.session?.id === sessionId
      && state.sessionSelectionGeneration === selectionGeneration
    ) {
      state.loadingAttachments = false;
      updateSessionHeader();
    }
  }
}

function updateSessionHeader() {
  elements.sessionWorkspace.textContent = state.workspace?.name || "Workspace";
  elements.sessionTitle.textContent = state.session?.title || "选择或新建会话";
  elements.sessionIdentity.hidden = !state.session;
  elements.sessionIdValue.textContent = state.session?.id || "";
  elements.sessionActions.hidden = !state.session;
  elements.sessionStatus.textContent = state.session?.status || "idle";
  elements.sessionStatus.className = `status-badge ${state.session?.status || "idle"}`;
  const enabled = Boolean(state.session)
    && !state.running
    && !state.deletingSession
    && ServiceHealth.canExecute(state.serviceState);
  elements.newSessionButton.disabled = !state.workspace
    || state.creatingSession
    || state.serviceState === "disconnected";
  elements.attachmentButton.disabled = !enabled
    || state.loadingAttachments
    || state.uploadingAttachments
    || state.removingAttachments.size > 0;
  elements.messageInput.disabled = !enabled;
  elements.sendButton.disabled = !enabled
    || state.loadingAttachments
    || state.uploadingAttachments
    || state.removingAttachments.size > 0;
  elements.deleteSessionButton.disabled = !canDeleteSession();
  elements.composerState.textContent = !state.session
    ? "未选择会话"
    : state.deletingSession
      ? "正在删除会话"
      : !ServiceHealth.canExecute(state.serviceState)
        ? state.serviceState === "degraded"
          ? "执行服务暂不可用，请稍后重试"
          : "服务连接不可用"
      : state.running
        ? "正在执行"
        : state.loadingAttachments
          ? "正在加载附件"
          : state.uploadingAttachments
            ? state.uploadLabel
            : state.removingAttachments.size > 0
              ? "正在移除附件"
              : "就绪";
  syncAttachmentRemovalState();
}

async function copySessionId() {
  if (!state.session) return;
  try {
    await navigator.clipboard.writeText(state.session.id);
    showToast("Session ID 已复制");
  } catch (_) {
    showToast(`Session ID：${state.session.id}`);
  }
}

async function openSessionContext() {
  if (!state.session) return;
  elements.sessionContextContent.textContent = "正在加载…";
  elements.sessionContextDialog.showModal();
  try {
    const context = await api(
      `/api/sessions/${encodeURIComponent(state.session.id)}/context`,
    );
    if (state.session?.id !== context.session_id) return;
    elements.sessionContextContent.textContent = JSON.stringify(
      SessionInspector.redactContext(context),
      null,
      2,
    );
  } catch (error) {
    elements.sessionContextContent.textContent = error.message;
  }
}

async function openSessionPerformance() {
  if (!state.session) return;
  const selectedId = state.session.id;
  elements.sessionPerformanceContent.textContent = "正在加载…";
  elements.sessionPerformanceDialog.showModal();
  try {
    const history = await api(
      `/api/sessions/${encodeURIComponent(selectedId)}/messages`,
    );
    if (state.session?.id !== selectedId) return;
    renderSessionPerformance(SessionInspector.buildPerformanceTraces(history));
  } catch (error) {
    elements.sessionPerformanceContent.textContent = error.message;
  }
}

function renderSessionPerformance(traces) {
  elements.sessionPerformanceContent.replaceChildren();
  if (!traces.length) {
    elements.sessionPerformanceContent.textContent = "当前 Session 暂无可分析的执行事件。";
    return;
  }
  traces.slice().reverse().forEach((trace, index) => {
    const details = document.createElement("details");
    details.className = "performance-trace";
    details.open = index === 0;
    const summary = document.createElement("summary");
    const heading = document.createElement("span");
    heading.className = "performance-trace-heading";
    const title = document.createElement("strong");
    title.textContent = trace.title;
    const meta = document.createElement("span");
    meta.textContent = `${formatFullClockTime(trace.startedAt)} · ${trace.runIds.length} Run`;
    heading.append(title, meta);
    const duration = document.createElement("strong");
    duration.className = "performance-trace-duration";
    duration.textContent = `总计 ${formatPreciseDuration(trace.totalMs)}`;
    summary.append(heading, duration);

    const insight = document.createElement("div");
    insight.className = "performance-trace-insight";
    insight.textContent = trace.slowestStep
      ? `最慢间隔：${trace.slowestStep.label} · ${formatPreciseDuration(trace.slowestStep.deltaMs)}`
      : "暂无可计算的步骤间隔";

    const header = document.createElement("div");
    header.className = "performance-step performance-step-header";
    ["时间", "步骤", "距上一步", "累计"].forEach((label) => {
      const cell = document.createElement("span");
      cell.textContent = label;
      header.append(cell);
    });
    const rows = document.createElement("div");
    rows.className = "performance-steps";
    rows.append(header);
    trace.steps.forEach((step) => {
      const row = document.createElement("div");
      row.className = "performance-step";
      if (step === trace.slowestStep) row.classList.add("slowest");
      const time = document.createElement("time");
      time.textContent = formatClockTime(step.at);
      const label = document.createElement("span");
      label.className = "performance-step-label";
      label.textContent = step.label;
      const delta = document.createElement("span");
      delta.className = "performance-step-duration";
      delta.textContent = `+ ${formatPreciseDuration(step.deltaMs)}${
        step.operationMs == null ? "" : ` · 往返 ${formatPreciseDuration(step.operationMs)}`
      }`;
      if (step.operationMs != null) {
        delta.title = `从工具下发到结果回传的可观测往返耗时 ${formatPreciseDuration(step.operationMs)}`;
      }
      const elapsed = document.createElement("span");
      elapsed.textContent = formatPreciseDuration(step.elapsedMs);
      row.append(time, label, delta, elapsed);
      rows.append(row);
    });
    details.append(summary, insight, rows);
    elements.sessionPerformanceContent.append(details);
  });
}

function clearConversation() {
  elements.messageTimeline.replaceChildren();
  showConversationEmpty("选择或新建会话");
  elements.usageSummary.textContent = "";
  resetExecutionPanel();
}

function clearCurrentSessionSelection() {
  closeEventSource();
  state.activeTurnId = null;
  state.sessionSelectionGeneration += 1;
  void state.skillCandidates?.selectWorkspace(state.workspace?.id || null);
  state.composerComposing = false;
  state.session = null;
  state.pendingAttachments = [];
  state.loadingAttachments = false;
  state.activeUploadOperationGeneration = null;
  state.uploadingAttachments = false;
  state.uploadLabel = "正在上传附件";
  state.removingAttachments.clear();
  state.seenEvents.clear();
  state.streamMessages.clear();
  state.toolElements.clear();
  state.timelineTools = SessionInspector.createToolTimelineState();
  state.timelineToolElements.clear();
  renderAttachmentTray();
  clearConversation();
  setRunning(false);
}

function showConversationEmpty(text) {
  elements.messageTimeline.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "conversation-empty";
  empty.id = "conversationEmpty";
  const mark = document.createElement("div");
  mark.className = "empty-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = "W";
  const label = document.createElement("strong");
  label.textContent = text;
  empty.append(mark, label);
  elements.messageTimeline.append(empty);
}

function removeConversationEmpty() {
  elements.messageTimeline.querySelector(".conversation-empty")?.remove();
}

async function uploadFiles(fileList, {label = "正在上传附件"} = {}) {
  const files = Array.from(fileList || []);
  elements.attachmentInput.value = "";
  if (
    !state.session
    || !files.length
    || state.running
    || state.loadingAttachments
    || state.uploadingAttachments
    || state.removingAttachments.size > 0
    || state.deletingSession
  ) return;
  if (state.pendingAttachments.length + files.length > maxFilesPerTurn) {
    showToast(`每个 Turn 最多添加 ${maxFilesPerTurn} 个附件。`);
    return;
  }

  const uploadingSessionId = state.session.id;
  const uploadingSelectionGeneration = state.sessionSelectionGeneration;
  const uploadOperationGeneration = ++state.uploadOperationGeneration;
  state.activeUploadOperationGeneration = uploadOperationGeneration;
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  state.uploadingAttachments = true;
  state.uploadLabel = label;
  updateSessionHeader();
  try {
    const uploaded = await api(
      `/api/sessions/${encodeURIComponent(uploadingSessionId)}/attachments`,
      {method: "POST", body: form},
    );
    if (state.session?.id !== uploadingSessionId) return;
    state.pendingAttachments = withoutDeletedAttachments(
      uploadingSessionId,
      mergeAttachments(state.pendingAttachments, uploaded),
    );
    renderAttachmentTray();
  } catch (error) {
    if (
      state.session?.id === uploadingSessionId
      && state.sessionSelectionGeneration === uploadingSelectionGeneration
    ) showError(error);
  } finally {
    if (state.activeUploadOperationGeneration === uploadOperationGeneration) {
      state.activeUploadOperationGeneration = null;
      state.uploadingAttachments = false;
      state.uploadLabel = "正在上传附件";
      updateSessionHeader();
    }
  }
}

function mergeAttachments(...lists) {
  const byId = new Map();
  lists.flat().forEach((attachment) => byId.set(attachment.id, attachment));
  return Array.from(byId.values());
}

function recordDeletedAttachment(sessionId, attachmentId) {
  let deletedIds = state.deletedAttachmentIdsBySession.get(sessionId);
  if (!deletedIds) {
    deletedIds = new Set();
    state.deletedAttachmentIdsBySession.set(sessionId, deletedIds);
  }
  deletedIds.add(attachmentId);
}

function withoutDeletedAttachments(sessionId, attachments) {
  const deletedIds = state.deletedAttachmentIdsBySession.get(sessionId);
  if (!deletedIds?.size) return attachments;
  return attachments.filter((attachment) => !deletedIds.has(attachment.id));
}

function canRemoveAttachments() {
  return Boolean(state.session)
    && !state.running
    && !state.loadingAttachments
    && !state.uploadingAttachments
    && state.removingAttachments.size === 0
    && !state.deletingSession
    && ServiceHealth.canExecute(state.serviceState);
}

function canDeleteSession() {
  return Boolean(state.session)
    && !state.running
    && !state.loadingAttachments
    && !state.uploadingAttachments
    && state.removingAttachments.size === 0
    && !state.deletingSession
    && state.serviceState !== "disconnected";
}

function syncAttachmentRemovalState() {
  const disabled = !canRemoveAttachments();
  elements.attachmentTray.querySelectorAll(".attachment-chip button").forEach(
    (button) => { button.disabled = disabled; },
  );
}

function attachmentFilename(attachment) {
  return attachment.original_filename || attachment.filename || "attachment";
}

function attachmentContentUrl(attachment) {
  return attachment.content_url || `/api/attachments/${attachment.id}/content`;
}

function formatFileSize(sizeBytes) {
  const bytes = Math.max(0, Number(sizeBytes) || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function createAttachmentElement(attachment, {history = false, onRemove = null} = {}) {
  const filename = attachmentFilename(attachment);
  const isImage = String(attachment.mime_type || "").startsWith("image/");
  const element = document.createElement(history ? "a" : "div");
  element.className = `${history ? "history-attachment" : "attachment-chip"}`
    + `${isImage ? " image-attachment" : ""}`;
  if (history) {
    element.href = attachmentContentUrl(attachment);
    element.target = "_blank";
    element.rel = "noopener";
  }

  const icon = document.createElement("b");
  icon.className = "attachment-fallback-icon";
  icon.textContent = isImage ? "IMG" : "FILE";
  icon.hidden = isImage;
  if (isImage) {
    const image = document.createElement("img");
    image.className = "attachment-thumbnail";
    image.src = attachmentContentUrl(attachment);
    image.alt = filename;
    image.addEventListener("error", () => {
      image.remove();
      icon.hidden = false;
    });
    element.append(image);
  }

  const metadata = document.createElement("span");
  metadata.className = "attachment-metadata";
  const name = document.createElement("span");
  name.className = "attachment-name";
  name.textContent = filename;
  const size = document.createElement("small");
  size.className = "attachment-size";
  size.textContent = formatFileSize(attachment.size_bytes);
  metadata.append(name, size);
  element.append(icon, metadata);

  if (onRemove) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = "移除";
    remove.setAttribute("aria-label", `移除 ${filename}`);
    remove.textContent = "×";
    remove.disabled = !canRemoveAttachments();
    remove.addEventListener("click", onRemove);
    element.append(remove);
  }
  return element;
}

function renderAttachmentTray() {
  elements.attachmentTray.replaceChildren();
  state.pendingAttachments.forEach((attachment) => {
    elements.attachmentTray.append(
      createAttachmentElement(attachment, {
        onRemove: () => removeAttachment(attachment.id),
      }),
    );
  });
}

async function removeAttachment(attachmentId) {
  if (!canRemoveAttachments()) return;
  const removingSessionId = state.session.id;
  const removingSelectionGeneration = state.sessionSelectionGeneration;
  const removalOperation = {
    sessionId: removingSessionId,
    selectionGeneration: removingSelectionGeneration,
  };
  state.removingAttachments.set(attachmentId, removalOperation);
  updateSessionHeader();
  try {
    await api(`/api/attachments/${attachmentId}`, { method: "DELETE" });
    recordDeletedAttachment(removingSessionId, attachmentId);
    if (state.session?.id !== removingSessionId) return;
    state.pendingAttachments = withoutDeletedAttachments(
      removingSessionId,
      state.pendingAttachments,
    );
    renderAttachmentTray();
  } catch (error) {
    if (
      state.session?.id === removingSessionId
      && state.sessionSelectionGeneration === removingSelectionGeneration
    ) showError(error);
  } finally {
    if (state.removingAttachments.get(attachmentId) === removalOperation) {
      state.removingAttachments.delete(attachmentId);
      updateSessionHeader();
    }
  }
}

async function sendMessage() {
  if (
    !state.session
    || state.running
    || state.loadingAttachments
    || state.uploadingAttachments
    || state.removingAttachments.size > 0
    || state.deletingSession
    || !ServiceHealth.canExecute(state.serviceState)
  ) return;
  const submittingSessionId = state.session.id;
  const message = elements.messageInput.value.trim();
  if (!message && !state.pendingAttachments.length) return;
  setRunning(true);
  try {
    await state.skillManager.waitForPendingChanges(state.workspace?.id);
    if (state.session?.id !== submittingSessionId) return;
    const accepted = await api(`/api/sessions/${encodeURIComponent(submittingSessionId)}/turns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        attachment_ids: state.pendingAttachments.map((item) => item.id),
        file_references: state.autocomplete.getFileReferences(),
        client_request_id: randomId(),
      }),
    });
    if (state.session?.id !== submittingSessionId) return;
    state.pendingAttachments = [];
    elements.messageInput.value = "";
    state.autocomplete.reset({ keepSkills: true });
    state.composerComposing = false;
    resizeComposer();
    renderAttachmentTray();
    connectEvents(accepted.turn_id);
  } catch (error) {
    if (state.session?.id !== submittingSessionId) return;
    if (error.code === "execution_unavailable") {
      setExecutionState("degraded");
    }
    setRunning(false);
    showError(error);
  }
}

function connectEvents(turnId) {
  closeEventSource();
  state.activeTurnId = turnId;
  state.turnStartedAt ||= Date.now();
  state.lastTransportActivityAt = Date.now();
  state.lastBusinessActivityAt = Date.now();
  elements.executionPanel.hidden = false;
  const abortController = new AbortController();
  const source = {close: () => abortController.abort()};
  state.eventSource = source;
  void fetchEventStream(turnId, abortController.signal, source).catch((error) => {
    if (error.name !== "AbortError") void reconcileEventSourceError(source, turnId);
  });
}

async function fetchEventStream(turnId, signal, source) {
  const headers = state.debugIdentity
    ? state.debugIdentity.headers({Accept: "text/event-stream"})
    : {Accept: "text/event-stream"};
  const response = await fetch(`/api/turns/${turnId}/events`, {headers, signal});
  if (!response.ok || !response.body) throw new Error("事件流连接失败");
  if (state.eventSource === source) {
    if (state.eventSource !== source) return;
    state.disconnectEpisodeActive = false;
    state.lastTransportActivityAt = Date.now();
    setTransportState("ready");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const {done, value} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    const frames = buffer.replaceAll("\r\n", "\n").split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const parsed = parseEventFrame(frame);
      if (!parsed || state.eventSource !== source) continue;
      if (parsed.type === "heartbeat") {
        handleHeartbeat();
        continue;
      }
      state.disconnectEpisodeActive = false;
      state.lastTransportActivityAt = Date.now();
      state.lastBusinessActivityAt = Date.now();
      setTransportState("ready");
      handleEvent(parsed.type, parsed.payload, {
        key: `${turnId}:${parsed.id}`,
        turnId,
        history: false,
        occurredAt: new Date().toISOString(),
      });
    }
    if (done) return;
  }
}

function parseEventFrame(frame) {
  let id = "";
  let type = "message";
  const data = [];
  frame.split("\n").forEach((line) => {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  });
  if (!eventTypes.includes(type)) return null;
  try {
    return {id, type, payload: JSON.parse(data.join("\n") || "{}")};
  } catch (_) {
    return null;
  }
}

async function reconcileEventSourceError(source, turnId) {
  if (state.eventSource !== source || state.disconnectEpisodeActive) return;
  state.disconnectEpisodeActive = true;
  state.connectionCheckPending = true;
  setTransportState("reconnecting");
  try {
    const turn = await api(`/api/turns/${turnId}`);
    if (terminalEvents.includes(`turn.${turn.status}`)) {
      const sessionStatus = turn.status === "failed"
        ? "error"
        : turn.status === "interrupted"
          ? "interrupted"
          : "idle";
      finishTurn(sessionStatus);
    }
  } catch (_) {
    setTransportState("disconnected");
  } finally {
    state.connectionCheckPending = false;
  }
}

function handleHeartbeat() {
  state.disconnectEpisodeActive = false;
  state.lastTransportActivityAt = Date.now();
  setTransportState("ready");
  updateExecutionPanel();
}

function closeEventSource() {
  state.eventSource?.close();
  state.eventSource = null;
}

function handleEvent(type, payload, context) {
  if (context.key && state.seenEvents.has(context.key)) return;
  if (context.key) state.seenEvents.add(context.key);
  if (type === "message.assistant.delta" && context.history) return;
  removeConversationEmpty();

  switch (type) {
    case "turn.started":
      state.turnStartedAt = Date.parse(payload.started_at) || Date.now();
      elements.executionPanel.hidden = false;
      updateExecutionPanel();
      break;
    case "turn.progress":
      renderProgress(payload);
      break;
    case "message.user":
      applyToolResults(payload, context);
      if (SessionInspector.isVisibleUserMessage(payload)) appendUserMessage(payload);
      break;
    case "message.assistant.delta":
      appendAssistantDelta(context.turnId, payload.text || "");
      break;
    case "message.assistant.completed":
      completeAssistantMessage(context.turnId, payload.text || "");
      break;
    case "subscription.progress":
      completeAssistantMessage(payload.noticeId || `${context.turnId}:subscription`, payload.text || "");
      break;
    case "tool.started":
      renderToolStarted(payload);
      break;
    case "tool.completed":
      renderToolCompleted(payload);
      break;
    case "frontend_tool.deferred":
      renderFrontendToolDeferred(payload, context);
      break;
    case "context.compacted":
      appendSystemEvent(`上下文已压缩 · ${payload.pre_tokens || 0} tokens`);
      break;
    case "usage.updated":
      elements.usageSummary.textContent = `${payload.input_tokens || 0} in · ${payload.output_tokens || 0} out${payload.cost_usd == null ? "" : ` · $${Number(payload.cost_usd).toFixed(4)}`}`;
      break;
    case "turn.failed":
      state.turnEndedAt = Date.now();
      appendSystemEvent(payload, true);
      finishTurn("error");
      break;
    case "turn.cancelled":
      state.turnEndedAt = Date.parse(payload.cancelled_at) || Date.now();
      appendSystemEvent("执行已停止");
      finishTurn("idle");
      break;
    case "turn.interrupted":
      state.turnEndedAt = Date.parse(payload.interrupted_at) || Date.now();
      state.currentPhase = userFacingUI.formatUserFacingError(payload, {outcomeUnknown: true});
      appendSystemEvent(payload, true, {outcomeUnknown: true});
      finishTurn("interrupted");
      break;
    case "turn.completed":
      state.turnEndedAt = Date.parse(payload.completed_at) || Date.now();
      finishTurn("idle");
      break;
    default:
      break;
  }
  scrollTimeline();
}

function appendUserMessage(payload) {
  const wrapper = messageElement("user", "你", payload.text || "");
  if (payload.attachments?.length) {
    const attachments = document.createElement("div");
    attachments.className = "message-attachments";
    payload.attachments.forEach((attachment) => {
      attachments.append(createAttachmentElement(attachment, {history: true}));
    });
    wrapper.append(attachments);
  }
  const injected = persistedInputContext(payload);
  if (Object.keys(injected).length) {
    wrapper.append(contextDetails("Agent Host 注入上下文", injected));
  }
  elements.messageTimeline.append(wrapper);
}

function persistedInputContext(payload) {
  const context = {};
  for (const key of ["file_references", "frontend_tools", "page_state"]) {
    const value = payload[key];
    if (Array.isArray(value) ? value.length : value && Object.keys(value).length) {
      context[key] = value;
    }
  }
  return SessionInspector.redactContext(context);
}

function contextDetails(label, value) {
  const details = document.createElement("details");
  details.className = "context-details";
  const summary = document.createElement("summary");
  summary.textContent = label;
  const content = document.createElement("pre");
  content.textContent = JSON.stringify(SessionInspector.redactContext(value), null, 2);
  details.append(summary, content);
  return details;
}

function renderFrontendToolDeferred(payload, context) {
  const tool = SessionInspector.reduceEvent(
    state.timelineTools,
    "frontend_tool.deferred",
    payload,
  );
  if (!tool) return;
  const details = document.createElement("div");
  details.className = "timeline-tool";
  details.dataset.toolName = tool.name;
  details.dataset.toolUseId = tool.id;
  details.dataset.startedAt = context.occurredAt || new Date().toISOString();
  const summary = document.createElement("div");
  const prefix = document.createElement("span");
  summary.style.cssText = "display:flex;align-items:center;gap:8px;padding:9px 11px;font-size:12px";
  prefix.textContent = "工具";
  const name = document.createElement("strong");
  name.textContent = userFacingUI.businessToolLabel(tool.name);
  name.style.cssText = "flex:1;min-width:0;overflow-wrap:anywhere";
  const status = document.createElement("span");
  status.className = "timeline-tool-status";
  status.textContent = "等待结果";
  summary.append(prefix, name, status);
  const business = document.createElement("p");
  business.className = "tool-business-summary";
  business.style.cssText = "margin:8px 11px;white-space:pre-line;overflow-wrap:anywhere;line-height:1.6";
  business.textContent = userFacingUI.formatToolReceipt({name: tool.name, state: "deferred"});
  const technical = contextDetails("技术详情", {tool: tool.name, arguments: tool.input});
  technical.classList.add("tool-technical-details");
  const input = technical.querySelector("pre");
  input.className = "timeline-tool-input";
  const output = document.createElement("pre");
  output.className = "timeline-tool-output";
  output.hidden = true;
  technical.append(output);
  details.append(summary, business, technical);
  state.timelineToolElements.set(tool.id, details);
  elements.messageTimeline.append(details);
}

function applyToolResults(payload, context) {
  for (const result of payload.tool_results || []) {
    const update = SessionInspector.reduceEvent(state.timelineTools, "message.user", {
      ...payload,
      tool_results: [result],
    });
    const details = state.timelineToolElements.get(update?.id);
    if (!details) continue;
    details.classList.add(update.isError ? "failed" : "completed");
    const startedAt = Date.parse(details.dataset.startedAt);
    const completedAt = Date.parse(context.occurredAt || new Date().toISOString());
    const duration = Number.isNaN(startedAt) || Number.isNaN(completedAt)
      ? ""
      : ` · ${formatDuration(completedAt - startedAt)}`;
    details.querySelector(".timeline-tool-status").textContent = `${
      update.isError ? "失败" : "完成"
    }${duration}`;
    const output = details.querySelector(".timeline-tool-output");
    output.hidden = false;
    output.textContent = formatPersistedToolOutput(update.output);
    details.querySelector(".tool-business-summary").textContent = userFacingUI.formatToolReceipt({
      name: details.dataset.toolName, state: update.isError ? "error" : "done", outputPreview: update.output,
    });
    const injected = persistedInputContext(payload);
    if (Object.keys(injected).length) {
      details.querySelector(".tool-technical-details").append(contextDetails("续跑时 Agent Host 注入上下文", injected));
    }
  }
}

function formatPersistedToolOutput(value) {
  if (typeof value !== "string") {
    return JSON.stringify(SessionInspector.redactContext(value), null, 2);
  }
  try {
    return JSON.stringify(
      SessionInspector.redactContext(JSON.parse(value)),
      null,
      2,
    );
  } catch {
    return value;
  }
}

function appendAssistantDelta(turnId, text) {
  let wrapper = state.streamMessages.get(turnId);
  if (!wrapper) {
    wrapper = messageElement("assistant", "Claude", "");
    wrapper.classList.add("streaming");
    state.streamMessages.set(turnId, wrapper);
    elements.messageTimeline.append(wrapper);
  }
  wrapper.querySelector(".message-body").textContent += text;
}

function completeAssistantMessage(turnId, text) {
  let wrapper = state.streamMessages.get(turnId);
  if (!wrapper) {
    wrapper = messageElement("assistant", "Claude", text);
    elements.messageTimeline.append(wrapper);
  } else {
    wrapper.querySelector(".message-body").textContent = text;
    wrapper.classList.remove("streaming");
  }
  state.streamMessages.delete(turnId);
}

function messageElement(role, labelText, text) {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${role} message-${role}`;
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = labelText;
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  wrapper.append(label, body);
  return wrapper;
}

function renderProgress(payload) {
  elements.executionPanel.hidden = false;
  state.currentPhase = payload.message || "正在执行";
  state.lastBusinessActivityAt = Date.now();
  appendExecutionStep(payload.phase || "progress", state.currentPhase, payload.occurred_at);
  updateExecutionPanel();
}

function appendExecutionStep(phase, message, occurredAt) {
  const row = document.createElement("div");
  row.className = "execution-step";
  row.dataset.phase = phase;
  const dot = document.createElement("span");
  dot.className = "execution-step-dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = message;
  const time = document.createElement("time");
  time.textContent = occurredAt ? formatClockTime(occurredAt) : "";
  row.append(dot, label, time);
  elements.executionSteps.append(row);
}

function renderToolStarted(payload) {
  elements.executionPanel.hidden = false;
  state.currentPhase = `正在${userFacingUI.businessToolLabel(payload.name)}`;
  updateExecutionPanel();
  const details = document.createElement("div");
  details.className = "tool-event";
  details.dataset.toolName = payload.name || "Tool";
  details.dataset.toolUseId = payload.tool_use_id;
  const summary = document.createElement("div");
  const stateLabel = document.createElement("span");
  summary.style.cssText = "display:flex;align-items:center;gap:8px;padding:9px 11px;font-size:12px";
  stateLabel.className = "tool-state";
  stateLabel.textContent = "运行中";
  const name = document.createElement("strong");
  name.textContent = userFacingUI.businessToolLabel(payload.name);
  name.style.cssText = "flex:1;min-width:0;overflow-wrap:anywhere";
  const duration = document.createElement("span");
  duration.className = "tool-duration";
  summary.append(stateLabel, name, duration);
  const business = document.createElement("p");
  business.className = "tool-business-summary";
  business.style.cssText = "margin:8px 11px;white-space:pre-line;overflow-wrap:anywhere;line-height:1.6";
  business.textContent = userFacingUI.formatToolReceipt({name: payload.name, state: "running"});
  const technical = contextDetails("技术详情", {tool: payload.name || "Tool"});
  const input = document.createElement("pre");
  input.textContent = formatPersistedToolOutput(payload.input_preview || "");
  const content = document.createElement("pre");
  content.className = "tool-content";
  technical.append(input, content);
  details.append(summary, business, technical);
  state.toolElements.set(payload.tool_use_id, details);
  elements.executionSteps.append(details);
}

function renderToolCompleted(payload) {
  let details = state.toolElements.get(payload.tool_use_id);
  if (!details) {
    renderToolStarted(payload);
    details = state.toolElements.get(payload.tool_use_id);
  }
  details.classList.add(payload.is_error ? "failed" : "completed");
  details.querySelector(".tool-state").textContent = payload.is_error ? "失败" : "完成";
  details.querySelector(".tool-duration").textContent = payload.duration_ms == null
    ? ""
    : formatDuration(payload.duration_ms);
  details.querySelector(".tool-content").textContent = formatPersistedToolOutput(payload.output_preview || "");
  details.querySelector(".tool-business-summary").textContent = userFacingUI.formatToolReceipt({
    name: details.dataset.toolName, state: payload.is_error ? "error" : "done", outputPreview: payload.output_preview,
  });
  state.currentPhase = payload.is_error
    ? "工具执行失败，模型正在处理结果"
    : "工具执行完成，模型正在处理结果";
  updateExecutionPanel();
  if (!payload.is_error && details.dataset.toolName === "mcp__data_mcp__ask") {
    void renderDataAskResult(payload.output_preview || "");
  }
}

async function renderDataAskResult(outputPreview) {
  const resultId = outputPreview.match(/"resultId"\s*:\s*"([^"]+)"/)?.[1];
  const agentId = outputPreview.match(/"agentId"\s*:\s*"([^"]+)"/)?.[1];
  if (!resultId || !agentId) return;
  const existing = elements.messageTimeline.querySelector(`[data-data-result-id="${CSS.escape(resultId)}"]`);
  if (existing) return;
  try {
    const result = await api(`/api/data-agents/${encodeURIComponent(agentId)}/results/${encodeURIComponent(resultId)}`);
    const card = document.createElement("article");
    card.className = "data-result-card";
    card.dataset.dataResultId = resultId;
    const heading = document.createElement("div");
    heading.className = "data-result-heading";
    const title = document.createElement("strong");
    title.textContent = `问数结果 · ${result.rowCount} 行${result.truncated ? "（展示已截断）" : ""}`;
    const evidence = document.createElement("span");
    evidence.textContent = `SQLBot record #${result.recordId}`;
    heading.append(title, evidence);
    card.append(heading);
    if (result.rows.length) card.append(buildDataResultChart(result), buildDataResultTable(result));
    else {
      const empty = document.createElement("p"); empty.textContent = "查询成功，结果为空。"; card.append(empty);
    }
    const sql = contextDetails("SQL 与证据", {sql: result.sql, fieldsUsed: result.fieldsUsed, evidence: result.evidence});
    card.append(sql);
    elements.messageTimeline.append(card);
    scrollTimeline();
  } catch (error) { appendSystemEvent(error.message, true); }
}

function buildDataResultTable(result) {
  const wrap = document.createElement("div"); wrap.className = "data-result-table-wrap";
  const table = document.createElement("table");
  const head = document.createElement("thead"); const headRow = document.createElement("tr");
  result.columns.forEach((column) => { const th = document.createElement("th"); th.textContent = column; headRow.append(th); });
  head.append(headRow); table.append(head);
  const body = document.createElement("tbody");
  result.rows.slice(0, 50).forEach((row) => {
    const tr = document.createElement("tr");
    result.columns.forEach((column) => { const td = document.createElement("td"); td.textContent = row[column] == null ? "—" : String(row[column]); tr.append(td); });
    body.append(tr);
  });
  table.append(body); wrap.append(table); return wrap;
}

function buildDataResultChart(result) {
  const hint = result.chartHint || {};
  const chart = document.createElement("div"); chart.className = "data-result-chart";
  const y = Array.isArray(hint.y) ? hint.y[0] : hint.y;
  const points = result.rows.slice(0, 12).map((row) => Number(row[y])).filter(Number.isFinite);
  if (!y || !points.length || hint.type === "table") { chart.hidden = true; return chart; }
  const max = Math.max(...points, 1);
  result.rows.slice(0, points.length).forEach((row) => {
    const item = document.createElement("div"); item.className = "data-result-bar";
    const label = document.createElement("span"); label.textContent = String(row[hint.x] ?? "");
    const bar = document.createElement("i"); bar.style.width = `${Math.max(2, Number(row[y]) / max * 100)}%`;
    const value = document.createElement("b"); value.textContent = String(row[y]);
    item.append(label, bar, value); chart.append(item);
  });
  return chart;
}

function appendSystemEvent(text, isError = false, options = {}) {
  const event = document.createElement("div");
  event.className = `system-event${isError ? " error" : ""}`;
  event.textContent = isError ? userFacingUI.formatUserFacingError(text, options) : text;
  if (isError) event.append(contextDetails("技术详情", typeof text === "string" ? {message: text} : {
    code: text?.code, message: text?.message, details: text?.details,
  }));
  elements.messageTimeline.append(event);
}

async function refreshServiceHealth() {
  try {
    const health = await api("/api/health");
    setExecutionState(
      health.status === "degraded" || health.execution?.status === "degraded"
        ? "degraded"
        : "ready",
    );
    setTransportState("ready");
  } catch (_) {
    setTransportState("disconnected");
  }
}

function setTransportState(transportState) {
  state.transportState = transportState;
  renderServiceState();
}

function setExecutionState(executionState) {
  state.executionState = executionState;
  renderServiceState();
}

function renderServiceState() {
  const serviceState = ServiceHealth.deriveState(
    state.transportState,
    state.executionState,
  );
  state.serviceState = serviceState;
  elements.serviceStatus.className = `service-status ${serviceState}`;
  const labels = {
    ready: "就绪",
    degraded: "执行服务不可用",
    reconnecting: "重连中",
    disconnected: "已断开",
  };
  elements.serviceStatusText.textContent = labels[serviceState] || serviceState;
  const connectionLabels = {
    ready: "已连接",
    degraded: "执行不可用",
    reconnecting: "重连中",
    disconnected: "已断开",
  };
  elements.executionConnection.textContent = connectionLabels[serviceState] || serviceState;
  updateSessionHeader();
  updateExecutionPanel();
}

function updateExecutionPanel() {
  if (elements.executionPanel.hidden) return;
  const now = Date.now();
  const startedAt = state.turnStartedAt || now;
  elements.executionElapsed.textContent = formatDuration((state.turnEndedAt || now) - startedAt);
  if (state.lastTransportActivityAt) {
    elements.executionHeartbeat.textContent = `最后心跳 ${formatRelative(now - state.lastTransportActivityAt)}`;
  } else {
    elements.executionHeartbeat.textContent = "等待心跳";
  }
  const isQuiet = state.running
    && state.serviceState === "ready"
    && state.lastBusinessActivityAt
    && now - state.lastBusinessActivityAt >= 30_000;
  elements.executionPhase.textContent = isQuiet
    ? "仍在运行，等待模型响应"
    : state.currentPhase;
}

function resetExecutionPanel() {
  elements.executionPanel.hidden = true;
  elements.executionSteps.replaceChildren();
  elements.executionPhase.textContent = "准备执行";
  elements.executionElapsed.textContent = "0 秒";
  elements.executionHeartbeat.textContent = "等待心跳";
  elements.executionConnection.textContent = {
    disconnected: "已断开",
    reconnecting: "重连中",
    degraded: "执行不可用",
    ready: "已连接",
  }[state.serviceState] || state.serviceState;
  state.currentPhase = "准备执行";
  state.turnStartedAt = null;
  state.turnEndedAt = null;
  state.lastTransportActivityAt = null;
  state.lastBusinessActivityAt = null;
  state.disconnectEpisodeActive = false;
  state.connectionCheckPending = false;
}

function formatDuration(milliseconds) {
  const value = Math.max(0, Number(milliseconds) || 0);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)} 秒`;
  const minutes = Math.floor(value / 60_000);
  const seconds = Math.floor((value % 60_000) / 1000);
  return `${minutes} 分 ${seconds} 秒`;
}

function formatPreciseDuration(milliseconds) {
  const value = Math.max(0, Number(milliseconds) || 0);
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(1)} 秒`;
  const minutes = Math.floor(value / 60_000);
  const seconds = ((value % 60_000) / 1000).toFixed(1);
  return `${minutes} 分 ${seconds} 秒`;
}

function formatRelative(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds < 2 ? "刚刚" : `${seconds} 秒前`;
}

function formatClockTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatFullClockTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function finishTurn(status) {
  closeEventSource();
  state.activeTurnId = null;
  if (state.session) {
    state.session.status = status;
  }
  setRunning(false);
  refreshSessions();
}

function setRunning(running) {
  if (running && !state.running) {
    resetExecutionPanel();
    elements.executionPanel.hidden = false;
    state.currentPhase = "正在提交请求";
    state.turnStartedAt = Date.now();
    state.turnEndedAt = null;
    state.lastTransportActivityAt = Date.now();
    state.lastBusinessActivityAt = Date.now();
  }
  state.running = running;
  elements.stopButton.hidden = !running;
  elements.sendButton.hidden = running;
  updateSessionHeader();
}

async function stopTurn() {
  if (!state.activeTurnId) return;
  try {
    await api(`/api/turns/${state.activeTurnId}/cancel`, { method: "POST" });
    elements.composerState.textContent = "正在停止";
  } catch (error) {
    showError(error);
  }
}

function openRenameDialog() {
  if (!state.session) return;
  elements.renameInput.value = state.session.title;
  elements.renameDialog.showModal();
  elements.renameInput.focus();
  elements.renameInput.select();
}

function openDeleteDialog() {
  if (!canDeleteSession()) return;
  elements.deleteDialog.showModal();
}

async function renameSession(event) {
  event.preventDefault();
  if (!state.session) return;
  try {
    const renamed = await api(`/api/sessions/${state.session.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: elements.renameInput.value }),
    });
    state.session = renamed;
    elements.renameDialog.close();
    await loadSessions({ preserveSelection: true });
  } catch (error) {
    showError(error);
  }
}

async function deleteSession(event) {
  event.preventDefault();
  if (!canDeleteSession()) return;
  const deletingSessionId = state.session.id;
  const deletingSelectionGeneration = state.sessionSelectionGeneration;
  const deletionOperation = {
    sessionId: deletingSessionId,
    selectionGeneration: deletingSelectionGeneration,
  };
  state.deletingSession = deletionOperation;
  elements.deleteDialog.close();
  updateSessionHeader();
  try {
    await api(`/api/sessions/${deletingSessionId}`, { method: "DELETE" });
    state.deletedAttachmentIdsBySession.delete(deletingSessionId);
    const deletingCurrentSession = state.session?.id === deletingSessionId
      && state.sessionSelectionGeneration === deletingSelectionGeneration;
    if (deletingCurrentSession) {
      clearCurrentSessionSelection();
    }
    await loadSessions({ preserveSelection: !deletingCurrentSession });
  } catch (error) {
    if (
      state.deletingSession === deletionOperation
      && state.session?.id === deletingSessionId
      && state.sessionSelectionGeneration === deletingSelectionGeneration
    ) showError(error);
  } finally {
    if (state.deletingSession === deletionOperation) {
      state.deletingSession = null;
      updateSessionHeader();
    }
  }
}

function handleComposerKeydown(event) {
  if (event.defaultPrevented) return;
  if (state.autocomplete?.handleKeydown(event)) return;
  if (state.composerComposing || event.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

function resizeComposer() {
  elements.messageInput.style.height = "auto";
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 160)}px`;
}

function scrollTimeline() {
  requestAnimationFrame(() => {
    elements.messageTimeline.scrollTop = elements.messageTimeline.scrollHeight;
  });
}

function formatTime(value) {
  const date = new Date(SessionInspector.parseTimestamp(value));
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function randomId() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

let toastTimer;
function showError(error) {
  showToast(userFacingUI?.formatUserFacingError(error) || "助手暂时无法打开，请联系技术支持。");
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  toastTimer = window.setTimeout(() => { elements.toast.hidden = true; }, 5000);
}

function openSidebar() {
  elements.sessionSidebar.classList.add("open");
  elements.mobileScrim.hidden = false;
}

function closeSidebar() {
  elements.sessionSidebar.classList.remove("open");
  elements.mobileScrim.hidden = true;
}

if (typeof module === "object" && module.exports) {
  module.exports = {initialize, state};
} else {
  initialize().catch(showError);
}
