"""Production renderer/controller regression; synthetic data and mocked local saves, never live."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from itertools import pairwise
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ["shelves-7", "shelves-9", "rank-stack", "automatic", "grouping", "grouping-widths"]


@pytest.mark.parametrize("name", ["adjacent-hole", "tall-side-stack"])
@pytest.mark.parametrize("width", [1440, 1920])
def test_ordered_holes_use_following_cards(region_bundle, name, width):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 1200})
            await page.route("**/*", lambda route: route.abort())
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            before = await page.evaluate("name => window.prepareRegion(name)", name)
            await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{name}-{width}-before.png"))
            previous = None
            for iteration in range(2):
                outcome = await page.evaluate("window.runRegion()")
                stem = f"{name}-{width}-{iteration}"
                (directory / f"{stem}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}.png"))
                assert not errors
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert outcome["saves"] == 1
                roots = outcome["roots"]
                cards = {w["id"]: w for w in roots}
                assert [w["id"] for w in sorted(roots, key=lambda w: (w["y"], w["x"]))] == [w["id"] for w in before["widgets"]]
                if name == "adjacent-hole":
                    assert cards["trend"]["y"] == cards["share"]["y"]
                    assert cards["share"]["x"] == cards["trend"]["x"] + cards["trend"]["w"]
                else:
                    assert cards["comparison"]["y"] < cards["rank-a"]["y"] + cards["rank-a"]["h"]
                    assert cards["duration"]["h"] == cards["average"]["h"] == 3
                    assert grid_audit(roots)["emptyCells"] == 0
                geometry = [[w[k] for k in ["id", "x", "y", "w", "h"]] for w in roots]
                if previous is not None:
                    assert geometry == previous
                previous = geometry
                for old, new in zip(before["widgets"], outcome["widgets"]):
                    assert old["id"] == new["id"] and old["data"] == new["data"]
                    assert old["config"] == new["config"]
                assert all(w["metricComplete"] is not False for w in outcome["rendered"])
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("width", [1440, 1920])
def test_metric_only_row_bottoms(region_bundle, width):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": width, "height": 1000})
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            before = await page.evaluate("window.prepareRegion('metric-only-bottoms')")
            previous = None
            for iteration in range(2):
                outcome = await page.evaluate("window.runRegion()")
                stem = f"metric-only-bottoms-{width}-{iteration}"
                (directory / f"{stem}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}.png"))
                assert not errors
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert outcome["saves"] == 1
                assert grid_audit(outcome["roots"])["emptyCells"] == 0
                metrics = outcome["roots"][:5]
                assert len({(w["y"], w["h"]) for w in metrics}) == 1
                geometry = [[w[k] for k in ["id", "x", "y", "w", "h"]] for w in outcome["roots"]]
                if previous is not None:
                    assert geometry == previous
                previous = geometry
                for old, new in zip(before["widgets"], outcome["widgets"]):
                    assert old["id"] == new["id"] and old["data"] == new["data"]
                    assert old["config"] == new["config"]
                rendered = [w for w in outcome["rendered"] if w["metricComplete"] is not None]
                assert len(rendered) == 5 and all(w["metricComplete"] for w in rendered)
                bottoms = [w["pixels"]["bottom"] for w in rendered]
                assert max(bottoms) - min(bottoms) <= 1
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["group-background-white", "group-background-pastel"])
def test_new_group_background_separates_preserved_cards(region_bundle, name):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            before = await page.evaluate("name => window.prepareRegion(name)", name)
            outcome = await page.evaluate("window.runRegion()")
            colors = await page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-widget-id]')]
              .map(e => [e.getAttribute('data-widget-id'), getComputedStyle(e).backgroundColor]))""")
            outcome["computedBackgrounds"] = colors
            (directory / f"{name}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
            await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{name}.png"))
            assert not errors
            assert outcome["result"]["status"] == "success", outcome["result"]
            assert outcome["saves"] == 1
            originals = {w["id"]: w for w in before["widgets"]}
            after = {w["id"]: w for w in outcome["widgets"]}
            group = next(w for w in outcome["widgets"] if w["id"] not in originals)
            assert group["config"]["bgOpacity"] == 1
            group_rgb = [int(group["config"]["bgColor"][i:i+2], 16) for i in [1, 3, 5]]
            assert colors[group["id"]] == f"rgb({group_rgb[0]}, {group_rgb[1]}, {group_rgb[2]})"
            for widget_id in ["amount", "orders"]:
                old, new = originals[widget_id], after[widget_id]
                assert old["data"] == new["data"] and old["config"]["bgColor"] == new["config"]["bgColor"]
                rgb = [int(old["config"]["bgColor"][i:i+2], 16) for i in [1, 3, 5]]
                assert colors[widget_id] == f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"
                assert sum((a-b)**2 for a, b in zip(rgb, group_rgb)) >= 1600
            assert originals["existing"]["config"] == after["existing"]["config"]
            assert originals["existing-child"] == after["existing-child"]
            assert colors["existing"] == "rgb(252, 232, 230)"
            assert colors["existing-child"] == "rgba(220, 226, 233, 0.5)"
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["column-balance", "column-balance-locked"])
def test_small_chart_column_deficit(region_bundle, name):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            errors, requests = [], []

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await context.route("**/*", offline)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            before = await page.evaluate("name => window.prepareRegion(name)", name)
            previous = None
            for iteration in range(2):
                outcome = await page.evaluate("window.runRegion()")
                stem = f"{name}-{iteration}"
                (directory / f"{stem}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}.png"))
                assert not errors and not requests
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert outcome["saves"] == 1, "unchanged second run must not write again"
                roots = outcome["roots"]
                geometry = [[w[k] for k in ["id", "x", "y", "w", "h"]] for w in roots]
                h = 6 if name.endswith('-locked') else 7
                assert geometry == [["rank-a", 0, 0, 8, 14], ["rank-b", 8, 0, 8, 14],
                                    ["trend", 16, 0, 8, h], ["share", 16, h, 8, h]]
                if previous is not None:
                    assert geometry == previous, "repeated compaction changed the balanced region"
                previous = geometry
                assert grid_audit(roots)["emptyCells"] == (16 if name.endswith('-locked') else 0)
                for old, new in zip(before["widgets"], outcome["widgets"]):
                    assert {k: v for k, v in old.items() if k not in {"x", "y", "w", "h", "order"}} == {
                        k: v for k, v in new.items() if k not in {"x", "y", "w", "h", "order"}}
                rendered = {w["id"]: w for w in outcome["rendered"]}
                for widget_id in ["trend", "share"]:
                    assert sum(rendered[widget_id]["ink"]) > 100
                for widget_id in ["rank-a", "rank-b"]:
                    assert "City 10" in rendered[widget_id]["text"], "leaderboard lost its final row"
                if not name.endswith('-locked'):
                    assert abs(rendered["rank-b"]["pixels"]["bottom"] - rendered["share"]["pixels"]["bottom"]) <= 1
            await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("width", [1440, 1920])
