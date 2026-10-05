# Dashboard Remote Compact Solver Design

## Goal

Make AgentHost the execution home for CPU-heavy compact-dashboard search while
keeping the existing private bridge, strict geometry boundary, cancellation,
and single frontend persistence transaction.

## Responsibilities

AgentHost receives a `constraint-v1` problem prepared by Davinci. The problem
contains only IDs, hierarchy, candidate shapes, fixed columns, reading-order
constraints, costs, and a bounded compute budget. It must not receive card
titles, metric values, datasets, filters, or other business configuration.

AgentHost owns:

- mixed-height placement search
- row, column, and cross-row arrangement comparison
- container-profile search
- gap and alignment quality scoring
- deterministic plan fingerprinting
- ranking the best plan plus up to seven distinct alternatives
- cancellation, compute-budget, and capacity enforcement

Davinci continues to own DOM measurement, fallback reason wording, hard final
validation, alternative history after successful saves, and persistence.

## Compatibility

The request remains `constraint-v1`. Existing request validation and the private
`dashboardLayoutSolve` route remain authoritative. The response retains the
current top-level `placements` field and adds an optional `alternatives` array,
so an older frontend can continue consuming the best plan.

Each alternative contains a deterministic `planKey`, placements, and quality:

```json
{
  "planKey": "sha256:...",
  "placements": [],
  "quality": {
    "gapCells": 0,
    "largestGapCells": 0,
    "misalignedBlockCount": 0,
    "alignedBoundaryCount": 4,
    "totalHeight": 16
  }
}
```

The backend returns at most eight candidates including the top-level best plan.
Candidates must have distinct plan keys and pass the independent geometry audit.

## Candidate Search

The bounded solver keeps multiple audited incumbents instead of returning after
the first row or skyline seed. Row seeds, skyline seeds, and bounded MaxRects
states all feed one ranked candidate collector.

The collector is capped by:

- the request deadline
- `MAX_EXPANSIONS`
- existing beam and profile limits
- eight final distinct plans
- 200 nodes and the existing shape-count limit

Search exhaustion with at least one audited incumbent returns `feasible` and the
best collected candidates, with `searchPruned:true` when appropriate. Exhaustion
without an incumbent remains a truthful non-feasible status so Davinci can use
its O(n) fallback. The backend never labels bounded-search exhaustion as proof
that a legal layout does not exist.

## Quality Ordering

Hard validity is evaluated first: identity, integer geometry, admitted shapes,
bounds, fixed columns, parent variants, reading order, non-overlap, and container
containment.

Valid plans are ordered lexicographically by:

1. lower connected internal gap area
2. lower largest connected gap
3. fewer misaligned multi-card blocks across adjacent rows
4. more shared left, right, and internal column boundaries
5. lower total canvas height
6. lower shape-preference cost
7. deterministic geometry fingerprint

Whitespace and alignment are quality dimensions, not reasons to reject a valid
plan. This preserves the requirement that an approved ordering is not refused
because no visually perfect arrangement was found.

## Plan Fingerprints

The plan key hashes normalized geometry sorted by stable node ID. Parent scope,
`x`, `y`, `w`, `h`, and external row-boundary indices are included. Generated
container IDs are normalized through their member identity, matching the
frontend alternative-history semantics.

Fingerprints do not contain titles, data, or user-visible content. Equal geometry
always produces the same key across processes.

## Capacity And Cancellation

The existing per-invocation ownership checks, pending-tool-call checks, duplicate
dispatch guard, maximum active-job count, process isolation option, deadline,
and disconnect cancellation remain in place.

AgentHost may return HTTP 429 when capacity is exhausted. Davinci handles this as
a disclosed basic-layout fallback; AgentHost must not queue work past the tool
deadline. A cancelled or disconnected request must release its capacity slot
when native computation actually stops.

## Observability

Successful responses report elapsed time, expansion count, pruning, candidate
count, selected method, and per-candidate quality. Logs record session ID,
tool-call ID, status, elapsed time, nodes, shapes, expansions, and candidate
count, but never dashboard titles or values.

The frontend receipt separately reports its preparation and persistence phases,
allowing production traces to distinguish browser preparation, backend search,
DOM verification, and saving.

## Error Contract

Backend statuses remain machine-oriented and bounded:

- `feasible`
- `budget_exhausted`
- `search_exhausted`
- `infeasible_candidates`
- `cancelled`
- `service_unavailable`

AgentHost does not compose end-user prose because it does not receive titles or
chart labels. Davinci maps these statuses to Chinese messages, card type and
title, the selected fallback size, and an actionable suggestion.

## Tests And Benchmarks

- Solver tests prove up to eight distinct, audited, deterministically ordered
  plans and stable fingerprints.
- Quality tests cover three-card rows, cross-row boundaries, mixed-height stacks,
  rankings beside charts, tables, and nested container profiles.
- Budget tests prove the best incumbent survives deadline and expansion limits.
- Route tests retain ownership, pending-call, duplicate-call, 429, cancellation,
  and strict request validation coverage while accepting extended responses.
- A 200-node benchmark records wall time, expansion count, candidate count, and
  peak worker memory. It must stay inside the supplied budget and must not block
  the FastAPI event loop.
- Bridge tests prove the extended response passes through unchanged and legacy
  single-plan responses remain supported.

## Non-Goals

- Dashboard persistence from AgentHost.
- DOM, font, legend, table, or hidden-Tab measurement in Python.
- Sending business data to the geometry service.
- Modifying subscriptions or unrelated AgentHost features.
