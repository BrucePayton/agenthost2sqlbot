"""Keep real SDK receipts while native subscription actions run without inference.

The original SDK tool use remains pending until the next semantic interaction.
Program calls have independent durable identities and never become SDK results.
"""

from dataclasses import asdict

from app.runtime.contracts import RuntimeRequest, RuntimeToolResult


def subscription_sdk_results(request: RuntimeRequest, task: dict) -> tuple[RuntimeToolResult, ...]:
    """Return only genuine SDK receipts, including ones withheld during native work."""
    results = {}
    for raw in task.get("sdk_pending_results", []):
        if isinstance(raw, dict) and raw.get("origin", "model") == "model":
            result = RuntimeToolResult(**raw)
            results[result.tool_call_id] = result
    for result in request.tool_results:
        if result.origin == "model":
            results[result.tool_call_id] = result
    return tuple(results.values())


def retain_subscription_sdk_results(request: RuntimeRequest, task: dict) -> None:
    """Checkpoint actual deferred receipts before bypassing the SDK on this turn."""
    task["sdk_pending_results"] = [asdict(result) for result in subscription_sdk_results(request, task)]


def acknowledge_subscription_sdk_results(task: dict, delivered: tuple[RuntimeToolResult, ...]) -> None:
    """Only an observed SDK result acknowledges delivery; a failed query can resume."""
    ids = {result.tool_call_id for result in delivered}
    pending = [raw for raw in task.get("sdk_pending_results", [])
               if raw.get("tool_call_id") not in ids]
    if pending:
        task["sdk_pending_results"] = pending
    else:
        task.pop("sdk_pending_results", None)