def test_mixed_metric_row_boundaries(region_bundle, width):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": width, "height": 1000})
            errors, requests = [], []

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await context.route("**/*", offline)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            for cache in ["cold", "warm"]:
                before = await page.evaluate("window.prepareRegion('mixed-metric-rows')")
                outcome = await page.evaluate("window.runRegion()")
                stem = f"mixed-metric-rows-{width}-{cache}"
                (directory / f"{stem}.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2))
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}.png"))
                assert not errors and not requests
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert outcome["calls"] == outcome["saves"] == 1
                roots = outcome["roots"]
                assert [w["id"] for w in sorted(roots, key=lambda w: (w["y"], w["x"]))] == [w["id"] for w in before["widgets"]]
                audit = grid_audit(roots)
                assert not audit["overlaps"]
                # Chart widths are explicitly locked in this fixture. A one-column
                # row tail is allowed; holes beneath any metric are not.
                assert audit["emptyCells"] <= 6, audit
                assert max(w["y"] + w["h"] for w in roots) == 12
                for old, new in zip(before["widgets"], outcome["widgets"]):
                    assert old["id"] == new["id"] and old["data"] == new["data"]
                    assert old["config"] == new["config"]
                for item in outcome["rendered"]:
                    if item["metricComplete"] is not None:
                        assert item["metricComplete"], item
                        widget = next(w for w in roots if w["id"] == item["id"])
                        assert widget["w"] <= 6 and widget["h"] == 6, widget
                        peer = next(w for w in outcome["rendered"] if w["id"] ==
                                    ("share" if widget["y"] == 0 else "empty-trend"))
                        assert abs(item["pixels"]["height"] - peer["pixels"]["height"]) <= 1
                    elif item["id"] != "empty-trend":
                        assert sum(item["ink"]) > 100, item
                assert "未找到符合条件的结果" in next(w for w in outcome["rendered"] if w["id"] == "empty-trend")["text"]
            await browser.close()
    asyncio.run(scenario())


def region_source_hashes(davinci):
    dashboard = davinci / "webapp/share/containers/WorkBenchNew/DashboardV2"
    sources = {p.name: p for p in sorted((dashboard / "agent/edit").glob("*.ts"))
               if not p.name.endswith(".test.ts")}
    sources.update({
        "layoutUtils.ts": dashboard / "utils/layoutUtils.ts",
        "LayoutWidget/index.tsx": davinci / "webapp/app/components/DashboardWidgets/LayoutWidget/index.tsx",
        "constants.ts": dashboard / "constants.ts",
        "Canvas/index.tsx": dashboard / "components/Canvas/index.tsx",
        "Canvas/index.less": dashboard / "components/Canvas/index.less",
    })
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}


