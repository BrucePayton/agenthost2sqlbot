export function selectEmbedWorkspaces(workspaces) {
  const available = Array.isArray(workspaces) ? workspaces : [];
  return {
    agentWorkspace: available.find((workspace) => workspace.available) || null,
    skillWorkspace: available.find((workspace) => (
      workspace.kind === "personal"
      && workspace.available === true
      && workspace.can_manage_skills === true
    )) || null,
  };
}

export function requestHeaders(identityHeaders, options = {}) {
  const headers = new Headers({
    ...identityHeaders,
    ...(options.headers || {}),
  });
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
}
