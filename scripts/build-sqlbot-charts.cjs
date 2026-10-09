const esbuild = require('esbuild');
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const vendor = path.join(root, 'web/sqlbot-chart/vendor');
const source = JSON.parse(fs.readFileSync(path.join(vendor, 'SOURCE.json')));
for (const [file, digest] of Object.entries(source.files)) {
  const actual = crypto.createHash('sha256').update(fs.readFileSync(path.join(vendor, file))).digest('hex');
  if (actual !== digest) throw Error(`SQLBot source changed: ${file}; review and repin the upstream source`);
}
for (const [name, version] of Object.entries(source.dependencies)) {
  const actual = JSON.parse(fs.readFileSync(path.join(root, 'node_modules', name, 'package.json'))).version;
  if (actual !== version) throw Error(`SQLBot renderer dependency drift: ${name} ${actual} != ${version}`);
}
esbuild.build({
  entryPoints: ['web/sqlbot-chart/main.js'], bundle: true, minify: true,
  format: 'iife', globalName: 'SQLBotCharts', platform: 'browser', target: ['chrome100'],
  outfile: 'app/web/static/sqlbot-charts.js', legalComments: 'linked',
  alias: {'@/views/chat/component': vendor, '@/i18n': path.join(root, 'web/sqlbot-chart/ui-adapter.js')},
  inject: ['web/sqlbot-chart/ui-adapter.js'],
  banner: {js:'/*! SQLBot native chart renderer | © FIT2CLOUD | /static/sqlbot-charts.LICENSE.txt */'},
  logLevel: 'info',
}).catch(error => { console.error(error); process.exit(1); });
