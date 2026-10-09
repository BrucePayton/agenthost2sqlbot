/* SQLBot question UI: no model HTML is trusted; all server content uses textContent. */
(function (root) {
  "use strict";
  const stageNames = {"datasource-result":"选择数据源", "sql-result":"生成 SQL", "chart-result":"生成图表", "analysis-result":"数据分析", "predict-result":"数据预测", recommended_question_result:"推荐问题"};
  const operationNames = {EXTRACT_KEYWORDS:"提取关键词", FILTER_TERMS:"检索业务术语", FILTER_SQL_EXAMPLE:"检索 SQL 示例", FILTER_CUSTOM_PROMPT:"匹配自定义提示词", CHOOSE_TABLE:"选择数据表", CHOOSE_DATASOURCE:"选择数据源", GENERATE_SQL:"生成 SQL", EXECUTE_SQL:"执行 SQL", GENERATE_CHART:"生成图表", GENERATE_PICTURE:"生成图片", ANALYSIS:"数据分析", PREDICT:"数据预测", RECOMMENDED_QUESTION:"推荐问题"};
  function parse(value) { if (typeof value !== "string") return value; try { return JSON.parse(value); } catch (_) { return value; } }
  function text(value) { return typeof value === "string" ? value : JSON.stringify(value, null, 2); }
  // Streaming TextDecoder and a frame buffer preserve UTF-8 and SSE split across network chunks.
  async function consume(response, receive) {
    if (!response.ok) { const e = await response.json(); throw new Error(e?.error?.message || `请求失败 (${response.status})`); }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", doneEvent = false;
    function frames(flush = false) {
      const chunks = buffer.split(/\r?\n\r?\n/); buffer = chunks.pop();
      if (flush && buffer) { chunks.push(buffer); buffer = ""; }
      for (const chunk of chunks) {
        const data = chunk.split(/\r?\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        const event = JSON.parse(data); if (event.type === "done") doneEvent = true; receive(event);
      }
    }
    try {
      while (true) { const {value, done} = await reader.read(); if (done) break; buffer += decoder.decode(value, {stream:true}); frames(); }
      buffer += decoder.decode(); frames(true);
      if (!doneEvent) throw new Error("连接已中断，当前回答可能不完整。请重新提问。");
    } finally { reader.releaseLock(); }
  }
  if (typeof module !== "undefined") module.exports = {consume, parse};
  if (!root.document) return;
  const $ = id => document.getElementById(id);
  const el = (tag, value, cls) => { const n = document.createElement(tag); if (value != null) n.textContent = String(value); if (cls) n.className = cls; return n; };
  function detail(title, value, open=false) { const d=el("details",null,"trace-detail"); d.open=open; d.append(el("summary",title),el("pre",text(value))); return d; }
  function button(label, fn) { const b=el("button",label,"secondary"); b.type="button"; b.onclick=fn; return b; }
  function table(columns, rows) {
    const wrap=el("div",null,"result-table"), t=el("table"), head=t.createTHead().insertRow();
    for (const c of columns) head.append(el("th",c));
    const body=t.createTBody(); for (const row of rows) { const tr=body.insertRow(); for (const c of columns) tr.append(el("td",row[c] == null ? "—" : (typeof row[c]==="object" ? text(row[c]) : row[c]))); }
    wrap.append(t); return wrap;
  }
  function download(result) {
    const escape = value => { let s=String(value??""); if (/^[=+@-]/.test(s)) s="'"+s; return '"'+s.replaceAll('"','""')+'"'; };
    const csv="\ufeff"+[result.columns,...result.rows.map(r=>result.columns.map(c=>r[c]))].map(row=>row.map(escape).join(",")).join("\r\n");
    const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"})), a=el("a"); a.href=url; a.download=`问数结果-${result.recordId}.csv`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function execution(parent, presentation) {
    for (const warning of presentation?.warnings||[]) parent.append(el("p",warning,"trace-warning"));
    const log=presentation?.execution; if (!log) return;
    const box=el("details",null,"execution-details"), total=log.total_tokens??"—", duration=log.duration??"—";
    box.append(el("summary",`执行明细 · ${duration} 秒 · ${total} Tokens`));
    for (const step of log.steps||[]) {
      const d=el("details",null,"execution-step");
      d.append(el("summary",`${step.error?"失败":"完成"} · ${operationNames[step.operate]||step.operate} · ${step.duration??"—"} 秒${step.total_tokens?` · ${step.total_tokens} Tokens`:""}`));
      const message=parse(step.message);
      if (Array.isArray(message) && message.some(m=>m && typeof m==="object" && "content" in m)) {
        for (const m of message) d.append(detail(({system:"系统提示词",human:"输入问题",ai:"模型回答",tool:"工具结果"})[m.type]||m.type||"内容",m.content));
      } else d.append(el("pre",text(message??"无匹配内容")));
      box.append(d);
    }
    parent.append(box);
  }
  function makeTurn(question, label="问数") {
    const card=el("article",null,"question-turn");card.append(el("div",label,"eyebrow"),el("h3",question,"asked-question"));
    const status=el("p","准备中…","stream-status");status.setAttribute("role","status");
    const stages=el("div",null,"query-stages"), output=el("div",null,"query-output");card.append(status,stages,output);
    const started=Date.now(), timer=setInterval(()=>{status.dataset.elapsed=`${Math.round((Date.now()-started)/1000)} 秒`;},1000);
    const parts=new Map();let failure=false;
    function section(type,title) {if(!parts.has(type)){const d=detail(title,"",true);stages.append(d);parts.set(type,d);}return parts.get(type);}
    function event(e) {
      if (stageNames[e.type]) {
        status.textContent=stageNames[e.type]+"…";
        if(e.content) {const d=section(e.type,stageNames[e.type]+" · 模型输出");d.querySelector("pre").textContent+=text(e.content);}
        if(e.reasoning_content) {const d=section(e.type+"-reason",stageNames[e.type]+" · 思考过程");d.querySelector("pre").textContent+=text(e.reasoning_content);}
      } else if(e.type==="host-stage" || e.type==="info") status.textContent=e.content||({"sql generated":"SQL 已生成，正在执行查询…","chart generated":"图表已生成，正在整理结果…"}[e.msg]||e.msg);
      else if(e.type==="datasource") stages.append(el("p",`已选择数据源：${e.datasource_name||e.id}${e.engine_type?` · ${e.engine_type}`:""}`,"source-choice"));
      else if(e.type==="brief") stages.append(detail("问题理解与查询说明",e.brief,true));
      else if(e.type==="sql") {section("sql","生成的 SQL").querySelector("pre").textContent=e.content;status.textContent="正在执行 SQL…";}
      else if(e.type==="sql-data") {stages.append(el("p","SQL 执行成功","source-choice"));status.textContent="正在整理查询结果…";}
      else if(e.type==="chart-type") stages.append(el("p",`图表类型：${e.content||"未提供"}`,"source-choice"));
      else if(e.type==="retry") {stages.append(el("p",e.content,"trace-warning"));parts.clear();}
      else if(e.type==="recommended_question") { const questions=parse(e.content); if(Array.isArray(questions)){ for(const q of questions) output.append(button(String(q),()=>{$("question").value=String(q);$("question").focus();})); } }
      else if(e.type==="predict-failed") {output.append(el("p","SQLBot 未生成可用的预测数据，请查看模型说明。","trace-warning"));}
      else if(e.type==="error") {if(failure)return;failure=true;status.textContent=({sqlbot_database_timeout:"数据库查询超时",sqlbot_model_quota_exceeded:"模型额度不足",sqlbot_transport_timeout:"SQLBot 响应超时",sqlbot_sql_execution_failed:"SQL 执行失败",sqlbot_chart_failed:"图表步骤失败"})[e.code]||"问数失败";card.classList.add("failed");output.append(el("p",text(parse(e.content)),"trace-error"));if(e.details)output.append(detail("错误详情",e.details));}
    }
    function finish(stopped=false) {clearInterval(timer);status.dataset.elapsed=`${((Date.now()-started)/1000).toFixed(1)} 秒`;if(!failure)status.textContent=stopped?"已停止接收；SQLBot 后台可能仍在执行。":"已完成";for(const [key,d] of parts) d.open=key==="analysis-result"||key==="predict-result";}
    return {card,event,finish,output,status,section};
  }
  let active=null, controllers=new Set();
  async function run(url,payload,turn,onResult) {
    const controller=new AbortController();controllers.add(controller);let failed=false,gotResult=false;
    const stop=button("停止接收",()=>controller.abort());turn.card.append(stop);
    try {
      await consume(await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:controller.signal}),e=>{
        turn.event(e);if(e.type==="error")failed=true;
        if(e.type==="result"){gotResult=true;onResult(e.result);}
      });
      if(!failed && !gotResult)throw new Error("SQLBot 未返回完整结果，请重新提问。");
    } catch(e) {if(e.name!=="AbortError")turn.event({type:"error",content:e.message});}
    finally {turn.finish(controller.signal.aborted);controllers.delete(controller);stop.remove();}
  }
  function renderResult(turn,result,agent) {
    const out=turn.output;out.append(el("p",`返回 ${result.rowCount} 行 · Record ${result.recordId}${result.truncated?" · 当前展示数据已截断":""}`,"result-meta"));
    const record=result.presentation?.record||{};
    for(const [key,title] of [["sql_answer","SQL 生成说明"],["chart_answer","图表生成说明"],["analysis","数据分析"],["predict","数据预测"]]) if(record[key]) out.append(detail(title,record[key]));
    const chart=el("div",null,"sqlbot-result-chart");out.append(chart);
    if(root.SQLBotCharts) void root.SQLBotCharts.mount(chart,result).catch(e=>{chart.textContent=e.message;out.append(table(result.columns,result.rows));});
    out.append(detail("查看 / 复制 SQL",result.sql));
    const tools=el("div",null,"result-actions");tools.append(button("复制 SQL",async()=>{try{await navigator.clipboard.writeText(result.sql);}catch(_){notify("复制失败，请展开 SQL 手动复制。");}}),button("导出当前数据 CSV",()=>download(result)));
    for(const [kind,label] of [["analysis","数据分析"],...(record.chart?[["predict","数据预测"]]:[]),["recommend","推荐问题"]]) tools.append(button(label,async(e)=>{
      const b=e.currentTarget;b.disabled=true;const follow=makeTurn(label,label);out.append(follow.card);
      await run(`/api/data-agents/${agent.id}/results/${result.resultId}/actions/${kind}`,{},follow,r=>{
        execution(follow.output,r);
        if(r.predictionData){const d=r.predictionData, rows=Array.isArray(d)?d:(d.data||[]);follow.output.append(el("p","以下为模型预测数据，不是实际查询结果。","trace-warning"),table(d.fields||Object.keys(rows[0]||{}),rows));}
      });b.disabled=false;
    }));
    out.append(tools);execution(out,result.presentation);
    out.append(detail("数据依据",{数据集:result.evidence.datasetRefs,使用字段:result.fieldsUsed,SQLBot记录:result.recordId}));
  }
  root.DataAgentChat={open(agent,opened){
    for(const c of controllers)c.abort();active={agent,opened};
    $("questionPanel").hidden=false;$("questionTitle").textContent=agent.name;$("questionResult").replaceChildren();$("askButton").disabled=false;
    $("questionForm").onsubmit=async e=>{
      e.preventDefault();const context=active, question=$("question").value.trim();if(!question)return;
      $("askButton").disabled=true;const turn=makeTurn(question);$("questionResult").append(turn.card);
      await run(`/api/data-agents/${context.agent.id}/ask/stream`,{session_id:context.opened.session_id,question},turn,r=>renderResult(turn,r,context.agent));
      if(context===active)$("askButton").disabled=false;
    };$("questionPanel").scrollIntoView({behavior:"smooth",block:"start"});$("question").focus();
  }};
})(typeof window!=="undefined"?window:globalThis);
