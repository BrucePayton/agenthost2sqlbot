// Real parent bridge, subscription controller/store and navigation shell.
// Only unrelated editor leaf controls and upstream authentication are fixtures.
const fs = require('node:fs/promises');
const path = require('node:path');
const esbuild = require('esbuild');

async function main() {
  const [davinciRoot, outfile] = process.argv.slice(2);
  const webapp = path.join(davinciRoot, 'webapp');
  const root = path.join(webapp, 'share/containers/CollaborativeSpace');
  const less = require(path.join(webapp, 'node_modules/less'));
  const entry = `
    import React, {useCallback, useRef} from 'react';
    import ReactDOM from 'react-dom';
    import AgentLauncher from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/launcher/index.tsx'))};
    import {createParentBridgeV2} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/bridge/createParentBridgeV2.ts'))};
    import {TOOL_CONTRACTS, CONTRACT_DIGEST, CONTRACT_VERSION, PROTOCOL_VERSION} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/contracts/generated-v2.ts'))};
    import {SpaceMessageRuleAgentController} from ${JSON.stringify(path.join(root, 'agent/SpaceMessageRuleAgentController.ts'))};
    import {createSubscriptionAgentDraftHandoff} from ${JSON.stringify(path.join(root, 'agent/subscriptionAgentDraftHandoff.ts'))};
    import {createSubscriptionDraftStore} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/draft/SubscriptionDraftStore.ts'))};
    import {createSubscriptionDraftPort} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/draft/SubscriptionDraftPort.ts'))};
    import {createSubscriptionDraftPolicy} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/draft/SubscriptionDraftPolicy.ts'))};
    import SubscriptionConfigStep from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/SubscriptionConfigStep/SubscriptionConfigStep.tsx'))};
    import {useSubscriptionConfigStep} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/SubscriptionConfigContexts/SubscriptionConfigStepContext.ts'))};
    import PushModeConfigNode from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/SubscriptionConfigNodes/PushModeConfigNode/index.tsx'))};
    const store = createSubscriptionDraftStore();
    const port = createSubscriptionDraftPort(store);
    const handoff = createSubscriptionAgentDraftHandoff();
    handoff.publishPort(port);
    port.start({policy:createSubscriptionDraftPolicy({createMode:'blank',isPushPersonal:false,readOnly:false}),
      scene:'data-alert',source:'blank',state:{trigger:{frequency:'daily',dailyMode:'everyday',dailyTimes:['09:00'],sendType:'conditional',effectiveMode:'long'}}});
    window.stageEvents = []; window.toolEvents = []; window.lookupPending = false;
    window.subscriptionSnapshot = () => ({taskId:port.getTaskId(),revision:port.read().revision,state:port.read().state});
    const page = {...window.__runtimeContext.state, page:{...window.__runtimeContext.state.page,
      kind:'collaborative-space',workflow:'subscription',route:'/share/collaborative-space/1001/message',
      space:{id:'1001',role:'owner'},viewMode:'self'}};
    const controller = new SpaceMessageRuleAgentController({
      getPageState:()=>page, getActorObId:()=> 'fixture-user', draftHandoff:handoff,
      execution:{captureExecutionScope:()=>({ownerId:'collaborative-space',generation:1,pageInstanceId:page.page.instanceId,routeRevision:1,spaceId:'1001'}),
        assertExecutionScope:()=>{},waitForNavigationAck:async()=>{}},
      optionLoaders:{datasets:async()=> {window.lookupPending=true; await new Promise(resolve=>{window.releaseLookup=resolve}); window.lookupPending=false; return [];}}
    });
    const getRuntimeContext = () => ({...window.__runtimeContext, contractDigest:CONTRACT_DIGEST,
      context:controller.getContextItems(),state:page,tools:TOOL_CONTRACTS.filter(tool=>['space.message_rule.apply_draft','space.message_rule.search_options','space.message_rule.get_context'].includes(tool.action))
        .map(tool=>({name:tool.action,description:tool.description,parameters:tool.inputSchema}))});
    window.__contract={contractDigest:CONTRACT_DIGEST,contractVersion:CONTRACT_VERSION,protocolVersion:PROTOCOL_VERSION};
    function Probe() {
      const context=useSubscriptionConfigStep();
      return <div><strong id="native-step">{context.currentStep.key}</strong>
        <span id="native-time">{context.state.trigger?.dailyTimes?.join(',')}</span>
        <button id="manual-trigger" onClick={()=>context.goToStep('trigger')}>手动查看执行时间</button></div>;
    }
    // A real node must mount after the stage notification and before the
    // deferred tool; a probe alone cannot catch an effect changing revision.
    function PushModeStep() {
      return <><Probe/><PushModeConfigNode/></>;
    }
    function Parent() {
      const bridge=useRef(null);
      const onIframeElement=useCallback((frame,allowedOrigin)=>{
        bridge.current?.destroy(); if(!frame||!allowedOrigin)return;
        bridge.current=createParentBridgeV2({allowedOrigin,getIframeWindow:()=>frame.contentWindow,
          getRuntimeContext,subscribeRuntimeContext:()=>()=>{},
          onSubscriptionStage:stage=>{window.stageEvents.push(stage);return controller.presentSubscriptionStage(stage)},
          executeTool:async(command)=>{
            window.toolEvents.push({action:command.name,phase:'started'});
            if(command.name==='space.message_rule.apply_draft')await new Promise(resolve=>{window.releaseApply=resolve});
            try {const data=await controller.execute(command.name,command.args);
              window.toolEvents.push({action:command.name,phase:'completed'});return {status:'success',data,issues:[]};
            } catch(error){window.toolEvents.push({phase:'failed',message:String(error)});throw error;}
          }});
      },[]);
      return <><section style={{width:650,height:800}}><SubscriptionConfigStep type="data-alert" initialStep="finalize"
        draftStore={store} agentDraftPort={port} obId="fixture-user" spaceId="1001" ownerType={2}
        components={{trigger:Probe,datasets:Probe,conditions:Probe,pushMode:PushModeStep,receivers:Probe,content:Probe,finalize:Probe}} /></section>
        <AgentLauncher actorObId="fixture-user" open={true} size="small" onToggle={()=>{}} onIframeElement={onIframeElement}/></>;
    }
    ReactDOM.render(<Parent/>,document.getElementById('root'));
  `;
  await esbuild.build({stdin:{contents:entry,loader:'tsx',resolveDir:webapp},outfile,bundle:true,platform:'browser',
    define:{'process.env.NODE_ENV':'"test"'},loader:{'.svg':'dataurl','.png':'dataurl','.gif':'dataurl','.jpg':'dataurl'},
    plugins:[{name:'subscription-fixture-leaves',setup(build){
      build.onResolve({filter:/^utils\/request$/},()=>({path:'token',namespace:'fixture'}));
      build.onResolve({filter:/^\.\/StepNavigation$|^\.\/StepActions$|^\.\/CurrentConfigSummary$|^\.\/SendPreviewDrawer$/},args=>({path:path.basename(args.path),namespace:'fixture'}));
      build.onResolve({filter:/^\.\.\/SubscriptionConfigNodes$/},()=>({path:'nodes',namespace:'fixture'}));
      build.onResolve({filter:/^\.\.\/useOpenedRuleDatasetAccess$/},()=>({path:'dataset-access',namespace:'fixture'}));
      build.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:args.path==='token'
        ? 'export const getToken=()=>"fixture-token";export default function request(){throw new Error("Unexpected upstream request")}'
        : args.path==='nodes'?'export const DEFAULT_SUBSCRIPTION_CONFIG_NODE_COMPONENTS={}'
        : args.path==='dataset-access'?'export const useOpenedRuleDatasetAccess=()=>[]'
        : 'export const '+args.path+'=()=>null'}));
      build.onLoad({filter:/\.(less|css)$/},async args=>{
        const source=await fs.readFile(args.path,'utf8');
        const css=args.path.endsWith('.less')?(await less.render(source,{filename:args.path,javascriptEnabled:true})).css:source;
        const classes=Object.fromEntries([...source.matchAll(/\.([a-zA-Z][\w-]*)/g)].map(match=>[match[1],match[1]]));
        return {loader:'js',contents:'const style=document.createElement("style");style.textContent='+JSON.stringify(css)+';document.head.append(style);export default '+JSON.stringify(classes)};
      });
    }}]});
}
main().catch(error=>{process.stderr.write(String(error));process.exitCode=1});
