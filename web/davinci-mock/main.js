import {
  MESSAGE_TYPES,
  PROTOCOL,
  PROTOCOL_VERSION,
  createDavinciEnvelope,
  validateDavinciEnvelope,
} from "../shared/davinci-protocol.js";

const DEFAULT_STATE = Object.freeze({
  route: "/dashboard/1024",
  contextVersion: 1,
  filters: { region: "南区", period: "最近7天" },
  metrics: { itemCount: 4734, weeklyChangePct: -10.88, bidAmount: 40388380 },
  widgets: [
    { id: "trend", title: "物品量趋势", values: [742, 711, 695, 681, 652, 631, 622] },
    { id: "category", title: "品类构成", values: { 手机: 2130, 平板: 1164, 笔记本: 865, 其他: 575 } },
    { id: "detail", title: "核心指标明细", values: [] },
  ],
});

function clone(value) {
  return structuredClone(value);
}

function validRoute(route) {
  return route === "/dashboard/1024" || route === "/datasets";
}

export function createDavinciStore({ route = DEFAULT_STATE.route, now = () => new Date().toISOString() } = {}) {
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
        supportedCommands: ["navigateTo"],
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
          title: `${state.filters.region}经营仪表盘`,
          contextVersion: state.contextVersion,
          capturedAt: now(),
        },
        filters: [
          { field: "区域", operator: "eq", value: state.filters.region },
          { field: "时间", operator: "relative", value: state.filters.period },
        ],
        metrics: clone(state.metrics),
        widgets: clone(state.widgets),
      };
    },
    navigateTo(args) {
      let path;
      if (args?.destination === "datasets" && args.resourceId == null) {
        path = "/datasets";
      } else if (
        args?.destination === "dashboard" &&
        (args.resourceId == null || args.resourceId === "1024")
      ) {
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
          contextVersion: state.contextVersion,
        },
      };
    },
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
    title.textContent = dashboard ? "评估仪表盘" : "数据集";
    for (const button of document.querySelectorAll("[data-route]")) {
      button.classList.toggle("active", button.dataset.route === store.state.route);
    }
    if (!dashboard) {
      content.innerHTML = `
        <div class="dataset-grid">
          <article class="card"><h2>经营指标数据集</h2><p>最近更新：今天 15:40</p><p>12 个字段 · 48,320 行</p></article>
          <article class="card"><h2>物品评估明细</h2><p>最近更新：今天 15:36</p><p>27 个字段 · 104,882 行</p></article>
          <article class="card"><h2>品类维表</h2><p>最近更新：昨天 23:00</p><p>8 个字段 · 426 行</p></article>
        </div>`;
      return;
    }
    const metrics = store.state.metrics;
    const trend = store.state.widgets[0].values;
    const categories = store.state.widgets[1].values;
    content.innerHTML = `
      <div class="filter-row">
        <label class="filter">区域 <select id="regionFilter"><option>南区</option><option>华东区</option><option>华北区</option></select></label>
        <span class="filter">时间 ${store.state.filters.period}</span>
      </div>
      <div class="metric-grid">
        <article class="card"><span class="metric-label">评估物品量</span><p class="metric-value">${metrics.itemCount.toLocaleString("zh-CN")}</p></article>
        <article class="card"><span class="metric-label">周环比</span><p class="metric-value negative">${metrics.weeklyChangePct}%</p></article>
        <article class="card"><span class="metric-label">出价金额</span><p class="metric-value">¥${metrics.bidAmount.toLocaleString("zh-CN")}</p></article>
      </div>
      <div class="widget-grid">
        <article class="card"><h3>物品量趋势</h3><div class="bars">${trend.map((value) => `<i style="height:${Math.round(value / 8)}px"></i>`).join("")}</div></article>
        <article class="card"><h3>品类构成</h3><div class="category-list">${Object.entries(categories).map(([name, value]) => `<div><span>${name}</span><strong>${value.toLocaleString("zh-CN")}</strong></div>`).join("")}</div></article>
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
      payload: store.hostContext(),
    }));
  }

  function sendResult(request, messageType, payload, contextVersion = store.state.contextVersion) {
    postToAgent(createDavinciEnvelope({
      messageType,
      requestId: request.requestId,
      toolCallId: request.toolCallId,
      nonce,
      contextVersion,
      payload,
    }));
  }

  function sendFailure(request, code, message) {
    const resultType = request.messageType === MESSAGE_TYPES.UI_COMMAND
      ? MESSAGE_TYPES.UI_ACK
      : MESSAGE_TYPES.CAPABILITY_RESULT;
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
        store.setRegion("华东区");
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
      now: Date.now(),
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
