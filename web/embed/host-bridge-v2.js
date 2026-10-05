import {
  CONTRACT_DIGEST,
  CONTRACT_VERSION,
  PROTOCOL_VERSION,
  PUBLIC_TOOL_CONTRACTS
} from '../shared/generated/davinci-contracts-v2.js'

const DEFAULT_TIMEOUT_MS = 15_000
const DEFAULT_HANDSHAKE_RETRY_MS = 250
const DEFAULT_HANDSHAKE_TIMEOUT_MS = 10_000
const DEFAULT_TOOL_ACK_GRACE_MS = 6_000
const PANEL_ACTIONS = ['resize', 'close', 'reauthenticate', 'drag', 'status']
const PANEL_SIZES = ['small', 'medium', 'large', 'fullscreen']
const PANEL_RUN_STATES = [
  'idle', 'running', 'finished', 'failed', 'stopped', 'unknown'
]

const unitFraction = value =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1

/** Launcher centre as viewport fractions; null clears it, undefined means malformed. */
function toLauncherPosition(value) {
  if (value === null) return null
  if (value && typeof value === 'object' && unitFraction(value.x) && unitFraction(value.y)) {
    return { x: value.x, y: value.y }
  }
  return undefined
}

function defaultId() {
  return globalThis.crypto?.randomUUID?.() || `id-${Date.now()}-${Math.random()}`
}

function timeoutFor(name) {
  const contract = PUBLIC_TOOL_CONTRACTS.find(item => item.action === name)
  return Number.isInteger(contract?.timeoutMs)
    ? contract.timeoutMs
    : DEFAULT_TIMEOUT_MS
}

function validRuntimeContext(value, pageInstanceId) {
  return Boolean(
    value &&
      value.state?.schemaVersion === 'davinci-page-state-v1' &&
      value.state?.page?.instanceId === pageInstanceId &&
      typeof value.profileId === 'string' &&
      typeof value.catalogDigest === 'string' &&
      typeof value.toolSetId === 'string' &&
      Array.isArray(value.tools) &&
      (value.context === undefined || Array.isArray(value.context))
  )
}