def test_region_source_hashes_include_native_inner_frame(tmp_path):
    files = {
        "webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/metricCompactProbe.ts": b"probe",
        "webapp/share/containers/WorkBenchNew/DashboardV2/agent/edit/probe.test.ts": b"excluded",
        "webapp/share/containers/WorkBenchNew/DashboardV2/utils/layoutUtils.ts": b"border constant",
        "webapp/app/components/DashboardWidgets/LayoutWidget/index.tsx": b"native frame",
        "webapp/share/containers/WorkBenchNew/DashboardV2/constants.ts": b"root constants",
        "webapp/share/containers/WorkBenchNew/DashboardV2/components/Canvas/index.tsx": b"root frame",
        "webapp/share/containers/WorkBenchNew/DashboardV2/components/Canvas/index.less": b"root frame styles",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert region_source_hashes(tmp_path) == {
        name: hashlib.sha256(content).hexdigest() for name, content in {
            "metricCompactProbe.ts": b"probe", "layoutUtils.ts": b"border constant",
            "LayoutWidget/index.tsx": b"native frame",
            "constants.ts": b"root constants", "Canvas/index.tsx": b"root frame",
            "Canvas/index.less": b"root frame styles",
        }.items()
    }


@pytest.fixture(scope="module")
def region_bundle(tmp_path_factory):
    directory = Path(os.environ.get("REGION_SCREENSHOT_DIR", tmp_path_factory.mktemp("region-layout")))
    directory.mkdir(parents=True, exist_ok=True)
    outfile = directory / "region-parent.js"
    davinci = Path(os.environ.get("DAVINCI_ROOT", ROOT.parent / "davinci"))
    source_hashes = region_source_hashes(davinci)
    result = subprocess.run([
        "node", str(ROOT / "tests/browser/build_metric_layout_parent.cjs"), str(davinci),
        str(outfile), str(ROOT / "tests/browser/region_layout_parent.tsx"),
    ], cwd=ROOT, capture_output=True, text=True, check=False)
    (directory / "build.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stderr[-6000:]
    assert region_source_hashes(davinci) == source_hashes, "Production sources changed during bundle compilation"
    metadata = {
        "provenance": "synthetic fixtures; production renderers/controller; local in-memory save",
        "live": False, "bundleSha256": hashlib.sha256(outfile.read_bytes()).hexdigest(),
        "sourceSha256": source_hashes,
        "cacheProtocol": "Fresh page/modules for cold; same page/modules with reset fixture for three warm runs.",
        "notRun": ["live backend persistence", "user/model end-to-end timing", "S01-S07 original inputs", "real 40-card dashboard"],
    }
    (directory / "environment.json").write_text(json.dumps(metadata, indent=2))
    return outfile, directory


def grid_audit(items):
    """Count empty cells and their connected regions independently of production quality flags."""
    bottom = max((w["y"] + w["h"] for w in items), default=0)
    occupied, overlaps = set(), []
    for w in items:
        assert all(isinstance(w[key], int) for key in ("x", "y", "w", "h")), w
        assert 0 <= w["x"] < w["x"] + w["w"] <= 24 and w["y"] >= 0 and w["h"] > 0, w
        cells = {(x, y) for x in range(w["x"], w["x"] + w["w"])
                 for y in range(w["y"], w["y"] + w["h"])}
        if occupied & cells:
            overlaps.append(w["id"])
        occupied |= cells
    empty = {(x, y) for x in range(24) for y in range(bottom)} - occupied
    remaining, largest = set(empty), 0
    while remaining:
        stack, count = [remaining.pop()], 0
        while stack:
            x, y = stack.pop()
            count += 1
            for cell in [(x-1, y), (x+1, y), (x, y-1), (x, y+1)]:
                if cell in remaining:
                    remaining.remove(cell)
                    stack.append(cell)
        largest = max(largest, count)
    return {"height": bottom, "emptyCells": len(empty), "largestEmptyRegion": largest,
            "emptyRatio": len(empty) / (24 * bottom) if bottom else 0, "overlaps": overlaps}


def validate_root_frames(snapshot, check):
    frames = {item["id"]: item for item in snapshot.get("rootFrames", [])}
    check(set(frames) == {w["id"] for w in snapshot["roots"]}, "missing native root frame evidence")
    step = (snapshot["canvas"]["width"] - 16 + 10) / 24
    for widget in snapshot["roots"]:
        item = frames.get(widget["id"])
        if not item:
            continue
        frame, card = item["frame"], item["card"]
        check(item.get("boxSizing") == "border-box" and item.get("borderWidths") == [2, 2, 2, 2],
              f"native root computed box model differs: {widget['id']}")
        check(item["borderX"] == item["borderY"] == 4, f"native root border missing: {widget['id']}")
        check(abs(frame["width"] - (widget["w"] * step - 10)) <= 0.51,
              f"root grid width differs from native grid: {widget['id']}")
        check(abs(frame["height"] - (widget["h"] * 40 - 10)) <= 0.51,
              f"root grid height differs from native grid: {widget['id']}")
        check(abs(frame["width"] - card["width"] - 4) <= 0.01 and
              abs(frame["height"] - card["height"] - 4) <= 0.01,
              f"root frame content does not deduct both borders: {widget['id']}")
        if item["innerCanvas"]:
            check(abs(item["innerCanvas"]["width"] - card["width"]) <= 0.01,
                  f"inner canvas does not use root content width: {widget['id']}")


def test_region_audit_distinguishes_filled_stack_from_avoidable_hole():
    filled = [{"id": "rank", "x": 0, "y": 0, "w": 8, "h": 20},
              {"id": "a", "x": 8, "y": 0, "w": 16, "h": 10},
              {"id": "b", "x": 8, "y": 10, "w": 16, "h": 10}]
    assert grid_audit(filled)["emptyCells"] == 0
    broken = [*filled[:2], {**filled[2], "y": 20}]
    assert grid_audit(broken)["largestEmptyRegion"] == 160
    assert grid_audit(broken)["emptyCells"] == 240


def metric_shelves(widgets, rendered):
    """Equal y does not join metric blocks separated by a chart or an empty lane."""
    by_row = {}
    pixels = {item["id"]: item["pixels"] for item in rendered}
    for widget in widgets:
        if widget["type"] != "metric":
            continue
        parent = widget.get("parentId") or widget["config"].get("layoutParentId") or "root"
        by_row.setdefault((str(parent), widget["y"]), []).append(widget)
    shelves = []
    for (parent, y), row in by_row.items():
        members = []
        for widget in sorted(row, key=lambda w: w["x"]):
            if members and members[-1]["x"] + members[-1]["w"] != widget["x"]:
                shelves.append({"parent": parent, "y": y, "members": members})
                members = []
            members.append({"id": widget["id"], "x": widget["x"], "w": widget["w"], "h": widget["h"],
                            "pixelHeight": pixels[widget["id"]]["height"]})
        shelves.append({"parent": parent, "y": y, "members": members})
    return shelves


def test_metric_shelves_do_not_join_metrics_across_a_chart():
    widgets = [{"id": "a", "type": "metric", "config": {}, "x": 0, "y": 0, "w": 3, "h": 4},
               {"id": "chart", "type": "line", "config": {}, "x": 3, "y": 0, "w": 15, "h": 8},
               {"id": "b", "type": "metric", "config": {}, "x": 18, "y": 0, "w": 3, "h": 3},
               {"id": "c", "type": "metric", "config": {}, "x": 21, "y": 0, "w": 3, "h": 3}]
    rows = metric_shelves(widgets, [{"id": w["id"], "pixels": {"height": w["h"] * 40 - 10}} for w in widgets])
    assert [[m["id"] for m in row["members"]] for row in rows] == [["a"], ["b", "c"]]


def has_aligned_metric_region(outcome, widget_id):
    widgets = {w["id"]: w for w in outcome.get("widgets", [])}
    rendered = {w["id"]: w for w in outcome["rendered"]}
    current, actual = widgets.get(widget_id), rendered.get(widget_id)
    if not current or not actual or not actual.get("metricComplete"):
        return False
    pixels = actual["pixels"]
    if pixels["height"] > 380:
        return False
    parent = lambda w: w.get("parentId") or w.get("config", {}).get("layoutParentId")
    for peer in widgets.values():
        if (peer["type"] not in {"line", "bar", "pie"} or parent(peer) != parent(current) or
                peer["y"] != current["y"] or peer["h"] != current["h"] or peer["id"] not in rendered):
            continue
        other = rendered[peer["id"]]["pixels"]
        if abs(pixels["y"] - other["y"]) <= 1 and abs(pixels["height"] - other["height"]) <= 1:
            return True
    return False


@pytest.mark.parametrize("case", ["aligned", "different-parent", "different-height", "too-tall", "unreadable"])
def test_metric_region_exception_requires_readable_local_alignment(case):
    height = 400 if case == "too-tall" else 230
    outcome = {"widgets": [
        {"id": "m", "type": "metric", "y": 0, "h": 7, "parentId": "group"},
        {"id": "c", "type": "line", "y": 0, "h": 7,
         "parentId": "other" if case == "different-parent" else "group"}],
        "rendered": [
            {"id": "m", "metricComplete": case != "unreadable", "pixels": {"y": 0, "height": height}},
            {"id": "c", "pixels": {"y": 0, "height": height - (30 if case == "different-height" else 0)}}]}
    assert has_aligned_metric_region(outcome, "m") == (case == "aligned")


def metric_landscape_checks(before, outcome, witness, check):
    """Require a real readable alternative, respecting explicit sizes and protected children."""
    if witness is None:
        return
    preset = before["request"]["preset"]
    locked_ids = {rule["widgetId"] for rule in preset.get("sizeOverrides", [])}
    locked_types = {rule["chartType"] for rule in preset.get("typeSizes", [])}
    alternatives = {item["id"]: item for item in witness["rendered"]}
    actual = {item["id"]: item for item in outcome["rendered"]}
    outcome["landscapeAlternatives"] = []
    for original in before["widgets"]:
        widget_id = original["id"]
        if (original["type"] != "metric" or widget_id in locked_ids or
                original["config"]["chartType"] in locked_types or original.get("parentId") or
                original["config"].get("layoutParentId")):
            continue
        alternative = alternatives.get(widget_id)
        if not alternative or widget_id not in actual:
            continue
        pixels = alternative["pixels"]
        if not alternative["metricComplete"] or pixels["width"] <= pixels["height"] or pixels["height"] > 190:
            continue
        current = actual[widget_id]["pixels"]
        evidence = {"id": widget_id, "actualPixels": current, "verifiedAlternativePixels": pixels}
        outcome["landscapeAlternatives"].append(evidence)
        coordinated = has_aligned_metric_region(outcome, widget_id) and current["height"] <= pixels["height"] * 2
        check(current["width"] + 1 >= current["height"] or coordinated,
              f"automatic metric remains portrait despite verified compact landscape alternative: {evidence}")


@pytest.mark.parametrize("exemption", [None, "locked", "protected", "unverified"])
def test_landscape_check_requires_verified_unlocked_alternative(exemption):
    original = {"id": "m", "type": "metric", "config": {"chartType": 2001}}
    preset = {}
    if exemption == "locked":
        preset["sizeOverrides"] = [{"widgetId": "m", "width": 1}]
    if exemption == "protected":
        original["parentId"] = "existing"
    before = {"widgets": [original], "request": {"preset": preset}}
    outcome = {"rendered": [{"id": "m", "pixels": {"width": 40, "height": 120}}]}
    witness = {"rendered": [{"id": "m", "metricComplete": exemption != "unverified",
                             "pixels": {"width": 180, "height": 110}}]}
    failures = []
    metric_landscape_checks(before, outcome, witness,
                            lambda condition, message: None if condition else failures.append(message))
    assert len(failures) == (1 if exemption is None else 0)


CLIPPING = """() => {
  const clipped = [];
  for (const card of document.querySelectorAll('[data-widget-card]')) {
    if (card.querySelector('[data-widget-card]')) continue;
    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!node.textContent.trim() || parent.closest('svg,[aria-hidden="true"]')) continue;
      if (card.querySelector('table') && !parent.closest('thead,[data-card-heading]')) continue;
      if (getComputedStyle(parent).visibility === 'hidden') continue;
      const range = document.createRange(); range.selectNodeContents(node);
      for (const rect of range.getClientRects()) {
        if (!rect.width || !rect.height) continue;
        let ancestor = parent;
        while (ancestor) {
          const style = getComputedStyle(ancestor), box = ancestor.getBoundingClientRect();
          const clipX = ancestor === card || /hidden|clip|auto|scroll/.test(style.overflowX);
          const clipY = ancestor === card || /hidden|clip|auto|scroll/.test(style.overflowY);
          if ((clipX && (rect.left < box.left-1 || rect.right > box.right+1)) ||
              (clipY && (rect.top < box.top-1 || rect.bottom > box.bottom+1))) {
            clipped.push({id:card.dataset.widgetId,text:node.textContent}); break;
          }
          if (ancestor === card) break;
          ancestor = ancestor.parentElement;
        }
      }
    }
  }
  return clipped;
}"""


def validate_region(name, before, outcome, witness, errors, requests):
    """Collect every failed invariant so a packing failure does not hide data or clipping failures."""
    if name == "grouping-widths":
        return validate_group_widths(before, outcome, witness, errors, requests)
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    validate_root_frames(before, check)
    validate_root_frames(outcome, check)
    check(not errors, f"browser errors: {errors}")
    check(not requests, f"unexpected network: {requests}")
    check(outcome["result"]["status"] == "success", f"controller result: {outcome['result']}")
    check(outcome["result"].get("data", {}).get("persisted") is True, "not persisted")
    check(outcome["calls"] == outcome["saves"] == 1, "expected exactly one call and one local save")
    check(outcome["localToolAndRenderMs"] <= 30000, "local tool + stable render exceeded 30s")
    check(outcome["localObservedMs"] <= 30000, "local observed tool + render exceeded 30s")
    check(not outcome["clippedText"], f"clipped content: {outcome['clippedText']}")
    check(not before["clippedText"], f"invalid fixture clips before layout: {before['clippedText']}")
    originals = {w["id"]: w for w in before["widgets"]}
    after = {w["id"]: w for w in outcome["widgets"]}
    check(len(after) == len(outcome["widgets"]), "duplicate widget IDs")
    check(originals.keys() <= after.keys(), "lost original IDs")
    added = after.keys() - originals.keys()
    check(len(added) == (1 if name == "grouping" else 0), f"unexpected new IDs: {added}")
    geometry = {"x", "y", "w", "h", "order"}
    approved = set(before["request"]["preset"].get("groups", [{}])[0].get("widgetIds", []))
    expected_order = [w["id"] for w in before["roots"]]
    if name == "grouping" and len(added) == 1:
        expected_order = [next(iter(added)), *[wid for wid in expected_order if wid not in approved]]
    actual_order = [w["id"] for w in sorted(outcome["roots"], key=lambda w: (w["y"], w["x"]))]
    check(actual_order == expected_order, f"root reading order changed: {actual_order}")
    for widget_id, original in originals.items():
        if widget_id not in after:
            continue
        current = after[widget_id]
        old_config, new_config = dict(original["config"]), dict(current["config"])
        ignored = geometry | {"config"}
        if widget_id in approved:
            ignored |= {"parentId"}
            old_config.pop("layoutParentId", None)
            new_config.pop("layoutParentId", None)
            check(new_config.pop("rootGridSize", None) == {"w": original["w"], "h": original["h"]},
                  f"incorrect restore dimensions: {widget_id}")
        check(old_config == new_config, f"non-layout config changed: {widget_id}")
        check({k: v for k, v in original.items() if k not in ignored} ==
              {k: v for k, v in current.items() if k not in ignored}, f"data/metadata changed: {widget_id}")
    for size in before["request"]["preset"].get("sizeOverrides", []):
        current = after[size["widgetId"]]
        check((current["w"], current["h"]) == (size["width"], size["height"]), f"size lock changed: {size}")
    audit = grid_audit(outcome["roots"])
    outcome["gridAudit"] = audit
    check(not audit["overlaps"], f"overlap: {audit['overlaps']}")
    outcome["metricRows"] = metric_shelves(list(after.values()), outcome["rendered"])
    metric_landscape_checks(before, outcome, witness, check)
    for row in outcome["metricRows"]:
        members = row["members"]
        if len(members) >= 2:
            check(len({m["h"] for m in members}) == 1, f"metric shelf grid heights differ: {row}")
            check(max(m["pixelHeight"] for m in members) - min(m["pixelHeight"] for m in members) <= 1,
                  f"metric shelf pixel heights differ: {row}")
    for item in before["rendered"]:
        original = originals[item["id"]]
        if original["type"] == "metric":
            check(item["metricComplete"], f"invalid fixture metric content: {item['id']}")
        if original["type"] in {"line", "bar"}:
            check(sum(item["ink"]) > 100, f"blank chart before layout: {item['id']}")
    for item in outcome["rendered"]:
        w = after[item["id"]]
        if w["type"] == "metric":
            check(item["metricComplete"], f"incomplete metric: {w['id']}")
            check(item["pixels"]["height"] <= 190 or has_aligned_metric_region(outcome, w["id"]),
                  f"giant metric without a coordinated region: {w['id']}")
            check(w["name"] in item["text"] and str(w["data"]["value"]) in item["text"], f"lost metric text: {w['id']}")
        if w["type"] in {"line", "bar"}:
            check(sum(item["ink"]) > 100, f"blank chart canvas: {w['id']}")
        if w["type"] == "table":
            check(item["tableRows"] == len(w["data"]), "table rows missing")
            check(all(header in item["tableHeaders"] for header in ["Month", "City", "Revenue", "Orders"]), "table headers missing")
    check(after["detail"]["w"] == 24, "wide table narrowed or split")
    if name in {"shelves-7", "shelves-9", "rank-stack"}:
        if witness is not None:
            check(grid_audit(witness["roots"])["emptyCells"] == 0, "invalid independent packing witness")
            check(all(w["metricComplete"] for w in witness["rendered"] if w["metricComplete"] is not None), "witness metrics do not fit")
        check(audit["emptyCells"] == 0, f"avoidable gap despite feasible witness: {audit}")
    if name == "rank-stack":
        rank = after["rank"]
        pixels = next(w["pixels"] for w in outcome["rendered"] if w["id"] == "rank")
        check(rank["w"] <= 8 and pixels["height"] > pixels["width"], "leaderboard is not narrow/tall")
        a, b = after["trend-a"], after["trend-b"]
        check(a["x"] == b["x"] == rank["x"] + rank["w"] and
              a["y"] == rank["y"] and b["y"] == a["y"] + a["h"] and
              b["y"] + b["h"] <= rank["y"] + rank["h"], "two charts are not stacked alongside leaderboard")
    if name == "automatic":
        metrics = [w for w in after.values() if w["type"] == "metric"]
        check(witness is not None, "missing real-rendered two-by-two automatic witness")
        if witness is not None:
            validate_root_frames(witness, check)
            check(all(w["metricComplete"] for w in witness["rendered"] if w["metricComplete"] is not None),
                  "automatic region witness does not fit real metric content")
            check(grid_audit(witness["roots"])["height"] == after["trend"]["h"] + after["detail"]["h"],
                  "automatic region witness is not a shorter feasible arrangement")
            witness_widgets = {w["id"]: w for w in witness["widgets"]}
            check(witness_widgets.keys() == originals.keys(), "automatic witness changed widget IDs")
            for widget_id, original in originals.items():
                proposed = witness_widgets[widget_id]
                check({k: v for k, v in proposed.items() if k not in geometry} ==
                      {k: v for k, v in original.items() if k not in geometry},
                      f"automatic witness changed non-layout data/config: {widget_id}")
                if original["type"] != "metric":
                    check((proposed["w"], proposed["h"]) == (original["w"], original["h"]),
                          f"automatic witness resized a locked non-metric: {widget_id}")
            tail_shapes = [{k: witness_widgets[widget_id][k] for k in ("id", "x", "y", "w", "h")}
                           for widget_id in ("m3", "m4", "m5", "m6")]
            check([(w["x"], w["y"], w["w"], w["h"]) for w in tail_shapes] ==
                  [(0, 3, 6, 3), (6, 3, 6, 3), (0, 6, 6, 3), (6, 6, 6, 3)],
                  "automatic witness is not the real 6x3 two-by-two capability")
            proof_audit = grid_audit(witness["roots"])
            check(not proof_audit["overlaps"] and proof_audit["emptyCells"] == 12,
                  "automatic two-by-two witness has invalid geometry or gap budget")
            check(sum(next(w for w in witness["rendered"] if w["id"] == "trend")["ink"]) > 100,
                  "automatic witness chart is blank")
            metric_area = sum(w["w"] * w["h"] for w in metrics)
            proof_area = sum(w["w"] * w["h"] for w in witness["widgets"] if w["type"] == "metric")
            outcome["automaticGapComparison"] = {
                "actual": audit, "readableTwoByTwo": proof_audit, "verifiedTailShapes": tail_shapes,
                "actualMetricArea": metric_area, "proofMetricArea": proof_area,
            }
            check(audit["emptyCells"] <= proof_audit["emptyCells"],
                  f"avoidable automatic gap versus real readable 6x3 two-by-two: {audit['emptyCells']} > {proof_audit['emptyCells']}")
            check(metric_area <= proof_area, "automatic reduced gaps by inflating metrics beyond readable witness area")
        check(len({w["y"] for w in metrics}) >= 2, "metrics are not a local multi-shelf region")
        check(after["detail"]["y"] <= after["trend"]["h"], "avoidable blank region below/alongside trend")
        check(all(w["w"] <= 12 for w in metrics), "automatic metric expanded into a giant wide card")
    if name == "grouping":
        check(after["existing-child"] == originals["existing-child"], "protected child changed")
        check({k: v for k, v in after["existing"].items() if k not in {"x", "y", "order"}} ==
              {k: v for k, v in originals["existing"].items() if k not in {"x", "y", "order"}}, "existing container resized or reconfigured")
        if len(added) == 1:
            group = after[next(iter(added))]
            members = [w for w in after.values() if w.get("parentId") == group["config"]["cardUid"]]
            outcome["newGroupGeometry"] = {
                "container": {k: group[k] for k in ("id", "x", "y", "w", "h")},
                "children": [{k: w[k] for k in ("id", "x", "y", "w", "h")} for w in members],
            }
            check(group["w"] == 24, "single new group did not use the available root row width")
            check({w["id"] for w in members} == approved, "new group membership changed")
            check([w["id"] for w in sorted(members, key=lambda w: (w["y"], w["x"]))] ==
                  before["request"]["preset"]["groups"][0]["widgetIds"], "group reading order changed")
            check(group["h"] <= 20, "new group inflated despite compact content")
            if witness is not None:
                witness_group = next(w for w in witness["roots"] if w["type"] == "flatLayout" and w["id"] != "existing")
                witness_fits = all(w["metricComplete"] for w in witness["rendered"] if w["metricComplete"] is not None)
                check(witness_fits, "group witness does not fit real metric content")
                if witness_fits:
                    check(group["h"] <= witness_group["h"], "avoidable group height despite readable compact witness")
                tail_witness = witness.get("tailFill")
                if tail_witness is not None:
                    tail_ids = {"m3", "m4", "m5", "m6"}
                    tail_fit = all(w["metricComplete"] for w in tail_witness["rendered"] if w["id"] in tail_ids)
                    check(tail_fit, "tail full-row witness is not readable")
                    tail = sorted((w for w in members if w["id"] in tail_ids), key=lambda w: w["x"])
                    proposed = [w for w in tail_witness["widgets"] if w["id"] in tail_ids]
                    outcome["tailRowWitness"] = {"actual": tail, "proposed": proposed, "readable": tail_fit}
                    check(not grid_audit([w for w in tail_witness["widgets"]
                                          if w.get("parentId") == group["config"]["cardUid"]])["overlaps"],
                          "tail full-row witness overlaps another child")
                    if tail_fit:
                        check(tail[0]["x"] == 0 and sum(w["w"] for w in tail) == 24 and
                              all(b["x"] == a["x"] + a["w"] for a, b in pairwise(tail)),
                              "isolated four-metric tail leaves avoidable blank width despite readable compact full-row witness")
                        check(len({w["h"] for w in tail}) == 1 and max(w["h"] for w in tail) <= 4,
                              "isolated metric tail is taller than its verified common compact height")
            outcome["newGroupGridAudit"] = grid_audit(members)
            check(not outcome["newGroupGridAudit"]["overlaps"], "new group children overlap")
    return failures


def validate_group_widths(before, outcome, witness, errors, requests):
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    validate_root_frames(before, check)
    validate_root_frames(outcome, check)

    check(not errors and not requests, f"browser/network errors: {errors}, {requests}")
    check(outcome["result"]["status"] == "success", f"controller result: {outcome['result']}")
    check(outcome["result"].get("data", {}).get("persisted") is True, "not persisted")
    check(outcome["calls"] == outcome["saves"] == 1, "expected one call / local mocked save")
    check(outcome["localObservedMs"] <= 30000, "local tool+render exceeded 30s")
    check(not before["clippedText"] and not outcome["clippedText"], "clipped text before/after grouping")
    originals = {w["id"]: w for w in before["widgets"]}
    after = {w["id"]: w for w in outcome["widgets"]}
    check(len(after) == len(outcome["widgets"]), "duplicate IDs")
    check(originals.keys() <= after.keys(), "lost original IDs")
    groups = [w for w in outcome["roots"] if w["type"] == "flatLayout"]
    check(len(groups) == len(after.keys() - originals.keys()) == 2, "expected exactly two new groups")
    for old in originals.values():
        if old["id"] not in after:
            continue
        new = after[old["id"]]
        old_config, new_config = dict(old["config"]), dict(new["config"])
        ignored = {"x", "y", "w", "h", "order", "config"}
        if old["type"] == "metric":
            ignored.add("parentId")
            new_config.pop("layoutParentId", None)
            check(new_config.pop("rootGridSize", None) == {"w": old["w"], "h": old["h"]}, "bad restore size")
        check(old_config == new_config, f"config changed: {old['id']}")
        check({k: v for k, v in old.items() if k not in ignored} ==
              {k: v for k, v in new.items() if k not in ignored}, f"data/metadata changed: {old['id']}")
    for group, proposal in zip(groups, before["request"]["preset"]["groups"], strict=False):
        members = [w for w in after.values() if w.get("parentId") == group["config"]["cardUid"]]
        check([w["id"] for w in sorted(members, key=lambda w: (w["y"], w["x"]))] == proposal["widgetIds"],
              f"group membership/order changed: {proposal['title']}")
        check(not grid_audit(members)["overlaps"], "group children overlap")
        for y in {w["y"] for w in members}:
            row = [w for w in members if w["y"] == y]
            check(len({w["h"] for w in row}) == 1, f"group metric shelf heights differ: {row}")
    for snapshot in [before, outcome, witness]:
        if snapshot is None:
            continue
        for item in snapshot["rendered"]:
            if item["metricComplete"] is not None:
                check(item["metricComplete"], f"incomplete real metric: {item['id']}")
            if item["id"] == "detail":
                check(item["tableRows"] == 6 and len(item["tableHeaders"]) == 4, "real table content missing")
    check(all(w["pixels"]["height"] <= 190 for w in outcome["rendered"] if w["metricComplete"] is not None), "giant grouped metric")
    audit = outcome["gridAudit"] = grid_audit(outcome["roots"])
    check(not audit["overlaps"], "root overlap")
    check(audit["emptyCells"] == 0, f"avoidable inter-group width gaps: {audit}")
    check(after["detail"]["w"] == 24 and after["detail"]["h"] == originals["detail"]["h"], "table size changed")
    check(witness is not None, "missing real-rendered paired-width witness")
    metric_landscape_checks(before, outcome, witness, check)
    if witness is not None:
        witness_fits = all(w["metricComplete"] for w in witness["rendered"] if w["metricComplete"] is not None)
        check(witness_fits, "paired-width witness does not fit real metric content")
        if witness_fits:
            check(audit["height"] <= grid_audit(witness["roots"])["height"],
                  "joint group widths produce unnecessary height versus readable paired witness")
    outcome["newGroupGeometry"] = [{k: w[k] for k in ("id", "x", "y", "w", "h")} for w in after.values()]
    return failures


@pytest.mark.parametrize("width", [1440, 1920, 768])
@pytest.mark.parametrize("name", SCENARIOS)
def test_production_region_layout(region_bundle, width, name):
    async def scenario():
        bundle, directory = region_bundle
        reports = []
        reference_witness = None
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": width, "height": 1000})
            requests, errors = [], []

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await context.route("**/*", offline)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def capture_witness(filename):
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / filename))

            await page.expose_function("captureRegionWitness", capture_witness)
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            for index, cache in enumerate(["cold", "warm-1", "warm-2", "warm-3"]):
                stem = f"{name}-{width}-{cache}"
                try:
                    before = await page.evaluate("name => window.prepareRegion(name)", name)
                    await page.wait_for_timeout(500)
                    before["clippedText"] = await page.evaluate(CLIPPING)
                    await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}-before.png"))
                    started = time.monotonic()
                    outcome = await page.evaluate("window.runRegion()")
                    await page.wait_for_timeout(500)
                    outcome["localObservedMs"] = (time.monotonic() - started) * 1000
                    outcome["clippedText"] = await page.evaluate(CLIPPING)
                    await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}-after.png"))
                    if index == 0:
                        reference_witness = await page.evaluate("filename => window.inspectRegionWitness(filename)", f"{stem}-witness.png")
                    witness = reference_witness
                    failures = validate_region(name, before, outcome, witness, errors, requests)
                    report = {"scenario": name, "width": width, "cache": cache, "before": before,
                              "outcome": outcome, "witness": witness, "errors": errors[:],
                              "blockedRequests": requests[:], "failures": failures, "live": False}
                except (PlaywrightError, AssertionError, KeyError, TypeError, ValueError) as exc:
                    report = {"scenario": name, "width": width, "cache": cache, "live": False,
                              "errors": errors[:], "failures": [f"harness/runtime failure: {exc}"]}
                (directory / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                reports.append(report)
            await browser.close()
        failures = {r["cache"]: r["failures"] for r in reports if r["failures"]}
        elapsed = [r["outcome"]["localObservedMs"] for r in reports if "outcome" in r]
        summary = {"scenario": name, "width": width, "live": False,
                   "runs": [{"cache": r["cache"], "failures": r["failures"],
                             "localObservedMs": r.get("outcome", {}).get("localObservedMs"),
                             "calls": r.get("outcome", {}).get("calls"),
                             "mockSaves": r.get("outcome", {}).get("saves")} for r in reports],
                   "maxLocalObservedMs": max(elapsed, default=None), "passed": not failures}
        (directory / f"summary-{name}-{width}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        assert not failures, json.dumps({"scenario": name, "width": width, "failures": failures}, ensure_ascii=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [768, 1440, 1920])
@pytest.mark.parametrize("name", ["chart-group", "chart-group-preserve", "chart-pairs", "chart-pairs-offset"])
def test_grouped_chart_columns_and_empty_tail(region_bundle, width, name):
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": width, "height": 1100})
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await page.route("**/*", offline)
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            for cache in ["cold", "warm-1", "warm-2", "warm-3"]:
                before = await page.evaluate("name => window.prepareRegion(name)", name)
                outcome = await page.evaluate("window.runRegion()")
                stem = f"{name}-{width}-{cache}"
                outcome["clippedText"] = await page.evaluate(CLIPPING)
                (directory / f"{stem}.json").write_text(json.dumps(
                    {"before": before, "outcome": outcome, "errors": errors, "requests": requests, "live": False},
                    ensure_ascii=False, indent=2))
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}.png"))
                assert not errors and not requests
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert outcome["result"]["data"]["persisted"] is True
                assert outcome["calls"] == outcome["saves"] == 1
                assert not outcome["clippedText"], outcome["clippedText"]
                geometry_errors = []
                validate_root_frames(outcome, lambda valid, message: geometry_errors.append(message) if not valid else None)
                assert not geometry_errors, geometry_errors
                originals = {w["id"]: w for w in before["widgets"]}
                after = {w["id"]: w for w in outcome["widgets"]}
                assert len(after) == len(originals) + 1
                children = [after[wid] for wid in originals]
                assert [w["id"] for w in sorted(children, key=lambda w: (w["y"], w["x"]))] == list(originals)
                for old, new in zip(originals.values(), children):
                    ignored = {"x", "y", "w", "h", "order", "parentId", "config"}
                    assert {k: v for k, v in old.items() if k not in ignored} == {k: v for k, v in new.items() if k not in ignored}
                    assert {k: v for k, v in new["config"].items() if k not in {"layoutParentId", "rootGridSize"}} == old["config"]
                audit = grid_audit(children)
                assert not audit["overlaps"]
                if name == "chart-group-preserve":
                    assert children[1]["y"] > children[0]["y"]
                    assert any(issue["code"] == "LAYOUT_COMPARISON_STACKED" for issue in outcome["result"]["issues"])
                else:
                    assert after[outcome["roots"][0]["id"]]["w"] == 24
                    assert audit["emptyCells"] == 0, audit
                    for left_id, right_id in before["request"]["preset"]["groups"][0]["comparisonPairs"]:
                        left, right = after[left_id], after[right_id]
                        assert left["y"] == right["y"] and left["h"] == right["h"]
                        assert left["x"] == 0 and right["x"] == left["w"]
                        assert left["w"] + right["w"] == 24
                    if name == "chart-pairs-offset":
                        assert children[0]["y"] + children[0]["h"] <= after["amount"]["y"]
                    assert not any(issue["code"] == "LAYOUT_COMPARISON_STACKED" for issue in outcome["result"]["issues"])
                    assert children[-1]["w"] == 24 and children[-1]["x"] == 0
                    assert children[-1]["y"] == max(w["y"] + w["h"] for w in children[:-1])
                rendered = {item["id"]: item for item in outcome["rendered"]}
                for child in children[:-1]:
                    assert sum(rendered[child["id"]]["ink"]) > 100, rendered[child["id"]]
                assert children[-1]["data"] == []
                assert "未找到符合条件的结果" in rendered[children[-1]["id"]]["text"]
            await browser.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [1440, 1920, 768])
