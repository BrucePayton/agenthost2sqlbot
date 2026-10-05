/** Stable message identities are shared by live events and persisted history. */
export function subscriptionMessageId(event) {
  return event.payload?.noticeId || event.id;
}

/** Only current live starts may move the native form; history never calls this. */
export function createSubscriptionStageForwarder(sendStage) {
  const seen = new Set();
  const revisions = new Map();
  const steps = new Set(["trigger", "datasets", "conditions", "pushMode", "receivers", "content", "finalize"]);
  return (notice, activeRunId) => {
    if (!notice || notice.phase !== "started" || notice.runId !== activeRunId ||
        ![notice.noticeId, notice.taskId, notice.runId].every(value =>
          typeof value === "string" && value.trim() && value.length <= 256) ||
        !steps.has(notice.step) || !Number.isSafeInteger(notice.revision) || notice.revision < 1 ||
        notice.revision < (revisions.get(notice.taskId) || 0) || seen.has(notice.noticeId)) return false;
    // The parent still checks the live task/revision and manual navigation.
    // Do not consume a notice when the bridge is not ready to forward it yet.
    if (!sendStage(notice)) return false;
    seen.add(notice.noticeId);
    if (seen.size > 96) seen.delete(seen.values().next().value);
    revisions.set(notice.taskId, notice.revision);
    if (revisions.size > 96) revisions.delete(revisions.keys().next().value);
    return true;
  };
}
