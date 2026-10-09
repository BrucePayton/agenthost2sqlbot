/* Host owns the data backend, authenticated identity and session binding. */
function createDataAgentContext({root, select, reset, status, backend, agentLabel, manage, api, changed, error}) {
  let key = null, generation = 0, binding = null, busy = false, running = false;
  const isMcp = () => binding?.backend === "mcp";
  function render(notify = true) {
    root.hidden = !key;
    const mcp = isMcp();
    select.hidden = mcp;
    if (agentLabel) agentLabel.hidden = mcp;
    if (manage) manage.hidden = mcp;
    select.disabled = busy || running || Boolean(binding?.agent_id);
    reset.disabled = busy || running || !binding || (!mcp && !binding.chat_id);
    if (backend) {
      backend.value = binding?.backend || "sqlbot";
      backend.disabled = busy || running || !binding;
      const option = Array.from(backend.options || []).find(o => o.value === "mcp");
      if (option) option.disabled = !binding?.mcp_available && !mcp;
      backend.title = binding?.mcp_unavailable_reason || "";
    }
    status.textContent = busy ? "正在更新取数方式与上下文…" : mcp
      ? "MCP：搜索表 → 查看字段 → 查询数据"
      : binding?.chat_id ? `SQLBot 会话 ${binding.chat_id} · 追问可沿用上下文`
      : binding?.agent_id ? "下一次问数将创建 SQLBot 会话" : "请选择问数助手，或切换至 MCP 工具";
    if (notify) changed();
  }
  async function loadAgents(version, current) {
    const agents = await api("/api/data-agents");
    if (version !== generation) return;
    select.replaceChildren();
    select.add(new Option("选择 Data Agent", ""));
    agents.filter(a => a.status === "published" || a.id === current.agent_id)
      .forEach(a => select.add(new Option(a.name, a.id)));
    select.value = current.agent_id || "";
  }
  async function load(sessionId) {
    key = sessionId; binding = null; const version = ++generation;
    busy = Boolean(key); select.replaceChildren(); render();
    if (!key) return;
    try {
      const current = await api(`/api/sessions/${encodeURIComponent(sessionId)}/data-agent`);
      if (version !== generation) return;
      binding = current;
      // MCP does not require a SQLBot assistant or a working Service-2 catalog.
      if (!isMcp()) await loadAgents(version, current);
    } catch (e) { if (version === generation) error(e); }
    finally { if (version === generation) { busy = false; render(); } }
  }
  async function save(shouldReset, target = binding?.backend || "sqlbot") {
    if (!key || busy || running || !binding) return;
    const version = generation, sessionId = key;
    const agentId = target === "sqlbot" ? select.value || binding.agent_id : null;
    busy = true; render();
    try {
      const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/data-agent`, {
        method: "PUT", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({agent_id: agentId || null, backend: target, reset: shouldReset}),
      });
      if (version === generation) {
        const previous = binding.backend || "sqlbot";
        binding = result;
        if (target === "sqlbot" && previous !== target) await loadAgents(version, result);
      }
    } catch (e) { if (version === generation) { select.value = binding?.agent_id || ""; error(e); } }
    finally { if (version === generation) { busy = false; render(); } }
  }
  select.addEventListener("change", () => void save(false));
  backend?.addEventListener("change", () => void save(false, backend.value));
  reset.addEventListener("click", () => void save(true));
  return {load, ready: () => !key || (!busy && Boolean(binding) && (isMcp() || Boolean(binding.agent_id))),
    setRunning(value) { running = value; render(false); }};
}
if (typeof module !== "undefined") module.exports = {createDataAgentContext};
