import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path
from threading import Event
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, model_validator

from app.api.dependencies import AppServices, Identity, get_services
from app.dashboard_layout.diagnostics import capture, store_for
from app.dashboard_layout.preparation_diagnostics import (
    sanitize_preparation_diagnostics,
)
from app.dashboard_layout.solver import GeometryModel, LayoutProblem, solve

router = APIRouter(prefix="/api")
Services = Annotated[AppServices, Depends(get_services)]
READING_CONTRACT = "local-regions-v1"


class SolveRequest(GeometryModel):
    toolCallId: str = Field(min_length=1, max_length=256)
    layoutAttempt: int = Field(default=1, ge=1, le=2)
    problem: LayoutProblem
    preparationDiagnostics: Any = None


class AppliedChange(GeometryModel):
    """Geometry only, in frontend grid coordinates (not solver parent variants)."""
    id: str = Field(min_length=1, max_length=128)
    x: int = Field(ge=0, le=24)
    y: int = Field(ge=0, le=10000)
    w: int = Field(ge=1, le=24)
    h: int = Field(ge=1, le=10000)


FrontendErrorCode = Literal[
    "INVALID_ARGUMENT", "RESOURCE_CHANGED", "PERMISSION_DENIED",
    "EXECUTION_FAILED", "PERSISTENCE_OUTCOME_UNKNOWN",
]
FrontendIssueCode = Literal[
    "CHILD_SET_MISMATCH", "CONTAINER_CHANGE_REJECTED", "CONTAINER_NOT_FOUND",
    "CONTENT_SIZE_FALLBACK", "CONTENT_SIZING_LIMIT", "DUPLICATE_WIDGET",
    "GROUPING_COMMITTED_CONFLICT", "GROUPING_CONFIRMATION_REQUIRED", "GROUPING_CONTENT_UNAVAILABLE",
    "GROUPING_CONTEXT_CHANGED", "GROUPING_INVALID_PROPOSAL", "GROUPING_NOT_AVAILABLE",
    "GROUPING_NOT_WRITABLE", "GROUPING_PENDING_WRITES", "GROUPING_PENDING_WRITES_FAILED",
    "GROUPING_PENDING_WRITES_TIMEOUT", "GROUPING_PREFLIGHT_FAILED", "GROUPING_RELOAD_REQUIRED",
    "GROUPING_PREVIEW_REQUIRED", "GROUPING_SAVE_REJECTED", "GROUPING_SAVE_UNCONFIRMED",
    "GROUPING_UNSAVED_CHANGES", "LAYOUT_BELOW_MINIMUM", "LAYOUT_BOUNDARY_ALIGNMENT",
    "LAYOUT_CANCELLED", "LAYOUT_CANVAS_CHANGED", "LAYOUT_CANVAS_UNAVAILABLE",
    "LAYOUT_CHILD_OVERFLOW", "LAYOUT_COMPARISON_STACKED", "LAYOUT_CONFLICT",
    "LAYOUT_CONTAINER_CYCLE", "LAYOUT_CONTAINER_UNSUPPORTED", "LAYOUT_CONTAINER_WHITESPACE",
    "LAYOUT_CONTENT_CHANGED",
    "LAYOUT_CONTENT_NOT_READY_BEFORE_DEADLINE", "LAYOUT_CONTENT_OVERFLOW",
    "LAYOUT_CONTENT_UNAVAILABLE", "LAYOUT_EMPTY_CANVAS", "LAYOUT_EMPTY_CHANGE",
    "LAYOUT_EXTERNAL_EMPTY_RATIO", "LAYOUT_FRAME_ALIGNMENT", "LAYOUT_HIDDEN_CONTENT_UNAVAILABLE",
    "LAYOUT_INCOMPLETE", "LAYOUT_INPUT_UNAVAILABLE", "LAYOUT_INVALID_BOUNDS",
    "LAYOUT_INVALID_IDENTITY", "LAYOUT_INVALID_OVERRIDE", "LAYOUT_INVALID_SHAPE",
    "LAYOUT_LARGE_EMPTY_REGION", "LAYOUT_LEADERBOARD_SHAPE", "LAYOUT_LINEAR_FALLBACK",
    "LAYOUT_MEASUREMENT_LIMIT", "LAYOUT_MEASUREMENT_TIMEOUT", "LAYOUT_NATIVE_PROJECTION_CHANGED",
    "LAYOUT_NESTED_CONTAINER_UNSUPPORTED", "LAYOUT_NO_READABLE_CANDIDATES",
    "LAYOUT_ORDER_INVALID", "LAYOUT_ORDER_MISMATCH", "LAYOUT_OUT_OF_BOUNDS",
    "LAYOUT_OVERLAP", "LAYOUT_POSITIONS_RETAINED", "LAYOUT_REGIONAL_EMPTY_RATIO",
    "LAYOUT_REMAINING_SPACE", "LAYOUT_RULE_INCOMPLETE",
    "LAYOUT_GROUPING_ONLY",
    "LAYOUT_SCOPE_UNSUPPORTED", "LAYOUT_STALE_BEFORE_SAVE", "LAYOUT_VERIFICATION_FALLBACK",
    "NOT_A_LAYOUT_CONTAINER", "NOT_A_TAB_LAYOUT", "PERSISTENCE_OUTCOME_UNKNOWN",
    "PERSIST_FAILED", "READ_ONLY", "RESOURCE_STALE", "WIDGET_NOT_FOUND",
]


