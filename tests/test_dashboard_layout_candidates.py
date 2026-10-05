from app.dashboard_layout.solver import (
    LayoutProblem,
    audit,
    plan_key,
    rank_candidates,
)


def make_problem():
    return LayoutProblem.model_validate({
        "version": "constraint-v1",
        "budgetMs": 2000,
        "nodes": [
            {
                "id": id,
                "shapes": [
                    {"w": 8, "h": 4, "cost": 0},
                    {"w": 12, "h": 4, "cost": 0},
                    {"w": 16, "h": 4, "cost": 0},
                ],
            }
            for id in ("a", "b", "c", "d")
        ],
    })


def placement(id, x, y, w):
    return {"id": id, "x": x, "y": y, "w": w, "h": 4, "variant": 0}


def test_rank_candidates_prefers_gaps_then_alignment_then_height():
    problem = make_problem()
    aligned = [
        placement("a", 0, 0, 12), placement("b", 12, 0, 12),
        placement("c", 0, 4, 12), placement("d", 12, 4, 12),
    ]
    misaligned = [
        placement("a", 0, 0, 8), placement("b", 8, 0, 16),
        placement("c", 0, 4, 16), placement("d", 16, 4, 8),
    ]
    gapped = [
        placement("a", 0, 0, 12), placement("b", 12, 0, 12),
        placement("c", 0, 4, 8), placement("d", 8, 4, 8),
    ]

    ranked = rank_candidates(problem, [misaligned, gapped, aligned])

    assert list(ranked[0].placements) == aligned
    assert ranked[0].quality["largestGapCells"] == 0
    assert ranked[0].quality["alignedBoundaryCount"] > ranked[1].quality["alignedBoundaryCount"]
    assert ranked[-1].quality["gapCells"] > 0


def test_rank_candidates_prefers_regionally_valid_layout_over_smaller_local_hole():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "metric", "kind": "metric", "shapes": [
                {"w": 9, "h": 2}, {"w": 21, "h": 12},
            ]},
            {"id": "chart", "kind": "chart", "shapes": [{"w": 24, "h": 8}]},
        ],
        "orders": [["metric", "chart"]],
    })
    locally_sparse = [
        {"id": "metric", "x": 0, "y": 0, "w": 9, "h": 2, "variant": 0},
        {"id": "chart", "x": 0, "y": 2, "w": 24, "h": 8, "variant": 0},
    ]
    regionally_valid = [
        {"id": "metric", "x": 0, "y": 0, "w": 21, "h": 12, "variant": 0},
        {"id": "chart", "x": 0, "y": 12, "w": 24, "h": 8, "variant": 0},
    ]

    ranked = rank_candidates(problem, [locally_sparse, regionally_valid])

    assert ranked[0].quality["gapCells"] == 36
    assert list(ranked[0].placements) == regionally_valid


def test_equal_quality_prefers_chart_growth_over_metric_growth():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "metric", "kind": "metric", "shapes": [
                {"w": 6, "h": 4, "cost": 0},
                {"w": 12, "h": 4, "cost": 600},
            ]},
            {"id": "chart", "kind": "chart", "shapes": [
                {"w": 12, "h": 4, "cost": 0},
                {"w": 18, "h": 4, "cost": 50},
            ]},
        ],
        "orders": [["metric", "chart"]],
    })
    metric_growth = [
        placement("metric", 0, 0, 12), placement("chart", 12, 0, 12),
    ]
    chart_growth = [
        placement("metric", 0, 0, 6), placement("chart", 6, 0, 18),
    ]

    ranked = rank_candidates(problem, [metric_growth, chart_growth])

    assert list(ranked[0].placements) == chart_growth


