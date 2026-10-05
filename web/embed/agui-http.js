/** Preserve structured rejection information before the AG-UI client flattens it. */
export async function requireAgUiResponse(response) {
  if (response.ok) return response;
  let payload;
  try { payload = await response.json(); } catch {}
  const details = payload?.error;
  throw Object.assign(new Error(details?.message || `HTTP ${response.status}`), {
    status: response.status,
    code: details?.code,
    requestId: details?.request_id,
    details: details?.details,
  });
}

/** Only an explicit pre-run rejection makes it safe to restore a user draft. */
export function isUnacceptedAgUiRequest(error, pendingInput) {
  if (error?.status === 409 && error?.code === "session_skills_changed") {
    return pendingInput?.accepted === false;
  }
  return error?.status === 422 && error?.code === "invalid_request" &&
    !["turn", "continuation"].includes(error?.details?.stage) &&
    pendingInput?.accepted === false;
}
