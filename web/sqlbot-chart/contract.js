// Mirrors SQLBot DisplayChartBlock + ChartComponent axis mapping.
export const types = ['table', 'column', 'bar', 'line', 'pie'];
export function chartAxes(config, formatted = []) {
  const axes = [];
  function add(value, type) {
    if (!value || typeof value.value !== 'string' || typeof value.name !== 'string') throw Error('SQLBot 图表字段配置不完整');
    axes.push({...value, ...(type ? {type} : {}), formatNumber: formatted.includes(value.value)});
  }
  (config.columns || []).forEach(c => add(c));
  const axis = config.axis || {};
  if (axis.x) add(axis.x, 'x');
  if (axis.y) (Array.isArray(axis.y) ? axis.y : [axis.y]).forEach(y => add({
    ...y, 'multi-quota': (axis['multi-quota']?.value || []).includes(y.value),
  }, 'y'));
  if (axis.series) add(axis.series, 'series');
  if (axis['multi-quota']?.name) axes.push({name: axis['multi-quota'].name, value: axis['multi-quota'].name, type:'other-info', hidden:true});
  return axes;
}
export function receiptIds(value) {
  const raw = typeof value === 'string' ? value : (JSON.stringify(value) || '');
  function walk(item, depth = 0) {
    if (!item || depth > 5) return null;
    if (typeof item === 'string') { try { return walk(JSON.parse(item), depth+1); } catch { return null; } }
    if (Array.isArray(item)) { for (const part of item) { const hit=walk(part,depth+1); if(hit)return hit; } return null; }
    if (item.resultId && item.agentId) return {resultId:item.resultId, agentId:item.agentId};
    return walk(item.content || item.text, depth+1);
  }
  let result = walk(value);
  if (!result) {
    // Persisted tool previews may be cut off after the leading identifiers.
    const text = raw.replace(/\\+"/g, '"');
    result = {resultId:text.match(/"resultId"\s*:\s*"([\w-]+)"/)?.[1], agentId:text.match(/"agentId"\s*:\s*"([\w-]+)"/)?.[1]};
  }
  return /^[\w-]{1,128}$/.test(result.resultId || '') && /^[\w-]{1,128}$/.test(result.agentId || '') ? result : null;
}
