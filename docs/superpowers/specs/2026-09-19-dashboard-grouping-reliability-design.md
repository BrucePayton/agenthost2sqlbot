# Dashboard Grouping Reliability Design

## Goal

Make dashboard order strategies 3B and 3C reliably regroup and compact both ungrouped and already-grouped dashboards, with readable group titles, adaptive group colors, bounded execution time, and direct actionable failures.

## Confirmed Rules

- User-visible cards are planning inputs. Hidden framework nodes, empty hidden text nodes, and unchanged orphan implementation nodes are preserved but ignored.
- Existing flat-layout children are user-visible planning inputs even when their stored `layoutEditable` value is false; Tab children remain owned by their Tab and are not regrouped.
- New flat containers remain exactly 12 or 24 columns wide. Their height is derived from compacted child content plus the native title/header allowance.
- Metric cards retain a minimum readable grid size of 3 by 2.
- Group titles use `阶段：内容摘要`. All group summaries are generated in one bounded structured-output model call. Titles must preserve the supplied stage, summarize supplied card titles, be unique on the page, and remain concise. Semantic indistinguishability is the stage plus normalized visible title content: same-stage groups with the same non-empty visible content are merged in stable first-seen order even when their chart types differ. Real visible card type participates only when title content is empty. Invalid, timed-out, or failed output falls back once to a deterministic summary without retrying the model. Reserved-title avoidance is best-effort after grounded candidates; if every grounded title of at most 16 characters is reserved, planning reuses the clearest grounded `阶段：内容` title instead of inventing IDs, types, numeric suffixes, or failing.
- Group backgrounds follow the approved batch policy: colored or mixed metric groups prefer white, with warm fallback when white cannot separate from the canvas; white-content groups use the approved cool/warm sequence and avoid adjacent duplicates. `#EDF7ED` is excluded.

## Architecture

### Host semantic planning

`extract_dashboard_structure` distinguishes planning visibility from direct layout editability. It flattens existing native flat groups into visible cards, ignores implementation-only nodes, and preserves Tab ownership. Semantic compilation performs one optional batch title-generation call after grouping decisions are known. A strict schema validates group identity, stage prefix, title length, uniqueness, and grounding; deterministic titles remain the fallback.

### Planner-to-write handoff

The Host records the canonical `layoutArguments` returned by the semantic planner. The subsequent `dashboard.set_widget_layout` call uses that recorded object instead of model-reconstructed JSON. Planner calls count as consumed only after successful compilation, so a structure/precondition failure can be retried after rereading. Malformed calls and missing revisions fail immediately. Deferred frontend execution is bounded and returns its real stage/error to the transcript.

### Frontend grouping

Flat-group transition validation ignores unchanged unresolved hidden implementation nodes but still rejects newly introduced or modified invalid parents. Group height generation and solver validation use the same native header-aware formula, preventing a generated height from being immediately rejected. Width selection stays unchanged at 12 or 24 columns.

Background selection is computed for the complete ordered group batch, not independently per group. It classifies groups from actual chart types and effective child backgrounds, then applies the approved global or alternating palette.

## Failure Handling

- No planning, validation, or solver failure dispatches a save.
- Errors expose the exact stage, code, blocking node/group, and relevant dimensions or constraint values to the frontend result.
- Non-retryable layout failures terminate the operation immediately.
- Deferred frontend waits have a short explicit limit and never create a model retry loop.

## Verification

Regression fixtures cover the seven reported sessions:

1. Canonical planner output reaches layout write without JSON recopy.
2. Unchanged hidden/orphan text nodes are ignored while changed invalid parents are rejected.
3. Titles are concise, grounded, unique, and batch-generated with deterministic fallback.
4. Multiple colored metric groups do not all become yellow and the approved palette policy is preserved.
5. Existing flat-group children remain eligible for semantic regrouping.
6. Five metric cards produce a header-aware feasible container height.
7. Invalid calls and deferred failures terminate within bounded time without repeated retries.

Both repositories run focused tests first, then their complete affected suites. No non-flat layout behavior or 12/24 width policy changes.
