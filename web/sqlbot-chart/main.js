import './style.css';
import {getChartInstance} from './vendor/index.ts';
import {chartAxes, receiptIds, types} from './contract.js';
export {receiptIds, chartAxes};
const active = new Map();
let sequence = 0;
const labels = {column:'柱状图', bar:'条形图', line:'折线图', pie:'饼图', table:'表格'};
function element(tag, text) { const n=document.createElement(tag); if(text!=null)n.textContent=text; return n; }
export function destroy(root) { active.get(root)?.(); active.delete(root); }
export function destroyAll() { for (const root of [...active.keys()]) destroy(root); }
export async function mount(root, result) {
  destroy(root); root.replaceChildren();
  const config = result.chartHint || {};
  if (!types.includes(config.type)) throw Error('SQLBot 未返回可渲染的图表配置，请重新问数。');
  const axes = chartAxes(config);
  if (!axes.length || (['column','bar','line'].includes(config.type) && (!config.axis?.x || !config.axis?.y)) || (config.type === 'pie' && (!config.axis?.series || !config.axis?.y))) throw Error('该历史结果仅保存了图表类型，缺少 SQLBot 图表配置，请重新问数。');
  const heading = element('strong', config.title || labels[config.type]);
  const controls = element('div'); controls.className='sqlbot-chart-controls';
  const select = element('select'); select.setAttribute('aria-label','图表类型');
  const choices = ['column','bar','line'].includes(config.type) ? ['column','bar','line','table'] : [...new Set([config.type,'table'])];
  choices.forEach(type => { const option=element('option',labels[type]); option.value=type; select.append(option); });
  select.value=config.type;
  const labelToggle=element('input'); labelToggle.type='checkbox';
  const label=element('label'); label.append(labelToggle, document.createTextNode(' 显示数值'));
  const formatToggle=element('input'); formatToggle.type='checkbox';
  const formatLabel=element('label'); formatLabel.append(formatToggle,document.createTextNode(' 千分位'));
  controls.append(select,label,formatLabel);
  const viewport=element('div'); viewport.className='sqlbot-chart-viewport';
  const canvas=element('div'); canvas.id=`sqlbot-native-chart-${++sequence}`; canvas.className='sqlbot-chart-canvas'; viewport.append(canvas);
  const notice=element('p'); notice.className='sqlbot-chart-notice'; notice.setAttribute('role','status');
  const attribution=element('a','SQLBot · © FIT2CLOUD'); attribution.href='https://github.com/dataease/SQLBot'; attribution.target='_blank'; attribution.rel='noopener noreferrer'; attribution.className='sqlbot-chart-attribution';
  root.append(heading,controls,viewport,notice,attribution);
  if (result.truncated) notice.textContent=`当前图表使用已缓存的 ${result.rows.length} 行数据，完整结果为 ${result.rowCount} 行。`;
  let instance, disposed=false, rendering=Promise.resolve();
  const formats=axes.filter(a=> !a.hidden && result.rows.some(row=>typeof row[a.value]==='number' || (typeof row[a.value]==='string' && row[a.value].trim()!=='' && Number.isFinite(Number(row[a.value]))))).map(a=>a.value);
  function draw() {
    rendering=rendering.then(async () => {
      if(disposed) return;
      instance?.destroy(); canvas.replaceChildren();
      const formatted=formatToggle.checked ? formats : [];
      instance=getChartInstance(select.value,canvas.id);
      if(!instance) throw Error('不支持的 SQLBot 图表类型');
      instance.showLabel=labelToggle.checked;
      instance.init(chartAxes(config,formatted),result.rows.map(row=>({...row})),formatted);
      // Upstream render() is void; awaiting the native engines also catches render failures.
      if(instance.chart) await instance.chart.render();
      else if(instance.table) {
        instance.table.changeSheetSize(viewport.clientWidth,viewport.clientHeight);
        await instance.table.render();
      }
      root.dataset.chartStatus='ready'; root.dataset.chartType=select.value;
    }).catch(error=>{ root.dataset.chartStatus='error'; notice.textContent=`图表渲染失败：${error.message}`; });
    return rendering;
  }
  select.addEventListener('change',draw); labelToggle.addEventListener('change',draw); formatToggle.addEventListener('change',draw);
  let resizeTimer;
  const observer=new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(!disposed && instance?.chart) void instance.chart.changeSize(viewport.clientWidth,viewport.clientHeight);},100);});
  observer.observe(viewport);
  active.set(root,()=>{disposed=true;observer.disconnect();clearTimeout(resizeTimer);instance?.destroy();});
  if(!result.rows.length) { notice.textContent='查询成功，结果为空。';root.dataset.chartStatus='empty';return; }
  await draw();
}
