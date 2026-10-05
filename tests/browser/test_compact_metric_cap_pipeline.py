"""Compact 3x-minimum coverage through real DOM and the route compute adapter.

This synthetic five-metric fixture is separate from organize-mode evidence and
does not claim live HTTP, persistence or original forty-card acceptance.
"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

from app.dashboard_layout.routes import READING_CONTRACT, solver_revision
from app.dashboard_layout.solver import audit
from tests.browser.test_organize_pipeline import MemoryDiagnostics, compute_request, solve_request
from tests.browser.test_region_layout import grid_audit, region_bundle, region_source_hashes


def _assert_frozen_metric_caps(value, applied):
    """Use pre-growth DOM observations and frozen diagnostic floors, not expanded shapes."""
    diagnostics = value["preparationDiagnostics"]
    assert diagnostics["mode"] == "compact"
    assert diagnostics["sizing"] == "content"
    events = diagnostics["events"]
    by_id = {row["id"]: row for row in applied}
    metrics = [node for node in value["nodes"] if node["kind"] == "metric"]
    assert len(metrics) == 5
    for node in metrics:
        evidence = [event for event in events if event["nodeId"] == node["id"]]
        original = next(event for event in evidence if event["phase"] == "original")
        assert original["reason"] == "unchanged"
        assert all(value is None for value in original.get("explicit", {}).values())
        observed = [event for event in evidence if event["phase"] == "observation"]
        assert len(observed) == 1 and observed[0]["reason"] == "observed"
        observation = observed[0]
        assert not observation.get("truncated"), "Need a complete pre-growth observed profile"
        assert len(observation["sizes"]) == observation["outputCount"] > 0
        baseline = min(observation["sizes"], key=lambda size: (size["w"] * size["h"], size["w"], size["h"]))
        floors = [event["growthFloor"] for event in evidence if event["phase"] == "filter_growth"]
        assert floors and all(floor == baseline for floor in floors), (node["id"], baseline, floors)
        assert any((shape["w"], shape["h"]) == (baseline["w"], baseline["h"]) for shape in node["shapes"])
        maximum = {"w": min(24, 3 * baseline["w"]), "h": min(100, 3 * baseline["h"])}
        for shape in [*node["shapes"], by_id[node["id"]]]:
            assert 0 < shape["w"] <= maximum["w"], (node["id"], "width", baseline, shape)
            assert 0 < shape["h"] <= maximum["h"], (node["id"], "height", baseline, shape)


def test_compact_metric_caps_from_real_dom_cold_and_three_warm_runs(region_bundle):
    """Check real compact minima, locked trend, reading order and gap without templates."""
    async def run():
        bundle, directory = region_bundle
        davinci = Path(os.environ.get("DAVINCI_ROOT", Path(__file__).resolve().parents[3] / "davinci"))
        hashes = region_source_hashes(davinci)
        assert hashes == json.loads((directory / "environment.json").read_text())["sourceSha256"]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL"))
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1200})
                await page.route("**/*", lambda route: route.abort())
                requests, responses, errors = [], [], []
                store = MemoryDiagnostics()
                page.on("pageerror", lambda error: errors.append(str(error)))

                async def compute(value):
                    requests.append(value)
                    response = await asyncio.to_thread(compute_request, solve_request(value), store)
                    responses.append(response)
                    return response

                await page.expose_function("solveRegion", compute)
                await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
                await page.add_script_tag(path=str(bundle))
                for iteration in range(4):
                    requests.clear()
                    responses.clear()
                    errors.clear()
                    store.records.clear()
                    before = await page.evaluate("window.prepareRegion('metric-only-bottoms')")
                    stem = f"compact-metric-only-bottoms-1440-{iteration}"
                    await page.screenshot(path=str(directory / f"{stem}-before.png"), full_page=True)
                    outcome = await page.evaluate("window.runRegion()")
                    (directory / f"{stem}.json").write_text(json.dumps({
                        "evidenceType": "synthetic_real_dom_compact_route_adapter", "live": False,
                        "cache": "cold" if iteration == 0 else "warm", "before": before,
                        "outcome": outcome, "problems": requests, "solverResults": responses,
                        "diagnosticRecords": list(store.records.values()), "pageErrors": errors
                    }, ensure_ascii=False, indent=2))
                    await page.screenshot(path=str(directory / f"{stem}-after.png"), full_page=True)
                    assert not errors, errors
                    assert before["request"]["preset"] == {
                        "mode": "compact", "sizing": "content",
                        "sizeOverrides": [{"widgetId": "trend", "width": 24, "height": 6}]
                    }
                    assert outcome["result"]["status"] == "success", outcome["result"]
                    assert len(requests) == len(responses) == outcome["calls"] == outcome["saves"] == 1
                    summary = outcome["result"]["data"]["summary"]
                    assert summary["executionMode"] == "remote"
                    assert responses[0]["readingContract"] == READING_CONTRACT
                    assert responses[0]["solverRevision"] == solver_revision()
                    assert summary["layoutRunId"] == responses[0]["layoutRunId"]
                    assert summary["layoutRunIds"] == [responses[0]["layoutRunId"]]
                    record = store.records[summary["layoutRunId"]]
                    assert record["result"]["readingContract"] == READING_CONTRACT
                    assert record["result"]["solverRevision"] == solver_revision()
                    assert outcome["localToolAndRenderMs"] < 30000
                    roots = outcome["roots"]
                    assert len(roots) == len({row["id"] for row in roots}) == 6
                    assert {row["id"] for row in roots} == {row["id"] for row in before["roots"]}
                    problem = solve_request(requests[0]).problem
                    solved = responses[0]
                    variants = {row["id"]: row["variant"] for row in solved["placements"]}
                    applied = [{**{key: row[key] for key in ("id", "x", "y", "w", "h")},
                                "variant": variants[row["id"]]} for row in roots]
                    report = audit(problem, applied, reading_orders=solved["readingOrders"])
                    assert report["valid"], report
                    assert solved["readingOrders"] == problem.orders == [["m0", "m1", "m2", "m3", "m4", "trend"]]
                    _assert_frozen_metric_caps(requests[0], applied)
                    trend = next(row for row in roots if row["id"] == "trend")
                    assert (trend["w"], trend["h"]) == (24, 6)
                    geometry = grid_audit(roots)
                    assert not geometry["overlaps"] and geometry["emptyCells"] == 0, geometry
                    metrics = [row for row in outcome["rendered"] if row["id"] != "trend"]
                    assert len(metrics) == 5 and all(row["metricComplete"] is True for row in metrics)
                    rendered_trend = next(row for row in outcome["rendered"] if row["id"] == "trend")
                    assert sum(rendered_trend["ink"]) > 100
                    old_by_id = {row["id"]: row for row in before["widgets"]}
                    for row in outcome["widgets"]:
                        old = old_by_id[row["id"]]
                        assert row["data"] == old["data"]
                        assert row["config"].get("scorecardFontSize") == old["config"].get("scorecardFontSize")
            finally:
                await browser.close()
        assert region_source_hashes(davinci) == hashes, "Frontend sources changed during compact replay"

    asyncio.run(run())
