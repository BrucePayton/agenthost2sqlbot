"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const SessionInspector = require("../../app/web/static/session-inspector.js");

class Element {
  constructor(tag = "div") {
    this.tagName = tag; this.children = []; this.dataset = {}; this.style = {}; this.className = "";
    this.hidden = false; this.open = false; this._text = "";
    this.classList = {add() {}, remove() {}, toggle() {}};
  }
  append(...children) { this.children.push(...children); }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  get visibleText() {
    if (this.hidden) return "";
    return this._text + this.children.filter(child => this.tagName !== "details" || this.open || child.tagName === "summary")
      .map(child => child.visibleText).join("");
  }
  querySelector(selector) {
    for (const child of this.children) {
      if (selector.startsWith(".") ? child.className.split(" ").includes(selector.slice(1)) : child.tagName === selector) return child;
      const found = child.querySelector(selector); if (found) return found;
    }
    return null;
  }
  setAttribute() {}
  addEventListener() {}
}

async function harness() {
  const nodes = new Map();
  const document = {body: new Element(), querySelector: () => null, createElement: tag => new Element(tag),
    getElementById: id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); }};
  const context = {document, SessionInspector, module: {exports: {}}, console,
    window: {clearTimeout() {}, setTimeout() {}}, testPresentation: await import('../../web/embed/user-facing-error.js')};
  // Exercise actual classic-script renderers without initialization, network or test-only production exports.
  vm.runInNewContext(fs.readFileSync(require.resolve('../../app/web/static/app.js'), 'utf8') + `
    try { userFacingUI = testPresentation; } catch { /* Before the shared formatter integration. */ }
    module.exports = {renderFrontendToolDeferred, applyToolResults, renderToolStarted, renderToolCompleted, appendSystemEvent, state};`, context);
  return {...context.module.exports, nodes};
}

const receipt = {status: 'error', error: {code: 'INVALID_ARGUMENT', message: 'raw-backend'},
  issues: [{code: 'LAYOUT_NO_READABLE_CANDIDATES', widgetIds: ['7'], constraints: {widgetType: 'Metric', widgetTitle: '总营收'}}], token: 'secret-token'};

test('workbench deferred receipts use the same summary and keep redacted IO explicitly collapsed', async () => {
  const h = await harness();
  h.renderFrontendToolDeferred({tool_use_id: 't', name: 'dashboard.set_widget_layout', arguments: {token: 'secret-input'}}, {});
  const result = {tool_call_id: 't', is_error: true, content: JSON.stringify(receipt)};
  const original = JSON.stringify(result);
  h.applyToolResults({tool_results: [result]}, {});
  const row = h.state.timelineToolElements.get('t');
  assert.match(row.visibleText, /调整看板布局.*指标卡.*总营收/s);
  assert.doesNotMatch(row.visibleText, /INVALID_ARGUMENT|dashboard\.|raw-backend/);
  const details = row.querySelector('details');
  assert.ok(details);
  assert.match(details.visibleText, /技术详情/);
  details.open = true;
  assert.match(details.visibleText, /INVALID_ARGUMENT/);
  assert.doesNotMatch(details.visibleText, /secret-token|secret-input/);
  assert.equal(JSON.stringify(result), original);
  assert.equal(h.state.timelineTools.tools.get('t').output, result.content);
});

test('workbench live tool and standalone error paths never default to raw server prose', async () => {
  const h = await harness();
  h.renderToolStarted({tool_use_id: 'live', name: 'dashboard.set_widget_layout', input_preview: '{}'});
  h.renderToolCompleted({tool_use_id: 'live', is_error: true, output_preview: JSON.stringify(receipt)});
  const row = h.state.toolElements.get('live');
  assert.match(row.visibleText, /调整看板布局.*指标卡.*总营收/s);
  assert.doesNotMatch(row.visibleText, /INVALID_ARGUMENT|dashboard\.|raw-backend/);
  h.appendSystemEvent({code: 'forbidden', message: 'raw-backend'}, true);
  assert.match(h.nodes.get('messageTimeline').visibleText, /权限/);
  assert.doesNotMatch(h.nodes.get('messageTimeline').visibleText, /raw-backend/);
});
