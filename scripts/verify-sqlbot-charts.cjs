// Compare the shipped Host chart classes against the actual SQLBot browser bundle.
const fs=require('node:fs'), path=require('node:path'), vm=require('node:vm'), assert=require('node:assert/strict');
const crypto=require('node:crypto'), acorn=require('acorn'), esbuild=require('esbuild');
const root=path.resolve(__dirname,'..'), vendor=path.join(root,'web/sqlbot-chart/vendor');
const input=process.argv[2];
if(!input) throw Error('Usage: node scripts/verify-sqlbot-charts.cjs <deployed SQLBot index.js>');
const native=fs.readFileSync(input,'utf8');
const manifest=JSON.parse(fs.readFileSync(path.join(vendor,'SOURCE.json')));
assert.equal(crypto.createHash('sha256').update(native).digest('hex'),manifest.deployedBundleSha256,'SQLBot version changed: repin and review the chart renderer');
const declarations=new Map();
for(const node of acorn.parse(native,{ecmaVersion:'latest',sourceType:'module'}).body){
 if(node.id)declarations.set(node.id.name,native.slice(node.start,node.end));
 if(node.type==='VariableDeclaration') for(const d of node.declarations) if(d.id.type==='Identifier') declarations.set(d.id.name,`const ${native.slice(d.start,d.end)};`);
}
class Chart {constructor(){this.spec={};} theme(){} options(spec){if(spec)this.spec=spec;return this.spec;} render(){} destroy(){}}
class TableSheet {constructor(container,data,options){this.data=data;this.options=options;}on(){}getCanvasElement(){return {addEventListener(){}};}render(){}destroy(){}changeSheetSize(){}}
const document={getElementById:()=>({parentElement:{}}),createElement:()=>({style:{},children:[],appendChild(n){this.children.push(n)}}),createTextNode:text=>({text})};
const ResizeObserver=class{observe(){}disconnect(){}};
const shared={console:{debug(){}},document,ResizeObserver};
const ctx=vm.createContext({...shared,vo:(a,b,c)=>{a[b]=c},v1n:Chart,JEn:TableSheet,
 cL:(rows,fn)=>rows.filter(fn), d3:(a,b)=>a.endsWith(b),Mvt:(a,b,c)=>a.replace(b,c),d8e:(a,k)=>a.some(x=>x[k]),
 BO:(a,k)=>a.includes(k),pA:fn=>fn,en:{RANGE_SORT:'sort',GLOBAL_COPIED:'copy',GLOBAL_CONTEXT_MENU:'context'},
 L5e:x=>x,$t:{success(){}},nCn(){}});
const names=['uRe','oce','cp','sce','kke','ice','b1n','y1n','E1n','eCn','tCn','rCn'];
vm.runInContext(names.map(name=>{assert.ok(declarations.has(name),name);return declarations.get(name)}).join('\n')+';globalThis.classes={bar:b1n,column:y1n,line:E1n,pie:rCn,table:tCn};',ctx);
async function main(){
 const built=await esbuild.build({stdin:{contents:`export {Bar as bar} from './charts/Bar.ts';export {Column as column} from './charts/Column.ts';export {Line as line} from './charts/Line.ts';export {Pie as pie} from './charts/Pie.ts';export {Table as table} from './charts/Table.ts';`,resolveDir:vendor},bundle:true,write:false,platform:'node',format:'cjs',logLevel:'silent',
 alias:{'@/views/chat/component':vendor,'@/i18n':path.join(root,'web/sqlbot-chart/ui-adapter.js')},inject:[path.join(root,'web/sqlbot-chart/ui-adapter.js')],
 plugins:[{name:'engines',setup(b){b.onResolve({filter:/^@antv\/(g2|s2)(\/.*)?$/},args=>({path:args.path,namespace:'engine'}));b.onLoad({filter:/.*/,namespace:'engine'},args=>({contents:args.path.endsWith('.css')?'':args.path==='@antv/g2'?'export const Chart=globalThis.Chart;':'export const TableSheet=globalThis.TableSheet;export const S2Event={RANGE_SORT:"sort",GLOBAL_COPIED:"copy",GLOBAL_CONTEXT_MENU:"context"};export const copyToClipboard=()=>Promise.resolve();',loader:'js'}));}}]});
 const host=vm.createContext({...shared,Chart,TableSheet,module:{exports:{}},setTimeout,clearTimeout});
 vm.runInContext(built.outputFiles[0].text,host);
 const classes=host.module.exports;
 const samples=[{category:'A',amount:1200,other:-30,region:'正常'}, {category:'B',amount:-25,other:0,region:'异常'}, {category:'C',amount:null,other:2,region:'正常'}];
 const axis=[{name:'质检项',value:'category',type:'x'},{name:'数量',value:'amount',type:'y',formatNumber:true},{name:'结果',value:'region',type:'series'}];
 function normalize(value,rows,key=''){
  if(typeof value==='function') return ['fn',...rows.concat([1000,-12,'1234.50',null]).map(row=>{try{return normalize(value(row),[],key)}catch{return 'throws'}})];
  if(Array.isArray(value)) return value.map(v=>normalize(v,rows,key));
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,normalize(value[k],rows,k)]));
  return value;
 }
 let count=0;
 for(const type of ['column','bar','line','pie','table']) for(const showLabel of [false,true]) for(const variant of ['standard','percent','multi']){
  const rows=variant==='percent'?samples.map(r=>({...r,amount:'12.50%'})):samples;
  const axes=variant==='multi'?[axis[0],{...axis[1],'multi-quota':true},{name:'其他',value:'other',type:'y','multi-quota':true},{name:'指标',value:'指标',type:'other-info',hidden:true}]:axis;
  const left=new classes[type]('test'),right=new ctx.classes[type]('test');left.showLabel=right.showLabel=showLabel;
  left.init(axes,rows,['amount']);right.init(axes,rows,['amount']);
  const l=type==='table'?{data:left.table.data,options:left.table.options}:left.chart.options();
  const r=type==='table'?{data:right.table.data,options:right.table.options}:right.chart.options();
  assert.deepEqual(JSON.parse(JSON.stringify(normalize(l,rows))),JSON.parse(JSON.stringify(normalize(r,rows))),`${type}/${showLabel}/${variant}`);
  left.destroy();right.destroy();count++;
 }
 console.log(JSON.stringify({status:'passed',cases:count,nativeBundleSha256:manifest.deployedBundleSha256,types:['column','bar','line','pie','table']}));
}
main().catch(error=>{console.error(error);process.exit(1)});
