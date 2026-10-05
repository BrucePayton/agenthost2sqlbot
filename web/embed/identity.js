const OBID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;

export function canonicalObId(obId) {
  const value = String(obId || "").trim();
  if (!OBID_PATTERN.test(value)) throw new Error("invalid OBID");
  return value;
}

export function createIdentityHeaders(sessionToken) {
  const token = String(sessionToken || "").trim();
  if (!token) throw new Error("missing Host session token");
  return { Authorization: `Bearer ${token}` };
}

export function createLegacyIdentityHeaders(obId) {
  return { "X-Davinci-ObId": canonicalObId(obId) };
}

export function sessionStorageKey(obId, workspaceId) {
  const actor = canonicalObId(obId);
  const workspace = String(workspaceId || "").trim();
  if (!workspace) throw new Error("invalid Workspace ID");
  return `davinci-agent:session:v1:${encodeURIComponent(actor)}:${encodeURIComponent(workspace)}`;
}
