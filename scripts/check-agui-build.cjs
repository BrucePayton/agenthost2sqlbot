const fs = require('node:fs/promises')
const path = require('node:path')
const esbuild = require('esbuild')

const TARGETS = [
  ['web/embed/main.js', 'app/web/static/embed.js'],
  ['web/davinci-mock/main.js', 'demo/davinci_mock/static/app.js']
]

/** Rebuild in memory so stale bridge code cannot pass merely on a matching digest. */
async function checkBuiltAssets(root, targets = TARGETS) {
  for (const [entry, artifact] of targets) {
    const output = path.join(root, artifact)
    const result = await esbuild.build({
      absWorkingDir: root,
      entryPoints: [entry],
      outfile: output,
      bundle: true,
      format: 'esm',
      platform: 'browser',
      write: false,
      logLevel: 'silent'
    })
    const built = result.outputFiles.find(file => file.path === output)
    const current = await fs.readFile(output)
    if (!built || !current.equals(Buffer.from(built.contents))) {
      throw new Error(`${artifact} is stale; run npm run build:agui`)
    }
  }
}

module.exports = { checkBuiltAssets }

if (require.main === module) {
  checkBuiltAssets(path.resolve(__dirname, '..')).catch(error => {
    process.stderr.write(`${error.message}\n`)
    process.exitCode = 1
  })
}