@pytest.mark.parametrize("name", ["automatic", "grouping"])
def test_read_only_production_measurement_export(region_bundle, width, name):
    """Separate page: diagnostic probes cannot warm or replace controller regression measurements."""
    async def scenario():
        bundle, directory = region_bundle
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": width, "height": 1000})
            errors, requests = [], []

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await context.route("**/*", offline)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            prepared = await page.evaluate("name => window.prepareRegion(name)", name)
            evidence = await page.evaluate("window.inspectMeasurements()")
            measurement = evidence["measurement"]
            supplemental = None
            if name == "automatic":
                supplemental = await page.evaluate("window.inspectSupplementalShapes()")
                (directory / f"{name}-{width}-supplemental-shapes.json").write_text(
                    json.dumps(supplemental, ensure_ascii=False, indent=2)
                )
            (directory / f"{name}-{width}-measurements.json").write_text(
                json.dumps(measurement, ensure_ascii=False, indent=2)
            )
            (directory / f"{name}-{width}-measurement-evidence.json").write_text(json.dumps({
                "live": False, "purpose": "Read-only production collector export for unit reproduction; no controller apply.",
                "scenario": name, "width": width, "request": prepared["request"], "evidence": evidence,
                "errors": errors, "blockedRequests": requests,
            }, ensure_ascii=False, indent=2))
            await browser.close()
        assert not errors and not requests
        assert evidence["before"] == evidence["after"], "collector changed widget/revision state or did not restore metric DOM"
        assert evidence["before"]["widgets"] == prepared["widgets"]
        assert evidence["after"]["calls"] == evidence["after"]["saves"] == 0
        if supplemental is not None:
            assert supplemental["before"] == supplemental["after"], "supplemental probe did not restore state/DOM"
            assert supplemental["after"]["calls"] == supplemental["after"]["saves"] == 0
            for item in supplemental["samples"]:
                if item["widgetId"] in {"m3", "m4", "m5", "m6"}:
                    exact = next(candidate for candidate in item["candidates"]
                                 if candidate["requested"] == {"width": 6, "height": 3})
                    assert {"width": 6, "height": 3} in exact["verified"], item
        assert measurement["canvasWidth"] == width - 16
        assert [item["widgetId"] for item in measurement["items"]] == [w["id"] for w in prepared["widgets"]]
        metrics = [item for item in measurement["items"] if item["widgetId"].startswith("m")]
        assert len(metrics) == 7
        for item in metrics:
            assert item["state"] == "ready" and item["contentWidth"] > 0 and item["contentHeight"] > 0
            assert item["compactSizes"], item
            assert all(isinstance(size["width"], int) and isinstance(size["height"], int) and
                       0 < size["width"] <= 24 and size["height"] > 0 for size in item["compactSizes"])

    asyncio.run(scenario())


