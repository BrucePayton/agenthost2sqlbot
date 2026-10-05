// Exercise the production metric renderer, measurement and layout controller.
const fs = require('node:fs/promises')
const path = require('node:path')
const esbuild = require('esbuild')

async function main() {
  const [davinciRoot, outfile, entryFile] = process.argv.slice(2)
  const webapp = path.join(davinciRoot, 'webapp')
  const less = require(path.join(webapp, 'node_modules/less'))
  const modulePath = relative => JSON.stringify(path.join(webapp, relative))
  const entry = `
    import React, {useState} from 'react';
    import ReactDOM from 'react-dom';
    import MetricWidget from ${modulePath('app/components/DashboardWidgets/MetricWidget/index.tsx')};
    import Chart from ${modulePath('app/components/DashboardWidgets/Chart/index.tsx')};
    import LayoutWidget from ${modulePath('app/components/DashboardWidgets/LayoutWidget/index.tsx')};
    import {TagLibraryContext} from ${modulePath('app/components/DashboardPanel/TagLibraryContext.tsx')};
    import {DashboardLayoutController} from ${modulePath('share/containers/WorkBenchNew/DashboardV2/agent/edit/DashboardLayoutController.ts')};
    import {hasCompleteMetricText} from ${modulePath('share/containers/WorkBenchNew/DashboardV2/agent/edit/metricCompactProbe.ts')};
    let revision = 1, dataRevision = 1, saves = 0, setReady, setWidget;
    let widget = {id:'metric',name:'留的QQ',type:'metric',x:0,y:0,w:12,h:8,order:0,
      config:{chartType:2001,cardUid:'metric-uid',filterList:[],datasets:[{datasetUid:'retained-dataset'}],
        format:{nullable:null},scorecardTitle:'留的QQ',scorecardFontSize:'auto',
        customTags:{tagIds:[159],position:'belowTitle'}}};
    const tags = {tags:[{id:159,name:'标签333',color:'#c2410c'},
      {id:160,name:'本月累计',color:'#047857'},{id:161,name:'已审核',color:'#0369a1'},
      {id:162,name:'重点客户',color:'#a21caf'}],ready:true};
    const multi = window.metricScenario === 'multi';
    const metricData = [
      {title:'营业收入',value:38585,tags:[160],comparisons:[{description:'环比',calcType:'diffRate',value:0.72}]},
      {title:'本季度累计成交金额',value:146892,tags:[161,162],comparisons:[
        {description:'同比',calcType:'diffRate',value:18.6},{description:'环比',calcType:'diffRate',value:-2.4}]},
      {title:'新增客户',value:924,tags:[162],comparisons:[]},
      {title:'有效订单',value:628,tags:[160,161],comparisons:[{description:'较上期',calcType:'diff',value:36}]}
    ];
    let widgets = [widget];
    if (multi) {
      widgets = metricData.map((data,index)=>({id:'metric-'+index,name:data.title,type:'metric',
        x:index*6,y:0,w:6,h:8,order:index,data:{value:data.value,comparisons:data.comparisons},
        datasetSnapshot:{value:data.value,nullable:null},roles:[7],tagIds:data.tags,
        config:{chartType:2001,cardUid:'metric-uid-'+index,filterList:[],rootLayoutMode:'rows',
          datasets:[{datasetUid:'retained-dataset-'+index,nullable:null}],format:{nullable:null},
          scorecardTitle:data.title,scorecardFontSize:'auto',
          customTags:{tagIds:data.tags,position:index===2?'titleRight':'belowTitle'}}}));
      widgets.push({id:'wide-chart',name:'月度成交趋势',type:'line',x:0,y:8,w:24,h:10,order:4,
        config:{chartType:4001,cardUid:'wide-chart-uid',rootLayoutMode:'rows',filterList:[],
          datasets:[{datasetUid:'chart-dataset'}],cols:[{key:'month',name:'月份'}],
          metrics:[{key:'revenue',name:'成交金额'}],xAxes:{label:{visible:true}},
          yAxesLeft:{label:{visible:true}},format:{nullable:null}},
        data:[{month:'一月',revenue:120},{month:'二月',revenue:180},{month:'三月',revenue:145},
          {month:'四月',revenue:260},{month:'五月',revenue:220},{month:'六月',revenue:310}]});
    }
    function App() {
      const [ready, updateReady] = useState(false);
      const [items, updateWidget] = useState(widgets);
      setReady = updateReady; setWidget = updateWidget;
      if (multi) return <TagLibraryContext.Provider value={tags}>
        <div data-testid="dashboard-v2-canvas" style={{position:'relative',width:'calc(100vw - 16px)',
          height:Math.max(...items.map(w=>w.y+w.h))*40}}>
          {items.map(current=><div key={current.id} data-widget-card
            data-testid={'dashboard-v2-widget-card-'+current.id} data-content-measure-ready={ready}
            style={{position:'absolute',left:current.x*(window.innerWidth-16+10)/24,top:current.y*40,
              width:current.w*(window.innerWidth-16+10)/24-10,height:current.h*40-10,
              background:'white',paddingTop:32,boxSizing:'border-box',overflow:'hidden'}}>
            {current.type==='metric'?<MetricWidget widget={current} value={current.data.value} isPending={!ready}
              contrastResults={current.data.comparisons.map((c,index)=>({...c,id:'contrast-'+index,loading:!ready}))}/>
              :<><header style={{position:'absolute',top:6,left:16,fontSize:16}}>{current.name}</header>
                <Chart config={current.config} data={current.data} isPending={!ready}/></>}
          </div>)}
        </div>
      </TagLibraryContext.Provider>;
      const current = items.find(w => w.id === 'metric');
      const group = items.find(w => w.type === 'flatLayout');
      const width = current.w * (window.innerWidth - 16 + 10) / 24 - 10;
      const metric = (inner) => <div data-widget-card data-testid="dashboard-v2-widget-card-metric"
          data-content-measure-ready={ready}
          style={{width:inner?'100%':width,height:inner?'100%':current.h*40-10,background:'white',paddingTop:32,boxSizing:'border-box'}}>
          <TagLibraryContext.Provider value={tags}>
            <MetricWidget widget={current} value={38585} isPending={!ready}
              contrastResults={[{id:'contrast',description:'',calcType:'diffRate',value:0.72,loading:!ready}]}/>
          </TagLibraryContext.Provider>
        </div>;
      return <div data-testid="dashboard-v2-canvas" style={{width:'calc(100vw - 16px)'}}>
        {group ? <section key={group.id} data-testid="group-frame" style={{width:group.w*(window.innerWidth-16+10)/24-10,
          height:group.h*40-10,background:'#eef5fb',boxSizing:'border-box'}}>
          <header style={{height:50,padding:'0 16px',display:'flex',alignItems:'center',fontWeight:600}}>{group.config.title}</header>
          <div style={{height:'calc(100% - 50px)'}}><LayoutWidget chartType={19001} layoutWidget={group}
            allWidgets={items} readonly renderChild={()=>metric(true)}/></div>
        </section> : metric(false)}
      </div>;
    }
    ReactDOM.render(<App/>, document.getElementById('root'));
    const controller = new DashboardLayoutController({
      getSnapshot:()=>({widgets,pageState:{schemaVersion:'davinci-page-state-v1',
        page:{kind:'dashboard',instanceId:'metric-fixture',route:'/',resource:{type:'dashboard',id:'fixture'}},
        revisions:{resourceRevision:revision,dataRevision,routeRevision:1},
        permissions:{canRead:true,canOperate:true,canPersist:true},ui:{busy:false,activeFilters:[]},
        dataStatus:{loadingWidgetIds:[],errorWidgetIds:[]}}}),
      setMeasurementActive:active=>{if(active) setTimeout(()=>{dataRevision++;setReady(true)},100)},
      persist:async(before,next,guard)=>{guard?.();saves++;revision++;widgets=next;widget=next[0];setWidget(next);return next},
      persistGroups:async(before,next,guard)=>{guard();saves++;revision++;widgets=next.map(w=>w._isNew?{...w,id:'saved-group',_isNew:undefined}:w);
        widget=widgets.find(w=>w.id==='metric');setWidget(widgets);return widgets},
      getResourceRevision:()=>revision,now:()=>new Date().toISOString()
    });
    // Settle the real renderers before screenshots or content-size assertions.
    const settle = async()=>{
      await document.fonts.ready;
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      await new Promise(resolve=>setTimeout(resolve,300));
    };
    window.prepareMetricRow = async()=>{setReady(true);await settle()};
    window.runMetricRow = async()=>{
      const before=JSON.parse(JSON.stringify(widgets));
      // Keep the chart explicitly full-width so it cannot consume a leftover metric-row gap.
      const result=await controller.apply({preset:{mode:'compact',sizing:'content',
        typeSizes:[{chartType:4001,width:24,height:10}]}},
        {signal:new AbortController().signal,expiresAt:Date.now()+30000});
      await settle();
      const rect=element=>{
        const r=element.getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};
      };
      const metrics=widgets.filter(w=>w.type==='metric').map(w=>{
        const card=document.querySelector('[data-testid="dashboard-v2-widget-card-'+w.id+'"]');
        return {id:w.id,pixels:rect(card),complete:hasCompleteMetricText(card),text:card.innerText};
      });
      const chart=document.querySelector('[data-testid="dashboard-v2-widget-card-wide-chart"]');
      return {result,saves,before,widgets,metrics,chart:rect(chart),
        canvas:rect(document.querySelector('[data-testid="dashboard-v2-canvas"]'))};
    };
    // Render smaller common heights independently; never call the controller or persistence again.
    window.checkSmallerMetricRows = async()=>{
      const heights=[];
      const metrics=widgets.filter(w=>w.type==='metric');
      try {
        for(let height=1;height<Math.max(...metrics.map(w=>w.h));height++) {
          setWidget(widgets.map(w=>w.type==='metric'?{...w,w:6,h:height}:w));
          await settle();
          heights.push({height,complete:metrics.map(w=>hasCompleteMetricText(
            document.querySelector('[data-testid="dashboard-v2-widget-card-'+w.id+'"]')))});
        }
      } finally {setWidget(widgets);await settle()}
      return {heights,saves};
    };
    window.runLayout = async(grouped=false)=>{
      if(grouped==='regroup') {
        widget={...widget,parentId:'old-group-uid',config:{...widget.config,
          layoutParentId:'old-group-uid',rootGridSize:{w:6,h:5}}};
        widgets=[{id:'old-group',name:'旧分组',type:'flatLayout',x:0,y:0,w:12,h:12,order:0,
          config:{chartType:19001,cardUid:'old-group-uid',title:'旧分组',filterList:[]}},widget];
        setWidget(widgets);
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      }
      const before=JSON.parse(JSON.stringify(widget));
      const result=await controller.apply(grouped?{expectedResourceRevision:1,preset:{mode:'reorder',sizing:'content',
        orderedWidgetIds:['metric'],groups:[{title:'整体汇总',widgetIds:['metric']}],groupingConfirmed:true,
        ...(grouped==='regroup'?{regroup:{containerWidgetIds:['old-group'],confirmed:true}}:{})}}:{preset:{mode:'compact',sizing:'content'}},
        {signal:new AbortController().signal,expiresAt:Date.now()+30000});
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      await new Promise(resolve=>setTimeout(resolve,300));
      const metric=document.querySelector('[data-metric-ready]');
      const rect=metric.getBoundingClientRect();
      const frame=document.querySelector('[data-testid="group-frame"]');
      return {result,saves,before,widgets,widget,complete:hasCompleteMetricText(metric),
        frameComplete:!frame||hasCompleteMetricText(frame),text:metric.innerText,pixels:{width:rect.width,height:rect.height}};
    };
  `
  await esbuild.build({
    stdin: { contents: entryFile ? await fs.readFile(entryFile, 'utf8') : entry,
      loader: 'tsx', resolveDir: webapp }, outfile,
    bundle: true, platform: 'browser', define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'fixture-styles', setup(build) {
      if (entryFile) {
        // Region fixtures supply local data; an unexpected backend call must fail, never go live.
        build.onResolve({ filter: /^utils\/request$/ }, () => ({ path: 'request', namespace: 'region-offline' }))
        build.onLoad({ filter: /.*/, namespace: 'region-offline' }, () => ({
          contents: 'export default function request(){throw new Error("Region fixture attempted a backend request")}'
        }))
      }
      build.onLoad({filter:/\.css$/},async args=>({loader:'js',contents:'const s=document.createElement("style");s.textContent='+JSON.stringify(await fs.readFile(args.path,'utf8'))+';document.head.append(s);'}))
      build.onResolve({ filter: /^utils\/util$/ }, () => ({ path: 'ids', namespace: 'fixture' }))
      build.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({
        contents: 'let id=0;export const uuid=()=>"fixture-id-"+(++id);export const isMobileDevice=()=>false' +
          (entryFile ? ';export const getParentUrl=()=>location.href;export const sensor=()=>({track:(...args)=>{(window.regionTelemetry??=[]).push(args)}})' : '')
      }))
      build.onResolve({ filter: /^(@share|components|utils|containers)\// }, async args => {
        const base = args.path.startsWith('@share/') ? path.join(webapp, args.path.replace('@share/', 'share/'))
          : path.join(webapp, 'app', args.path)
        return build.resolve(base, { kind: args.kind, resolveDir: args.resolveDir })
      })
      build.onLoad({ filter: /\.less$/ }, async args => {
        const source = await fs.readFile(args.path, 'utf8')
        let { css } = await less.render(source, { filename: args.path, javascriptEnabled: true })
        for (const match of [...css.matchAll(/url\(['"]?([^)'"\s]+)['"]?\)/g)]) {
          if (!/\.(otf|woff2?|ttf)$/.test(match[1])) continue
          const bytes = await fs.readFile(path.resolve(path.dirname(args.path), match[1]))
          css = css.replace(match[0], 'url(data:font/otf;base64,' + bytes.toString('base64') + ')')
        }
        const classes = Object.fromEntries([...source.matchAll(/\.([a-zA-Z][\w-]*)/g)].map(match => [match[1], match[1]]))
        return { loader: 'js', contents: 'const s=document.createElement("style");s.textContent=' + JSON.stringify(css) + ';document.head.append(s);export default ' + JSON.stringify(classes) }
      })
    }}]
  })
}

main().catch(error => { console.error(error); process.exitCode = 1 })