def test_equal_metric_peers_split_their_filled_region_evenly():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": metric, "kind": "metric", "shapes": [
                {"w": 4, "h": 4, "cost": 0},
                {"w": 6, "h": 4, "cost": 200},
                {"w": 8, "h": 4, "cost": 400},
            ]}
            for metric in ("left", "right")
        ] + [
            {"id": "bar", "kind": "chart", "shapes": [{"w": 12, "h": 8}]},
            {"id": "pie", "kind": "chart", "shapes": [{"w": 12, "h": 12}]},
        ],
        "orders": [["left", "right", "bar", "pie"]],
    })
    uneven = [
        placement("left", 0, 0, 8), placement("right", 8, 0, 4),
        {"id": "bar", "x": 0, "y": 4, "w": 12, "h": 8, "variant": 0},
        {"id": "pie", "x": 12, "y": 0, "w": 12, "h": 12, "variant": 0},
    ]
    even = [
        placement("left", 0, 0, 6), placement("right", 6, 0, 6),
        {"id": "bar", "x": 0, "y": 4, "w": 12, "h": 8, "variant": 0},
        {"id": "pie", "x": 12, "y": 0, "w": 12, "h": 12, "variant": 0},
    ]

    ranked = rank_candidates(problem, [uneven])

    assert list(ranked[0].placements) == even
    assert ranked[0].quality["metricRowImbalance"] == 0


def test_five_metric_peers_fill_twenty_four_columns_with_one_cell_difference():
    nodes = [
        {"id": f"metric-{index}", "kind": "metric", "shapes": [
            {"w": 4, "h": 4}, {"w": 5, "h": 4},
        ]}
        for index in range(5)
    ] + [{"id": "line", "kind": "chart", "shapes": [{"w": 24, "h": 8}]}]
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": nodes,
        "orders": [[node["id"] for node in nodes]],
    })
    sparse = [
        placement(f"metric-{index}", index * 4, 0, 4)
        for index in range(5)
    ] + [{"id": "line", "x": 0, "y": 4, "w": 24, "h": 8, "variant": 0}]

    ranked = rank_candidates(problem, [sparse])

    metrics = [row for row in ranked[0].placements if row["id"].startswith("metric-")]
    assert sum(row["w"] for row in metrics) == 24
    assert max(row["w"] for row in metrics) - min(row["w"] for row in metrics) == 1
    assert ranked[0].quality["gapCells"] == 0


def test_isolated_chart_row_expands_to_an_available_full_width_shape():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "metric", "kind": "metric", "shapes": [{"w": 24, "h": 4}]},
            {"id": "line", "kind": "chart", "shapes": [
                {"w": 22, "h": 8, "cost": 0},
                {"w": 24, "h": 8, "cost": 50},
            ]},
        ],
        "orders": [["metric", "line"]],
    })
    side_gaps = [
        {"id": "metric", "x": 0, "y": 0, "w": 24, "h": 4, "variant": 0},
        {"id": "line", "x": 1, "y": 4, "w": 22, "h": 8, "variant": 0},
    ]

    ranked = rank_candidates(problem, [side_gaps])

    line = next(row for row in ranked[0].placements if row["id"] == "line")
    assert (line["x"], line["w"]) == (0, 24)
    assert ranked[0].quality["gapCells"] == 0


def test_isolated_portrait_ranking_is_not_forced_to_full_width():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [{"id": "rank", "kind": "rank", "shapes": [
            {"w": 6, "h": 15, "cost": 0},
            {"w": 24, "h": 15, "cost": 50},
        ]}],
        "orders": [["rank"]],
    })
    portrait = [{"id": "rank", "x": 0, "y": 0, "w": 6, "h": 15, "variant": 0}]

    ranked = rank_candidates(problem, [portrait])

    assert list(ranked[0].placements) == portrait


