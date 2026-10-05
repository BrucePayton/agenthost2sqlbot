"""Read-only geometry tracing around the unchanged production probe and renderer."""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

TRACE = r"""() => {
  const nativeRect = Element.prototype.getBoundingClientRect;
  const box = e => {
    const r = nativeRect.call(e);
    return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};
  };
  const describe = e => {
    const s = getComputedStyle(e);
    return {tag:e.tagName,classes:e.className,inline:e.getAttribute('style'),box:box(e),
      clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,offsetHeight:e.offsetHeight,
      css:Object.fromEntries(['font-family','font-size','font-weight','line-height',
        'box-sizing','height','min-height','max-height','padding-top','padding-bottom',
        'padding-left','padding-right','border-top-width','border-bottom-width',
        'border-left-width','border-right-width',
        'margin-top','margin-bottom','overflow-x','overflow-y','display','align-items',
        'justify-content','position','top','transform'].map(k=>[k,s.getPropertyValue(k)]))};
  };
  const snapshot = root => {
    const texts = [];
    for(const marker of root.querySelectorAll('[data-metric-title],[data-metric-value]')) {
      const walker = document.createTreeWalker(marker,NodeFilter.SHOW_TEXT);
      let node;
      while((node=walker.nextNode())) {
        if(!node.textContent.trim()) continue;
        const range=document.createRange();range.selectNodeContents(node);
        const rects=[...range.getClientRects()].map(r=>({x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}));
        const ancestors=[];
        for(let e=node.parentElement;e;e=e.parentElement) {
          ancestors.push(describe(e));
          if(e===root) break;
        }
        texts.push({text:node.textContent,rects,ancestors});
      }
    }
    const frame=[];
    for(let e=root;e && frame.length<7;e=e.parentElement) frame.push(describe(e));
    return {root:describe(root),texts,frame};
  };
  window.metricFrameTraces=[];
  const seen=new Set();
  Element.prototype.getBoundingClientRect=function() {
    const result=nativeRect.call(this);
    if(this instanceof HTMLElement && this.hasAttribute('inert') &&
      this.querySelector('[data-metric-value]') &&
      this.closest('[data-widget-id]')?.dataset.widgetId==='m3' && result.height<=110) {
      const key=this.style.width+'|'+this.style.height+'|'+this.querySelector('[data-metric-value]').style.fontSize;
      if(!seen.has(key)) {seen.add(key); window.metricFrameTraces.push(snapshot(this));}
    }
    return result;
  };
  window.inspectMetricFrame=()=>snapshot(document.querySelector('[data-widget-id="m3"] [data-metric-ready]'));
  window.stopMetricFrameTrace=()=>{Element.prototype.getBoundingClientRect=nativeRect;};
}"""


async def main():
    bundle, directory = Path(sys.argv[1]), Path(sys.argv[2])
    directory.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1000})
        await page.route("**/*", lambda route: route.abort())
        await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
        await page.add_script_tag(path=str(bundle))
        before = await page.evaluate("window.prepareRegion('grouping')")
        await page.evaluate(TRACE)
        source = await page.evaluate("window.inspectMetricFrame()")
        outcome = await page.evaluate("window.runRegion()")
        actual = await page.evaluate("window.inspectMetricFrame()")
        traces = await page.evaluate("window.metricFrameTraces")
        await page.evaluate("window.stopMetricFrameTrace()")
        await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / "actual-1920.png"))
        (directory / "frames.json").write_text(json.dumps({
            "bundle": str(bundle), "live": False, "before": before, "outcome": outcome,
            "source": source, "actual": actual, "probes": traces,
        }, ensure_ascii=False, indent=2))
        print(json.dumps({"probeFrames": len(traces), "actualRoot": actual["root"],
                          "result": outcome["result"]["status"]}, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
