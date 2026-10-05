"""Capture caught production exceptions without changing their handling."""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    bundle, directory = Path(sys.argv[1]), Path(sys.argv[2])
    directory.mkdir(parents=True, exist_ok=True)
    exceptions = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 768, "height": 1000})
        await page.route("**/*", lambda route: route.abort())
        await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
        await page.add_script_tag(path=str(bundle))
        before = await page.evaluate("window.prepareRegion('grouping-widths')")
        session = await page.context.new_cdp_session(page)
        await session.send("Debugger.enable")

        async def paused(event):
            try:
                local = await session.send("Debugger.evaluateOnCallFrame", {
                    "callFrameId": event["callFrames"][0]["callFrameId"],
                    "expression": """JSON.stringify({scopeWidth, inner, explicit, result:result2,
                      widget:{id:widget.id,w:widget.w,h:widget.h},
                      card:{rect:entry.card.getBoundingClientRect().toJSON(),
                        offsetWidth:entry.card.offsetWidth,offsetHeight:entry.card.offsetHeight},
                      metric:{rect:entry.root.getBoundingClientRect().toJSON(),
                        connected:entry.root.isConnected},
                      contains:entry.card.contains(entry.root),fonts:document.fonts.status})""",
                    "returnByValue": True,
                })
                exceptions.append({"reason": event.get("reason"), "data": event.get("data"), "local": local,
                                   "frames": [{"functionName": frame["functionName"],
                                               "location": frame["location"], "url": frame["url"]}
                                              for frame in event["callFrames"]]})
            finally:
                await session.send("Debugger.resume")

        session.on("Debugger.paused", paused)
        await session.send("Debugger.setPauseOnExceptions", {"state": "all"})
        outcome = await page.evaluate("window.runRegion()")
        await session.send("Debugger.setPauseOnExceptions", {"state": "none"})
        (directory / "exceptions.json").write_text(json.dumps({
            "bundle": str(bundle), "live": False, "diagnosticTimingOnly": True,
            "before": before, "outcome": outcome, "exceptions": exceptions,
        }, ensure_ascii=False, indent=2))
        print(json.dumps({"result": outcome["result"], "exceptions": exceptions}, ensure_ascii=False))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
