// web/shared/davinci-protocol.js
var PROTOCOL = "davinci-agent-host";
var PROTOCOL_VERSION = "1";
var MESSAGE_TTL_MS = 15e3;
var MESSAGE_TYPES = Object.freeze({
  HOST_CONTEXT: "HOST_CONTEXT",
  CAPABILITY_REQUEST: "DAVINCI_CAPABILITY_REQUEST",
  CAPABILITY_RESULT: "DAVINCI_CAPABILITY_RESULT",
  UI_COMMAND: "UI_COMMAND",
  UI_ACK: "UI_ACK"
});
var CAPTURE_TOOL = Object.freeze({
  name: "dashboard.capture_current_view",
  description: "Capture the dashboard exactly as the user currently sees it.",
  parameters: { type: "object", properties: {}, additionalProperties: false }
});
var NAVIGATE_TOOL = Object.freeze({
  name: "navigateTo",
  description: "Navigate the Davinci host to an allowed application view.",
  parameters: {
    type: "object",
    properties: {
      destination: { type: "string", enum: ["dashboard", "datasets"] },
      resourceId: { type: "string" }
    },
    required: ["destination"],
    additionalProperties: false
  }
});
function messageId() {
  return globalThis.crypto?.randomUUID?.() ?? `msg-${Date.now()}-${Math.random()}`;
}
function createDavinciEnvelope({
  messageType,
  requestId,
  toolCallId,
  nonce,
  contextVersion,
  payload,
  now = Date.now()
}) {
  return {
    protocol: PROTOCOL,
    protocolVersion: PROTOCOL_VERSION,
    messageType,
    messageId: messageId(),
    requestId,
    toolCallId,
    nonce,
    issuedAt: now,
    expiresAt: now + MESSAGE_TTL_MS,
    contextVersion,
    payload
  };
}
function validateDavinciEnvelope(envelope, event, expected) {
  if (!envelope || typeof envelope !== "object" || event.origin !== expected.expectedOrigin || event.source !== expected.expectedSource || envelope.protocol !== PROTOCOL || envelope.protocolVersion !== PROTOCOL_VERSION || envelope.nonce !== expected.nonce || typeof envelope.messageId !== "string" || !Object.values(MESSAGE_TYPES).includes(envelope.messageType) || !Number.isFinite(envelope.issuedAt) || !Number.isFinite(envelope.expiresAt) || expected.now > envelope.expiresAt || envelope.issuedAt > expected.now + 1e3) {
    return { ok: false, code: "ORIGIN_REJECTED" };
  }
  if (envelope.messageType !== MESSAGE_TYPES.HOST_CONTEXT && envelope.contextVersion !== expected.contextVersion) {
    return { ok: false, code: "CONTEXT_STALE" };
  }
  return { ok: true };
}

