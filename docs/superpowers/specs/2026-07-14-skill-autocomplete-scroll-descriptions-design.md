# Skill Autocomplete Scroll and Descriptions Design

**Date:** 2026-07-14

## Goal

Make the `/` Skill autocomplete reliably verifiable with a large Skill catalog
and show each Skill's `SKILL.md` frontmatter description in the compact,
single-row style used by Codex.

## Confirmed Root Causes

The previous commit removed the ten-item client limit and called
`scrollIntoView` for keyboard navigation. A freshly loaded Chromium page can
scroll the current 77-item catalog to the final item with both the mouse wheel
and arrow keys. The missing protection is a real-browser regression test that
actually reaches the final item; the existing unit test moves only from the
first item to the second.

Descriptions are empty for the configured external Skills because Session
materialization deliberately mounts each external Skill as a symbolic link,
while the catalog description reader rejects every symbolic-link component
before reading `SKILL.md`. This makes the existing description API ineffective
for the project's external-Skill configuration.

## Backend Design

Keep `GET /api/sessions/{session_id}/skills` and its response schema unchanged.
`list_skills` continues to take names from the persisted Session snapshot and
sort them alphabetically.

For ordinary copied Skills, continue to read only a regular
`.claude/skills/<name>/SKILL.md` beneath the materialized Session workspace.
When the persisted snapshot declares `skills_root_env`, allow the description
reader to follow the deliberately materialized external Skill link and read the
same `SKILL.md` that the Agent uses. Do not expose the resolved source path.

Every frontmatter read remains bounded to 64 KiB. A missing file, invalid YAML,
non-string description, or filesystem error produces an empty description and
does not make the Session unusable. Unexpected links remain rejected for
Sessions that do not declare an external Skill root.

This behavior also applies to existing external-Skill Sessions, so no database
migration or Session recreation is required. External description changes are
visible in the same way that external Skill content changes are already visible
to those Sessions.

## Frontend Design

Each Skill option contains two explicit text elements:

- a non-shrinking Skill name;
- a muted description that uses the remaining width, stays on one line, and
  truncates with an ellipsis.

The full description is available through the option's tooltip. A Skill with
no valid description renders only its name. Text continues to be assigned with
DOM text properties rather than HTML injection.

The listbox keeps its current maximum height and vertical overflow behavior.
Pointer selection, keyboard selection, ARIA roles, filtering by name or
description, alphabetical order, and inserted `/skill-name ` text are
unchanged.

## Verification Design

Add regression coverage at three levels:

1. A catalog test proves that an external, materialized Skill link returns its
   bounded frontmatter description, while an unexpected link in an ordinary
   Session still returns an empty description.
2. A JavaScript controller test proves that name and description are rendered
   as separate elements with safe text and the full-description tooltip.
3. A Chromium browser test supplies at least 77 Skills, opens `/`, and proves
   both wheel scrolling and repeated ArrowDown navigation can reach the final
   option. It also checks the compact one-line description styling.

Run the focused Python, Node, and browser tests first, followed by the complete
test suite. Existing unrelated working-tree changes remain untouched.
