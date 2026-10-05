export const PROTOCOL = "davinci-agent-host";
export const PROTOCOL_VERSION = "1";
export const MESSAGE_TTL_MS = 15_000;

export const MESSAGE_TYPES = Object.freeze({
  HOST_CONTEXT: "HOST_CONTEXT",
  CAPABILITY_REQUEST: "DAVINCI_CAPABILITY_REQUEST",
  CAPABILITY_RESULT: "DAVINCI_CAPABILITY_RESULT",
  UI_COMMAND: "UI_COMMAND",
  UI_ACK: "UI_ACK",
});

const CAPTURE_TOOL = Object.freeze({
  name: "dashboard.capture_current_view",
  description: "Capture the dashboard exactly as the user currently sees it.",
  parameters: { type: "object", properties: {}, additionalProperties: false },
});

const NAVIGATE_TOOL = Object.freeze({
  name: "navigateTo",
  description: "Navigate the Davinci host to an allowed application view.",
  parameters: {
    type: "object",
    properties: {
      destination: { type: "string", enum: ["dashboard", "datasets"] },
      resourceId: { type: "string" },
    },
    required: ["destination"],
    additionalProperties: false,
  },
});

function messageId() {
  return globalThis.crypto?.randomUUID?.() ?? `msg-${Date.now()}-${Math.random()}`;
}

export function createDavinciEnvelope({
  messageType,
  requestId,
  toolCallId,
  nonce,
  contextVersion,
  payload,
  now = Date.now(),
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
    payload,
  };
}

export function validateDavinciEnvelope(envelope, event, expected) {
  if (
    !envelope || typeof envelope !== "object" ||
    event.origin !== expected.expectedOrigin ||
    event.source !== expected.expectedSource ||
    envelope.protocol !== PROTOCOL ||
    envelope.protocolVersion !== PROTOCOL_VERSION ||
    envelope.nonce !== expected.nonce ||
    typeof envelope.messageId !== "string" ||
    !Object.values(MESSAGE_TYPES).includes(envelope.messageType) ||
    !Number.isFinite(envelope.issuedAt) ||
    !Number.isFinite(envelope.expiresAt) ||
    expected.now > envelope.expiresAt ||
    envelope.issuedAt > expected.now + 1_000
  ) {
    return { ok: false, code: "ORIGIN_REJECTED" };
  }
  if (
    envelope.messageType !== MESSAGE_TYPES.HOST_CONTEXT &&
    envelope.contextVersion !== expected.contextVersion
  ) {
    return { ok: false, code: "CONTEXT_STALE" };
  }
  return { ok: true };
}

export function validateHostContext(context) {
  if (!context || typeof context !== "object" || !Number.isInteger(context.contextVersion)) return false;
  if (!Array.isArray(context.supportedCapabilities) || !Array.isArray(context.supportedCommands)) return false;
  if (context.pageType === "dashboard") {
    return context.resourceId === "1024" &&
      context.supportedCapabilities.length === 1 &&
      context.supportedCapabilities[0] === CAPTURE_TOOL.name &&
      context.supportedCommands.length === 1 &&
      context.supportedCommands[0] === NAVIGATE_TOOL.name;
  }
  return context.pageType === "dataset" && context.resourceId === null &&
    context.supportedCapabilities.length === 0 &&
    context.supportedCommands.length === 1 &&
    context.supportedCommands[0] === NAVIGATE_TOOL.name;
}

export function toolsForHostContext(context) {
  if (!validateHostContext(context)) return [];
  return [CAPTURE_TOOL, NAVIGATE_TOOL].map((tool) => structuredClone(tool));
}
