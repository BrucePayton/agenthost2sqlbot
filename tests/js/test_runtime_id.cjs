const assert = require('node:assert/strict')
const test = require('node:test')
const { pathToFileURL } = require('node:url')
const path = require('node:path')

const root = path.resolve(__dirname, '../..')

async function runtimeId() {
  return import(pathToFileURL(path.join(root, 'web/shared/runtime-id.js')))
}

test('creates a UUID when randomUUID is unavailable in an HTTP iframe', async () => {
  const { createRuntimeId } = await runtimeId()
  const cryptoWithoutRandomUUID = {
    getRandomValues(bytes) {
      bytes.forEach((_, index) => {
        bytes[index] = index
      })
      return bytes
    }
  }

  assert.equal(
    createRuntimeId(cryptoWithoutRandomUUID),
    '00010203-0405-4607-8809-0a0b0c0d0e0f'
  )
})
