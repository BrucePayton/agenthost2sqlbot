"""Run real Davinci metric rendering and local sizing without a live backend."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def metric_bundle(tmp_path_factory):
    outfile = tmp_path_factory.mktemp("metric-layout") / "parent.js"
    davinci = Path(os.environ.get("DAVINCI_ROOT", ROOT.parent / "davinci"))
    subprocess.run(
        ["node", str(ROOT / "tests/browser/build_metric_layout_parent.cjs"), str(davinci), str(outfile)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return outfile


@pytest.mark.parametrize("width", [1440, 1920])
@pytest.mark.parametrize("grouped", [False, True, "regroup"])
def test_pending_metric_compacts_once_without_clipping(metric_bundle, width, tmp_path, grouped):
    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 700})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(metric_bundle))
            await page.wait_for_function("typeof window.runLayout === 'function'")
            outcome = await page.evaluate("grouped => window.runLayout(grouped)", grouped)
            screenshot_dir = Path(os.environ.get("METRIC_SCREENSHOT_DIR", tmp_path))
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            mode = "regroup" if grouped == "regroup" else "grouped" if grouped else "after"
            await page.screenshot(path=str(screenshot_dir / f"metric-{mode}-{width}.png"))
            print(json.dumps({"viewport": width, "outcome": outcome}, ensure_ascii=False))
            assert not errors, errors
            assert outcome["result"]["status"] == "success", outcome
            assert outcome["result"]["data"]["persisted"] is True, outcome
            assert outcome["saves"] == 1
            if grouped:
                assert outcome["result"]["data"]["grouping"]["groups"][0]["widgetIds"] == ["metric"]
                assert await page.get_by_test_id("group-frame").count() == 1
                assert outcome["pixels"]["width"] < width / 4, outcome
                assert outcome["pixels"]["height"] < 250, outcome
                if grouped == "regroup":
                    assert outcome["result"]["data"]["grouping"]["removedContainerWidgetIds"] == ["old-group"]
                    assert {item["id"] for item in outcome["widgets"]} == {"metric", "saved-group"}
                    assert outcome["widget"]["id"] == outcome["before"]["id"]
                    before_config = dict(outcome["before"]["config"])
                    after_config = dict(outcome["widget"]["config"])
                    assert before_config.pop("layoutParentId") == "old-group-uid"
                    assert after_config.pop("layoutParentId") != "old-group-uid"
                    assert before_config == after_config
            else:
                assert outcome["widget"]["w"] * outcome["widget"]["h"] < 12 * 8 / 2, outcome
            assert outcome["complete"] is True, outcome
            assert outcome["frameComplete"] is True, outcome
            for text in ["留的QQ", "38,585", "0.72%", "标签333"]:
                assert text in outcome["text"], outcome
            await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [1440, 1920])
def test_four_metrics_fill_equal_content_row_above_wide_chart(metric_bundle, width, tmp_path):
    """Use production renderers to verify row geometry, minimum height and preservation."""
    async def scenario():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.evaluate("window.metricScenario = 'multi'")
            await page.add_script_tag(path=str(metric_bundle))
            await page.wait_for_function("typeof window.runMetricRow === 'function'")
            await page.evaluate("window.prepareMetricRow()")
            chart = page.get_by_test_id("dashboard-v2-widget-card-wide-chart")
            await chart.locator("canvas").first.wait_for(state="visible")
            await page.wait_for_timeout(1000)
            screenshot_dir = Path(os.environ.get("METRIC_SCREENSHOT_DIR", tmp_path))
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_dir / f"metric-row-before-{width}.png"), full_page=True)
            outcome = await page.evaluate("window.runMetricRow()")
            await page.wait_for_timeout(1000)
            await page.screenshot(path=str(screenshot_dir / f"metric-row-after-{width}.png"), full_page=True)
            outcome["smallerRows"] = await page.evaluate("window.checkSmallerMetricRows()")
            outcome["chartInkPixels"] = await chart.locator("canvas").first.evaluate("""canvas => {
                const context = canvas.getContext('2d');
                if (!context) return 0;
                const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
                let ink = 0;
                for (let i = 0; i < pixels.length; i += 4) {
                    if (pixels[i + 3] > 0 && Math.min(pixels[i], pixels[i+1], pixels[i+2]) < 220) ink++;
                }
                return ink;
            }""")
            outcome["clippedText"] = await page.evaluate("""() => {
                const clipped = [];
                for (const card of document.querySelectorAll('[data-widget-card]')) {
                    if (!card.querySelector('[data-metric-ready]')) continue;
                    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
                    let text;
                    while ((text = walker.nextNode())) {
                        if (!text.textContent.trim()) continue;
                        if (text.parentElement.closest('[aria-hidden="true"]') ||
                            getComputedStyle(text.parentElement).visibility === 'hidden') continue;
                        const range = document.createRange();
                        range.selectNodeContents(text);
                        for (const rect of range.getClientRects()) {
                            let parent = text.parentElement;
                            while (parent) {
                                const style = getComputedStyle(parent), bounds = parent.getBoundingClientRect();
                                const clipX = parent === card || /hidden|clip|auto|scroll/.test(style.overflowX);
                                const clipY = parent === card || /hidden|clip|auto|scroll/.test(style.overflowY);
                                if ((clipX && (rect.left < bounds.left-1 || rect.right > bounds.right+1)) ||
                                    (clipY && (rect.top < bounds.top-1 || rect.bottom > bounds.bottom+1))) {
                                    clipped.push({id:card.dataset.testid,text:text.textContent}); break;
                                }
                                if (parent === card) break;
                                parent = parent.parentElement;
                            }
                        }
                    }
                }
                return clipped;
            }""")
            (screenshot_dir / f"metric-row-{width}.json").write_text(
                json.dumps({"viewport": width, "errors": errors, "outcome": outcome}, ensure_ascii=False, indent=2)
            )
            await browser.close()

            assert not errors, errors
            assert outcome["result"]["status"] == "success", outcome
            assert outcome["result"]["data"]["persisted"] is True, outcome
            assert outcome["saves"] == outcome["smallerRows"]["saves"] == 1
            metrics = [widget for widget in outcome["widgets"] if widget["type"] == "metric"]
            assert len(metrics) == 4
            assert {widget["w"] for widget in metrics} == {6}, metrics
            assert len({widget["h"] for widget in metrics}) == 1, metrics
            assert len({widget["y"] for widget in metrics}) == 1, metrics
            assert sorted(widget["x"] for widget in metrics) == [0, 6, 12, 18], metrics
            assert sum(widget["w"] for widget in metrics) == 24
            assert all(metric["complete"] for metric in outcome["metrics"]), outcome
            assert not outcome["clippedText"], outcome["clippedText"]
            expected_text = [
                ["营业收入", "38,585", "本月累计", "环比", "0.72%"],
                ["本季度累计成交金额", "146,892", "已审核", "重点客户", "同比", "18.60%", "环比", "2.40%"],
                ["新增客户", "924", "重点客户"],
                ["有效订单", "628", "本月累计", "已审核", "较上期", "36"],
            ]
            for index, fragments in enumerate(expected_text):
                metric = next(item for item in outcome["metrics"] if item["id"] == f"metric-{index}")
                assert all(text in metric["text"] for text in fragments), metric
            pixels = sorted((item["pixels"] for item in outcome["metrics"]), key=lambda item: item["x"])
            assert max(item["width"] for item in pixels) - min(item["width"] for item in pixels) < 1
            assert max(item["height"] for item in pixels) - min(item["height"] for item in pixels) < 1
            assert abs(pixels[0]["x"] - outcome["canvas"]["x"]) < 1
            assert abs(pixels[-1]["right"] - outcome["canvas"]["right"]) < 1
            for left, right in zip(pixels, pixels[1:]):
                assert right["x"] - left["right"] == pytest.approx(10, abs=1)
            assert outcome["chart"]["y"] >= max(item["bottom"] for item in pixels) + 9
            wide_chart = next(widget for widget in outcome["widgets"] if widget["id"] == "wide-chart")
            assert wide_chart["w"] == 24
            assert outcome["chartInkPixels"] > 100, outcome
            assert all(not all(trial["complete"]) for trial in outcome["smallerRows"]["heights"]), outcome
            assert metrics[0]["h"] < 8, metrics
            before = {item["id"]: item for item in outcome["before"]}
            after = {item["id"]: item for item in outcome["widgets"]}
            assert before.keys() == after.keys()
            geometry_fields = {"x", "y", "w", "h", "order"}
            for widget_id, original in before.items():
                assert {key: value for key, value in original.items() if key not in geometry_fields} == {
                    key: value for key, value in after[widget_id].items() if key not in geometry_fields
                }, widget_id

    asyncio.run(scenario())
