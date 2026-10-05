"""Synthetic content through production DOM preparation, Host solver and renderers."""
import asyncio
import copy
import json
import os
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import pytest
from playwright.async_api import async_playwright
from pydantic import ValidationError

from app.dashboard_layout.routes import READING_CONTRACT, SolveRequest, recorded_compute, solver_revision
from app.dashboard_layout.solver import audit
from tests.browser.test_region_layout import region_bundle, grid_audit, region_source_hashes


def solve_request(value):
    """Adapt the browser callback to the Host request model before solving."""
    problem = dict(value)
    preparation = problem.pop("preparationDiagnostics", None)
    return SolveRequest.model_validate({"toolCallId": "organize-browser-replay",
                                       "problem": problem,
                                       "preparationDiagnostics": preparation})


class MemoryDiagnostics:
    """Test-only record sink; response metadata still comes from the real route worker."""

    def __init__(self):
        self.records = {}

    def save(self, record):
        """Retain independent snapshots without writing a production diagnostics store."""
        self.records[record["layoutRunId"]] = copy.deepcopy(record)
        return True


def compute_request(request, store=None):
    """Use the real route compute/metadata path, bypassing only HTTP and owner auth."""
    return recorded_compute(request.problem, time.monotonic() + request.problem.budgetMs / 1000,
                            Event(), store if store is not None else MemoryDiagnostics(),
                            str(uuid4()), "organize-browser-session", request.toolCallId,
                            request.preparationDiagnostics)


@pytest.mark.parametrize("name", ["organize-metric-trend-rank", "organize-metrics-charts-table"])
@pytest.mark.parametrize("width", [1440, 1920])
def test_organize_real_preparation_and_solver(region_bundle, name, width):
    async def run():
        bundle, directory = region_bundle
        davinci = Path(os.environ.get("DAVINCI_ROOT", Path(__file__).resolve().parents[3] / "davinci"))
        source_hashes = region_source_hashes(davinci)
        assert source_hashes == json.loads((directory / "environment.json").read_text())["sourceSha256"]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL"))
            page = await browser.new_page(viewport={"width": width, "height": 1200})
            await page.route("**/*", lambda route: route.abort())
            problems = []
            adapter_errors = []
            solver_results = []
            store = MemoryDiagnostics()
            async def compute(value):
                problems.append(value)
                try:
                    request = solve_request(value)
                except ValidationError as error:
                    adapter_errors.extend(error.errors(include_input=False, include_url=False))
                    raise
                result = await asyncio.to_thread(compute_request, request, store)
                solver_results.append(result)
                return result
            await page.expose_function("solveRegion", compute)
            await page.set_content('<html><body style="margin:8px;background:#f4f5f7"><div id="root"></div></body></html>')
            await page.add_script_tag(path=str(bundle))
            for iteration in range(4):
                problems.clear()
                adapter_errors.clear()
                solver_results.clear()
                store.records.clear()
                before = await page.evaluate("name => window.prepareRegion(name)", name)
                stem = f"{name}-{width}-{iteration}"
                await page.screenshot(path=str(directory / f"{stem}-before.png"), full_page=True)
                outcome = await page.evaluate("window.runRegion()")
                (directory / f"{stem}.json").write_text(json.dumps(dict(before=before, outcome=outcome,
                    problems=problems, adapterErrors=adapter_errors, solverResults=solver_results,
                    diagnosticRecords=list(store.records.values()),
                    cache="cold" if iteration == 0 else "warm"), ensure_ascii=False, indent=2))
                await page.screenshot(path=str(directory / f"{stem}-after.png"), full_page=True)
                assert not adapter_errors, adapter_errors
                assert outcome["result"]["status"] == "success", outcome["result"]
                assert len(problems) == 1
                assert len(solver_results) == 1
                summary = outcome["result"]["data"]["summary"]
                assert summary["executionMode"] == "remote"
                assert solver_results[0]["readingContract"] == READING_CONTRACT
                assert solver_results[0]["solverRevision"] == solver_revision()
                # The receipt exposes a run ID, not duplicate protocol fields. Bind
                # remote application to the real recorded response without schema drift.
                assert summary["layoutRunId"] == solver_results[0]["layoutRunId"]
                assert summary["layoutRunIds"] == [solver_results[0]["layoutRunId"]]
                record = store.records[summary["layoutRunId"]]
                assert record["result"]["readingContract"] == READING_CONTRACT
                assert record["result"]["solverRevision"] == solver_revision()
                assert outcome["saves"] == 1
                assert outcome["localToolAndRenderMs"] < 30000
                assert all(r["metricComplete"] is not False for r in outcome["rendered"])
                geometry = grid_audit(outcome["roots"])
                assert not geometry["overlaps"], geometry
                assert geometry["emptyCells"] == 0, geometry
                for old, new in zip(before["widgets"], outcome["widgets"]):
                    assert old["id"] == new["id"] and old["data"] == new["data"]
                    assert old["config"].get("scorecardFontSize") == new["config"].get("scorecardFontSize")
                rows = {r["id"]: r for r in outcome["roots"]}
                if name.endswith("rank"):
                    # References are not templates: audit the selected reading order
                    # and applied domain geometry, including valid nested tilings.
                    solved = solver_results[0]
                    variants = {row["id"]: row["variant"] for row in solved["placements"]}
                    applied = [{**{key: row[key] for key in ("id", "x", "y", "w", "h")},
                                "variant": variants[row["id"]]} for row in outcome["roots"]]
                    report = audit(solve_request(problems[0]).problem, applied,
                                   reading_orders=solved["readingOrders"])
                    assert report["valid"], report
                    assert report["gapCells"] == 0, report
                else:
                    assert rows["m1"]["x"] == rows["m2"]["x"]
                    assert rows["m2"]["y"] > rows["m1"]["y"]
                    assert rows["share"]["y"] == rows["distribution"]["y"]
                    assert rows["detail"]["w"] == 24
            await browser.close()
        assert region_source_hashes(davinci) == source_hashes, "Frontend sources changed during replay"
    asyncio.run(run())