// web/davinci-mock/main.js
var DEFAULT_STATE = Object.freeze({
  route: "/dashboard/1024",
  contextVersion: 1,
  filters: { region: "\u5357\u533A", period: "\u6700\u8FD17\u5929" },
  metrics: { itemCount: 4734, weeklyChangePct: -10.88, bidAmount: 40388380 },
  widgets: [
    { id: "trend", title: "\u7269\u54C1\u91CF\u8D8B\u52BF", values: [742, 711, 695, 681, 652, 631, 622] },
    { id: "category", title: "\u54C1\u7C7B\u6784\u6210", values: { \u624B\u673A: 2130, \u5E73\u677F: 1164, \u7B14\u8BB0\u672C: 865, \u5176\u4ED6: 575 } },
    { id: "detail", title: "\u6838\u5FC3\u6307\u6807\u660E\u7EC6", values: [] }
  ]
});
function clone(value) {
  return structuredClone(value);
}
function validRoute(route) {
  return route === "/dashboard/1024" || route === "/datasets";
}
function createDavinciStore({ route = DEFAULT_STATE.route, now = () => (/* @__PURE__ */ new Date()).toISOString() } = {}) {
  if (!validRoute(route)) throw new Error("Unsupported Mock Davinci route");
  const state = clone(DEFAULT_STATE);
  state.route = route;
  return {
    state,
    hostContext() {
      const dashboard = state.route === "/dashboard/1024";
      return {
        pageType: dashboard ? "dashboard" : "dataset",
        resourceId: dashboard ? "1024" : null,
        contextVersion: state.contextVersion,
        supportedCapabilities: dashboard ? ["dashboard.capture_current_view"] : [],
        supportedCommands: ["navigateTo"]
      };
    },
    setRegion(region) {
      if (!region || region === state.filters.region) return;
      state.filters.region = region;
      state.contextVersion += 1;
    },
    setRoute(routeValue) {
      if (!validRoute(routeValue) || routeValue === state.route) return false;
      state.route = routeValue;
      state.contextVersion += 1;
      return true;
    },
    captureCurrentView() {
      if (state.route !== "/dashboard/1024") throw new Error("CAPABILITY_UNAVAILABLE");
      return {
        schemaVersion: "mock-dashboard-snapshot-v1",
        page: {
          pageType: "dashboard",
          dashboardId: "1024",
          title: `${state.filters.region}\u7ECF\u8425\u4EEA\u8868\u76D8`,
          contextVersion: state.contextVersion,
          capturedAt: now()
        },
        filters: [
          { field: "\u533A\u57DF", operator: "eq", value: state.filters.region },
          { field: "\u65F6\u95F4", operator: "relative", value: state.filters.period }
        ],
        metrics: clone(state.metrics),
        widgets: clone(state.widgets)
      };
    },
    navigateTo(args) {
      let path;
      if (args?.destination === "datasets" && args.resourceId == null) {
        path = "/datasets";
      } else if (args?.destination === "dashboard" && (args.resourceId == null || args.resourceId === "1024")) {
        path = "/dashboard/1024";
      } else {
        return { ok: false, error: "TARGET_NOT_FOUND", message: "The requested Davinci destination does not exist." };
      }
      state.route = path;
      state.contextVersion += 1;
      return {
        ok: true,
        ack: {
          schemaVersion: "davinci-ui-ack-v1",
          status: "executed",
          destination: args.destination,
          path,
          contextVersion: state.contextVersion
        }
      };
    }
  };
}
function initializeBrowser() {
  const root = document.querySelector("#davinciApp");
  if (!root) return;
  const content = document.querySelector("#davinciContent");
  const title = document.querySelector("#pageTitle");
  const frame = document.querySelector("#agentFrame");
  const drawer = document.querySelector("#agentDrawer");
  const launcher = document.querySelector("#agentLauncher");
  const close = document.querySelector("#agentClose");
  const agentOrigin = root.dataset.agentOrigin;
  const nonce = root.dataset.nonce;
  const store = createDavinciStore({ route: location.pathname === "/datasets" ? "/datasets" : "/dashboard/1024" });
  const controls = { delayToolResultMs: 0, mutateBeforeCapture: false };
  function render() {
    const dashboard = store.state.route === "/dashboard/1024";
    title.textContent = dashboard ? "\u8BC4\u4F30\u4EEA\u8868\u76D8" : "\u6570\u636E\u96C6";
    for (const button of document.querySelectorAll("[data-route]")) {
      button.classList.toggle("active", button.dataset.route === store.state.route);
    }
    if (!dashboard) {
      content.innerHTML = `
        <div class="dataset-grid">
          <article class="card"><h2>\u7ECF\u8425\u6307\u6807\u6570\u636E\u96C6</h2><p>\u6700\u8FD1\u66F4\u65B0\uFF1A\u4ECA\u5929 15:40</p><p>12 \u4E2A\u5B57\u6BB5 \xB7 48,320 \u884C</p></article>
          <article class="card"><h2>\u7269\u54C1\u8BC4\u4F30\u660E\u7EC6</h2><p>\u6700\u8FD1\u66F4\u65B0\uFF1A\u4ECA\u5929 15:36</p><p>27 \u4E2A\u5B57\u6BB5 \xB7 104,882 \u884C</p></article>
          <article class="card"><h2>\u54C1\u7C7B\u7EF4\u8868</h2><p>\u6700\u8FD1\u66F4\u65B0\uFF1A\u6628\u5929 23:00</p><p>8 \u4E2A\u5B57\u6BB5 \xB7 426 \u884C</p></article>
        </div>`;
      return;
    }
    const metrics = store.state.metrics;
    const trend = store.state.widgets[0].values;
    const categories = store.state.widgets[1].values;
    content.innerHTML = `
      <div class="filter-row">
        <label class="filter">\u533A\u57DF <select id="regionFilter"><option>\u5357\u533A</option><option>\u534E\u4E1C\u533A</option><option>\u534E\u5317\u533A</option></select></label>
        <span class="filter">\u65F6\u95F4 ${store.state.filters.period}</span>
      </div>
      <div class="metric-grid">
        <article class="card"><span class="metric-label">\u8BC4\u4F30\u7269\u54C1\u91CF</span><p class="metric-value">${metrics.itemCount.toLocaleString("zh-CN")}</p></article>
        <article class="card"><span class="metric-label">\u5468\u73AF\u6BD4</span><p class="metric-value negative">${metrics.weeklyChangePct}%</p></article>
        <article class="card"><span class="metric-label">\u51FA\u4EF7\u91D1\u989D</span><p class="metric-value">\xA5${metrics.bidAmount.toLocaleString("zh-CN")}</p></article>
      </div>
      <div class="widget-grid">
        <article class="card"><h3>\u7269\u54C1\u91CF\u8D8B\u52BF</h3><div class="bars">${trend.map((value) => `<i style="height:${Math.round(value / 8)}px"></i>`).join("")}</div></article>
        <article class="card"><h3>\u54C1\u7C7B\u6784\u6210</h3><div class="category-list">${Object.entries(categories).map(([name, value]) => `<div><span>${name}</span><strong>${value.toLocaleString("zh-CN")}</strong></div>`).join("")}</div></article>
      </div>`;
    const region = document.querySelector("#regionFilter");
    region.value = store.state.filters.region;
    region.addEventListener("change", () => {
      store.setRegion(region.value);
      render();
      sendHostContext();
    });
  }
  function postToAgent(message) {
    frame.contentWindow?.postMessage(message, agentOrigin);
  }
  function sendHostContext() {
    postToAgent(createDavinciEnvelope({
      messageType: MESSAGE_TYPES.HOST_CONTEXT,
      nonce,
      contextVersion: store.state.contextVersion,
      payload: store.hostContext()
    }));
  }
  function sendResult(request, messageType, payload, contextVersion = store.state.contextVersion) {
    postToAgent(createDavinciEnvelope({
      messageType,
      requestId: request.requestId,
      toolCallId: request.toolCallId,
      nonce,
      contextVersion,
      payload
    }));
  }
  function sendFailure(request, code, message) {
    const resultType = request.messageType === MESSAGE_TYPES.UI_COMMAND ? MESSAGE_TYPES.UI_ACK : MESSAGE_TYPES.CAPABILITY_RESULT;
    sendResult(request, resultType, { error: code, message });
  }
  function executeRequest(request) {
    if (request.messageType === MESSAGE_TYPES.CAPABILITY_REQUEST) {
      if (request.payload?.capability !== "dashboard.capture_current_view" || store.state.route !== "/dashboard/1024") {
        sendFailure(request, "CAPABILITY_UNAVAILABLE", "Dashboard capture is unavailable on this page.");
        return;
      }
      if (controls.mutateBeforeCapture) {
        controls.mutateBeforeCapture = false;
        store.setRegion("\u534E\u4E1C\u533A");
        render();
        sendHostContext();
      }
      sendResult(request, MESSAGE_TYPES.CAPABILITY_RESULT, store.captureCurrentView());
      return;
    }
    if (request.messageType === MESSAGE_TYPES.UI_COMMAND) {
      const result = store.navigateTo(request.payload);
      if (!result.ok) {
        sendFailure(request, result.error, result.message);
        return;
      }
      history.pushState({}, "", result.ack.path);
      render();
      sendHostContext();
      sendResult(request, MESSAGE_TYPES.UI_ACK, result.ack, store.state.contextVersion);
    }
  }
  window.addEventListener("message", (event) => {
    const request = event.data;
    if (request?.protocol !== PROTOCOL || request?.protocolVersion !== PROTOCOL_VERSION) return;
    const validation = validateDavinciEnvelope(request, event, {
      expectedOrigin: agentOrigin,
      expectedSource: frame.contentWindow,
      nonce,
      contextVersion: store.state.contextVersion,
      now: Date.now()
    });
    if (!validation.ok) {
      if (validation.code === "CONTEXT_STALE" && event.origin === agentOrigin && event.source === frame.contentWindow) {
        sendFailure(request, "CONTEXT_STALE", "The Davinci page changed before the Tool was executed.");
      }
      return;
    }
    if (controls.delayToolResultMs > 0) {
      const delay = controls.delayToolResultMs;
      controls.delayToolResultMs = 0;
      setTimeout(() => executeRequest(request), delay);
      return;
    }
    executeRequest(request);
  });
  document.addEventListener("click", (event) => {
    const route = event.target.closest?.("[data-route]")?.dataset.route;
    if (!route || !store.setRoute(route)) return;
    history.pushState({}, "", route);
    render();
    sendHostContext();
  });
  window.addEventListener("popstate", () => {
    if (store.setRoute(location.pathname)) {
      render();
      sendHostContext();
    }
  });
  launcher.addEventListener("click", () => {
    drawer.hidden = false;
    launcher.hidden = true;
  });
  close.addEventListener("click", () => {
    drawer.hidden = true;
    launcher.hidden = false;
  });
  frame.addEventListener("load", sendHostContext);
  render();
  window.__davinciMock = { store, controls, sendHostContext };
  for (const delay of [0, 100, 300]) setTimeout(sendHostContext, delay);
}
if (typeof document !== "undefined") initializeBrowser();
export {
  createDavinciStore
};
