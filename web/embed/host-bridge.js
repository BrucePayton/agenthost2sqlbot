import {
  CONTRACT_DIGEST,
  CONTRACT_VERSION,
  PROTOCOL_VERSION,
  PUBLIC_TOOL_CONTRACTS
} from '../shared/generated/davinci-contracts.js'

const DEFAULT_COMMAND_TIMEOUT_MS = 15_000
const MIN_COMMAND_TIMEOUT_MS = 1_000
const MAX_COMMAND_TIMEOUT_MS = 120_000

function buildTimeouts(toolContracts, defaultTimeoutMs) {
  const timeouts = new Map()
  for (const contract of toolContracts || []) {
    const timeoutMs = contract?.timeoutMs
    if (
      typeof contract?.action === 'string' &&
      Number.isInteger(timeoutMs) &&
      timeoutMs >= MIN_COMMAND_TIMEOUT_MS &&
      timeoutMs <= MAX_COMMAND_TIMEOUT_MS
    ) {
      timeouts.set(contract.action, timeoutMs)
    }
  }
  return {
    get(action) {
      return timeouts.get(action) || defaultTimeoutMs
    }
  }
}

function defaultId() {
  return globalThis.crypto?.randomUUID?.() || `id-${Date.now()}-${Math.random()}`
}

export function createHostBridge({
  parentOrigin,
  parentWindow = globalThis.parent,
  windowObject = globalThis.window,
  createId = defaultId,
  now = Date.now,
  toolContracts = PUBLIC_TOOL_CONTRACTS,
  defaultCommandTimeoutMs = DEFAULT_COMMAND_TIMEOUT_MS
}) {
  let helloId = null
  let binding = null
  let contextWaiter = null
  let readyWaiter = null
  const commandWaiters = new Map()
  const commandTimeouts = buildTimeouts(
    toolContracts,
    defaultCommandTimeoutMs
  )

  const base = () => ({
    protocolVersion: PROTOCOL_VERSION,
    contractVersion: CONTRACT_VERSION,
    contractDigest: CONTRACT_DIGEST
  })

  const post = (message) => {
    parentWindow.postMessage({ ...base(), ...message }, parentOrigin)
  }

  const validBase = (event) =>
    event.origin === parentOrigin &&
    event.source === parentWindow &&
    event.data?.protocolVersion === PROTOCOL_VERSION &&
    event.data?.contractVersion === CONTRACT_VERSION &&
    event.data?.contractDigest === CONTRACT_DIGEST

  const handleMessage = (event) => {
    if (!validBase(event)) return
    const message = event.data
    if (message.type === 'DAVINCI_AGENT_BRIDGE_READY') {
      if (message.helloId !== helloId || typeof message.bridgeNonce !== 'string') return
      binding = {
        bridgeNonce: message.bridgeNonce,
        pageInstanceId: message.pageInstanceId,
        contextVersion: message.contextVersion
      }
      if (readyWaiter) {
        readyWaiter.resolve(binding)
        readyWaiter = null
      }
      return
    }
    if (
      !binding ||
      message.bridgeNonce !== binding.bridgeNonce ||
      message.pageInstanceId !== binding.pageInstanceId
    ) return
    if (message.type === 'DAVINCI_AGENT_CONTEXT_RESPONSE') {
      if (!contextWaiter || message.context?.pageInstanceId !== binding.pageInstanceId) return
      binding.contextVersion = message.context.contextVersion
      const waiter = contextWaiter
      contextWaiter = null
      waiter.resolve(message.context)
      return
    }
    if (message.type === 'DAVINCI_AGENT_CONTEXT_CHANGED') {
      if (message.context?.pageInstanceId === binding.pageInstanceId) {
        binding.contextVersion = message.context.contextVersion
      }
      return
    }
    if (message.type !== 'DAVINCI_AGENT_UI_ACK') return
    const commandId = message.ack?.commandId
    const waiter = commandWaiters.get(commandId)
    if (!waiter) return
    clearTimeout(waiter.timer)
    commandWaiters.delete(commandId)
    binding.contextVersion = message.ack.contextVersion
    waiter.resolve(message.ack)
  }

  return {
    start() {
      windowObject.addEventListener('message', handleMessage)
      helloId = createId()
      const promise = new Promise((resolve, reject) => {
        readyWaiter = { resolve, reject }
      })
      post({ type: 'DAVINCI_AGENT_BRIDGE_HELLO', helloId })
      return promise
    },
    isReady() {
      return binding !== null
    },
    async requestContext() {
      if (!binding) throw new Error('Davinci HostBridge is not ready')
      if (contextWaiter) return contextWaiter.promise
      let resolve
      let reject
      const promise = new Promise((yes, no) => {
        resolve = yes
        reject = no
      })
      contextWaiter = { promise, resolve, reject }
      post({
        type: 'DAVINCI_AGENT_CONTEXT_REQUEST',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId
      })
      return promise
    },
    execute(action, args) {
      if (!binding) return Promise.reject(new Error('Davinci HostBridge is not ready'))
      const commandId = createId()
      const timeoutMs = commandTimeouts.get(action)
      const expiresAt = now() + timeoutMs
      const promise = new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          commandWaiters.delete(commandId)
          reject(new Error('Davinci frontend command timed out'))
        }, timeoutMs)
        commandWaiters.set(commandId, { resolve, reject, timer })
      })
      post({
        type: 'DAVINCI_AGENT_UI_COMMAND',
        bridgeNonce: binding.bridgeNonce,
        pageInstanceId: binding.pageInstanceId,
        command: {
          commandId,
          pageInstanceId: binding.pageInstanceId,
          expectedContextVersion: binding.contextVersion,
          expiresAt,
          action,
          args
        }
      })
      return promise
    },
    destroy() {
      windowObject.removeEventListener('message', handleMessage)
      for (const waiter of commandWaiters.values()) clearTimeout(waiter.timer)
      commandWaiters.clear()
      if (contextWaiter) contextWaiter.reject(new Error('Davinci HostBridge closed'))
      if (readyWaiter) readyWaiter.reject(new Error('Davinci HostBridge closed'))
      contextWaiter = null
      readyWaiter = null
      binding = null
    }
  }
}
