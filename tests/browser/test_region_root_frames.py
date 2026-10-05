"""Native root-frame geometry control; no controller apply or persistence."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from playwright.async_api import async_playwright
from test_region_layout import (
    region_bundle as region_bundle,  # noqa: PLC0414 -- expose the shared pytest fixture
)
from test_region_layout import validate_root_frames


@pytest.fixture(scope="module")
def root_frame_bundle(request, tmp_path_factory):
    control = os.environ.get("REGION_FRAME_CONTROL_BUNDLE")
    if control:
        return Path(control), Path(os.environ.get("REGION_SCREENSHOT_DIR", tmp_path_factory.mktemp("root-frame-control")))
    return request.getfixturevalue("region_bundle")


@pytest.mark.parametrize("width", [1440, 1920, 768])
def test_native_root_frames(root_frame_bundle, width):
    async def scenario():
        bundle, directory = root_frame_bundle
        directory.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 1000})
            await page.route("**/*", lambda route: route.abort())
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            snapshot = await page.evaluate("window.prepareRegion('grouping')")
            snapshot["rootFrames"] = await page.evaluate("""roots => roots.map(w => {
              const card=document.querySelector('[data-widget-id="'+w.id+'"]');
              const frame=card.closest('[data-root-frame]') || card;
              const grid=card.querySelector('.react-grid-layout');
              const s=getComputedStyle(frame);
              return {id:w.id,frame:frame.getBoundingClientRect().toJSON(),
                card:card.getBoundingClientRect().toJSON(),
                innerCanvas:grid ? grid.getBoundingClientRect().toJSON() : null,
                boxSizing:s.boxSizing,
                borderWidths:[s.borderLeftWidth,s.borderRightWidth,s.borderTopWidth,s.borderBottomWidth].map(parseFloat),
                borderX:parseFloat(s.borderLeftWidth)+parseFloat(s.borderRightWidth),
                borderY:parseFloat(s.borderTopWidth)+parseFloat(s.borderBottomWidth)};
            })""", snapshot["roots"])
            failures = []
            validate_root_frames(snapshot, lambda condition, message: None if condition else failures.append(message))
            (directory / f"root-frames-{width}.json").write_text(json.dumps({
                "bundle": str(bundle), "live": False, "controllerApplied": False,
                "snapshot": snapshot, "failures": failures,
            }, ensure_ascii=False, indent=2))
            await browser.close()
        assert not failures, failures

    asyncio.run(scenario())
