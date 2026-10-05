const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')
const { pathToFileURL } = require('node:url')

const root = path.resolve(__dirname, '../..')

/** Minimal element stand-in: records listeners, holds dataset/text, ignores layout. */
function fakeElement(overrides = {}) {
  const listeners = {}
  return {
    listeners,
    dataset: {},
    style: {},
    hidden: false,
    textContent: '',
    value: '',
    scrollHeight: 0,
    offsetHeight: 0,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false } },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn) },
    removeEventListener() {},
    setAttribute() {},
    getAttribute() { return null },
    replaceChildren() {},
    append() {},
    focus() {},
    closest() { return null },
    contains() { return false },
    querySelector() { return null },
    querySelectorAll() { return [] },
    click(event = { preventDefault() {}, stopPropagation() {} }) {
      for (const fn of listeners.click || []) fn(event)
    },
    ...overrides
  }
}

/** Every unknown element is a fresh fake; collections are arrays so for-of works. */
function fakeElements() {
  const sizeOptions = ['small', 'medium', 'large', 'fullscreen'].map(size =>
    fakeElement({ dataset: { sizeOption: size } })
  )
  const sent = []
  const seed = {
    header: null,
    sizeOptions,
    abilityClusters: [],
    promptButtons: [],
    quickMenuRows: [],
    sendPanelCommand: command => sent.push(command),
    sent
  }
  return new Proxy(seed, {
    get(target, key) {
      if (typeof key === 'symbol') return target[key]
      if (!(key in target)) target[key] = fakeElement()
      return target[key]
    }
  })
}

async function loadPanelUi() {
  // The factory registers a document-level click handler that closes popovers.
  globalThis.document = {
    createElement: () => fakeElement(),
    addEventListener() {},
    removeEventListener() {}
  }
  const { createPanelUi } = await import(
    pathToFileURL(path.join(root, 'web/embed/panel-ui.js'))
  )
  return createPanelUi
}

test('panel starts at the saved size and replays it without reporting a change', async () => {
  const createPanelUi = await loadPanelUi()
  const elements = fakeElements()
  const changes = []
  const ui = createPanelUi({ elements, initialSize: 'large', onSizeChange: size => changes.push(size) })

  ui.setPanelCommands(['resize', 'close'])

  assert.equal(elements.panel.dataset.size, 'large')
  assert.equal(elements.currentSizeLabel.textContent, '大')
  assert.deepEqual(elements.sent, [{ action: 'resize', size: 'large' }])
  assert.deepEqual(changes, [])
})

test('choosing a size from the menu reports the change once', async () => {
  const createPanelUi = await loadPanelUi()
  const elements = fakeElements()
  const changes = []
  const ui = createPanelUi({ elements, initialSize: 'large', onSizeChange: size => changes.push(size) })
  ui.setPanelCommands(['resize', 'close'])

  elements.sizeOptions.find(button => button.dataset.sizeOption === 'medium').click()

  assert.equal(elements.panel.dataset.size, 'medium')
  assert.deepEqual(changes, ['medium'])
  assert.deepEqual(elements.sent.at(-1), { action: 'resize', size: 'medium' })
})

test('an unknown saved size falls back to small', async () => {
  const createPanelUi = await loadPanelUi()
  const elements = fakeElements()
  const ui = createPanelUi({ elements, initialSize: 'huge' })
  ui.setPanelCommands(['resize'])

  assert.equal(elements.panel.dataset.size, 'small')
})
