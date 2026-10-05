"use strict";

(function expose(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.SkillManager = api;
})(typeof window === "undefined" ? null : window, function buildModule() {
  function listItems(response) {
    if (Array.isArray(response)) return response;
    if (Array.isArray(response?.items)) return response.items;
    return [
      ...(Array.isArray(response?.global) ? response.global : []),
      ...(Array.isArray(response?.personal) ? response.personal : []),
    ];
  }

  function originLabel(origin) {
    if (origin?.type === "browser_directory") {
      return origin.source_name
        ? `browser directory · ${origin.source_name}`
        : "browser directory";
    }
    if (origin?.type === "archive_upload") return "archive upload";
    return String(origin?.type || "unknown").replaceAll("_", " ");
  }

  function appendFields(form, fields) {
    for (const [name, value] of Object.entries(fields)) {
      if (value !== null && value !== undefined && value !== "") {
        form.append(name, value);
      }
    }
  }

  function directoryForm(files, fields) {
    const form = new FormData();
    appendFields(form, fields);
    for (const file of files) {
      form.append("files", file, file.webkitRelativePath || file.name);
    }
    return form;
  }

  function archiveForm(file, fields) {
    const form = new FormData();
    appendFields(form, fields);
    if (file.name) form.append("archive", file, file.name);
    else form.append("archive", file);
    return form;
  }

  function createSessionCreationCoordinator({api, getSelection, commit, waitForSkills}) {
    async function create() {
      const started = getSelection();
      if (!started.workspaceId) return false;
      if (waitForSkills) {
        await waitForSkills(started.workspaceId);
        const ready = getSelection();
        if (ready.workspaceId !== started.workspaceId || ready.epoch !== started.epoch
          || ready.sessionGeneration !== started.sessionGeneration) return false;
      }
      const session = await api(
        `/api/workspaces/${encodeURIComponent(started.workspaceId)}/sessions`,
        {method: "POST"},
      );
      const current = getSelection();
      if (
        current.workspaceId !== started.workspaceId
        || current.epoch !== started.epoch
        || current.sessionGeneration !== started.sessionGeneration
      ) return false;
      await commit(session);
      return true;
    }

    return {create};
  }

  function createWorkspaceRefreshCoordinator({loadWorkspaces, getSelection, apply}) {
    async function refresh(workspaceId, lifecycleIsCurrent = () => true) {
      const started = getSelection();
      if (started.workspaceId !== workspaceId || !lifecycleIsCurrent()) return false;
      const workspaces = await loadWorkspaces();
      const current = getSelection();
      if (
        current.workspaceId !== workspaceId
        || current.epoch !== started.epoch
        || !lifecycleIsCurrent()
      ) return false;
      apply(workspaces, workspaceId);
      return true;
    }

    return {refresh};
  }

  function createCandidateController({api, autocomplete, onError}) {
    let generation = 0;

    async function load(key, path, filter) {
      const requestGeneration = ++generation;
      autocomplete.setSession({sessionId: key, skills: []});
      try {
        const response = await api(path);
        if (generation !== requestGeneration) return false;
        autocomplete.setSkills({
          sessionId: key,
          skills: listItems(response).filter(filter),
        });
        return true;
      } catch (error) {
        if (generation === requestGeneration) onError(error);
        return false;
      }
    }

    function selectWorkspace(workspaceId) {
      if (!workspaceId) {
        generation += 1;
        autocomplete.reset?.();
        return Promise.resolve(false);
      }
      const key = `workspace:${workspaceId}`;
      return load(
        key,
        `/api/workspaces/${encodeURIComponent(workspaceId)}/skills`,
        (item) => item.enabled === true,
      );
    }

    function selectSession(sessionId) {
      if (!sessionId) return selectWorkspace(null);
      return load(
        sessionId,
        `/api/sessions/${encodeURIComponent(sessionId)}/skills`,
        () => true,
      );
    }

    return {selectWorkspace, selectSession, reset: () => selectWorkspace(null)};
  }

  function createViewNavigationController({state, elements}) {
    let skillManager = null;
    let instructionsEditor = null;

    function isAvailable() {
      return state.workspace?.kind === "personal";
    }

    function showSkillManagement() {
      if (!isAvailable()) return false;
      state.activeView = "skills";
      elements.appShell.hidden = true;
      elements.skillManagementView.hidden = false;
      return true;
    }

    function showChat() {
      state.activeView = "chat";
      elements.skillManagementView.hidden = true;
      elements.appShell.hidden = false;
      return true;
    }

    function open() {
      if (!isAvailable() || !skillManager) return Promise.resolve(false);
      // 指令编辑器与 Skill 列表同屏，一起拉取。
      if (instructionsEditor) void instructionsEditor.refresh();
      return skillManager.open();
    }

    function syncEntry() {
      const available = isAvailable();
      elements.skillsButton.hidden = !available;
      elements.skillsButton.disabled = !available;
      if (!available && state.activeView === "skills") skillManager?.close();
      return available;
    }

    function setSkillManager(manager) {
      skillManager = manager;
    }

    function setInstructionsEditor(editor) {
      instructionsEditor = editor;
    }

    elements.skillsButton.addEventListener("click", () => { void open(); });
    showChat();

    return {
      showSkillManagement,
      showChat,
      syncEntry,
      setSkillManager,
      setInstructionsEditor,
      open,
    };
  }

  function createController({
    api,
    elements,
    getWorkspace,
    onChanged = () => {},
    onEnter = () => {},
    onLeave = () => {},
    onError = () => {},
  }) {
    let isOpen = false;
    let workspaceId = null;
    let lifecycleGeneration = 0;
    let requestGeneration = 0;
    let catalog = emptyCatalog();
    let pendingOwner = null;
    // Leaving the view does not cancel an already-dispatched settings write.
    const pendingWrites = new Set();
    let pendingConflict = null;
    let internalConflictClose = false;
    let rowButtons = [];

    function emptyCatalog() {
      return {
        global: [],
        personal: [],
        effective_count: 0,
        changes_apply_to: "new_sessions",
      };
    }

    function workspace() {
      return getWorkspace?.() || null;
    }

    function isPersonalManager() {
      const current = workspace();
      return current?.kind === "personal" && Boolean(current.can_manage_skills);
    }

    function isGlobalManager() {
      return Boolean(workspace()?.can_manage_global_skills);
    }

    function isCurrent(generation, expectedWorkspaceId) {
      return isOpen
        && lifecycleGeneration === generation
        && workspaceId === expectedWorkspaceId
        && workspace()?.id === expectedWorkspaceId;
    }

    function closeConflictDialog() {
      if (!elements.importConflictDialog.open) return;
      internalConflictClose = true;
      elements.importConflictDialog.close();
      internalConflictClose = false;
    }

    function resetDetail() {
      elements.detailPanel.hidden = true;
      elements.detailName.textContent = "选择一个 Skill";
      elements.detailDescription.textContent = "";
      elements.detailVersion.textContent = "";
      elements.detailHash.textContent = "";
      elements.detailSource.textContent = "";
      elements.detailUpdatedAt.textContent = "";
      elements.detailContent.textContent = "";
      elements.detailManifest.replaceChildren();
    }

    function clearLists() {
      catalog = emptyCatalog();
      elements.globalList.replaceChildren();
      elements.personalList.replaceChildren();
      elements.globalEmpty.hidden = true;
      elements.personalEmpty.hidden = true;
      elements.globalLoading.hidden = true;
      elements.personalLoading.hidden = true;
      rowButtons = [];
    }

    function syncPresentation() {
      const current = workspace();
      elements.workspaceName.textContent = current?.name || "Workspace";
      const personal = isPersonalManager();
      const global = isGlobalManager();
      elements.personalImportRoot.hidden = !personal;
      elements.personalImportButton.hidden = !personal;
      elements.personalArchiveButton.hidden = !personal;
      elements.globalImportRoot.hidden = !global;
      elements.globalImportButton.hidden = !global;
      elements.globalArchiveButton.hidden = !global;
      elements.personalDirectoryInput.hidden = true;
      elements.personalArchiveInput.hidden = true;
      elements.globalDirectoryInput.hidden = true;
      elements.globalArchiveInput.hidden = true;
      syncPendingState();
    }

    function syncPendingState() {
      const blocked = pendingOwner !== null || pendingConflict !== null;
      const controls = [
        elements.globalImportButton,
        elements.globalArchiveButton,
        elements.globalDirectoryInput,
        elements.globalArchiveInput,
        elements.personalImportButton,
        elements.personalArchiveButton,
        elements.personalDirectoryInput,
        elements.personalArchiveInput,
        ...rowButtons,
      ];
      for (const control of controls) control.disabled = blocked;
      const conflictBlocked = pendingOwner !== null || pendingConflict === null;
      elements.importRenameInput.disabled = conflictBlocked;
      elements.importOverwriteButton.disabled = conflictBlocked
        || Boolean(pendingConflict?.overwriteDisabled);
      elements.importRenameButton.disabled = conflictBlocked;
      elements.importCancelButton.disabled = conflictBlocked;
    }

    function acquirePending(kind, {resolveConflict = false} = {}) {
      if (pendingOwner !== null || (pendingConflict !== null && !resolveConflict)) {
        return null;
      }
      const token = {kind, workspaceId};
      token.done = new Promise((resolve) => { token.resolve = resolve; });
      pendingWrites.add(token);
      pendingOwner = token;
      syncPendingState();
      return token;
    }

    function releasePending(token) {
      pendingWrites.delete(token);
      token.resolve();
      if (pendingOwner !== token) return false;
      pendingOwner = null;
      syncPendingState();
      return true;
    }

    /** Wait for writes in this workspace, including writes from a closed view. */
    async function waitForPendingChanges(expectedWorkspaceId) {
      while (true) {
        const writes = [...pendingWrites].filter((item) => (
          item.workspaceId === expectedWorkspaceId
        ));
        if (!writes.length) return;
        await Promise.all(writes.map((item) => item.done));
      }
    }

    function inputFor(scope, kind) {
      if (scope === "global") {
        return kind === "directory"
          ? elements.globalDirectoryInput
          : elements.globalArchiveInput;
      }
      return kind === "directory"
        ? elements.personalDirectoryInput
        : elements.personalArchiveInput;
    }

    function triggerFor(scope, kind) {
      if (scope === "global") {
        return kind === "directory"
          ? elements.globalImportButton
          : elements.globalArchiveButton;
      }
      return kind === "directory"
        ? elements.personalImportButton
        : elements.personalArchiveButton;
    }

    function restoreImportFocus(scope, kind) {
      triggerFor(scope, kind).focus?.();
    }

    function clearConflict({clearInput = false, restoreFocus = false} = {}) {
      const conflict = pendingConflict;
      pendingConflict = null;
      elements.importRenameInput.value = "";
      elements.importConflictError.textContent = "";
      elements.importConflictError.hidden = true;
      closeConflictDialog();
      if (conflict && clearInput) inputFor(conflict.scope, conflict.kind).value = "";
      if (conflict && restoreFocus) restoreImportFocus(conflict.scope, conflict.kind);
      syncPendingState();
      return Boolean(conflict);
    }

    function invalidate({leave = true} = {}) {
      const wasOpen = isOpen;
      isOpen = false;
      workspaceId = null;
      lifecycleGeneration += 1;
      requestGeneration += 1;
      pendingOwner = null;
      clearConflict();
      clearLists();
      resetDetail();
      elements.changesNotice.hidden = true;
      syncPresentation();
      if (leave && wasOpen) onLeave();
    }

    function ensureCurrentWorkspace() {
      if (!isOpen) return false;
      if (workspace()?.id !== workspaceId) {
        invalidate();
        return false;
      }
      return true;
    }

    function makeButton(label, className, action) {
      const button = elements.globalList.ownerDocument.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.addEventListener("click", () => { void action(); });
      rowButtons.push(button);
      return button;
    }

    function renderList(scope, items, list, empty) {
      list.replaceChildren();
      empty.hidden = items.length !== 0;
      for (const item of items) {
        const row = list.ownerDocument.createElement("div");
        row.className = "skill-manager-row";
        row.dataset.skillId = item.id;
        row.dataset.skillScope = scope;
        row.setAttribute("role", "listitem");

        const name = makeButton(
          item.name,
          "skill-manager-name",
          () => selectDetail(item),
        );
        const description = list.ownerDocument.createElement("span");
        description.className = "skill-manager-description";
        description.textContent = item.description || "暂无描述";
        description.setAttribute("title", item.description || "暂无描述");
        const version = list.ownerDocument.createElement("span");
        version.className = "skill-manager-version";
        version.textContent = `v${item.version_no}`;
        const state = list.ownerDocument.createElement("span");
        state.className = `skill-manager-state ${item.enabled ? "enabled" : "disabled"}`;
        state.textContent = item.enabled ? "已开启" : "已关闭";
        const actions = list.ownerDocument.createElement("span");
        actions.className = "skill-manager-row-actions";

        if (scope === "global" && isPersonalManager()) {
          actions.append(makeButton(
            item.enabled ? "关闭" : "开启",
            "button secondary skill-manager-toggle",
            () => setGlobalEnabled(item.id, !item.enabled),
          ));
        }
        if (scope === "personal" && isPersonalManager()) {
          actions.append(
            makeButton(
              item.enabled ? "关闭" : "开启",
              "button secondary skill-manager-toggle",
              () => setPersonalEnabled(item, !item.enabled),
            ),
            makeButton(
              "归档",
              "button danger skill-manager-archive",
              () => archivePersonal(item),
            ),
          );
        }
        if (scope === "global" && isGlobalManager()) {
          actions.append(makeButton(
            "归档",
            "button danger skill-manager-archive",
            () => archiveGlobal(item),
          ));
        }
        row.append(name, description, version, state, actions);
        list.append(row);
      }
    }

    function renderCatalog(response) {
      catalog = {
        global: Array.isArray(response?.global) ? response.global : [],
        personal: Array.isArray(response?.personal) ? response.personal : [],
        effective_count: Number(response?.effective_count) || 0,
        changes_apply_to: response?.changes_apply_to || "new_sessions",
      };
      rowButtons = [];
      renderList("global", catalog.global, elements.globalList, elements.globalEmpty);
      renderList(
        "personal",
        catalog.personal,
        elements.personalList,
        elements.personalEmpty,
      );
      syncPendingState();
    }

    function renderDetail(detail) {
      elements.detailPanel.hidden = false;
      elements.detailName.textContent = detail.name;
      elements.detailDescription.textContent = detail.description || "暂无描述";
      elements.detailVersion.textContent = `v${detail.version_no} · ${detail.version_id}`;
      elements.detailHash.textContent = detail.bundle_hash;
      elements.detailSource.textContent = originLabel(detail.origin);
      elements.detailUpdatedAt.textContent = detail.updated_at || "";
      elements.detailContent.textContent = detail.content || "";
      elements.detailManifest.replaceChildren();
      for (const file of detail.files || []) {
        const entry = elements.detailManifest.ownerDocument.createElement("li");
        entry.textContent = `${file.path} · ${file.size_bytes} bytes · ${file.sha256}`;
        elements.detailManifest.append(entry);
      }
    }

    async function open() {
      const current = workspace();
      if (!current?.id || current.kind !== "personal") return false;
      if (isOpen) invalidate({leave: false});
      isOpen = true;
      workspaceId = current.id;
      lifecycleGeneration += 1;
      elements.changesNotice.hidden = true;
      clearLists();
      resetDetail();
      syncPresentation();
      onEnter();
      return refresh();
    }

    function close() {
      invalidate();
    }

    function reset() {
      close();
    }

    async function refresh() {
      if (!ensureCurrentWorkspace()) return false;
      const expectedWorkspaceId = workspaceId;
      const lifecycle = lifecycleGeneration;
      const request = ++requestGeneration;
      elements.globalLoading.hidden = false;
      elements.personalLoading.hidden = false;
      elements.globalEmpty.hidden = true;
      elements.personalEmpty.hidden = true;
      try {
        const response = await api(
          `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/skills`,
        );
        if (
          request !== requestGeneration
          || !isCurrent(lifecycle, expectedWorkspaceId)
        ) return false;
        renderCatalog(response);
        return true;
      } catch (error) {
        if (
          request === requestGeneration
          && isCurrent(lifecycle, expectedWorkspaceId)
        ) onError(error);
        return false;
      } finally {
        if (
          request === requestGeneration
          && isCurrent(lifecycle, expectedWorkspaceId)
        ) {
          elements.globalLoading.hidden = true;
          elements.personalLoading.hidden = true;
        }
      }
    }

    async function selectDetail(skill) {
      if (!ensureCurrentWorkspace() || pendingOwner !== null) return false;
      const expectedWorkspaceId = workspaceId;
      const lifecycle = lifecycleGeneration;
      const request = ++requestGeneration;
      try {
        const detail = await api(
          `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/skills/${encodeURIComponent(skill.id)}`,
        );
        if (
          request !== requestGeneration
          || !isCurrent(lifecycle, expectedWorkspaceId)
        ) return false;
        renderDetail(detail);
        return true;
      } catch (error) {
        if (
          request === requestGeneration
          && isCurrent(lifecycle, expectedWorkspaceId)
        ) onError(error);
        return false;
      }
    }

    async function afterMutation(expectedWorkspaceId, lifecycle) {
      if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
      if (!await refresh()) return false;
      if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
      await onChanged(
        expectedWorkspaceId,
        () => isCurrent(lifecycle, expectedWorkspaceId),
        catalog.effective_count,
      );
      if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
      elements.changesNotice.textContent = "仅对新会话生效";
      elements.changesNotice.hidden = false;
      return true;
    }

    async function mutate(kind, request) {
      if (!ensureCurrentWorkspace()) return false;
      const expectedWorkspaceId = workspaceId;
      const lifecycle = lifecycleGeneration;
      const token = acquirePending(kind);
      if (!token) return false;
      try {
        await request(expectedWorkspaceId);
        if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
        return await afterMutation(expectedWorkspaceId, lifecycle);
      } catch (error) {
        if (isCurrent(lifecycle, expectedWorkspaceId)) onError(error);
        return false;
      } finally {
        releasePending(token);
      }
    }

    function setGlobalEnabled(skillId, enabled) {
      return mutate("global-toggle", (expectedWorkspaceId) => api(
        `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/global-skills/${encodeURIComponent(skillId)}/setting`,
        {
          method: "PUT",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({enabled}),
        },
      ));
    }

    function setPersonalEnabled(skill, enabled) {
      return mutate("personal-toggle", (expectedWorkspaceId) => api(
        `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/skills/${encodeURIComponent(skill.id)}/enabled`,
        {
          method: "PATCH",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({expected_hash: skill.bundle_hash, enabled}),
        },
      ));
    }

    function archivePersonal(skill) {
      return mutate("personal-archive", (expectedWorkspaceId) => api(
        `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/skills/${encodeURIComponent(skill.id)}`,
        {
          method: "DELETE",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({expected_hash: skill.bundle_hash}),
        },
      ));
    }

    function archiveGlobal(skill) {
      return mutate("global-archive", () => api(
        `/api/admin/global-skills/${encodeURIComponent(skill.id)}`,
        {
          method: "DELETE",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({expected_hash: skill.bundle_hash}),
        },
      ));
    }

    function importPath(scope, kind, expectedWorkspaceId) {
      if (scope === "global") {
        return kind === "directory"
          ? "/api/admin/global-skills/import-directory"
          : "/api/admin/global-skills/import";
      }
      const base = `/api/workspaces/${encodeURIComponent(expectedWorkspaceId)}/skills`;
      return kind === "directory" ? `${base}/import-directory` : `${base}/import`;
    }

    function showConflict(scope, kind, payload, details, lifecycle, expectedWorkspaceId) {
      if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
      pendingConflict = {
        scope,
        kind,
        payload,
        details: details || {},
        lifecycle,
        workspaceId: expectedWorkspaceId,
        overwriteDisabled: false,
      };
      elements.importConflictName.textContent = details?.incoming_name || "未知 Skill";
      elements.importConflictExistingHash.textContent = details?.existing_hash || "未知";
      elements.importConflictIncomingHash.textContent = details?.incoming_hash || "未知";
      elements.importRenameInput.value = "";
      elements.importConflictError.textContent = "";
      elements.importConflictError.hidden = true;
      if (!elements.importConflictDialog.open) elements.importConflictDialog.showModal();
      syncPendingState();
      return true;
    }

    async function submitImport(scope, kind, payload, fields, {resolving = false} = {}) {
      if (!resolving && !ensureCurrentWorkspace()) return false;
      const conflict = resolving ? pendingConflict : null;
      const expectedWorkspaceId = conflict?.workspaceId || workspaceId;
      const lifecycle = conflict?.lifecycle ?? lifecycleGeneration;
      const token = acquirePending(`${scope}-${kind}-import`, {
        resolveConflict: resolving,
      });
      if (!token) return false;
      let completed = false;
      try {
        const form = kind === "directory"
          ? directoryForm(payload, fields)
          : archiveForm(payload, fields);
        await api(importPath(scope, kind, expectedWorkspaceId), {
          method: "POST",
          body: form,
        });
        if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
        if (resolving) clearConflict();
        if (!await afterMutation(expectedWorkspaceId, lifecycle)) return false;
        if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
        inputFor(scope, kind).value = "";
        restoreImportFocus(scope, kind);
        completed = true;
        return true;
      } catch (error) {
        if (!isCurrent(lifecycle, expectedWorkspaceId)) return false;
        if (error?.code === "skill_import_conflict") {
          showConflict(
            scope,
            kind,
            payload,
            error.details,
            lifecycle,
            expectedWorkspaceId,
          );
        } else if (resolving && error?.code === "skill_changed") {
          pendingConflict.overwriteDisabled = true;
          elements.importConflictError.textContent = "目标 Skill 已变化，覆盖已禁用。请取消后重新选择文件以获取最新状态，或改用重命名导入。";
          elements.importConflictError.hidden = false;
        } else {
          onError(error);
        }
        return false;
      } finally {
        releasePending(token);
        if (!completed) syncPendingState();
      }
    }

    function importDirectory(
      scope,
      fileList,
      conflict = {on_conflict: "fail"},
    ) {
      const files = Array.from(fileList || []);
      if (files.length === 0 || pendingConflict !== null) {
        return Promise.resolve(false);
      }
      return submitImport(scope, "directory", files, conflict);
    }

    function importArchive(scope, file) {
      if (!file || pendingConflict !== null) return Promise.resolve(false);
      return submitImport(scope, "archive", file, {on_conflict: "fail"});
    }

    function overwriteImport() {
      const conflict = pendingConflict;
      if (!conflict || conflict.overwriteDisabled) return Promise.resolve(false);
      return submitImport(
        conflict.scope,
        conflict.kind,
        conflict.payload,
        {
          on_conflict: "overwrite",
          expected_hash: conflict.details.existing_hash,
        },
        {resolving: true},
      );
    }

    function renameImport(targetName = elements.importRenameInput.value) {
      const conflict = pendingConflict;
      if (!conflict || pendingOwner !== null) return Promise.resolve(false);
      const name = String(targetName || "").trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(name)) {
        elements.importConflictError.textContent = "名称只能包含字母、数字、点、下划线和连字符，且须以字母或数字开头。";
        elements.importConflictError.hidden = false;
        return Promise.resolve(false);
      }
      return submitImport(
        conflict.scope,
        conflict.kind,
        conflict.payload,
        {on_conflict: "rename", target_name: name},
        {resolving: true},
      );
    }

    function cancelImport() {
      if (pendingOwner !== null || pendingConflict === null) return false;
      return clearConflict({clearInput: true, restoreFocus: true});
    }

    elements.backButton.addEventListener("click", close);
    elements.globalImportButton.addEventListener("click", () => {
      elements.globalDirectoryInput.click?.();
    });
    elements.globalArchiveButton.addEventListener("click", () => {
      elements.globalArchiveInput.click?.();
    });
    elements.personalImportButton.addEventListener("click", () => {
      elements.personalDirectoryInput.click?.();
    });
    elements.personalArchiveButton.addEventListener("click", () => {
      elements.personalArchiveInput.click?.();
    });
    elements.globalDirectoryInput.addEventListener("change", () => {
      void importDirectory("global", elements.globalDirectoryInput.files);
    });
    elements.globalArchiveInput.addEventListener("change", () => {
      void importArchive("global", elements.globalArchiveInput.files?.[0]);
    });
    elements.personalDirectoryInput.addEventListener("change", () => {
      void importDirectory("personal", elements.personalDirectoryInput.files);
    });
    elements.personalArchiveInput.addEventListener("change", () => {
      void importArchive("personal", elements.personalArchiveInput.files?.[0]);
    });
    elements.importOverwriteButton.addEventListener("click", () => {
      void overwriteImport();
    });
    elements.importRenameButton.addEventListener("click", () => {
      void renameImport();
    });
    elements.importCancelButton.addEventListener("click", cancelImport);
    elements.importConflictDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      cancelImport();
    });
    elements.importConflictDialog.addEventListener("close", () => {
      if (!internalConflictClose) cancelImport();
    });

    clearLists();
    resetDetail();
    elements.changesNotice.textContent = "仅对新会话生效";
    elements.changesNotice.hidden = true;
    syncPresentation();

    return {
      open,
      close,
      refresh,
      reset,
      selectDetail,
      setGlobalEnabled,
      waitForPendingChanges,
      setPersonalEnabled,
      archivePersonal,
      archiveGlobal,
      importDirectory,
      importArchive,
      overwriteImport,
      renameImport,
      cancelImport,
    };
  }


  /** 工作区指令（CLAUDE.md）编辑器：保存后只影响之后创建的会话。 */
  function createInstructionsController({
    api,
    elements,
    getWorkspace,
    isEditable,
    onChanged,
    onError,
    resetArmTimeoutMs = 8000,
  }) {
    const state = {
      loaded: false,
      busy: false,
      source: null,
      contentHash: null,
      baseline: "",
      maxBytes: 0,
      generation: 0,
      resetArmed: false,
      resetTimer: null,
    };
    const resetIdleLabel =
      (elements.resetButton && elements.resetButton.textContent) || "还原默认";
    const RESET_CONFIRM_LABEL = "确认还原";

    function instructionsPath(workspace) {
      return `/api/workspaces/${encodeURIComponent(workspace.id)}/instructions`;
    }

    function utf8Bytes(text) {
      if (typeof TextEncoder === "function") {
        return new TextEncoder().encode(text).length;
      }
      return unescape(encodeURIComponent(text)).length;
    }

    function visible() {
      return Boolean(elements.section) && isEditable();
    }

    function describeSource(source) {
      if (source === "custom") return "当前使用：自定义";
      if (source === "seed") return "当前使用：默认（工作区预置）";
      if (source === "template") return "当前使用：默认（模板）";
      return "当前使用：默认（内置）";
    }

    function renderStatus(extra) {
      if (!elements.status) return;
      const size = utf8Bytes(elements.editor ? elements.editor.value : "");
      const limit = state.maxBytes ? `${Math.round(state.maxBytes / 1024)} KB` : "";
      const parts = [describeSource(state.source)];
      if (limit) parts.push(`${size} / ${limit}`);
      if (extra) parts.push(extra);
      elements.status.textContent = parts.join("　·　");
    }

    function disarmReset() {
      if (state.resetTimer) {
        clearTimeout(state.resetTimer);
        state.resetTimer = null;
      }
      if (!state.resetArmed) return;
      state.resetArmed = false;
      if (elements.resetButton) {
        elements.resetButton.textContent = resetIdleLabel;
        elements.resetButton.setAttribute("aria-pressed", "false");
      }
      renderStatus();
    }

    // 面板 iframe 的 sandbox 没有 allow-modals，window.confirm() 会静默返回 false，
    // 所以改为页内两步确认：第一次点击武装，限时内再点一次才真正还原。
    function armReset() {
      if (state.busy || !state.loaded || state.source !== "custom") return;
      state.resetArmed = true;
      if (elements.resetButton) {
        elements.resetButton.textContent = RESET_CONFIRM_LABEL;
        elements.resetButton.setAttribute("aria-pressed", "true");
      }
      renderStatus("再点一次「确认还原」将删除你的自定义指令；修改正文或点保存可取消");
      state.resetTimer = setTimeout(disarmReset, resetArmTimeoutMs);
      if (state.resetTimer && typeof state.resetTimer.unref === "function") {
        state.resetTimer.unref();
      }
    }

    function syncButtons() {
      const dirty = elements.editor ? elements.editor.value !== state.baseline : false;
      if (elements.saveButton) {
        elements.saveButton.disabled = state.busy || !state.loaded || !dirty;
      }
      if (elements.resetButton) {
        elements.resetButton.disabled = state.busy || !state.loaded || state.source !== "custom";
      }
    }

    function apply(payload) {
      disarmReset();
      state.source = payload.source;
      state.contentHash = payload.content_hash;
      state.maxBytes = payload.max_bytes || state.maxBytes;
      state.baseline = payload.content || "";
      if (elements.editor) elements.editor.value = state.baseline;
      state.loaded = true;
      renderStatus();
      syncButtons();
    }

    function setBusy(busy) {
      state.busy = busy;
      if (busy) disarmReset();
      // 首次加载时正文还是空的，给一句提示，别让用户对着空白框猜。
      if (elements.loading) elements.loading.hidden = !busy;
      if (elements.status && busy && !state.loaded) {
        elements.status.textContent = "正在加载当前指令…";
      }
      syncButtons();
    }

    async function refresh() {
      const workspace = getWorkspace();
      if (!visible() || !workspace) {
        if (elements.section) elements.section.hidden = true;
        return;
      }
      elements.section.hidden = false;
      const generation = ++state.generation;
      setBusy(true);
      try {
        const payload = await api(instructionsPath(workspace));
        if (generation !== state.generation) return;
        apply(payload);
      } catch (error) {
        if (generation === state.generation && onError) onError(error);
      } finally {
        if (generation === state.generation) setBusy(false);
      }
    }

    async function save() {
      const workspace = getWorkspace();
      if (!workspace || state.busy || !elements.editor) return;
      disarmReset();
      const generation = ++state.generation;
      setBusy(true);
      try {
        const payload = await api(instructionsPath(workspace), {
          method: "PUT",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({
            content: elements.editor.value,
            expected_hash: state.source === "custom" ? state.contentHash : null,
          }),
        });
        if (generation !== state.generation) return;
        apply(payload);
        renderStatus("已保存，仅对新会话生效");
        if (onChanged) onChanged();
      } catch (error) {
        if (generation === state.generation && onError) onError(error);
      } finally {
        if (generation === state.generation) setBusy(false);
      }
    }

    async function reset() {
      const workspace = getWorkspace();
      if (!workspace || state.busy || state.source !== "custom") return;
      const generation = ++state.generation;
      setBusy(true);
      try {
        await api(instructionsPath(workspace), {
          method: "DELETE",
          headers: {"content-type": "application/json"},
          body: JSON.stringify({expected_hash: state.contentHash}),
        });
        if (generation !== state.generation) return;
        const payload = await api(instructionsPath(workspace));
        if (generation !== state.generation) return;
        apply(payload);
        renderStatus("已还原默认，仅对新会话生效");
        if (onChanged) onChanged();
      } catch (error) {
        if (generation === state.generation && onError) onError(error);
      } finally {
        if (generation === state.generation) setBusy(false);
      }
    }

    if (elements.editor) {
      elements.editor.addEventListener("input", () => {
        disarmReset();
        renderStatus();
        syncButtons();
      });
    }
    if (elements.saveButton) elements.saveButton.addEventListener("click", save);
    if (elements.resetButton) {
      elements.resetButton.addEventListener("click", () => {
        if (state.resetArmed) {
          disarmReset();
          reset();
          return;
        }
        armReset();
      });
    }

    return {refresh, save, reset};
  }

  return {
    createController,
    createInstructionsController,
    createCandidateController,
    createViewNavigationController,
    createSessionCreationCoordinator,
    createWorkspaceRefreshCoordinator,
  };
});
