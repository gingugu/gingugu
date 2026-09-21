# The conditional half of the `.ai/` assessment

The three always-check files live in `SKILL.md` step 2. These are the ones whose
relevance depends on what the diff touched. Work the list; do not decide from
memory which ones "probably did not change".

## Check if `src/` changed

- **`.ai/memory.md`** - update if module structure, the MCP tool surface, the
  storage schema, or release state changed.
- **`.ai/specs/01-architecture.md`** - update if a module or tool was added, the
  storage model changed, or a key decision was made.
- **`.ai/specs/dataflow.md`** - update if the store -> embed -> recall -> context
  flow changed, or relations and spreading activation changed.
- **`.ai/specs/product-spec.md`** - update the tool or feature status if
  something shipped, got blocked, or was descoped.

## Check if conventions or standards changed

- **`.claude/agents/`** - update the relevant subagent definition if its scope,
  tool allowlist, or model tier changed.
- **`.ai/standards/`** - update if testing, code, or database discipline
  changed.

## Also in lockstep with the tool surface

- **`docs/architecture.md`** - its mermaid diagrams describe the module map and
  the retrieval flow. A new module or a changed flow makes them wrong.
- **`CHANGELOG.md`** - already an always-check, but note the format: Keep a
  Changelog, entry under `[Unreleased]`, user-visible framing.

## What counts as done

A file is assessed when you have either updated it or can say, in one line, why
this diff did not touch what it describes. "I did not look" is not an answer,
and neither is silence - the PR body has to state which files were updated or
explicitly that none needed to be, with the reason.
