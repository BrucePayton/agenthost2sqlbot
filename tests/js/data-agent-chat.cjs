const {test}=require('node:test');
const assert=require('node:assert/strict');
const {consume}=require('../../app/web/static/data-agent-chat.js');
test('SSE preserves Chinese characters and frames split at every byte',async()=>{
 const bytes=new TextEncoder().encode(': heartbeat\r\n\r\ndata: {"type":"sql-result","content":"昨天收货"}\r\n\r\ndata: {"type":"done"}\n\n');
 let i=0;const response=new Response(new ReadableStream({pull(c){if(i===bytes.length)c.close();else c.enqueue(bytes.slice(i,++i));}}));
 const events=[];await consume(response,e=>events.push(e));assert.deepEqual(events,[{type:'sql-result',content:'昨天收货'},{type:'done'}]);
});
test('truncated stream is never reported as success',async()=>{
 await assert.rejects(consume(new Response('data: {"type":"sql"}\n\n'),()=>{}),/连接已中断/);
});
test('HTTP authorization error is exposed before parsing events',async()=>{
 await assert.rejects(consume(new Response(JSON.stringify({error:{message:'无权访问'}}),{status:403}),()=>{}),/无权访问/);
});
const {chartSpec}=require('../../app/web/static/data-agent-chat.js');
test('SQLBot object axes and multiple measures are rendered without silently dropping a measure',()=>{
 const s=chartSpec({type:'column',axis:{x:{name:'日期',value:'day'},y:[{value:'a'},{value:'b'}]}},['day','a','b'],[{day:'2026-09-29',a:10,b:20}]);
 assert.equal(s.encode.x,'day');assert.equal(s.data.length,2);assert.deepEqual(s.data.map(r=>r.__value),[10,20]);assert.equal(s.encode.color,'__metric');
});
// Component unit test uses an in-memory DOM, never a real browser or UI automation.
test('question view renders SQLBot trace, metrics and table without replacing prior turns',async()=>{
 const vm=require('node:vm'),fs=require('node:fs');
 class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.classList={add(){}};this.value='测试问题';this.textContent='';}
  append(...nodes){this.children.push(...nodes);} replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){} focus(){} scrollIntoView(){} remove(){}
  querySelector(tag){return this.children.find(c=>c.tag===tag);}
  createTHead(){const x=new Element('thead');x.insertRow=()=>{const row=new Element('tr');x.append(row);return row;};this.append(x);return x;}
  createTBody(){return this.createTHead();}
 }
 const ids=new Map(['questionPanel','questionTitle','questionResult','askButton','questionForm','question'].map(k=>[k,new Element('div')]));
 const doc={getElementById:k=>ids.get(k),createElement:tag=>new Element(tag)};
 const result={resultId:'r',recordId:1,columns:['次数'],rows:[{'次数':5}],rowCount:1,sql:'SELECT COUNT(*)',chartHint:{type:'table'},evidence:{datasetRefs:['rpt']},fieldsUsed:['次数'],presentation:{execution:{duration:1,total_tokens:42,steps:[{operate:'EXECUTE_SQL',duration:1,message:{count:1}}]}}};
 const events=[{type:'sql-result',content:'生成说明'},{type:'sql',content:result.sql},{type:'result',result},{type:'done'}];
 const context={window:{document:doc},document:doc,TextDecoder,AbortController,Response,ReadableStream,Blob,URL,setTimeout,clearTimeout,setInterval,clearInterval,console,fetch:async()=>new Response(events.map(e=>'data: '+JSON.stringify(e)+'\n\n').join(''))};
 vm.runInNewContext(fs.readFileSync(require.resolve('../../app/web/static/data-agent-chat.js'),'utf8'),context);
 context.window.DataAgentChat.open({id:'a',name:'RPT助手'},{session_id:'s'});
 await ids.get('questionForm').onsubmit({preventDefault(){}});
 await ids.get('questionForm').onsubmit({preventDefault(){}});
 const walk=n=>[n.textContent,...n.children.flatMap(walk)];
 const rendered=walk(ids.get('questionResult')).join('\n');
 assert.equal(ids.get('questionResult').children.length,2);assert.match(rendered,/执行明细 · 1 秒 · 42 Tokens/);assert.match(rendered,/生成的 SQL/);assert.match(rendered,/数据分析/);assert.match(rendered,/完成 · 执行 SQL/);assert.equal(ids.get('askButton').disabled,false);
});