export function createHostBridgeV2({
  parentOrigin,
  parentWindow = globalThis.parent,
  windowObject = globalThis.window,
  createId = defaultId,
  now = Date.now,
  handshakeRetryMs = DEFAULT_HANDSHAKE_RETRY_MS,
  handshakeTimeoutMs = DEFAULT_HANDSHAKE_TIMEOUT_MS,
  contextTimeoutMs = DEFAULT_HANDSHAKE_TIMEOUT_MS,
  toolAckGraceMs = DEFAULT_TOOL_ACK_GRACE_MS,
  toolTimeoutFor = timeoutFor,
  onPanelState = null,
  onLayoutSolve = null
}) {
  let helloId = null
  let binding = null
  let receiptLookup = false
  let subscriptionStages = false
  let stageSequence = 0
  const receiptWaiters = new Map()
  let panelCommands = []
  let runtimeContext = null
  let readyWaiter = null
  let contextWaiter = null
  let handshakeRetryTimer = null
  let handshakeTimeoutTimer = null
  const toolWaiters = new Map()

  const clearHandshakeTimers = () => {
    if (handshakeRetryTimer) clearTimeout(handshakeRetryTimer)
    if (handshakeTimeoutTimer) clearTimeout(handshakeTimeoutTimer)
    handshakeRetryTimer = null
    handshakeTimeoutTimer = null
  }

  const base = () => ({
    protocolVersion: PROTOCOL_VERSION,
    contractVersion: CONTRACT_VERSION,
    contractDigest: CONTRACT_DIGEST
  })
  const post = message =>
    parentWindow.postMessage({ ...base(), ...message }, parentOrigin)
  const validBase = event =>
    event.origin === parentOrigin &&
    event.source === parentWindow &&
    event.data?.protocolVersion === PROTOCOL_VERSION &&
    event.data?.contractVersion === CONTRACT_VERSION &&
    event.data?.contractDigest === CONTRACT_DIGEST

  const handleMessage = event => {
    if (!validBase(event)) return
    const message = event.data
    if (message.type === 'DAVINCI_AGENT_BRIDGE_READY') {
      if (
        message.helloId !== helloId ||
        typeof message.bridgeNonce !== 'string' ||
        typeof message.pageInstanceId !== 'string'
      ) return
      binding = {
        bridgeNonce: message.bridgeNonce,
        pageInstanceId: message.pageInstanceId
      }
      panelCommands = Array.isArray(message.panelCommands)
        ? PANEL_ACTIONS.filter(action => message.panelCommands.includes(action))
        : []
      receiptLookup = message.receiptLookup === true
      subscriptionStages = message.subscriptionStages === true
      clearHandshakeTimers()
      readyWaiter?.resolve(binding)
      readyWaiter = null
      return
    }
    if (!binding || message.bridgeNonce !== binding.bridgeNonce) return
    if (message.type === 'DAVINCI_AGENT_LAYOUT_CANCEL') {
      const waiter = toolWaiters.get(message.toolCallId)
      if (waiter?.pageInstanceId === message.pageInstanceId &&
        waiter.computeAttempt === message.layoutAttempt) waiter.computeController?.abort()
      return
    }
    if (message.type === 'DAVINCI_AGENT_LAYOUT_REQUEST') {
      const waiter = toolWaiters.get(message.toolCallId)
      if (!onLayoutSolve || !waiter || waiter.name !== 'dashboard.set_widget_layout' ||
        !waiter.scope.sessionId || waiter.computeController ||
        !Number.isInteger(message.layoutAttempt) || message.layoutAttempt < 1 || message.layoutAttempt > 2 ||
        message.layoutAttempt !== (waiter.computeAttempts || 0) + 1 ||
        waiter.pageInstanceId !== message.pageInstanceId ||
        binding.pageInstanceId !== message.pageInstanceId ||
        message.problem?.version !== 'constraint-v1' ||
        !Number.isInteger(message.problem?.budgetMs) ||
        message.problem.budgetMs <= 0 || message.problem.budgetMs > 120000) return
      const computeController = new AbortController()
      waiter.computeController = computeController
      waiter.computeAttempt = message.layoutAttempt
      waiter.computeAttempts = message.layoutAttempt
      const nonce = binding.bridgeNonce
      const reply = result => {
        if (toolWaiters.get(message.toolCallId) !== waiter ||
          computeController.signal.aborted || binding?.bridgeNonce !== nonce ||
          binding.pageInstanceId !== message.pageInstanceId) return
        post({ type: 'DAVINCI_AGENT_LAYOUT_RESULT', bridgeNonce: nonce,
          pageInstanceId: message.pageInstanceId, toolCallId: message.toolCallId,
          layoutAttempt: message.layoutAttempt, result })
      }
      Promise.resolve().then(() => onLayoutSolve(message.problem,
        { ...waiter.scope, toolCallId: message.toolCallId,
          layoutAttempt: message.layoutAttempt }, computeController.signal))
        .then(reply, () => reply({ version: 'constraint-v1', status: 'service_unavailable' }))
        .finally(() => {
          if (waiter.computeController === computeController) {
            waiter.computeController = null
            waiter.computeAttempt = null
          }
        })
      return
    }
    if (message.type === 'DAVINCI_AGENT_PANEL_STATE') {
      // 父页单向告知外层面板状态（如启动器被拖到哪）；载荷非法就整条丢弃。
      const launcher = toLauncherPosition(message.launcher)
      if (launcher !== undefined && onPanelState) onPanelState({ launcher })
      return
    }
    if (
      message.type === 'DAVINCI_AGENT_CONTEXT_RESPONSE' ||
      message.type === 'DAVINCI_AGENT_CONTEXT_CHANGED'
    ) {
      if (
        typeof message.pageInstanceId !== 'string' ||
        !validRuntimeContext(message.runtimeContext, message.pageInstanceId)
      ) return
      binding = { ...binding, pageInstanceId: message.pageInstanceId }
      for (const waiter of toolWaiters.values()) {
        if (waiter.pageInstanceId !== binding.pageInstanceId) waiter.computeController?.abort()
      }
      runtimeContext = message.runtimeContext
      if (contextWaiter) {
        clearTimeout(contextWaiter.timer)
        contextWaiter.resolve(runtimeContext)
        contextWaiter = null
      }
      return
    }
    if (message.type === 'DAVINCI_AGENT_RECEIPT_RESPONSE') {
      const waiter = receiptWaiters.get(message.requestId)
      if (!waiter || message.pageInstanceId !== waiter.pageInstanceId) return
      clearTimeout(waiter.timer)
      receiptWaiters.delete(message.requestId)
      waiter.resolve(message.receipt || { state: 'missing' })
      return
    }
    if (message.type !== 'DAVINCI_AGENT_UI_ACK') return
    const waiter = toolWaiters.get(message.toolCallId)
    if (!waiter || message.pageInstanceId !== waiter.pageInstanceId) return
    clearTimeout(waiter.timer)
    waiter.computeController?.abort()
    toolWaiters.delete(message.toolCallId)
    waiter.resolve(message.result)
  }

  return {
    start() {
      windowObject.addEventListener('message', handleMessage)
      helloId = createId()
      const promise = new Promise((resolve, reject) => {
        readyWaiter = { resolve, reject }
      })
      const sendHello = () => {
        if (binding || !readyWaiter) return
        post({ type: 'DAVINCI_AGENT_BRIDGE_HELLO', helloId,
          ...(onLayoutSolve ? { layoutSolverVersion: 'constraint-v1' } : {}) })
        handshakeRetryTimer = setTimeout(sendHello, handshakeRetryMs)
      }
      sendHello()
      handshakeTimeoutTimer = setTimeout(() => {
        clearHandshakeTimers()
        const waiter = readyWaiter
        readyWaiter = null
        waiter?.reject(new Error('Davinci HostBridge V2 handshake timed out'))
      }, handshakeTimeoutMs)
      return promise
    },
    async requestContext() {
      if (!binding) throw new Error('Davinci HostBridge V2 is not ready')
      if (contextWaiter) return contextWaiter.promise
      let resolve
      let reject
      const promise = new Promise((yes, no) => {
        resolve = yes
        reject = no
      })
      const timer = setTimeout(() => {
        const waiter = contextWaiter
        contextWaiter = null
        waiter?.reject(
          new Error('Davinci HostBridge V2 context request timed out')
        )
      }, contextTimeoutMs)
      contextWaiter = { promise, resolve, reject, timer }
      post({
        type: 'DAVINCI_AGENT_CONTEXT_REQUEST',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId
      })
      return promise
    },
    getPanelCommands() {
      return [...panelCommands]
    },
    /** Forward live progress only; history rendering never invokes navigation. */
    sendSubscriptionStage(notice) {
      if (!binding || !subscriptionStages || !notice ||
          !['trigger', 'datasets', 'conditions', 'pushMode', 'receivers',
            'content', 'finalize'].includes(notice.step) ||
          !['taskId', 'runId', 'noticeId'].every(key =>
            typeof notice[key] === 'string' && notice[key].length > 0 &&
            notice[key].length <= 256) ||
          !Number.isSafeInteger(notice.revision) || notice.revision < 1) return false
      post({
        type: 'DAVINCI_AGENT_SUBSCRIPTION_STAGE',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId,
        stage: { taskId: notice.taskId, runId: notice.runId,
          noticeId: notice.noticeId, step: notice.step, revision: notice.revision,
          sequence: ++stageSequence }
      })
      return true
    },
    sendPanelCommand(panel) {
      if (!binding) throw new Error('Davinci HostBridge V2 is not ready')
      if (!panelCommands.length) {
        throw new Error('Davinci parent page does not accept panel commands')
      }
      const action = panel?.action
      if (!panelCommands.includes(action)) {
        throw new Error(`Unsupported panel command action: ${String(action)}`)
      }
      if (action === 'resize' && !PANEL_SIZES.includes(panel.size)) {
        throw new Error(`Unsupported panel size: ${String(panel?.size)}`)
      }
      if (action === 'status' && !PANEL_RUN_STATES.includes(panel.run)) {
        throw new Error(`Unsupported panel run state: ${String(panel?.run)}`)
      }
      if (action === 'drag' && (
        !['start', 'move', 'end', 'cancel'].includes(panel.phase) ||
        !Number.isInteger(panel.pointerId) || panel.pointerId < 0 || panel.pointerId > 2147483647 ||
        ![panel.screenX, panel.screenY].every(point =>
          typeof point === 'number' && Number.isFinite(point) && Math.abs(point) <= 100000)
      )) throw new Error('Invalid panel drag command')
      post({
        type: 'DAVINCI_AGENT_PANEL_COMMAND',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId,
        panel: action === 'resize' ? { action, size: panel.size }
          : action === 'status' ? { action, run: panel.run }
          : action === 'drag' ? { action, phase: panel.phase, pointerId: panel.pointerId,
            screenX: panel.screenX, screenY: panel.screenY } : { action }
      })
    },
    /** Query a receipt without re-executing the original command. */
    lookupNativeReceipt(toolCallId, scope) {
      if (!binding || !receiptLookup) return Promise.resolve({ state: 'missing' })
      const requestId = createId()
      const promise = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          receiptWaiters.delete(requestId)
          reject(new Error('暂时无法读取原操作回执，请稍后检查'))
        }, contextTimeoutMs)
        receiptWaiters.set(requestId, {
          resolve, reject, timer, pageInstanceId: binding.pageInstanceId
        })
      })
      post({ type: 'DAVINCI_AGENT_RECEIPT_REQUEST', requestId, toolCallId, scope,
        bridgeNonce: binding.bridgeNonce, pageInstanceId: binding.pageInstanceId })
      return promise
    },
    executeToolCall(toolCallId, name, args, scope = {}) {
      if (!binding || !runtimeContext) {
        return Promise.reject(new Error('Davinci HostBridge V2 has no PageState'))
      }
      if (toolWaiters.has(toolCallId)) {
        return Promise.reject(new Error('Duplicate in-flight frontend ToolCall'))
      }
      const timeoutMs = toolTimeoutFor(name)
      const state = runtimeContext.state
      const preconditions = {
        ...(state.page?.resource?.id
          ? { resourceId: state.page.resource.id }
          : {}),
        ...(state.revisions?.resourceRevision !== undefined
          ? { resourceRevision: state.revisions.resourceRevision }
          : {}),
        ...(state.revisions?.dataRevision !== undefined
          ? { dataRevision: state.revisions.dataRevision }
          : {})
      }
      const promise = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          toolWaiters.get(toolCallId)?.computeController?.abort()
          toolWaiters.delete(toolCallId)
          reject(new Error('Davinci frontend Tool timed out'))
        }, timeoutMs + toolAckGraceMs)
        toolWaiters.set(toolCallId, {
          resolve,
          reject,
          timer,
          name,
          scope: { ...scope },
          pageInstanceId: binding.pageInstanceId
        })
      })
      post({
        type: 'DAVINCI_AGENT_UI_COMMAND',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId,
        command: {
          toolCallId,
          sessionId: scope.sessionId,
          workspaceId: scope.workspaceId,
          toolSetId: runtimeContext.toolSetId,
          pageInstanceId: binding.pageInstanceId,
          expiresAt: now() + timeoutMs,
          name,
          args,
          preconditions
        }
      })
      return promise
    },
    destroy() {
      windowObject.removeEventListener('message', handleMessage)
      clearHandshakeTimers()
      for (const waiter of toolWaiters.values()) {
        clearTimeout(waiter.timer)
        waiter.computeController?.abort()
        waiter.reject(new Error('Davinci HostBridge V2 closed'))
      }
      toolWaiters.clear()
      for (const waiter of receiptWaiters.values()) {
        clearTimeout(waiter.timer)
        waiter.reject(new Error('Davinci HostBridge V2 closed'))
      }
      receiptWaiters.clear()
      if (contextWaiter) clearTimeout(contextWaiter.timer)
      contextWaiter?.reject(new Error('Davinci HostBridge V2 closed'))
      readyWaiter?.reject(new Error('Davinci HostBridge V2 closed'))
      contextWaiter = null
      readyWaiter = null
      binding = null
      panelCommands = []
      runtimeContext = null
    }
  }
}