TABLE_READABILITY = """async () => {
  const card = document.querySelector('[data-widget-id="detail"]');
  const body = card.querySelector('.ant-table-body');
  if (!body) throw new Error('Real Table scroll body missing');
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
  body.scrollLeft = 0; body.scrollTop = 0; await frame(); await frame();
  const headers = [...card.querySelectorAll('thead th')].filter(e => e.getBoundingClientRect().width > 0 && e.innerText.trim());
  const left = body.getBoundingClientRect();
  const visibleHeaders = headers.filter(e => {
    const r = e.getBoundingClientRect(); return r.left >= left.left-1 && r.right <= left.right+1;
  }).length;
  const failures = [], checked = [];
  const cells = [...headers, ...card.querySelectorAll('tbody tr[data-row-key] td')];
  for (const cell of cells) {
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (!node.textContent.trim() || node.parentElement.closest('svg,[aria-hidden="true"]')) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      let r = range.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      let box = body.getBoundingClientRect();
      body.scrollLeft += r.left - box.left - (body.clientWidth-r.width)/2;
      if (cell.tagName === 'TD') body.scrollTop += r.top-box.top-(body.clientHeight-r.height)/2;
      await frame(); await frame();
      r = range.getBoundingClientRect(); box = body.getBoundingClientRect();
      const own = cell.getBoundingClientRect(), outer = card.getBoundingClientRect();
      const visibleY = cell.tagName === 'TD' ? box : card.querySelector('.ant-table-header').getBoundingClientRect();
      const readable = r.left >= Math.max(box.left, own.left, outer.left)-1 &&
        r.right <= Math.min(box.left+body.clientWidth, own.right, outer.right)+1 &&
        r.top >= Math.max(visibleY.top, own.top, outer.top)-1 &&
        r.bottom <= Math.min(visibleY.bottom, own.bottom, outer.bottom)+1;
      checked.push(node.textContent.trim());
      if (!readable) failures.push({text:node.textContent.trim(),cell:cell.tagName,
        textBox:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},
        own:{left:own.left,right:own.right,top:own.top,bottom:own.bottom},scrollLeft:body.scrollLeft});
    }
  }
  body.scrollLeft = 0; body.scrollTop = 0; await frame(); await frame();
  return {clientWidth:body.clientWidth,scrollWidth:body.scrollWidth,clientHeight:body.clientHeight,
    scrollHeight:body.scrollHeight,visibleHeaders,headers:headers.map(e=>e.innerText),checked,failures};
}"""