class FrontendOutcome(GeometryModel):
    """Allowlisted evidence; never accept a raw tool receipt with business text."""
    toolCallId: str = Field(min_length=1, max_length=256)
    status: Literal["success", "partial", "error", "unknown"]
    persisted: bool = False
    executionMode: Literal["remote", "linear_fallback"] | None = None
    fallbackReason: Literal["remote_unavailable", "candidate_validation_failed"] | None = None
    errorCode: FrontendErrorCode | None = None
    firstIssueCode: FrontendIssueCode | None = None
    writeDispatched: bool | None = None
    committed: bool | None = None
    persistenceStage: Literal["transport", "backend_response", "receipt_validation"] | None = None
    persistenceReason: Literal[
        "transport_failed", "backend_rejected", "response_unconfirmed", "receipt_incomplete",
        "local_state_conflict",
    ] | None = None
    preflightStage: Literal[
        "pending_writes", "snapshot_validation", "context_validation",
        "proposal_validation", "capability_validation", "permission_validation",
        "content_validation", "unknown",
    ] | None = None
    preflightError: str | None = Field(default=None, min_length=1, max_length=300)
    changes: list[AppliedChange] = Field(default_factory=list, max_length=200)
    changesTruncated: bool = False

    @model_validator(mode="after")
    def coherent_failure_evidence(self):
        failure_fields = (
            self.errorCode is not None or self.firstIssueCode is not None
            or self.writeDispatched is not None or self.committed is not None
            or self.persistenceStage is not None or self.persistenceReason is not None
            or self.preflightStage is not None or self.preflightError is not None
        )
        if failure_fields and (self.status != "error" or self.persisted):
            raise ValueError("failure evidence requires an unpersisted error outcome")
        if (self.persistenceStage is None) != (self.persistenceReason is None):
            raise ValueError("persistence stage and reason must be reported together")
        if (self.preflightStage is None) != (self.preflightError is None):
            raise ValueError("preflight stage and error must be reported together")
        if self.preflightStage is not None and self.writeDispatched is not False:
            raise ValueError("preflight evidence requires a confirmed undispatched write")
        if self.committed is not None and self.committed is not True:
            raise ValueError("committed evidence must be affirmative")
        expected_reasons = {
            "transport": "transport_failed",
            "receipt_validation": {"receipt_incomplete", "local_state_conflict"},
        }
        if self.persistenceStage is not None:
            if self.writeDispatched is not True:
                raise ValueError("persistence failure evidence requires a dispatched write")
            if self.persistenceStage == "backend_response":
                valid_reason = self.persistenceReason in {"backend_rejected", "response_unconfirmed"}
            else:
                expected = expected_reasons[self.persistenceStage]
                valid_reason = self.persistenceReason in expected if isinstance(expected, set) \
                    else expected == self.persistenceReason
            if not valid_reason:
                raise ValueError("persistence stage and reason do not match")
        if self.committed is True and (
            self.persistenceStage != "receipt_validation"
            or self.persistenceReason not in {"receipt_incomplete", "local_state_conflict"}
        ):
            raise ValueError("committed conflict requires receipt validation evidence")
        return self


