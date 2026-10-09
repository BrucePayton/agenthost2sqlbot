const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createDataAgentContext} = require('../../app/web/static/data-agent-context.js');
function harness(api) {
  const node = () => ({value:'', events:{}, addEventListener(k,fn){this.events[k]=fn}, replaceChildren(){}, add(){}});
  const root=node(), select=node(), reset=node(), status=node();
  global.Option = function(text, value){this.text=text;this.value=value};
  return {select,reset,status,root,controller:createDataAgentContext({root,select,reset,status,api,changed(){},error(e){throw e}})};
}
const flush = () => new Promise(setImmediate);
test('restores binding, resets context and blocks changes during a turn', async () => {
  const calls=[];
  const h=harness(async (url, options) => {
    calls.push([url,options]);
    if(url==='/api/data-agents') return [{id:'a',name:'DW',status:'published'}];
    return {agent_id:'a',chat_id:options ? null : 123};
  });
  await h.controller.load('session');
  assert.equal(h.select.value,'a'); assert.equal(h.controller.ready(),true);
  assert.equal(h.select.disabled,true); assert.equal(h.reset.disabled,false);
  h.controller.setRunning(true); h.reset.events.click(); await flush();
  assert.equal(calls.length,2);
  h.controller.setRunning(false); h.reset.events.click(); await flush();
  assert.equal(JSON.parse(calls[2][1].body).reset,true);
  assert.equal(h.reset.disabled,true);
});
test('late response cannot restore a deselected session', async () => {
  let resolve;
  const h=harness(url => url==='/api/data-agents' ? Promise.resolve([]) : new Promise(r=>resolve=r));
  const pending=h.controller.load('old');
  assert.equal(h.controller.ready(),false);
  await h.controller.load(null);
  resolve({agent_id:'old',chat_id:12}); await pending;
  assert.equal(h.root.hidden,true); assert.equal(h.select.value,'');
});

function backendHarness(api) {
  const node = () => ({value:'', events:{}, options:[], addEventListener(k,fn){this.events[k]=fn}, replaceChildren(){this.options=[];this.value=''}, add(option){this.options.push(option)}});
  const root=node(), select=node(), reset=node(), status=node(), backend=node(), agentLabel=node(), manage=node();
  backend.options=[{value:'sqlbot'},{value:'mcp'}];
  global.Option=function(text,value){this.text=text;this.value=value};
  const errors=[];
  let controller;
  controller=createDataAgentContext({root,select,reset,status,backend,agentLabel,manage,api,
    changed(){if(controller) controller.setRunning(false)},error(e){errors.push(e)}});
  return {controller,select,reset,status,backend,agentLabel,manage,errors};
}
test('MCP restores without fetching SQLBot assistants and stays ready with no agent', async () => {
  const calls=[];
  const h=backendHarness(async url => {calls.push(url);return {backend:'mcp',agent_id:null,chat_id:null,mcp_available:true}});
  await h.controller.load('mcp-session');
  assert.equal(h.controller.ready(),true);
  assert.equal(h.select.hidden,true);
  assert.equal(h.manage.hidden,true);
  assert.equal(h.backend.value,'mcp');
  assert.deepEqual(calls,['/api/sessions/mcp-session/data-agent']);
  h.controller.setRunning(true);
  assert.equal(h.backend.disabled,true);
  assert.equal(h.reset.disabled,true);
});
test('switch to MCP persists selection and failed switch restores previous mode', async () => {
  const calls=[];let fail=false;
  const h=backendHarness(async (url,options) => {
    calls.push([url,options]);
    if(url==='/api/data-agents')return [];
    if(options && fail) throw new Error('MCP unavailable');
    return {backend:options?'mcp':'sqlbot',agent_id:null,chat_id:null,mcp_available:true};
  });
  await h.controller.load('session');
  assert.equal(h.controller.ready(),false);
  h.backend.value='mcp';h.backend.events.change();await flush();
  assert.equal(JSON.parse(calls[2][1].body).backend,'mcp');
  assert.equal(h.controller.ready(),true);
  fail=true;
  h.backend.value='sqlbot';h.backend.events.change();await flush();
  assert.equal(h.backend.value,'mcp');
  assert.equal(h.errors.length,1);
});
