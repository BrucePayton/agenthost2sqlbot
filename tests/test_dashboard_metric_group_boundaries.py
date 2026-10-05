from app.dashboard_layout.regional import _metric_group_height_excess, _metric_group_imbalance, _metric_neighbor_imbalance
from app.dashboard_layout.solver import LayoutProblem


def test_independent_metric_groups_do_not_compare_sizes_across_chart():
    order = ["a", "b", "chart", "c", "d"]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": id, "kind": "metric", "shapes": [{"w": w, "h": h}]}
            for id, w, h in [("a", 6, 3), ("b", 6, 3), ("c", 4, 4), ("d", 4, 4)]
        ] + [{"id": "chart", "kind": "chart", "shapes": [{"w": 12, "h": 3}]}],
        "orders": [order],
        "orderSources": ["derived_geometry"],
    })
    rows = [
        dict(id=id, x=x, y=y, w=w, h=h, variant=0)
        for id, x, y, w, h in [
            ("a", 0, 0, 6, 3), ("b", 6, 0, 6, 3), ("chart", 12, 0, 12, 3),
            ("c", 0, 3, 4, 4), ("d", 4, 3, 4, 4),
        ]
    ]
    assert _metric_neighbor_imbalance(problem, rows, [order]) == 0
    uneven = [dict(row, w=5) if row["id"] == "b" else row for row in rows]
    assert _metric_neighbor_imbalance(problem, uneven, [order]) > 0


def test_single_metric_stretch_is_not_exempt_from_height_cost():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [{"id": "m", "kind": "metric", "shapes": [
            {"w": 4, "h": 3}, {"w": 4, "h": 8},
        ]}],
        "orders": [["m"]], "orderSources": ["derived_geometry"],
    })
    row = dict(id="m", x=0, y=0, w=4, h=8, variant=0)
    assert _metric_group_height_excess(problem, [row], problem.orders) == 5
    assert _metric_group_height_excess(problem, [dict(row, h=3)], problem.orders) == 0


def test_full_width_last_grid_card_is_a_span_not_size_imbalance():
    ids = [f"m{i}" for i in range(7)]
    rows = [dict(id=id, x=i % 2 * 4, y=i // 2 * 4,
                 w=8 if i == 6 else 4, h=4, variant=0) for i, id in enumerate(ids)]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [dict(id=row["id"], kind="metric", shapes=[dict(w=row["w"], h=row["h"])]) for row in rows],
        "orders": [ids], "orderSources": ["derived_geometry"],
    })
    assert _metric_group_imbalance(problem, rows, [ids]) == 0
    assert _metric_neighbor_imbalance(problem, rows, [ids]) == 0
    uneven = [dict(row, w=5) if row["id"] == "m1" else row for row in rows]
    assert _metric_group_imbalance(problem, uneven, [ids]) > 0


def test_aligned_metric_region_accepts_stacked_neighbors_without_external_gaps(monkeypatch):
    import time
    from app.dashboard_layout import regional

    ids = ["a", "b", "chart", "next"]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1", "orders": [ids], "orderSources": ["derived_geometry"],
        "nodes": [dict(id=id, kind="metric", shapes=[dict(w=6, h=4)]) for id in ids[:2]] + [
            dict(id="chart", kind="chart", shapes=[dict(w=18, h=4), dict(w=18, h=8)]),
            dict(id="next", kind="chart", shapes=[dict(w=18, h=4), dict(w=24, h=4)]),
        ],
    })
    metrics = [dict(id=id, x=0, y=i*4, w=6, h=4, variant=0) for i, id in enumerate(ids[:2])]
    fragmented = metrics + [dict(id="chart", x=6, y=0, w=18, h=4, variant=0),
                            dict(id="next", x=6, y=4, w=18, h=4, variant=0)]
    aligned = metrics + [dict(id="chart", x=6, y=0, w=18, h=8, variant=0),
                         dict(id="next", x=0, y=8, w=24, h=4, variant=0)]
    monkeypatch.setattr(regional, "region_plans", lambda *args, **kwargs: [])
    result = regional.refine_regions(problem, {
        "status": "feasible", "placements": fragmented, "readingOrders": [ids],
        "alternatives": [dict(placements=aligned, readingOrders=[ids])],
    }, deadline=time.monotonic() + 1)
    # Both arrangements fill their regions; the screenshot is not a fixed template.
    assert result["placements"] == fragmented
    assert result["quality"]["gapCells"] == 0