def test_measured_ranking_may_widen_to_continue_an_existing_column_boundary():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "top-left", "kind": "chart", "shapes": [{"w": 8, "h": 8}]},
            {"id": "top-middle", "kind": "chart", "shapes": [{"w": 8, "h": 8}]},
            {"id": "metric-left", "kind": "metric", "shapes": [{"w": 4, "h": 8}]},
            {"id": "metric-right", "kind": "metric", "shapes": [{"w": 4, "h": 8}]},
            {"id": "rank", "kind": "rank", "shapes": [
                {"w": 6, "h": 15}, {"w": 8, "h": 15},
            ]},
            {"id": "bar", "kind": "chart", "shapes": [
                {"w": 16, "h": 15}, {"w": 18, "h": 15},
            ]},
        ],
        "orders": [[
            "top-left", "top-middle", "metric-left", "metric-right", "rank", "bar",
        ]],
    })
    top = [
        {"id": "top-left", "x": 0, "y": 0, "w": 8, "h": 8, "variant": 0},
        {"id": "top-middle", "x": 8, "y": 0, "w": 8, "h": 8, "variant": 0},
        {"id": "metric-left", "x": 16, "y": 0, "w": 4, "h": 8, "variant": 0},
        {"id": "metric-right", "x": 20, "y": 0, "w": 4, "h": 8, "variant": 0},
    ]
    misaligned = top + [
        {"id": "rank", "x": 0, "y": 8, "w": 6, "h": 15, "variant": 0},
        {"id": "bar", "x": 6, "y": 8, "w": 18, "h": 15, "variant": 0},
    ]
    aligned = top + [
        {"id": "rank", "x": 0, "y": 8, "w": 8, "h": 15, "variant": 0},
        {"id": "bar", "x": 8, "y": 8, "w": 16, "h": 15, "variant": 0},
    ]

    ranked = rank_candidates(problem, [misaligned])

    rank = next(row for row in ranked[0].placements if row["id"] == "rank")
    assert rank["w"] == 8
    assert list(ranked[0].placements) == aligned
    assert ranked[0].quality["gapCells"] == 0
    assert ranked[0].quality["misalignedBlockCount"] < ranked[1].quality["misalignedBlockCount"]

    for constraint in ("fallback-only", "fixed-neighbor"):
        restricted = problem.model_copy(deep=True)
        if constraint == "fallback-only":
            restricted.nodes[4].shapes = restricted.nodes[4].shapes[:1]
        else:
            restricted.nodes[5].fixedX = 6
        kept = rank_candidates(restricted, [misaligned])
        assert list(kept[0].placements) == misaligned

    stacked = problem.model_copy(deep=True)
    stacked.nodes[-1].shapes = [shape.model_copy(update={"h": 7})
                               for shape in stacked.nodes[-1].shapes]
    stacked.nodes.append(stacked.nodes[-1].model_copy(update={
        "id": "lower", "shapes": [shape.model_copy(update={"h": 8})
                                     for shape in stacked.nodes[-1].shapes],
    }))
    stacked.orders[0].append("lower")
    stacked_input = misaligned[:-1] + [dict(misaligned[-1], h=7),
                                      dict(misaligned[-1], id="lower", y=15, h=8)]
    widened = rank_candidates(stacked, [stacked_input])[0]
    assert [(row["x"], row["w"]) for row in widened.placements[-2:]] == [(8, 16), (8, 16)]
    assert widened.quality["gapCells"] == 0


def test_plan_key_is_stable_and_geometry_sensitive():
    problem = make_problem()
    aligned = [
        placement("a", 0, 0, 12), placement("b", 12, 0, 12),
        placement("c", 0, 4, 12), placement("d", 12, 4, 12),
    ]
    misaligned = [
        placement("a", 0, 0, 8), placement("b", 8, 0, 16),
        placement("c", 0, 4, 16), placement("d", 16, 4, 8),
    ]

    assert plan_key(problem, aligned) == plan_key(problem, list(reversed(aligned)))
    assert plan_key(problem, aligned) != plan_key(problem, misaligned)


