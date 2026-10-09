const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync('app/web/static/app.js','utf8');
function fn(name,next) {return source.slice(source.indexOf(`function ${name}(`),source.indexOf(`function ${next}(`));}

test('historical terminal events retain server running state and do not close live execution',()=>{
  let finishes=0;
  const ctx={state:{seenEvents:new Set(),running:true,session:{status:'running'}},
    elements:{executionPanel:{}},removeConversationEmpty(){},scrollTimeline(){},
    appendSystemEvent(){},finishTurn(){finishes++;},userFacingUI:{formatUserFacingError:()=>''}};
  vm.runInNewContext(fn('handleEvent','appendUserMessage'),ctx);
  for(const type of ['turn.completed','turn.failed','turn.cancelled','turn.interrupted']) {
    ctx.handleEvent(type,{}, {history:true,occurredAt:'2026-10-09T01:00:00Z'});
    assert.equal(ctx.state.turnEndedAt,Date.parse('2026-10-09T01:00:00Z'));
  }
  assert.equal(finishes,0);assert.equal(ctx.state.session.status,'running');
  ctx.handleEvent('turn.completed',{}, {history:false});assert.equal(finishes,1);
});

test('session selection preserves replayed steps and reconnects an active turn',async()=>{
  for(const status of ['idle','running']) {
    let steps=[],connected=null;
    const state={session:null,sessions:[{id:'s',status}],workspace:{id:'data-question'},
      skillCandidates:{selectSession(){}},sessionSelectionGeneration:0,seenEvents:new Set(),
      streamMessages:new Map(),toolElements:new Map(),timelineToolElements:new Map()};
    const history=[{turn_id:'old',sequence:1,event_type:'tool.completed',payload:{}},
      {turn_id:'active',sequence:2,event_type:'turn.progress',payload:{}}];
    const ctx={state,dataAgentContext:{load(){}},SessionInspector:{createToolTimelineState:()=>new Map()},
      closeEventSource(){},closeSidebar(){},clearConversation(){steps=[];},renderSessions(){},renderAttachmentTray(){},
      updateSessionHeader(){},setRunning(){},loadPendingAttachments:async()=>{},api:async()=>history,
      handleEvent(type){steps.push(type);},connectEvents(id){connected=id;},resetExecutionPanel(){steps=[];},
      updateExecutionPanel(){},scrollTimeline(){},showError(e){throw e;}};
    vm.runInNewContext('async '+fn('selectSession','loadPendingAttachments').replace(/async\s*$/,''),ctx);
    await ctx.selectSession('s');
    assert.deepEqual(steps,['tool.completed','turn.progress']);
    assert.equal(connected,status==='running'?'active':null);
  }
});
