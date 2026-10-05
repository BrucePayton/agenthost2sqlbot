"""Numbered screenshot structures, using synthetic readable domains, not live data."""
import pytest

from app.dashboard_layout.solver import LayoutProblem, audit, solve


# IDs mirror the red labels. Domains are not coordinates copied from the images.
CASES = {
    "015": [("metric", 6, 4), ("metric", 6, 4), ("metric", 6, 4), ("chart", 18, 12)],
    "016": [("metric", 8, 4), ("chart", 8, 12), ("chart", 16, 12), ("metric", 16, 4)],
    "017": [("metric", 16, 4), ("chart", 16, 12), ("chart", 8, 16)],
    "018": [("metric", 4, 4), ("metric", 4, 4), ("chart", 8, 8), ("chart", 12, 8), ("chart", 24, 8)],
}


@pytest.mark.parametrize("case", CASES)
def test_numbered_regional_structure(case):
    """Solve from shapes and order; assert stacking rather than row-major order."""
    nodes = [dict(id=str(i), kind=kind, shapes=[dict(w=w, h=h)],
                  original=dict(x=0, y=(i-1)*16, w=max(4, (w+1)//2), h=h))
             for i, (kind, w, h) in enumerate(CASES[case], 1)]
    problem = LayoutProblem(version="constraint-v1", objective="organize-v1",
                            nodes=nodes, orders=[[n["id"] for n in nodes]], budgetMs=2000)
    result = solve(problem)
    assert result["status"] == "feasible"
    assert audit(problem, result["placements"])["valid"]
    assert result["gapCells"] == 0
    rows = {row["id"]: row for row in result["placements"]}
    assert set(rows) == {node["id"] for node in nodes}
    for node in nodes:
        assert rows[node["id"]]["w"] <= 2 * node["original"]["w"]
        assert rows[node["id"]]["h"] <= 2 * node["original"]["h"]

    def stack(first, second):
        """Two successive cards share both vertical edges without a grid hole."""
        a, b = rows[first], rows[second]
        assert (a["x"], a["w"], a["y"] + a["h"]) == (b["x"], b["w"], b["y"])

    if case == "015":
        stack("1", "2")
        stack("2", "3")
        right, left_bottom = "4", "3"
    elif case == "016":
        stack("1", "2")
        stack("3", "4")
        right, left_bottom = "3", "2"
        assert rows["2"]["y"] > rows["3"]["y"]
        assert rows["2"]["y"] + rows["2"]["h"] == rows["4"]["y"] + rows["4"]["h"]
    elif case == "017":
        stack("1", "2")
        right, left_bottom = "3", "2"
        assert rows["2"]["y"] > rows["3"]["y"]
    else:
        stack("1", "2")
        assert rows["1"]["y"] == rows["3"]["y"] == rows["4"]["y"]
        assert rows["1"]["x"] + rows["1"]["w"] == rows["3"]["x"]
        assert rows["3"]["x"] + rows["3"]["w"] == rows["4"]["x"]
        assert rows["2"]["y"] + rows["2"]["h"] == rows["3"]["y"] + rows["3"]["h"] == rows["4"]["y"] + rows["4"]["h"] == rows["5"]["y"]
        assert (rows["5"]["x"], rows["5"]["w"]) == (0, 24)
        return
    assert rows["1"]["x"] + rows["1"]["w"] == rows[right]["x"]
    assert rows["1"]["y"] == rows[right]["y"]
    if case != "016":
        assert rows[left_bottom]["y"] + rows[left_bottom]["h"] == rows[right]["y"] + rows[right]["h"]
