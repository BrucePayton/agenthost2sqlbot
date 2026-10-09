"use strict";

const $ = (id) => document.getElementById(id);
let authorizedDatasets = [];
let dataAgentProvider = "knowledge_mysql";

async function api(path, options = {}) {
  const headers = {...(options.headers || {})};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  if (response.status === 204) return null;
  const body = await response.json();
  if (!response.ok) throw new Error(body?.error?.message || `请求失败 (${response.status})`);
  return body;
}

function notify(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
  window.setTimeout(() => { $("notice").hidden = true; }, 4500);
}

async function loadHealth() {
  const health = await api("/api/data-agents/health");
  dataAgentProvider = health.provider;
  const blockers = [];
  const warnings = [];
  if (!health.enabled) blockers.push("Data Agent 功能开关");
  if (!health.asset_mapping_configured) blockers.push(health.provider === "starrocks_poc" ? "StarRocks 测试数据集" : "Asset MCP 与物理表的同源映射");
  if (!health.sqlbot_admin_configured) blockers.push("SQLBot 管理凭证");
  if (!health.sqlbot_secret_configured) blockers.push("SQLBot SECRET_KEY");
  if (!health.runtime_configured) blockers.push(health.provider === "starrocks_poc" ? "StarRocks 运行配置" : "MySQL 运行账号");
  if (!health.callback_source_restricted) warnings.push("SQLBot 回调来源网段未限制");
  $("health").className = `health ${blockers.length ? "blocked" : "ready"}`;
  $("health").textContent = blockers.length
    ? `阻塞项：${blockers.join("、")}。${warnings.join("；")}`
    : `配置已就绪：${health.provider === "starrocks_poc" ? "固定测试数据集（未接入用户数据权限）" : "Asset MCP 授权"} → ${health.sqlbot_url} → ${health.callback_url}${warnings.length ? `；${warnings.join("；")}` : ""}`;
}

async function loadDatasets() {
  try {
    authorizedDatasets = await api("/api/data-agents/datasets");
  } catch (error) {
    authorizedDatasets = [];
    $("datasets").textContent = `数据集加载失败：${error.message}。请修复配置后点击刷新。`;
    throw error;
  }
  if (!authorizedDatasets.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无已验证的同源数据集。请先配置 DATA_AGENT_RUNTIME_ASSET_REF 并通过 Asset MCP 校验。";
    $("datasets").replaceChildren(empty);
    return;
  }
  $("datasets").replaceChildren(...authorizedDatasets.map((dataset) => {
    const label = document.createElement("label");
    label.className = "dataset";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = dataset.ref;
    input.checked = true;
    const text = document.createElement("span");
    text.textContent = `${dataset.name} · ${dataset.fields.length} 个授权字段 — ${dataset.description}`;
    label.append(input, text);
    return label;
  }));
}

function action(label, handler, kind = "", accessibleName = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (accessibleName) button.setAttribute("aria-label", accessibleName);
  button.className = kind;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try { await handler(); } catch (error) { notify(error.message); }
    finally { button.disabled = false; }
  });
  return button;
}

async function loadAgents() {
  const agents = await api("/api/data-agents");
  $("empty").hidden = agents.length > 0;
  $("agents").replaceChildren(...agents.map((agent) => {
    const card = document.createElement("article");
    card.className = "agent-card";
    const title = document.createElement("h3");
    title.textContent = agent.name;
    const description = document.createElement("p");
    description.textContent = agent.description || "暂无描述";
    const meta = document.createElement("div");
    meta.className = "agent-meta";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = ({draft:"草稿", published:"已发布", disabled:"已停用"})[agent.status] || agent.status;
    const sync = document.createElement("span");
    sync.textContent = `SQLBot：${agent.sqlbot_sync_status}${agent.sqlbot_assistant_id ? ` #${agent.sqlbot_assistant_id}` : ""}`;
    meta.append(badge, sync);
    const datasets = document.createElement("p");
    datasets.textContent = `数据集：${agent.datasets.map((item) => item.name).join("、")}`;
    const actions = document.createElement("div");
    actions.className = "agent-actions";
    if (agent.status !== "published") actions.append(action("发布并同步", async () => {
      await api(`/api/data-agents/${agent.id}/publish`, {method:"POST"});
      notify("已发布并同步 SQLBot 高级小助手"); await loadAgents();
    }));
    if (agent.status === "published") actions.append(
      action("开启问数", async () => {
        const opened = await api(`/api/data-agents/${agent.id}/open`, {method:"POST"});
        window.location.href = opened.workbench_url;
      }, "", `${agent.name}：开启问数`),
      action("停用", async () => { await api(`/api/data-agents/${agent.id}/disable`, {method:"POST"}); await loadAgents(); }, "secondary"),
    );
    if (agent.status !== "published") actions.append(action("删除", async () => {
      if (!window.confirm(`确认删除“${agent.name}”？`)) return;
      await api(`/api/data-agents/${agent.id}`, {method:"DELETE"}); await loadAgents();
    }, "danger"));
    card.append(title, description, meta, datasets, actions);
    return card;
  }));
}

$("createForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const datasetRefs = [...$("datasets").querySelectorAll("input:checked")].map((item) => item.value);
  if (!datasetRefs.length) { notify("至少选择一个数据集"); return; }
  try {
    await api("/api/data-agents", {method:"POST", body:JSON.stringify({
      name:$("name").value.trim(), description:$("description").value.trim(), dataset_refs:datasetRefs,
    })});
    event.target.reset();
    $("datasets").querySelectorAll("input").forEach((item) => { item.checked = true; });
    notify("数据 Agent 草稿已创建"); await loadAgents();
  } catch (error) { notify(error.message); }
});
async function refreshAll() {
  $("refresh").disabled = true;
  try {
    const results = await Promise.allSettled([loadHealth(), loadDatasets(), loadAgents()]);
    const errors = [...new Set(results.filter((item) => item.status === "rejected")
      .map((item) => item.reason.message))];
    if (errors.length) notify(errors.join("；"));
  } finally {
    $("refresh").disabled = false;
  }
}
$("refresh").addEventListener("click", refreshAll);
refreshAll();


function openQuestionPanel(agent, opened) {
  window.DataAgentChat.open(agent, opened);
}
