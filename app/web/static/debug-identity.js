(function debugIdentityModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DebugIdentity = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function factory() {
  const storageKey = "workspace-agent.debug-obid";

  function createController({
    storage,
    fetchJson,
    isRunning,
    onIdentityChanged = async () => {},
  }) {
    let obId = storage.getItem(storageKey);

    async function select(nextObId) {
      if (isRunning()) throw new Error("Turn 运行中，不能切换 OBID。");
      const normalized = String(nextObId || "").trim();
      if (!normalized) throw new Error("请选择 OBID。");
      if (normalized === obId) return {obId, changed: false};
      obId = normalized;
      storage.setItem(storageKey, obId);
      await onIdentityChanged(obId);
      return {obId, changed: true};
    }

    async function loadUsers() {
      const users = await fetchJson("/api/debug/users");
      if (!users.length) {
        obId = null;
        storage.removeItem(storageKey);
        return users;
      }
      if (!users.some((user) => user.ob_id === obId)) {
        obId = users[0].ob_id;
        storage.setItem(storageKey, obId);
      }
      return users;
    }

    async function locateSession(sessionId) {
      const normalized = String(sessionId || "").trim();
      if (!normalized) throw new Error("请输入 Session ID。");
      const located = await fetchJson(
        `/api/debug/sessions/${encodeURIComponent(normalized)}`,
      );
      const selection = await select(located.ob_id);
      return {...located, identity_changed: selection.changed};
    }

    return {
      getObId: () => obId,
      headers: (initial = {}) => obId
        ? {...initial, "X-Davinci-ObId": obId}
        : {...initial},
      loadUsers,
      locateSession,
      select,
    };
  }

  return {createController};
});
