"""JSON stdin/stdout worker for an isolated, compatible solver interpreter."""
import sys

from app.dashboard_layout.diagnostics import capture
from app.dashboard_layout.solver import LayoutProblem, solve


def main():
    import json
    payload = sys.stdin.buffer.read(1500001)
    if len(payload) > 1500000:
        raise ValueError("layout payload too large")
    problem = LayoutProblem.model_validate_json(payload)
    with capture() as counters:
        result = solve(problem)
    result["_diagnosticCounters"] = dict(counters)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
