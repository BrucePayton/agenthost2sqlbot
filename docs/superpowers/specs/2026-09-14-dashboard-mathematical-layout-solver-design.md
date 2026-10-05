# Dashboard Mathematical Layout Solver Design

## Goal

Replace the recursive bounded MaxRects production path with an anytime CP-SAT
model that reliably returns a complete legal layout for a real 40-card
dashboard, then uses the remaining budget to improve whitespace and cross-row
alignment. Davinci remains responsible for DOM measurement, final validation,
persistence, and business-readable receipts.

## Confirmed Product Rules

- Every layout scope uses a 24-column integer grid.
- A completed horizontal row fills all 24 columns exactly. A non-metric card
  that is alone in a row expands to 24 columns when 24 is an admitted shape.
- Tables and pivot tables are at least half of their containing scope. If their
  containing FlatLayout is itself half-screen, they fill that FlatLayout.
- TabLayout uses the smallest exact width and height accepted by every pane.
  An unavailable pane uses its confirmed type fallback and does not reject the
  whole TabLayout.
- No-data and unavailable cards use the same size policy as available cards.
- The confirmed fallback size map supplied by the user is authoritative.
- Normal reading order is left-to-right. A contiguous earlier subsequence may
  also stack top-to-bottom in the left column before a later right card or
  right-column stack. This admits both small-left-stack plus large-right and
  large-left plus small-right-stack.
- Whitespace and alignment affect candidate ranking but never make a legal
  layout unsaveable. If every legal candidate misses a quality threshold, save
  the best legal candidate and return warnings.
- External whitespace, internal holes, and unmatched cross-row boundaries use
  the confirmed 15 percent quality threshold.
- The first request selects the highest-ranked candidate. An explicit repeat
  request selects a distinct legal candidate when one exists.

## Root Causes In The Current Code

The current `bounded.py` search expands MaxRects placements one card at a time,
retains only 48 partial states, and stops at 160,000 expansions. A realistic
40-card synthetic domain reaches that expansion ceiling in about 3.3 seconds
and returns only one candidate. The row and skyline seeds can return no complete
incumbent for mixed heights, fixed columns, or containers. In that case search
exhaustion becomes `budget_exhausted` even though a legal layout exists.

The older CP-SAT implementation also used the wrong model. It searched an
unrestricted two-dimensional floorplan, repeatedly added gap cuts and re-solved,
treated more than eight connected empty cells as invalid, and required strict
`(y, x)` row-major order. Those choices created unnecessary combinatorics and
excluded product-approved column stacks.

The frontend compounds the failure by using a no-backtracking shelf fallback.
It also contains stale fallback dimensions and stale quality constants of nine
cells, five percent whitespace, and 90 percent alignment.

## Mathematical Model

Use OR-Tools CP-SAT, already pinned by AgentHost, as the production solver.
Each card `i` has integer position variables `x_i`, `y_i`, selected width and
height `w_i`, `h_i`, candidate cost `c_i`, and variant `v_i`. One allowed-table
constraint selects an admitted shape, including parent variant compatibility.

For every scope:

- `NoOverlap2D` enforces non-overlap.
- `0 <= x_i`, `x_i + w_i <= 24`, and the existing height bounds enforce the
  canvas.
- The scope bottom is the maximum `y_i + h_i`.
- Occupied area is selected directly from the candidate table.
- Empty area is exactly `24 * bottom - sum(area_i)`.
- A non-metric card with no vertically overlapping peer selects an admitted
  24-column shape when available; completed horizontal rows have zero trailing
  columns.
- Table candidates below the scope-local 12-column minimum are removed by the
  frontend problem builder; a sole table in a FlatLayout retains only width 24.

For each adjacent pair in the requested order, a Boolean disjunction requires
the earlier card to be entirely left of or entirely above the later card. This
is a partial-order constraint, not a strict `(y, x)` sort. It admits both
confirmed column-stack reading patterns while prohibiting pairwise reversal.

Internal edge variables record whether a card starts or ends on columns 1-23.
The alignment objective rewards repeated boundaries. Candidate quality is also
audited after solving with exact connected-gap and adjacent-band metrics.

## Anytime Optimization

Before optimization, build a deterministic complete warm start from admitted
shapes in scope order. It is only an incumbent and correctness fallback; CP-SAT
is the optimization engine. Container profiles are prepared bottom-up by graph
depth rather than recursive search.

CP-SAT runs with a single request deadline and retains every audited improving
solution from its callback. Objectives are applied lexicographically:

1. minimize total empty cells and largest audited connected hole;
2. minimize unmatched boundaries and maximize shared boundaries;
3. minimize total root height;
4. minimize candidate shape cost.

The backend returns up to eight distinct audited candidates. If the deadline is
reached after any complete candidate exists, status remains `feasible` with
`searchPruned: true`, `optimal: false`, and the objective bound/gap when known.
`budget_exhausted` is reserved for a deadline reached before any legal complete
candidate can be constructed. Proven model infeasibility remains
`infeasible_candidates`.

## Frontend Integration

Davinci will use one shared order predicate for remote validation and final
persistence. Successful solver geometry is stamped with the requested compact
reading order, so native row-major sorting cannot reject an approved column
stack.

The fallback size table is replaced exactly with the user's confirmed JSON.
Unavailable tab children contribute fallback size evidence and readable issues
containing type, title, reason, and selected dimensions. The local emergency
fallback remains linear-time but fills complete rows and full-width single
non-metric cards; it is not reported as the backend algorithm.

The receipt continues to expose `executionMode`, `remoteStatus`, and
`fallbackReason`. Backend success includes solver method, elapsed time,
candidate count, pruning/optimality state, and quality metrics without exposing
dashboard business data.

## Validation

- Unit tests reproduce expansion exhaustion and prove a 40-card problem returns
  `feasible` with an audited incumbent inside its backend budget.
- 40-, 80-, and 200-card stress cases verify bounded runtime and identity.
- Geometry tests cover three cards in one row, both approved column-stack
  orders, a two-row spanning leaderboard, a full-row table, nested FlatLayout
  tables, and TabLayout fallback sizing.
- Quality tests verify the 15 percent thresholds rank and warn but never veto.
- Controller tests verify first-best, explicit distinct alternative, readable
  fallback receipts, exactly one persistence, and execution-path evidence.
- The repository-mandated S01-S07, original dashboard 333, and full 30-second
  browser/save benchmark must be recorded as passed or honestly `not_run` when
  the original fixture or environment is unavailable.

## Non-Goals

- Sending titles, values, datasets, filters, or DOM state to AgentHost.
- Claiming mathematical global optimality when CP-SAT reports only a feasible
  bounded solution.
- Rejecting a legal save because its quality warning exceeds 15 percent.
- Reintroducing the old repeated gap-cut CP-SAT loop.
