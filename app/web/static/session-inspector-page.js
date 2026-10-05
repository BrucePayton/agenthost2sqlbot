(function sessionInspectorPageModule(root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.SessionInspectorPage = api;
    root.addEventListener("DOMContentLoaded", () => api.initialize());
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function factory(root) {
  const keyEventTypes = new Set([
    "message.user",
    "message.assistant.completed",
    "tool.started",
    "tool.completed",
    "frontend_tool.deferred",
    "turn.failed",
    "turn.cancelled",
    "turn.interrupted",
  ]);

  function catalogPath({query = "", limit = 100, offset = 0, workspaceId = ""} = {}) {
    const params = new URLSearchParams();
    const normalized = String(query || "").trim();
    if (normalized) params.set("query", normalized);
    params.set("limit", String(limit));
    params.set("offset", String(offset));
    const normalizedWorkspaceId = String(workspaceId || "").trim();
    if (normalizedWorkspaceId) params.set("workspace_id", normalizedWorkspaceId);
    return `/api/inspector/sessions?${params.toString()}`;
  }

  function exportPath(sessionId, {turnId = "", format = "md"} = {}) {
    const params = new URLSearchParams();
    params.set("format", format);
    const normalizedTurnId = String(turnId || "").trim();
    if (normalizedTurnId) params.set("turn", normalizedTurnId);
    return `/api/inspector/sessions/${encodeURIComponent(sessionId)}/export?${params.toString()}`;
  }

  function curlCommand(origin, sessionId) {
    const target = `session-${String(sessionId).slice(0, 8)}.md`;
    return `curl -sS "${origin}${exportPath(sessionId)}" -o ${target}`;
  }

  function filterEvents(records = [], mode = "key") {
    return mode === "all"
      ? records.slice()
      : records.filter((record) => keyEventTypes.has(record.event_type));
  }

  function summarizeEvent(record = {}) {
    const payload = record.payload || {};
    const eventType = record.event_type || "event";
    if (eventType === "message.user") {
      const toolResults = Array.isArray(payload.tool_results) ? payload.tool_results : [];
      return {
        title: toolResults.length ? "前端工具结果回传" : "用户消息",
        text: payload.text || (toolResults.length ? `${toolResults.length} 个结果` : ""),
        tone: "user",
      };
    }
    if (eventType === "message.assistant.completed") {
      return {title: "Agent 回复", text: payload.text || "", tone: "assistant"};
    }
    if (eventType === "message.assistant.delta") {
      return {title: "Agent 流式片段", text: payload.text || payload.delta || "", tone: "assistant"};
    }
    if (eventType === "tool.started") {
      return {
        title: `调用工具 · ${normalizeToolName(payload.name || "tool")}`,
        text: payload.input_preview || "",
        tone: "tool",
      };
    }
    if (eventType === "tool.completed") {
      return {
        title: `${payload.is_error ? "工具失败" : "工具完成"} · ${normalizeToolName(payload.name || "tool")}`,
        text: payload.output_preview || "",
        tone: payload.is_error ? "failed" : "tool",
      };
    }
    if (eventType === "frontend_tool.deferred") {
      return {
        title: `等待页面工具 · ${normalizeToolName(payload.name || "tool")}`,
        text: stringifyCompact(payload.arguments),
        tone: "tool",
      };
    }
    if (["turn.failed", "turn.cancelled", "turn.interrupted"].includes(eventType)) {
      return {
        title: eventType.replace("turn.", "Turn "),
        text: payload.message || payload.reason || payload.code || "",
        tone: "failed",
      };
    }
    return {
      title: eventType,
      text: payload.message || payload.phase || stringifyCompact(payload),
      tone: "event",
    };
  }

  function normalizeToolName(value) {
    const name = String(value || "tool");
    for (const prefix of ["mcp__davinci_ui__", "mcp__davinci_data__"]) {
      if (name.startsWith(prefix)) {
        return name.slice(prefix.length).replaceAll("__", ".");
      }
    }
    return name;
  }

  function formatRunConfig(model, effort) {
    const parts = [];
    if (model) parts.push(model);
    if (effort) parts.push(`effort ${effort}`);
    return parts.length ? parts.join(" · ") : "默认模型 · 默认 effort";
  }

  function runConfigLabel(model, effort) {
    const label = root.document.createElement("span");
    label.className = "trace-run-config";
    label.textContent = formatRunConfig(model, effort);
    return label;
  }

  function stringifyCompact(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  }

  /** Map known reason codes while preserving future codes as plain text. */
  function feedbackReasonLabel(code) {
    return ({accurate: "结果准确", understood: "理解了需求", completed: "成功完成操作", efficient: "处理高效", clear: "展示清晰",
      inaccurate: "结果不准确", misunderstood: "没理解需求", incomplete: "操作失败或未完成", slow: "处理太慢或卡住",
      poorPresentation: "展示效果不符合预期", tooManySteps: "反复确认或步骤太多"})[code] || String(code);
  }

  /** Use Beijing calendar dates independently of the browser's timezone. */
  function beijingDate(value = Date.now()) {
    return new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0, 10);
  }

  function initialize() {
    const document = root.document;
    const byId = (id) => document.getElementById(id);
    const elements = {
      searchForm: byId("inspectorSearchForm"),
      searchInput: byId("inspectorSearchInput"),
      workspaceSelect: byId("inspectorWorkspaceSelect"),
      refreshButton: byId("inspectorRefreshButton"),
      sessionList: byId("inspectorSessionList"),
      sessionCount: byId("inspectorSessionCount"),
      catalogEmpty: byId("inspectorCatalogEmpty"),
      previousButton: byId("inspectorPreviousButton"),
      nextButton: byId("inspectorNextButton"),
      pageLabel: byId("inspectorPageLabel"),
      detail: byId("inspectorDetail"),
      owner: byId("inspectorSessionOwner"),
      title: byId("inspectorSessionTitle"),
      sessionId: byId("inspectorSessionId"),
      copyButton: byId("inspectorCopyButton"),
      exportLink: byId("inspectorExportLink"),
      curlButton: byId("inspectorCurlButton"),
      status: byId("inspectorSessionStatus"),
      metadata: byId("inspectorSessionMetadata"),
      eventMode: byId("inspectorEventMode"),
      timeline: byId("inspectorTimeline"),
      performance: byId("inspectorPerformance"),
      context: byId("inspectorContext"),
      timelinePanel: byId("inspectorTimelinePanel"),
      performancePanel: byId("inspectorPerformancePanel"),
      contextPanel: byId("inspectorContextPanel"),
      toast: byId("inspectorToast"),
    };
    const state = {
      query: "",
      workspaceId: "",
      limit: 100,
      offset: 0,
      total: 0,
      sessions: [],
      selectedId: null,
      detail: null,
      events: [],
      view: "timeline",
    };

    async function fetchJson(path) {
      const response = await root.fetch(path, {
        credentials: "same-origin",
        headers: {Accept: "application/json"},
      });
      const contentType = response.headers.get("content-type") || "";
      const body = contentType.includes("application/json")
        ? await response.json()
        : await response.text();
      if (!response.ok) {
        throw Object.assign(new Error(body?.error?.message || `请求失败 (${response.status})`), {status: response.status});
      }
      return body;
    }

    async function loadCatalog({keepSelection = true} = {}) {
      const catalog = await fetchJson(catalogPath(state));
      state.sessions = catalog.items;
      state.total = catalog.total;
      renderCatalog();
      if (!keepSelection || !state.sessions.some((item) => item.id === state.selectedId)) {
        if (!keepSelection) clearSelection();
      }
    }

    async function loadWorkspaces() {
      try {
        const workspaces = await fetchJson("/api/inspector/workspaces");
        for (const workspace of workspaces.items || []) {
          const option = document.createElement("option");
          option.value = workspace.workspace_id;
          option.textContent = `${workspace.workspace_name}（${workspace.session_count}）`;
          elements.workspaceSelect.append(option);
          byId("feedbackWorkspace")?.append(option.cloneNode(true));
        }
      } catch (_) {
        // Keep "全部工作区" only; the catalog itself still works without this list.
      }
    }

    function renderCatalog() {
      elements.sessionList.replaceChildren();
      elements.sessionCount.textContent = String(state.total);
      elements.catalogEmpty.hidden = state.sessions.length > 0;
      for (const session of state.sessions) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `catalog-item${session.id === state.selectedId ? " active" : ""}`;
        button.setAttribute("role", "listitem");
        const title = document.createElement("strong");
        title.textContent = session.title;
        const owner = document.createElement("span");
        owner.className = "catalog-item-meta";
        owner.textContent = `${session.user_display_name} · ${session.workspace_name}`;
        const counts = document.createElement("span");
        counts.className = "catalog-item-counts";
        const status = document.createElement("span");
        status.textContent = session.status;
        const total = document.createElement("span");
        total.textContent = `${session.turn_count} Turn · ${session.event_count} Event`;
        counts.append(status, total);
        button.append(title, owner, counts);
        button.addEventListener("click", () => void selectSession(session.id));
        elements.sessionList.append(button);
      }
      const page = Math.floor(state.offset / state.limit) + 1;
      const pages = Math.max(1, Math.ceil(state.total / state.limit));
      elements.pageLabel.textContent = `第 ${page} / ${pages} 页`;
      elements.previousButton.disabled = state.offset === 0;
      elements.nextButton.disabled = state.offset + state.limit >= state.total;
    }

    async function selectSession(sessionId, target = {}) {
      state.selectedId = sessionId;
      renderCatalog();
      try {
        const [detail, events] = await Promise.all([
          fetchJson(`/api/inspector/sessions/${encodeURIComponent(sessionId)}`),
          fetchJson(`/api/inspector/sessions/${encodeURIComponent(sessionId)}/events`).catch(error => {
            if (error.status !== 413 || !target.turnId) throw error;
            return fetchJson(`/api/inspector/sessions/${encodeURIComponent(sessionId)}/events?turnId=${encodeURIComponent(target.turnId)}`);
          }),
        ]);
        if (state.selectedId !== sessionId) return;
        state.detail = detail;
        state.events = events;
        renderDetail();
        if (target.messageId) focusFeedbackReply(target);
      } catch (error) {
        showToast(error.message);
      }
    }

    function renderDetail() {
      const session = state.detail.session;
      elements.detail.hidden = false;
      elements.owner.textContent = `${session.user_display_name} · ${session.identity_subject}`;
      elements.title.textContent = session.title;
      elements.sessionId.textContent = session.id;
      elements.exportLink.href = exportPath(session.id);
      elements.status.textContent = session.status;
      elements.status.className = `status-pill ${session.status}`;
      renderTaskFeedback(state.detail.feedback || []);
      renderMetadata(session);
      renderTimeline();
      renderPerformance();
      elements.context.textContent = JSON.stringify(state.detail.context, null, 2);
      switchView(state.view);
    }

    /** Locate persisted replies without interpreting user-supplied text as selectors. */
    function focusFeedbackReply(target) {
      switchView("timeline");
      elements.eventMode.value = "key";
      renderTimeline();
      const cards = [...elements.timeline.querySelectorAll(".event-card")];
      const card = cards.find(node => node.dataset.eventId === target.messageId) ||
        cards.find(node => node.dataset.turnId === target.turnId);
      if (card) { card.classList.add("feedback-target"); card.scrollIntoView({block: "center"}); }
      else showToast("未找到该回复，可导出原任务继续核对");
    }

    /** Display each task evaluation once, above the Session's complete timeline. */
    function renderTaskFeedback(votes) {
      const panel = byId("inspectorTaskFeedback");
      if (!panel) return;
      panel.replaceChildren();
      const title = document.createElement("strong"); title.textContent = "回复评价"; panel.append(title);
      if (!votes.length) { const empty = document.createElement("p"); empty.textContent = "暂无评价"; panel.append(empty); }
      for (const vote of votes) {
        const row = document.createElement("p");
        row.textContent = `${vote.rating === "up" ? "👍 赞" : "👎 踩"} · ${vote.displayName || vote.actorId || "评价人"} · ${(vote.reasons || []).map(feedbackReasonLabel).join("、") || "未补充原因"}${vote.comment ? "\n" + vote.comment : ""}`;
        const link = document.createElement("button"); link.type = "button"; link.className = "link-button";
        link.textContent = "定位被评价回复"; link.addEventListener("click", () => focusFeedbackReply(vote));
        panel.append(row, link);
      }
    }

    function renderMetadata(session) {
      elements.metadata.replaceChildren();
      const items = [
        ["Workspace", `${session.workspace_name} (${session.workspace_id})`],
        ["更新时间", formatDate(session.updated_at)],
        ["创建时间", formatDate(session.created_at)],
        ["Turn / Event", `${session.turn_count} / ${session.event_count}`],
        ["Claude Session", session.claude_session_id || "—"],
        ["最近错误", session.last_error_code || "—"],
      ];
      for (const [label, value] of items) {
        const wrapper = document.createElement("div");
        wrapper.className = "metadata-item";
        const term = document.createElement("dt");
        term.textContent = label;
        const description = document.createElement("dd");
        description.textContent = value;
        wrapper.append(term, description);
        elements.metadata.append(wrapper);
      }
    }

    function renderTimeline() {
      elements.timeline.replaceChildren();
      const visible = filterEvents(state.events, elements.eventMode.value);
      if (!visible.length) {
        elements.timeline.append(emptyMessage("当前范围没有可显示的事件。"));
        return;
      }
      let currentTurnId = null;
      let turnOrdinal = 0;
      for (const record of visible) {
        if (record.turn_id !== currentTurnId) {
          currentTurnId = record.turn_id;
          turnOrdinal += 1;
          elements.timeline.append(turnDivider(currentTurnId, turnOrdinal));
        }
        const payload = record.payload || {};
        const summary = summarizeEvent(record);
        const card = document.createElement("article");
        card.className = `event-card ${summary.tone}`;
        card.dataset.eventId = record.id;
        card.dataset.turnId = record.turn_id;
        const time = document.createElement("time");
        time.dateTime = record.created_at;
        time.textContent = formatClock(record.created_at);
        const content = document.createElement("div");
        const title = document.createElement("h3");
        title.textContent = summary.title;
        if (record.event_type === "message.user" && root.SessionInspector.isVisibleUserMessage(payload)) {
          title.append(" ", runConfigLabel(payload.model, payload.effort));
        }
        content.append(title);
        if (summary.text) {
          const text = document.createElement("p");
          text.textContent = summary.text;
          content.append(text);
        }
        const raw = document.createElement("details");
        raw.className = "event-raw";
        const rawTitle = document.createElement("summary");
        rawTitle.textContent = `${record.event_type} · ${record.turn_id}`;
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(record.payload, null, 2);
        raw.append(rawTitle, pre);
        content.append(raw);
        card.append(time, content);
        elements.timeline.append(card);
      }
    }

    function renderPerformance() {
      elements.performance.replaceChildren();
      const traces = root.SessionInspector.buildPerformanceTraces(state.events);
      if (!traces.length) {
        elements.performance.append(emptyMessage("暂无可计算的执行耗时。"));
        return;
      }
      traces.slice().reverse().forEach((trace, index) => {
        const details = document.createElement("details");
        details.className = "performance-trace";
        details.open = index === 0;
        const summary = document.createElement("summary");
        const title = document.createElement("strong");
        title.textContent = trace.title;
        const duration = document.createElement("span");
        duration.textContent = `${trace.runIds.length} Run · ${formatDuration(trace.totalMs)}`;
        summary.append(title, runConfigLabel(trace.model, trace.effort), duration);
        const body = document.createElement("div");
        body.className = "performance-trace-body";
        for (const step of trace.steps) {
          const row = document.createElement("div");
          row.className = "performance-step";
          const time = document.createElement("span");
          time.textContent = formatClock(step.at);
          const label = document.createElement("span");
          label.textContent = step.label;
          const delta = document.createElement("span");
          delta.textContent = `+${formatDuration(step.deltaMs)}`;
          row.append(time, label, delta);
          body.append(row);
        }
        details.append(summary, body);
        elements.performance.append(details);
      });
    }

    function turnDivider(turnId, ordinal) {
      const row = document.createElement("div");
      row.className = "turn-divider";
      const label = document.createElement("span");
      label.textContent = `Turn ${ordinal} · ${String(turnId || "未知").slice(0, 8)}`;
      const link = document.createElement("a");
      link.className = "link-button";
      link.href = exportPath(state.selectedId, {turnId});
      link.download = "";
      link.textContent = "导出此 Turn";
      row.append(label, link);
      return row;
    }

    function switchView(view) {
      state.view = view;
      elements.timelinePanel.hidden = view !== "timeline";
      elements.performancePanel.hidden = view !== "performance";
      elements.contextPanel.hidden = view !== "context";
      elements.eventMode.closest("label").hidden = view !== "timeline";
      document.querySelectorAll("[data-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === view);
      });
    }

    function clearSelection() {
      state.selectedId = null;
      state.detail = null;
      state.events = [];
      elements.detail.hidden = true;
    }

    function showToast(message) {
      elements.toast.textContent = message;
      elements.toast.hidden = false;
      root.clearTimeout(showToast.timer);
      showToast.timer = root.setTimeout(() => { elements.toast.hidden = true; }, 4000);
    }

    const feedbackState = {query: null, groupOffset: 0, detailOffset: 0, detailFilter: {}, revision: 0, limit: 50};

    /** Switch between the existing transcript and the new read-only feedback view. */
    function switchInspectorMode(mode) {
      if (!byId("inspectorFeedbackPanel")) return;
      byId("inspectorFeedbackPanel").hidden = mode !== "feedback";
      byId("inspectorSessionsPanel").hidden = mode === "feedback";
      elements.searchForm.hidden = mode === "feedback";
      document.querySelectorAll("[data-inspector-mode]").forEach(button =>
        button.classList.toggle("active", button.dataset.inspectorMode === mode));
      if (mode === "feedback" && !feedbackState.query) {
        resetFeedbackFilters();
        void loadFeedbackReport(true);
      }
    }

    /** Reset to the last seven Beijing calendar days, inclusive of today. */
    function resetFeedbackFilters() {
      byId("feedbackDateTo").value = beijingDate();
      byId("feedbackDateFrom").value = beijingDate(Date.now() - 6 * 86400000);
      for (const id of ["feedbackActorQuery", "feedbackWorkspace", "feedbackRating"]) byId(id).value = "";
      byId("feedbackGroupBy").value = "day";
    }

    /** Render table cells as plain text; only our own buttons can be interactive. */
    function feedbackTable(id, headings, rows) {
      const table = byId(id); table.replaceChildren();
      const header = document.createElement("thead"), heading = document.createElement("tr");
      for (const text of headings) { const cell = document.createElement("th"); cell.textContent = text; heading.append(cell); }
      header.append(heading); table.append(header);
      const body = document.createElement("tbody");
      for (const cells of rows) {
        const row = document.createElement("tr");
        for (const value of cells) { const cell = document.createElement("td");
          if (value?.nodeType) cell.append(value); else cell.textContent = value == null ? "—" : String(value);
          row.append(cell); }
        body.append(row);
      }
      if (!rows.length) { const row = document.createElement("tr"), cell = document.createElement("td");
        cell.colSpan = headings.length; cell.textContent = "没有符合筛选条件的评价"; row.append(cell); body.append(row); }
      table.append(body);
    }

    /** Keep each table's pagination independent from the full-result overview. */
    function feedbackPagination(prefix, total, offset) {
      byId(`${prefix}Prev`).disabled = offset === 0;
      byId(`${prefix}Next`).disabled = offset + feedbackState.limit >= total;
      byId(`${prefix}Page`).textContent = `第 ${Math.floor(offset / feedbackState.limit) + 1} 页 · 共 ${total} 条`;
    }

    /** Load bounded aggregate and detail queries with the same committed filter set. */
    async function loadFeedbackReport(reset = false) {
      if (reset) {
        feedbackState.query = { dateFrom: byId("feedbackDateFrom").value, dateTo: byId("feedbackDateTo").value,
          actorQuery: byId("feedbackActorQuery").value.trim(), workspaceId: byId("feedbackWorkspace").value,
          groupBy: byId("feedbackGroupBy").value };
        feedbackState.detailFilter = {}; feedbackState.groupOffset = 0; feedbackState.detailOffset = 0;
      }
      const revision = ++feedbackState.revision;
      const query = feedbackState.query;
      byId("feedbackStatus").textContent = "正在读取评价…";
      try {
        const statsQuery = new URLSearchParams({...query, limit: feedbackState.limit, offset: feedbackState.groupOffset});
        const detailQuery = new URLSearchParams({...query, ...feedbackState.detailFilter, limit: feedbackState.limit, offset: feedbackState.detailOffset});
        detailQuery.delete("groupBy");
        if (byId("feedbackRating").value) detailQuery.set("rating", byId("feedbackRating").value);
        const [stats, details] = await Promise.all([
          fetchJson(`/api/inspector/feedbackStats?${statsQuery}`), fetchJson(`/api/inspector/feedback?${detailQuery}`),
        ]);
        if (revision !== feedbackState.revision) return;
        const rate = value => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
        const summary = byId("feedbackSummary"); summary.replaceChildren();
        for (const [label, value] of [["有效评价", stats.summary.total], ["赞", stats.summary.up], ["踩", stats.summary.down],
          ["好评率", rate(stats.summary.positiveRate)], ["评价人数", stats.summary.actorCount]]) {
          const card = document.createElement("div"), title = document.createElement("span"), number = document.createElement("strong");
          title.textContent = label; number.textContent = String(value); card.append(title, number); summary.append(card);
        }
        const day = query.groupBy !== "actor", actor = query.groupBy !== "day";
        feedbackTable("feedbackGroups", [...(day ? ["日期"] : []), ...(actor ? ["评价人", "账号"] : []), "评价数", "赞", "踩", "好评率", "人数", "明细"],
          stats.rows.map(row => {
            const button = document.createElement("button"); button.type = "button"; button.textContent = "查看明细";
            button.addEventListener("click", () => {
              feedbackState.detailFilter = {...(row.date ? {dateFrom: row.date, dateTo: row.date} : {}), ...(row.actorId ? {actorId: row.actorId} : {})};
              feedbackState.detailOffset = 0; void loadFeedbackReport();
            });
            return [...(day ? [row.date] : []), ...(actor ? [row.displayName, row.account] : []), row.total, row.up, row.down, rate(row.positiveRate), row.actorCount, button];
          }));
        feedbackTable("feedbackDetails", ["首次评价（北京）", "评价人 / 账号", "评价", "原因", "备注", "任务"], details.items.map(item => {
          const link = document.createElement("button"); link.type = "button"; link.textContent = `原问题 ${item.taskId.slice(0, 8)}`;
          link.addEventListener("click", () => { switchInspectorMode("sessions"); void selectSession(item.sessionId, item); });
          return [new Date(item.createdAt).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"}), `${item.displayName} / ${item.account}`,
            item.rating === "up" ? "👍 赞" : "👎 踩", item.reasons.map(feedbackReasonLabel).join("、") || "—", item.comment || "—", link];
        }));
        feedbackPagination("feedbackGroups", stats.totalGroups, feedbackState.groupOffset);
        feedbackPagination("feedbackDetails", details.total, feedbackState.detailOffset);
        const filter = feedbackState.detailFilter;
        byId("feedbackDetailFilter").textContent = [filter.dateFrom, filter.actorId ? "已选评价人" : ""].filter(Boolean).join(" · ");
        byId("feedbackStatus").textContent = `${stats.dateFrom} 至 ${stats.dateTo} · 总览不受明细赞/踩筛选及分页影响`;
      } catch (error) {
        if (revision !== feedbackState.revision) return;
        byId("feedbackStatus").textContent = `读取失败：${error.message}`;
        for (const id of ["feedbackSummary", "feedbackGroups", "feedbackDetails"]) byId(id).replaceChildren();
      }
    }

    if (byId("feedbackFilterForm")) {
      document.querySelectorAll("[data-inspector-mode]").forEach(button =>
        button.addEventListener("click", () => switchInspectorMode(button.dataset.inspectorMode)));
      byId("feedbackFilterForm").addEventListener("submit", event => { event.preventDefault(); void loadFeedbackReport(true); });
      byId("feedbackFilterForm").addEventListener("reset", event => { event.preventDefault(); resetFeedbackFilters(); void loadFeedbackReport(true); });
      byId("feedbackRating").addEventListener("change", () => { feedbackState.detailOffset = 0; void loadFeedbackReport(); });
      byId("feedbackAllDetails").addEventListener("click", () => { feedbackState.detailFilter = {}; feedbackState.detailOffset = 0; void loadFeedbackReport(); });
      for (const [prefix, key] of [["feedbackGroups", "groupOffset"], ["feedbackDetails", "detailOffset"]]) {
        byId(`${prefix}Prev`).addEventListener("click", () => { feedbackState[key] = Math.max(0, feedbackState[key] - feedbackState.limit); void loadFeedbackReport(); });
        byId(`${prefix}Next`).addEventListener("click", () => { feedbackState[key] += feedbackState.limit; void loadFeedbackReport(); });
      }
    }

    elements.searchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      state.query = elements.searchInput.value.trim();
      state.offset = 0;
      void loadCatalog({keepSelection: false}).catch((error) => showToast(error.message));
    });
    elements.workspaceSelect.addEventListener("change", () => {
      state.workspaceId = elements.workspaceSelect.value;
      state.offset = 0;
      void loadCatalog({keepSelection: false}).catch((error) => showToast(error.message));
    });
    elements.refreshButton.addEventListener("click", () => {
      void loadCatalog().catch((error) => showToast(error.message));
    });
    elements.previousButton.addEventListener("click", () => {
      state.offset = Math.max(0, state.offset - state.limit);
      void loadCatalog({keepSelection: false}).catch((error) => showToast(error.message));
    });
    elements.nextButton.addEventListener("click", () => {
      state.offset += state.limit;
      void loadCatalog({keepSelection: false}).catch((error) => showToast(error.message));
    });
    elements.eventMode.addEventListener("change", renderTimeline);
    elements.copyButton.addEventListener("click", async () => {
      if (!state.selectedId) return;
      try {
        await root.navigator.clipboard.writeText(state.selectedId);
        showToast("Session ID 已复制");
      } catch (_) {
        showToast(`Session ID：${state.selectedId}`);
      }
    });
    elements.curlButton.addEventListener("click", async () => {
      if (!state.selectedId) return;
      const command = curlCommand(root.location.origin, state.selectedId);
      try {
        await root.navigator.clipboard.writeText(command);
        showToast("curl 命令已复制，粘贴给 Codex/Claude 即可拉取完整轨迹");
      } catch (_) {
        showToast(command);
      }
    });
    document.querySelectorAll("[data-view]").forEach((button) => {
      button.addEventListener("click", () => switchView(button.dataset.view));
    });

    void loadWorkspaces();
    void loadCatalog().catch((error) => showToast(error.message));
    const linked = new URLSearchParams(root.location?.search || "");
    if (linked.get("sessionId")) void selectSession(linked.get("sessionId"), {messageId: linked.get("messageId"), turnId: linked.get("turnId")});
    return {loadCatalog, selectSession, state};
  }

  function emptyMessage(text) {
    const element = root.document.createElement("div");
    element.className = "empty-state";
    element.textContent = text;
    return element;
  }

  function formatDate(value) {
    if (!value) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(value));
  }

  function formatClock(value) {
    const date = typeof value === "number" ? new Date(value) : new Date(value);
    if (!Number.isFinite(date.getTime())) return "—";
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
      hour12: false,
    }).format(date);
  }

  function formatDuration(milliseconds) {
    const value = Math.max(0, Number(milliseconds) || 0);
    if (value < 1000) return `${Math.round(value)} ms`;
    if (value < 60000) return `${(value / 1000).toFixed(1)} s`;
    return `${Math.floor(value / 60000)}m ${Math.round((value % 60000) / 1000)}s`;
  }

  return {catalogPath, curlCommand, exportPath, filterEvents, initialize, summarizeEvent, feedbackReasonLabel, beijingDate};
});