@pytest.mark.parametrize("width", [1440, 1920, 768])
@pytest.mark.parametrize("name", ["table-expand", "table-width-lock"])
def test_real_table_content_width_and_readability(region_bundle, width, name):
    """Use the real Ant Table, including horizontal scrolling, not a rectangle stand-in."""
    async def scenario():
        bundle, directory = region_bundle
        reports = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(viewport={"width": width, "height": 1000})
            errors, requests = [], []

            async def offline(route):
                requests.append(route.request.url)
                await route.abort()

            await context.route("**/*", offline)
            page = await context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            for cache in ["cold", "warm-1", "warm-2", "warm-3"]:
                stem = f"{name}-{width}-{cache}"
                before = await page.evaluate("name => window.prepareRegion(name)", name)
                before["tableReadability"] = await page.evaluate(TABLE_READABILITY)
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}-before.png"))
                outcome = await page.evaluate("window.runRegion()")
                outcome["tableReadability"] = await page.evaluate(TABLE_READABILITY)
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}-after.png"))
                await page.locator('[data-widget-id="detail"] .ant-table-body').evaluate(
                    "async e => {e.scrollLeft=e.scrollWidth; await new Promise(r=>requestAnimationFrame(r));}"
                )
                await page.get_by_test_id("dashboard-v2-canvas").screenshot(path=str(directory / f"{stem}-after-right.png"))
                failures = []

                def check(condition, message, failures=failures):
                    if not condition:
                        failures.append(message)

                validate_root_frames(before, check)
                validate_root_frames(outcome, check)
                check(not errors and not requests, f"browser/network errors: {errors}, {requests}")
                check(outcome["result"]["status"] == "success", f"controller result: {outcome['result']}")
                check(outcome["result"].get("data", {}).get("persisted") is True, "not persisted")
                check(outcome["calls"] == outcome["saves"] == 1, "expected one controller call / local mocked save")
                check(outcome["localToolAndRenderMs"] <= 30000, "local tool+render exceeded 30s")
                check(len(before["widgets"]) == len(outcome["widgets"]) == 1, "widget count changed")
                old, new = before["widgets"][0], outcome["widgets"][0]
                check({k: v for k, v in old.items() if k not in {"x", "y", "w", "order"}} ==
                      {k: v for k, v in new.items() if k not in {"x", "y", "w", "order"}},
                      "ID, original height, config or data changed")
                check(not grid_audit(outcome["roots"])["overlaps"], "table geometry invalid")
                for label, snapshot in [("before", before), ("after", outcome)]:
                    read = snapshot["tableReadability"]
                    check(not read["failures"], f"{label} unreadable text even after actual scrolling: {read['failures']}")
                    check(len(read["headers"]) == 6 and len(read["checked"]) == 42,
                          f"{label} real header/body text coverage missing: {read}")
                    check(snapshot["rendered"][0]["tableRows"] == 6, f"{label} missing real table rows")
                initial, final = before["tableReadability"], outcome["tableReadability"]
                check(initial["scrollWidth"] > initial["clientWidth"], "fixture must initially need horizontal scrolling")
                check(initial["checked"] == final["checked"], "rendered cell values changed")
                if name == "table-expand":
                    check(new["w"] == 24, f"isolated automatic table did not fill available row: {new['w']}")
                    check(final["clientWidth"] > initial["clientWidth"] * 2, "table viewport did not widen")
                    check(final["visibleHeaders"] > initial["visibleHeaders"], "expansion did not improve simultaneous column visibility")
                    if width >= 1440:
                        check(final["scrollWidth"] <= final["clientWidth"] + 1, "desktop expanded table still needs horizontal scrolling")
                        check(final["visibleHeaders"] == 6, "expanded desktop headers not all simultaneously visible")
                else:
                    check(new["w"] == old["w"] == 8, "explicit width lock changed")
                    check(final["clientWidth"] == initial["clientWidth"], "locked table pixel width changed")
                report = {"scenario": name, "width": width, "cache": cache, "live": False,
                          "before": before, "outcome": outcome, "failures": failures,
                          "errors": errors[:], "blockedRequests": requests[:]}
                (directory / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                reports.append(report)
            await browser.close()
        failures = {r["cache"]: r["failures"] for r in reports if r["failures"]}
        (directory / f"summary-{name}-{width}.json").write_text(json.dumps({
            "scenario": name, "width": width, "live": False, "passed": not failures,
            "runs": [{"cache": r["cache"], "failures": r["failures"],
                      "calls": r["outcome"]["calls"], "mockSaves": r["outcome"]["saves"]} for r in reports],
        }, ensure_ascii=False, indent=2))
        assert not failures, json.dumps(failures, ensure_ascii=False)

    asyncio.run(scenario())
