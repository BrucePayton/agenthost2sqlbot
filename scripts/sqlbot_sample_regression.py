"""Run a fixed natural-language sample through Host; never persist query rows.

Usage: python -m scripts.sqlbot_sample_regression --cases cases.json --output run-dir
Each case supplies case_id, agent_id, question; optional fields are preserved.
Every run opens a fresh session. There are no retries or question rewrites.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


def execution_summary(value: dict) -> list[dict]:
    return [
        {key: step.get(key) for key in
         ("operate", "duration", "error", "start_time", "finish_time", "total_tokens")}
        for step in value.get("execution", {}).get("steps", [])
    ]


async def consume(client: httpx.AsyncClient, path: str, payload: dict, report: dict,
                  phase: str) -> dict | None:
    result = None
    done = False
    async with client.stream("POST", path, json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            kind = event.get("type")
            if kind == "id":
                report["record_id" if phase == "query" else "analysis_record_id"] = event.get("id")
            elif kind == "sql":
                report["sql"] = event.get("content")
            elif kind == "chart-type":
                report["chart_type"] = event.get("content")
            elif kind == "analysis-result" and isinstance(event.get("content"), str):
                report["answer"] = report.get("answer", "") + event["content"]
            elif kind == "error":
                report["errors"].append({"phase": phase, "code": event.get("code"),
                                         "message": event.get("content"), "details": event.get("details")})
            elif kind == "sql-data" and event.get("content") == "execute-success":
                report["execute_success"] = True
            elif kind == "result":
                result = event["result"]
            elif kind == "done":
                done = True
    if not done:
        raise RuntimeError("SSE ended before done")
    return result


async def run_case(client: httpx.AsyncClient, case: dict) -> dict:
    report = {"case": case, "errors": [], "execute_success": False,
              "query_success": False, "analysis_success": False}
    start = time.monotonic()
    path = f"/api/data-agents/{case['agent_id']}"
    try:
        response = await client.post(path + "/open")
        response.raise_for_status()
        report["session_id"] = response.json()["session_id"]
        result = await consume(client, path + "/ask/stream",
                               {"session_id": report["session_id"], "question": case["question"]},
                               report, "query")
        report["query_seconds"] = round(time.monotonic() - start, 3)
        if result:
            report.update(query_success=True, record_id=result.get("recordId"),
                          sql=result.get("sql"), row_count=result.get("rowCount"),
                          chart_type=result.get("chartHint", {}).get("type"))
            report["steps"] = execution_summary(result.get("presentation", {}))
            result_id = result["resultId"]
            # Release the result rows before invoking analysis.
            del result
            analysis = await consume(client, path + f"/results/{result_id}/actions/analysis",
                                     {}, report, "analysis")
            if analysis:
                report["analysis_steps"] = execution_summary(analysis)
                report["answer"] = analysis.get("record", {}).get("analysis") or report.get("answer")
                report["analysis_success"] = bool(report["answer"]) and not report["errors"]
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        report["errors"].append({"phase": "transport", "message": str(exc)})
    report["seconds"] = round(time.monotonic() - start, 3)
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    if len(cases) != 10 or len({c["case_id"] for c in cases}) != 10:
        parser.error("Provide exactly 10 unique cases")
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.glob("*.json")):
        parser.error("Output directory must not contain previous results")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=240, trust_env=False) as client:
        for case in cases:
            report = await run_case(client, case)
            (args.output / f"{case['case_id']}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"case": case["case_id"], **{k: report.get(k) for k in
                  ("record_id", "query_success", "analysis_success", "seconds", "errors")}},
                  ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