@lru_cache(maxsize=1)
def solver_revision() -> str:
    """Fingerprint actual solver sources, including dirty/unreleased worktrees."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def recorded_compute(problem, deadline, cancelled, store, run_id, session_id, call_id,
                     preparation_diagnostics=None, layout_attempt=1):
    """Keep diagnostic I/O off the event loop and return an ID even on solve failure."""
    started = time.monotonic()

    def elapsed_ms(since):
        return round(min(10_000_000, max(0, (time.monotonic() - since) * 1000)), 3)

    preparation, preparation_status = sanitize_preparation_diagnostics(preparation_diagnostics, problem)
    order_sources = getattr(problem, "orderSources", None)
    summary = {
        "nodeCount": len(problem.nodes), "shapeCount": sum(len(n.shapes) for n in problem.nodes),
        "rootCount": sum(n.parentId is None for n in problem.nodes),
        "containerCount": sum(n.container for n in problem.nodes),
        "scopeCount": len({n.parentId for n in problem.nodes}), "orderCount": len(problem.orders),
        "orderedNodeCount": sum(len(order) for order in problem.orders),
        "orderSourceCounts": dict(Counter(order_sources if order_sources is not None
                                         else ["legacy_strict"] * len(problem.orders))),
        "requiredBeforeCount": len(getattr(problem, "requiredBefore", [])),
        "originalCount": sum(n.original is not None for n in problem.nodes),
        "kindCounts": {kind: sum(n.kind == kind for n in problem.nodes)
                       for kind in ("metric", "chart", "rank", "other")},
        "budgetMs": problem.budgetMs, "objective": problem.objective,
    }
    timings = {}
    record = {
        "layoutRunId": run_id, "sessionId": session_id, "toolCallId": call_id,
        "layoutAttempt": layout_attempt,
        "createdAt": time.time(), "solverRevision": solver_revision(),
        "problem": problem.model_dump(mode="json"), "result": None, "frontendOutcome": None,
        "preparationDiagnostics": preparation, "preparationDiagnosticsStatus": preparation_status,
        "trace": {"counters": {}, "inputSummary": summary, "stageTimingsMs": timings,
                  "readingContract": READING_CONTRACT},
        "state": "running",
    }
    timings["preparation"] = elapsed_ms(started)
    save_started = time.monotonic()
    if store:
        store.save(record)
    timings["initialSave"] = elapsed_ms(save_started)
    compute_started = time.monotonic()
    with capture() as counters:
        try:
            result = compute(problem, deadline, cancelled)
        except Exception as error:  # noqa: BLE001 - preserve a diagnostic ID at the worker boundary
            # No raw exception text or subprocess payload in the persisted record.
            result = {"version": problem.version, "status": "compute_error"}
            record["errorType"] = type(error).__name__
    timings["compute"] = elapsed_ms(compute_started)
    child_counters = result.pop("_diagnosticCounters", {})
    result = dict(result, solverRevision=record["solverRevision"], readingContract=READING_CONTRACT)
    record["trace"]["counters"] = dict(counters) | child_counters
    record["result"] = result
    record["state"] = "finished"
    record["finishedAt"] = time.time()
    timings["totalBeforeFinalSave"] = elapsed_ms(started)
    stored = store.save(record) if store else False
    return dict(result, layoutRunId=run_id, diagnosticsStored=stored,
                layoutAttempt=layout_attempt)


@router.get("/sessions/{session_id}/dashboardLayoutCapabilities")
async def get_layout_capabilities(session_id: str, response: Response,
                                  identity: Identity, services: Services):
    await services.workspace_access.require_session_owner(identity, session_id)
    response.headers["Cache-Control"] = "no-store"
    return {"version": "constraint-v1", "readingContract": READING_CONTRACT,
            "solverRevision": solver_revision()}


@router.get("/sessions/{session_id}/dashboardLayoutRuns/{run_id}")
async def get_layout_run(session_id: str, run_id: UUID, response: Response,
                         identity: Identity, services: Services):
    """Owner-only diagnostic read, independent of the expired tool invocation."""
    await services.workspace_access.require_session_owner(identity, session_id)
    response.headers["Cache-Control"] = "no-store"
    store = store_for(services)
    record = await asyncio.to_thread(store.get, str(run_id), session_id) if store else None
    if record is None:
        raise HTTPException(404, "Layout diagnostic unavailable or expired")
    return record


@router.post("/sessions/{session_id}/dashboardLayoutRuns/{run_id}/outcome")
async def report_layout_outcome(session_id: str, run_id: UUID, body: FrontendOutcome,
                                response: Response, identity: Identity, services: Services):
    """Attach bounded client evidence to its matching session/tool invocation."""
    await services.workspace_access.require_session_owner(identity, session_id)
    response.headers["Cache-Control"] = "no-store"
    store = store_for(services)
    saved = await asyncio.to_thread(store.outcome, str(run_id), session_id,
        body.model_dump(mode="json")) if store else False
    if not saved:
        raise HTTPException(404, "Layout diagnostic unavailable or expired")
    return {"stored": True}


class LayoutJobs:
    def __init__(self):
        self.active = 0
        self.claimed: dict[tuple[str, str, int], float] = {}


def compute(problem, deadline, cancelled):
    """Interpreter selection is deployment configuration, never request input."""
    python = os.environ.get("DAVINCI_LAYOUT_PYTHON")
    if not python:
        return solve(problem, deadline=deadline, cancelled=cancelled)
    completed = subprocess.run([python, "-m", "app.dashboard_layout.worker"],
        input=problem.model_dump_json(), text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=max(0.01, deadline-time.monotonic()+1), check=True)
    return json.loads(completed.stdout)


@router.post("/sessions/{session_id}/dashboardLayoutSolve")
async def solve_dashboard_layout(session_id: str, body: SolveRequest, request: Request,
                                 identity: Identity, services: Services):
    """Private geometry computation, never a new model tool or dashboard write."""
    await services.workspace_access.require_session_owner(identity, session_id)
    bridge = services.frontend_tool_bridges.active_for_thread(session_id)
    call = bridge.lookup_call(body.toolCallId) if bridge else None
    active = bool(call and call.pending and call.public_name == "dashboard.set_widget_layout"
                  and call.expires_at > asyncio.get_running_loop().time())
    if not active and not await services.deferred_frontend_tools.pending_layout_call(session_id, body.toolCallId):
        raise HTTPException(409, "No active layout invocation")
    if not hasattr(request.app.state, "dashboard_layout_jobs"):
        request.app.state.dashboard_layout_jobs = LayoutJobs()
    jobs = request.app.state.dashboard_layout_jobs
    now = time.monotonic()
    jobs.claimed = {key: expiry for key, expiry in jobs.claimed.items() if expiry > now}
    key = (session_id, body.toolCallId, body.layoutAttempt)
    if key in jobs.claimed:
        raise HTTPException(409, "Layout computation already dispatched")
    if jobs.active >= 2 or len(jobs.claimed) >= 1024:
        raise HTTPException(429, "Layout solver busy; no changes made")
    # No await between reservation and dispatch: at most two sequential attempts per invocation.
    jobs.claimed[key] = now + 120
    jobs.active += 1
    cancelled = Event()
    task = asyncio.create_task(asyncio.to_thread(recorded_compute, body.problem,
        now + body.problem.budgetMs/1000, cancelled, store_for(services),
        str(uuid4()), session_id, body.toolCallId, body.preparationDiagnostics,
        body.layoutAttempt))
    try:
        while not task.done():
            if await request.is_disconnected():
                cancelled.set()
            await asyncio.wait({task}, timeout=0.05)
        return await task
    finally:
        cancelled.set()
        # Retain the capacity slot until native computation actually stops.
        if task.done():
            jobs.active -= 1
        else:
            task.add_done_callback(lambda _: setattr(jobs, "active", jobs.active - 1))
