# Flat Group Business Title Design

## Problem

Flat-group titles currently avoid copying one member card by collapsing several
different card meanings into a shared prefix plus `多指标`. This is technically
inclusive but not useful to a dashboard reader. Titles such as
`结构拆解：城市成交多指标` do not explain whether the group presents scale,
conversion, composition, trends, rankings, or another analytical purpose.

The title must summarize the business meaning of every visible member card. It
must not expose implementation terminology, copy only one member, or replace
missing semantics with a generic placeholder.

## Scope

This change applies only to title generation and validation for newly compiled
flat groups in the 3A/3B/3C semantic grouping path.

It does not change:

- which cards belong to a group;
- card order within a group;
- the existing 12/24 outer-width policy;
- the compact layout solver or its card geometry;
- hidden, inactive-tab, or framework-only nodes, which remain excluded.

## Title Contract

The visible title keeps the form:

`<stage>：<business summary>`

The business summary combines two kinds of evidence:

1. **Business object or dimension**: the shared subject represented by the
   cards, derived from visible titles and semantic profile fields such as
   dimensions, grouping, drill paths, metrics, filters, dataset relation, and
   time grain.
2. **Analytical intent**: what the collection helps the reader understand,
   derived from the complete metric set and chart-type mix. Examples include
   scale, performance, composition, trend, ranking, comparison, and detail.
   These are semantic outcomes, not a fixed list used to decide membership.

Examples:

| Member-card meaning | Group title |
| --- | --- |
| City order-count, amount, and conversion rankings | `结构拆解：城市成交综合排行` |
| Transaction-method amount share charts | `结构拆解：交易方式成交结构` |
| Quality-model order metrics and rankings | `结构拆解：质检型号成交表现` |

The final visible title must not contain empty implementation fallbacks such as
`多指标`, `关联分析`, `综合分析`, or `数据分析`.

## Synthesis Pipeline

### 1. Build complete title evidence

For every group, construct one bounded title request containing every visible
member card, including:

- visible content;
- chart type and display role;
- semantic profile;
- the group's valid stage titles;
- reserved titles already present on the dashboard.

No representative-card sampling is allowed in the title phase. Every member
must contribute evidence even when several cards share a metric or dimension.

### 2. Derive a deterministic semantic frame

Before calling the model, derive:

- shared business-object candidates;
- distinct metric or analysis meanings;
- chart-level analytical intents;
- a coverage map linking each member to at least one candidate phrase.

This frame guides generation and provides independent validation. It does not
use title keywords to change group membership.

### 3. Generate one batch of titles

Use one existing title-model call for all groups. The model receives the
complete evidence and semantic frame and returns one unique title per group.
It must summarize the collection rather than enumerate card names or choose a
single representative card.

The prompt uses business-readable language and treats terms such as scale,
performance, composition, or ranking as examples. It does not hard-code domain
entities such as city, brand, model, region, or owner.

### 4. Validate before accepting

A generated title is accepted only when all conditions hold:

- the stage prefix is one of the group's valid stages;
- the summary includes a grounded business object or dimension;
- the summary expresses an analytical intent grounded in the metric and chart
  evidence;
- every distinct member meaning is covered by the object/intent combination;
- the title is not equal to one member title and is not only a shared dimension;
- the title contains none of the forbidden placeholder terms;
- the title is unique relative to reserved and newly assigned group titles;
- the title contains no internal field IDs, dataset IDs, or unsupported claims.

### 5. Deterministic fallback

If the model times out or returns an invalid title, construct a title from the
validated semantic frame. Prefer a grounded shared object plus a grounded
analytical intent. If that cannot cover every member, use a compact descriptive
combination of the distinct visible meanings.

The fallback must never return a forbidden generic term merely to satisfy a
length target. If no grounded title can be produced even from all visible
member meanings, stop before persistence and return bounded diagnostic evidence
instead of silently saving a misleading title or an untitled group.

## Length And Rendering

The current fixed 16-character generation limit is too aggressive and causes
semantic information to be replaced by generic words. Generation will use the
actual title-area width as a display budget where available. Contract-level
length bounds remain only as safety limits.

When a valid business title is wider than the available area, the frontend may
display an ellipsis and expose the complete title through the existing title
editing or hover affordance. Semantic content must not be discarded merely to
avoid visual truncation.

## Diagnostics

Session diagnostics record, per group:

- member IDs used as evidence;
- derived business-object and analytical-intent candidates;
- accepted title source: rule, model, or deterministic fallback;
- validation rejection reason;
- forbidden-term, single-member-copy, incomplete-coverage, duplicate, timeout,
  and malformed-output outcomes.

Diagnostics remain bounded and do not expose raw business datasets or model
chain-of-thought.

## Tests

Automated coverage must include:

- the three screenshot scenarios above;
- order-count, amount, and rate becoming a meaningful performance or ranking
  title rather than `多指标`;
- share charts becoming a structure title;
- mixed chart types producing a grounded collection intent;
- rejection of a copied member title;
- rejection of a shared dimension without analytical intent;
- rejection of every forbidden placeholder term;
- all-member evidence and coverage for groups with more than two cards;
- duplicate and reserved-title handling;
- long-title rendering without semantic truncation;
- hidden and inactive-tab cards not affecting the title;
- model timeout or invalid output using the deterministic fallback;
- inability to construct a grounded fallback stopping before persistence and
  returning a diagnostic rather than a misleading saved title.

## Success Criteria

- No newly generated flat-group title displays a forbidden placeholder term.
- Each accepted title is grounded in all visible member cards.
- Titles remain business-readable and distinguish groups with different
  analytical purposes.
- Group membership, card ordering, layout width rules, and compact solver
  behavior remain unchanged.
- Title generation still uses at most one bounded model call for the dashboard.