def test_large_gaps_are_quality_findings_not_hard_rejections():
    problem = make_problem()
    gapped = [
        placement("a", 0, 0, 12), placement("b", 12, 0, 12),
        placement("c", 0, 4, 8), placement("d", 8, 4, 8),
    ]

    ranked = rank_candidates(problem, [gapped])

    assert len(ranked) == 1
    assert ranked[0].quality["gapCells"] == 32


def test_order_accepts_small_left_stack_before_large_right_card():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "small-a", "shapes": [{"w": 8, "h": 4}]},
            {"id": "small-b", "shapes": [{"w": 8, "h": 4}]},
            {"id": "large", "shapes": [{"w": 16, "h": 8}]},
        ],
        "orders": [["small-a", "small-b", "large"]],
    })
    plan = [
        {"id": "small-a", "x": 0, "y": 0, "w": 8, "h": 4, "variant": 0},
        {"id": "small-b", "x": 0, "y": 4, "w": 8, "h": 4, "variant": 0},
        {"id": "large", "x": 8, "y": 0, "w": 16, "h": 8, "variant": 0},
    ]

    assert audit(problem, plan)["valid"]


def test_order_accepts_large_left_card_before_small_right_stack():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "large", "shapes": [{"w": 16, "h": 8}]},
            {"id": "small-a", "shapes": [{"w": 8, "h": 4}]},
            {"id": "small-b", "shapes": [{"w": 8, "h": 4}]},
        ],
        "orders": [["large", "small-a", "small-b"]],
    })
    plan = [
        {"id": "large", "x": 0, "y": 0, "w": 16, "h": 8, "variant": 0},
        {"id": "small-a", "x": 16, "y": 0, "w": 8, "h": 4, "variant": 0},
        {"id": "small-b", "x": 16, "y": 4, "w": 8, "h": 4, "variant": 0},
    ]

    assert audit(problem, plan)["valid"]


def test_order_accepts_two_consecutive_columns_in_business_sequence():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": node_id, "shapes": [{"w": 8, "h": 4}]}
            for node_id in ("one", "two", "three", "four")
        ],
        "orders": [["one", "two", "three", "four"]],
    })
    plan = [
        {"id": "one", "x": 0, "y": 0, "w": 8, "h": 4, "variant": 0},
        {"id": "two", "x": 0, "y": 4, "w": 8, "h": 4, "variant": 0},
        {"id": "three", "x": 8, "y": 0, "w": 8, "h": 4, "variant": 0},
        {"id": "four", "x": 8, "y": 4, "w": 8, "h": 4, "variant": 0},
    ]

    assert audit(problem, plan)["valid"]


def test_order_accepts_normal_row_major_traversal_around_a_spanning_card():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "top-left", "shapes": [{"w": 12, "h": 4}]},
            {"id": "right-span", "shapes": [{"w": 12, "h": 8}]},
            {"id": "bottom-left", "shapes": [{"w": 12, "h": 4}]},
        ],
        "orders": [["top-left", "right-span", "bottom-left"]],
    })
    plan = [
        {"id": "top-left", "x": 0, "y": 0, "w": 12, "h": 4, "variant": 0},
        {"id": "right-span", "x": 12, "y": 0, "w": 12, "h": 8, "variant": 0},
        {"id": "bottom-left", "x": 0, "y": 4, "w": 12, "h": 4, "variant": 0},
    ]

    assert audit(problem, plan)["valid"]


def test_order_rejects_a_true_pairwise_reversal():
    problem = LayoutProblem.model_validate({
        "version": "constraint-v1",
        "nodes": [
            {"id": "first", "shapes": [{"w": 12, "h": 4}]},
            {"id": "second", "shapes": [{"w": 12, "h": 4}]},
        ],
        "orders": [["first", "second"]],
    })
    reversed_plan = [
        {"id": "first", "x": 12, "y": 0, "w": 12, "h": 4, "variant": 0},
        {"id": "second", "x": 0, "y": 0, "w": 12, "h": 4, "variant": 0},
    ]

    assert audit(problem, reversed_plan) == {"valid": False, "reason": "order"}
