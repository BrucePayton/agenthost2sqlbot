"""Read-only CDP logpoints on a frozen bundle; diagnostic timing is not acceptance."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    bundle, directory = Path(sys.argv[1]), Path(sys.argv[2])
    directory.mkdir(parents=True, exist_ok=True)
    source = bundle.read_text()
    probes = [
        ("initial-shapes", "        preferredMetricSizes = metricSizeCandidates;",
         "({metricSizeCandidates,measuredMetricSizes,sizeOverrides,flexibleWidgetIds})"),
        ("row-result", "      return rowCache.get(key2);",
         "({targets:targets.map(w=>({id:w.id,x:w.x,y:w.y,w:w.w,h:w.h})),result:rowCache.get(key2)})"),
        ("feedback", "      const replay = chooseCompactLayout(",
         "({alternatives,evidence,packed:packed.map(w=>({id:w.id,x:w.x,y:w.y,w:w.w,h:w.h}))})"),
        ("replay", "    const justified = { widgets: packed, measuredMetricSizes: evidence };",
         "({passes,hasFeedback,packed:packed.map(w=>({id:w.id,x:w.x,y:w.y,w:w.w,h:w.h}))})"),
    ]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.route("**/*", lambda route: route.abort())
        await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
        session = await page.context.new_cdp_session(page)
        scripts = []
        session.on("Debugger.scriptParsed", lambda event: scripts.append(event))
        await session.send("Debugger.enable")
        await page.add_script_tag(path=str(bundle))
        await session.send("Runtime.evaluate", {"expression": "void 0"})
        (directory / "scripts.json").write_text(json.dumps(scripts, indent=2))
        script = max(scripts, key=lambda item: item.get("endLine", 0))
        actual_source = (await session.send("Debugger.getScriptSource", {"scriptId": script["scriptId"]}))["scriptSource"]
        assert actual_source.startswith(source), "CDP script differs from frozen bundle"
        suffix = actual_source[len(source):].strip()
        assert not suffix or suffix.startswith("//# sourceURL="), "Unexpected injected script suffix"
        await page.evaluate("window.__regionFeedbackTrace=[]")
        locations = []
        for label, marker, expression in probes:
            assert source.count(marker) == 1, (label, source.count(marker))
            line = source[:source.index(marker)].count("\n")
            condition = ("(window.__regionFeedbackTrace.push(JSON.parse(JSON.stringify({kind:"
                         + json.dumps(label) + ",value:" + expression + "}))),false)")
            result = await session.send("Debugger.setBreakpoint", {
                "location": {"scriptId": script["scriptId"], "lineNumber": line},
                "condition": condition,
            })
            locations.append({"kind": label, "requestedLine": line, "actual": result})
        before = await page.evaluate("window.prepareRegion('automatic')")
        outcome = await page.evaluate("window.runRegion()")
        trace = await page.evaluate("window.__regionFeedbackTrace")
        await session.send("Debugger.disable")
        await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / "automatic-1440-after.png"))
        evidence = {"live": False, "diagnosticTimingOnly": True,
                    "bundle": str(bundle), "bundleSha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                    "locations": locations, "before": before, "outcome": outcome, "trace": trace}
        (directory / "feedback-trace.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(json.dumps({"trace": trace, "calls": outcome["calls"], "saves": outcome["saves"]}, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
