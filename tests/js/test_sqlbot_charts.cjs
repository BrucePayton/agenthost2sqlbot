const {test}=require('node:test');
const assert=require('node:assert/strict');
const esbuild=require('esbuild');
const vm=require('node:vm');
const built=esbuild.buildSync({entryPoints:['web/sqlbot-chart/contract.js'],bundle:true,write:false,format:'cjs',platform:'node'});
const sandbox={module:{exports:{}}};vm.runInNewContext(built.outputFiles[0].text,sandbox);
const {receiptIds,chartAxes}=sandbox.module.exports;
const plain=x=>JSON.parse(JSON.stringify(x));
test('SDK nested and truncated receipts resolve chart result identifiers',()=>{
 const result={resultId:'result-1',agentId:'agent-1',rows:Array.from({length:200},()=>({value:'data'}))};
 const wire=JSON.stringify([{type:'text',text:JSON.stringify(result)}]);
 for(const payload of [result,JSON.stringify(result),wire,wire.slice(0,140)])assert.deepEqual(plain(receiptIds(payload)),{resultId:'result-1',agentId:'agent-1'});
 assert.equal(receiptIds('unrelated message'),null);
 assert.equal(receiptIds(undefined),null);
 assert.equal(receiptIds(null),null);
 assert.equal(receiptIds({resultId:'../secret',agentId:'a'}),null);
});
test('SQLBot multi-quota axes preserve labels, series and formatting',()=>{
 const config={type:'column',axis:{x:{name:'质检项',value:'item'},y:[{name:'正常',value:'ok'},{name:'异常',value:'bad'}],'multi-quota':{name:'检测结果',value:['ok','bad']}}};
 const axes=plain(chartAxes(config,['ok']));
 assert.deepEqual(axes.map(a=>a.type),['x','y','y','other-info']);
 assert.equal(axes[1]['multi-quota'],true); assert.equal(axes[1].formatNumber,true);
 assert.equal(axes[2].name,'异常');assert.equal(axes[3].hidden,true);
 assert.equal(config.axis.y[0]['multi-quota'],undefined);
});
test('SQLBot pie uses series and y rather than inventing an x axis',()=>{
 const axes=plain(chartAxes({type:'pie',axis:{y:{name:'数量',value:'count'},series:{name:'结果',value:'status'}}}));
 assert.deepEqual(axes.map(a=>a.type),['y','series']);
});
