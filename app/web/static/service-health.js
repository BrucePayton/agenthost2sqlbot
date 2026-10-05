(function serviceHealthModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ServiceHealth = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function factory() {
  function deriveState(transportState, executionState) {
    if (transportState !== "ready") return transportState;
    return executionState === "degraded" ? "degraded" : "ready";
  }

  function canExecute(serviceState) {
    return serviceState === "ready";
  }

  return { deriveState, canExecute };
});
