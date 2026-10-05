import time

import pytest

from app.dashboard_layout import regional
from app.dashboard_layout.solver import LayoutProblem, audit


@pytest.mark.parametrize("source", ["derived_geometry", "derived_stamp", "explicit_saved", None])
def test_complete_bands_can_change_columns_before_growth_cost(source, monkeypatch):
    metrics = [f"m{i}" for i in range(4)]
    order = metrics + ["rank", "chart"]
    body = {
        "version": "constraint-v1",
        "nodes": [
            {"id": id, "kind": "metric", "shapes": [{"w": 6, "h": 3, "cost": 1}]}
            for id in metrics
        ] + [
            {"id": "rank", "kind": "rank", "shapes": [
                {"w": 6, "h": 9, "cost": 1}, {"w": 6, "h": 12, "cost": 1},
            ]},
            {"id": "chart", "kind": "chart", "shapes": [
                {"w": 18, "h": 9, "cost": 1},
                {"w": 12, "h": 12, "cost": 10},
            ]},
        ],
        "orders": [order],
    }
    if source is not None:
        body["orderSources"] = [source]
    problem = LayoutProblem.model_validate(body)
    bands = [dict(id=id, x=i*6, y=0, w=6, h=3, variant=0) for i, id in enumerate(metrics)]
    bands += [dict(id="rank", x=0, y=3, w=6, h=9, variant=0),
              dict(id="chart", x=6, y=3, w=18, h=9, variant=0)]
    columns = [dict(id=id, x=0, y=i*3, w=6, h=3, variant=0) for i, id in enumerate(metrics)]
    columns += [dict(id="rank", x=6, y=0, w=6, h=12, variant=0),
                dict(id="chart", x=12, y=0, w=12, h=12, variant=0)]
    assert audit(problem, bands)["valid"] and audit(problem, columns)["valid"]
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(problem, {
        "status": "feasible", "placements": columns, "readingOrders": [order],
        "alternatives": [{"placements": bands, "readingOrders": [order]}],
    }, deadline=time.monotonic()+2)
    selected = {row["id"]: row for row in result["placements"]}
    assert result["quality"]["gapCells"] == 0
    assert selected["chart"]["w"] == (18 if source in ("derived_geometry", "derived_stamp") else 12)
