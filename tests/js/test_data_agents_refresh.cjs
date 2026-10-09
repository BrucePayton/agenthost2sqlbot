const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('failed dataset load ends loading state and refresh retries all three sections', async () => {
  // Unit-test DOM only; no browser or real UI operations.
  class Element {
    constructor() { this.children = []; this.events = {}; this.textContent = ''; }
    addEventListener(name, handler) { this.events[name] = handler; }
    replaceChildren(...children) { this.children = children; this.textContent = ''; }
    append(...children) { this.children.push(...children); }
  }
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  let failed = true;
  const calls = [];
  const context = {
    document: {getElementById: get, createElement: () => new Element()},
    window: {setTimeout() {}},
    fetch: async path => {
      calls.push(path);
      const error = path.endsWith('/datasets') && failed;
      const body = path.endsWith('/health') ? {enabled: true} :
        path.endsWith('/datasets') ? (error ? {error: {message: '集成未启用'}} :
          [{ref: 'test', name: '可用数据集', fields: [], description: ''}]) : [];
      return {status: error ? 503 : 200, ok: !error, json: async () => body};
    },
  };
  vm.runInNewContext(fs.readFileSync('app/web/static/data-agents.js', 'utf8'), context);
  await new Promise(setImmediate);
  assert.match(get('datasets').textContent, /数据集加载失败：集成未启用/);
  failed = false;
  calls.length = 0;
  await get('refresh').events.click();
  assert.deepEqual(calls.sort(), ['/api/data-agents', '/api/data-agents/datasets', '/api/data-agents/health']);
  assert.equal(get('datasets').children.length, 1);
  assert.equal(get('refresh').disabled, false);
});
