const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')
const esbuild = require('esbuild')
const { checkBuiltAssets } = require('../../scripts/check-agui-build.cjs')

test('rejects stale bridge code even when the contract digest is unchanged', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'agui-build-check-'))
  try {
    const entry = path.join(root, 'entry.js')
    const artifact = path.join(root, 'embed.js')
    await fs.writeFile(entry, 'export const CONTRACT_DIGEST = "same";\nexport const ready = true;\n')
    await esbuild.build({ absWorkingDir: root, entryPoints: ['entry.js'],
      outfile: artifact, bundle: true, format: 'esm', platform: 'browser' })
    const targets = [['entry.js', 'embed.js']]
    await checkBuiltAssets(root, targets)
    await fs.writeFile(entry, 'export const CONTRACT_DIGEST = "same";\nexport const ready = false;\n')
    await assert.rejects(checkBuiltAssets(root, targets), /embed.js is stale/)
  } finally {
    await fs.rm(root, { recursive: true, force: true })
  }
})
