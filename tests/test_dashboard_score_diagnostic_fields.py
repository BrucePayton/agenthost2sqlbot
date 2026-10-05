import time

from app.dashboard_layout.regional import refine_regions
from app.dashboard_layout.solver import LayoutProblem


def test_selected_score_field_names_match_actual_values():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [{"id": "m", "kind": "metric", "shapes": [{"w": 24, "h": 3, "cost": 7}]}],
        "orders": [["m"]],
        "orderSources": ["derived_geometry"],
    })
    result = refine_regions(problem, {
        "status": "feasible",
        "placements": [dict(id="m", x=0, y=0, w=24, h=3, variant=0)],
    }, deadline=time.monotonic() + 1)
    diagnostics = result["readingDiagnostics"]
    fields, values = diagnostics["scorePolicy"]["fields"], diagnostics["selectedScore"]
    assert len(fields) == len(values)
    named = dict(zip(fields, values))
    assert named["totalHeight"] == result["quality"]["totalHeight"] == 3
    assert named["firstMetricAnchorDisplacement"] == 0
    assert named["shapeCost"] == result["quality"]["shapeCost"] == 7
