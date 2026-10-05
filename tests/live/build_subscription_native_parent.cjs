// Real Davinci Controller/Handoff/Store. Only upstream resources, validation,
// and the mounted editor's navigation callback are isolated fixtures.
const fs = require('node:fs/promises');
const path = require('node:path');
const esbuild = require('esbuild');

async function main() {
  const [davinciRoot, outfile] = process.argv.slice(2);
  const webapp = path.join(davinciRoot, 'webapp');
  const root = path.join(webapp, 'share/containers/CollaborativeSpace');
  const fixturePath = process.env.SUBSCRIPTION_NATIVE_WIDGET_FIXTURE ||
    path.join(root, 'agent/__fixtures__/subscriptionWidgetComparison.json');
  const catalog = process.env.SUBSCRIPTION_NATIVE_CATALOG_FIXTURE
    ? JSON.parse(await fs.readFile(process.env.SUBSCRIPTION_NATIVE_CATALOG_FIXTURE, 'utf8'))
    : { datasets: [], fields: [], queryVars: [] };
  const entry = `
    import {SpaceMessageRuleAgentController} from ${JSON.stringify(path.join(root, 'agent/SpaceMessageRuleAgentController.ts'))};
    import {SpaceHomeAgentController,spaceAgentTargetPath} from ${JSON.stringify(path.join(root, 'agent/SpaceHomeAgentController.ts'))};
    import {createSubscriptionAgentDraftHandoff} from ${JSON.stringify(path.join(root, 'agent/subscriptionAgentDraftHandoff.ts'))};
    import {createPersonalMessageRuleScope,createSpaceMessageRuleScope} from ${JSON.stringify(path.join(root, 'agent/messageRuleAgentScope.ts'))};
    import {createSubscriptionDraftStore} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/draft/SubscriptionDraftStore.ts'))};
    import {createSubscriptionDraftPort} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/draft/SubscriptionDraftPort.ts'))};
    import {getConditionFieldKey} from ${JSON.stringify(path.join(root, 'pages/SubscriptionConfigDetail/SubscriptionConfigNodes/ConditionsConfigNode/utils.ts'))};
    import {TOOL_CONTRACTS, CONTRACT_DIGEST} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/contracts/generated-v2.ts'))};
    import {validateSchema} from ${JSON.stringify(path.join(webapp, 'share/containers/WorkBenchNew/agent/runtime/FrontendToolRegistry.ts'))};
    import fixture from ${JSON.stringify(fixturePath)};
    const catalog = ${JSON.stringify(catalog)};
    const page = {
      schemaVersion:'davinci-page-state-v1',
      page:{instanceId:'widget-continuation-page',kind:'other',workflow:'subscription',route:'/share/workbench-new/subscription'},
      permissions:{canRead:true,canOperate:true,canPersist:true},
      ui:{busy:false,activeFilters:[]},revisions:{routeRevision:1,resourceRevision:1},
      dataStatus:{loadingWidgetIds:[],errorWidgetIds:[]}
    };
    const spaces=(catalog.spaces||(catalog.space?[catalog.space]:[])).map(space=>({
      type:'organization',scope:'organization',role:'owner',status:'ACTIVE',membershipStatus:'joined',
      spaceUid:'space-'+space.id,...space}));
    let currentSpace=catalog.startInSpace?spaces.find(space=>space.id===catalog.startInSpace):undefined;
    if(currentSpace){
      page.page={...page.page,kind:'collaborative-space',route:spaceAgentTargetPath({spaceId:currentSpace.id,spaceUid:currentSpace.spaceUid,destination:'subscription'}),
        space:{...currentSpace,role:'owner'},viewMode:'self'};
      page.permissions.spaceCapabilities={canManage:true};
    }
    const store=createSubscriptionDraftStore();
    const port=createSubscriptionDraftPort(store);
    const handoff=createSubscriptionAgentDraftHandoff();
    handoff.publishPort(port);
    window.nativeEvents=[];
    port.attachNavigator(step=>{window.nativeEvents.push({type:'navigate',step});return true});
    const forbidden=()=>{throw new Error('Business query, preview, save or upstream access forbidden in isolated acceptance')};
    const execution={
      captureExecutionScope:()=>({ownerId:currentSpace?.id||'personal',generation:1,pageInstanceId:page.page.instanceId,routeRevision:page.revisions.routeRevision}),
      assertExecutionScope:scope=>{if(scope.routeRevision!==page.revisions.routeRevision)throw new Error('STALE_CONTEXT')},
      waitForNavigationAck:async()=>({contextVersion:page.revisions.routeRevision,page})
    };
    const openSpace=async target=>{
      currentSpace=spaces.find(space=>space.id===target.spaceId&&space.spaceUid===target.spaceUid);
      if(!currentSpace)throw new Error('Unknown native space target');
      page.page={...page.page,kind:'collaborative-space',route:spaceAgentTargetPath(target),space:{...currentSpace},viewMode:'self'};
      page.permissions.spaceCapabilities={canManage:['owner','admin'].includes(currentSpace.role)};
      page.revisions.routeRevision+=1;
      window.nativeEvents.push({type:'space-navigation',spaceName:currentSpace.name,destination:target.destination});
      return {contextVersion:page.revisions.routeRevision,page};
    };
    const spaceController=new SpaceHomeAgentController({
      getPageState:()=>page,getActorObId:()=> '900001',execution,openSpace,openCreatedSpace:forbidden,
      api:{fetchSpacePage:async params=>({list:spaces.slice((params.pageNo-1)*params.pageSize,params.pageNo*params.pageSize),
        total:catalog.spaceTotal??spaces.length,pageNo:params.pageNo,pageSize:params.pageSize}),
        fetchSpaceDetail:async uid=>{const space=spaces.find(item=>item.spaceUid===uid);if(!space)throw new Error('Unknown space');return space},
        fetchPendingSpaceInvites:async()=>[]}
    });
    const controller=new SpaceMessageRuleAgentController({
      getPageState:()=>page,getActorObId:()=> '900001',
      getScope:()=>currentSpace?createSpaceMessageRuleScope(currentSpace,true):createPersonalMessageRuleScope('900001'),draftHandoff:handoff,
      getMessagePageRoute:()=>page.page.route,matchesMessagePage:()=>true,
      execution,
      optionLoaders:{dashboards:async()=>({dashboards:catalog.dashboards||[fixture.dashboard]}),widgets:async()=>[fixture.widget],templates:async()=>[fixture.template],
        aiBanner:async()=>({aiEnabled:true,hasAiConfig:true}),
        datasets:async()=>catalog.datasets,fields:async(uid)=>({fields:catalog.fieldsByDataset?.[uid]||catalog.fields,queryVars:catalog.queryVars||[]}),
        enumValues:async()=>catalog.enumValues||[],members:async()=>catalog.members||[],groups:async()=>catalog.groups||[],tags:async()=>[]},
      validateDatasets:async datasets=>datasets.map(dataset=>({id:dataset.id,valid:true})),
      validateCreate:async()=>undefined,previewDataset:forbidden,createMessageRule:forbidden,confirmPersistentAction:forbidden,
      fetchMessageRuleList:async()=>({records:[],total:0})
    });
    const allowed=['space.list','space.get_context','space.open','space.message_rule.get_context','space.message_rule.search_options','space.message_rule.start_draft','space.message_rule.apply_draft','space.message_rule.review_draft'];
    window.nativeRuntime=()=>({state:page,context:controller.getContextItems(),contractDigest:CONTRACT_DIGEST,
      tools:TOOL_CONTRACTS.filter(tool=>allowed.includes(tool.action)).map(tool=>({name:tool.action,description:tool.description,parameters:tool.inputSchema}))});
    window.nativeSnapshot=()=>({taskId:port.getTaskId(),...port.read()});
    window.nativeValidate=(name,value,output=false)=>{
      const contract=TOOL_CONTRACTS.find(tool=>tool.action===name);
      if(!contract)throw new Error('Unknown native contract');
      return validateSchema(value,output?contract.outputSchema:contract.inputSchema);
    };
    window.nativeMetricKeys=()=>Object.fromEntries((port.read().state.datasets||[]).map(query=>[query.id,
      query.metrics.map(metric=>({key:getConditionFieldKey(metric),metric}))]));
    window.nativeExecute=async(name,args)=>{
      if(!allowed.includes(name)||args.includeDataCheck===true)forbidden();
      window.nativeEvents.push({type:'tool',name,args});
      try{return {status:'success',data:await (name.startsWith('space.message_rule.')?controller:spaceController).execute(name,args),issues:[]}}
      catch(error){return {status:'error',error:{code:error.code||'NATIVE_EXECUTION_ERROR',message:error.message,retryable:false,...(error.details?{details:error.details}:{})},issues:[]}}
    };
  `;
  await esbuild.build({stdin:{contents:entry,loader:'ts',resolveDir:webapp},outfile,bundle:true,platform:'browser',
    define:{'process.env.NODE_ENV':'"test"'},loader:{'.svg':'dataurl','.png':'dataurl','.gif':'dataurl','.jpg':'dataurl'},
    plugins:[{name:'isolate-upstream',setup(build){
      build.onResolve({filter:/^utils\/request$/},()=>({path:'request',namespace:'fixture'}));
      build.onLoad({filter:/^request$/,namespace:'fixture'},()=>({contents:'export const getToken=()=>"fixture-token";export default function request(){throw new Error("Unexpected upstream request")}' }));
      build.onLoad({filter:/\.(less|css)$/},async args=>({loader:'js',contents:'export default '+JSON.stringify(Object.fromEntries([...(await fs.readFile(args.path,'utf8')).matchAll(/\.([a-zA-Z][\w-]*)/g)].map(match=>[match[1],match[1]])))}));
    }}]});
}
main().catch(error=>{process.stderr.write(String(error));process.exitCode=1});
