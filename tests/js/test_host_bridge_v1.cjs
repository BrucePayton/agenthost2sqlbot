const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

const root = path.resolve(__dirname, '../..')

class FakeWindow {
  constructor() {
    this.listeners = new Set()
  }

  addEventListener(type, listener) {
    if (type === 'message') this.listeners.add(listener)
  }

  removeEventListener(type, listener) {
    if (type === 'message') this.listeners.delete(listener)
  }

  emit(event) {
    for (const listener of this.listeners) listener(event)
  }
}

test('V1 HostBridge binds READY, requests context and correlates UI ACK', async () => {
  const { createHostBridge } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const parentWindow = { postMessage: (value, origin) => sent.push({ value, origin }) }
  const bridge = createHostBridge({
    parentOrigin: 'http://local.aihuishou.com:5002',
    parentWindow,
    windowObject: iframeWindow,
    createId: (() => {
      let index = 0
      return () => `id-${++index}`
    })()
  })

  const ready = bridge.start()
  assert.equal(typeof ready?.then, 'function')
  const hello = sent.shift().value
  assert.equal(hello.type, 'DAVINCI_AGENT_BRIDGE_HELLO')

  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      helloId: hello.helloId,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      contextVersion: 3
    }
  })
  await ready

  const contextPromise = bridge.requestContext()
  assert.equal(sent.at(-1).value.type, 'DAVINCI_AGENT_CONTEXT_REQUEST')
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_CONTEXT_RESPONSE',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      context: { pageInstanceId: 'workbench-1', contextVersion: 3 }
    }
  })
  assert.equal((await contextPromise).contextVersion, 3)

  const commandPromise = bridge.execute('page.get_context', {}, 3)
  const command = sent.at(-1).value.command
  assert.equal(command.action, 'page.get_context')
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_UI_ACK',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      ack: {
        commandId: command.commandId,
        action: 'page.get_context',
        status: 'executed',
        contextVersion: 3,
        result: { summary: '当前页面：dashboard', contextVersion: 3 }
      }
    }
  })
  assert.equal((await commandPromise).result.summary, '当前页面：dashboard')
  bridge.destroy()
})

test('V1 HostBridge owns the latest context version for UI commands', async () => {
  const { createHostBridge } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const parentWindow = { postMessage: (value, origin) => sent.push({ value, origin }) }
  const bridge = createHostBridge({
    parentOrigin: 'http://local.aihuishou.com:5002',
    parentWindow,
    windowObject: iframeWindow,
    createId: (() => {
      let index = 0
      return () => `id-${++index}`
    })()
  })

  const ready = bridge.start()
  const hello = sent.shift().value
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      helloId: hello.helloId,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      contextVersion: 3
    }
  })
  await ready

  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_CONTEXT_CHANGED',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      context: { pageInstanceId: 'workbench-1', contextVersion: 4 }
    }
  })

  const commandPromise = bridge.execute('page.get_context', {}, 3)
  const command = sent.at(-1).value.command
  assert.equal(command.expectedContextVersion, 4)
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_UI_ACK',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      ack: {
        commandId: command.commandId,
        action: 'page.get_context',
        status: 'executed',
        contextVersion: 4,
        result: { summary: '当前页面：dashboard', contextVersion: 4 }
      }
    }
  })
  await commandPromise
  bridge.destroy()
})

test('generated browser contracts include canonical input schemas', async () => {
  const { PUBLIC_TOOL_CONTRACTS } = await import(
    pathToFileURL(path.join(root, 'web/shared/generated/davinci-contracts.js'))
  )
  const context = PUBLIC_TOOL_CONTRACTS.find((item) => item.action === 'page.get_context')
  assert.deepEqual(context.inputSchema, {
    type: 'object',
    properties: {},
    additionalProperties: false
  })
})

test('frontend command timeout is read from tool contract metadata', async () => {
  const { createHostBridge } = await import(
    pathToFileURL(path.join(root, 'web/embed/host-bridge.js'))
  )
  const iframeWindow = new FakeWindow()
  const sent = []
  const parentWindow = { postMessage: (value, origin) => sent.push({ value, origin }) }
  const bridge = createHostBridge({
    parentOrigin: 'http://local.aihuishou.com:5002',
    parentWindow,
    windowObject: iframeWindow,
    createId: () => 'capture-command',
    now: () => 1_000,
    toolContracts: [
      {
        action: 'custom.long_running_read',
        timeoutMs: 75_000
      }
    ]
  })

  const ready = bridge.start()
  const hello = sent.shift().value
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_BRIDGE_READY',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      helloId: hello.helloId,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      contextVersion: 3
    }
  })
  await ready

  const capture = bridge.execute('custom.long_running_read', {})
  const command = sent.at(-1).value.command
  assert.equal(command.expiresAt, 76_000)
  iframeWindow.emit({
    origin: 'http://local.aihuishou.com:5002',
    source: parentWindow,
    data: {
      type: 'DAVINCI_AGENT_UI_ACK',
      protocolVersion: '1.0',
      contractVersion: '1.0',
      contractDigest: hello.contractDigest,
      bridgeNonce: 'nonce-1',
      pageInstanceId: 'workbench-1',
      ack: {
        commandId: command.commandId,
        action: 'custom.long_running_read',
        status: 'executed',
        contextVersion: 3,
        result: {
          snapshotRef: 'snap-1',
          summary: '已采集 1 个组件',
          partialErrors: []
        }
      }
    }
  })
  await capture
  bridge.destroy()
})
