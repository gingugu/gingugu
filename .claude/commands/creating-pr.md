---
description: Create a PR - includes mandatory .ai/ knowledge base assessment and update
---

## Creating a PR in gingugu

Follow every step in order. Do not skip the `.ai/` assessment - it is mandatory.
This is a GitHub repo - use `gh`, not `glab`.

### 1. Understand what changed

Review the diff of all staged/committed changes:

```bash
git diff main...HEAD
```

Identify which layers were touched: server/handlers, storage/search/relations,
embeddings, UI (`ui/`), tests, CI, or documentation.

### 2. Assess .ai/ files that need updating

For every PR, evaluate each file below and update it if the work warrants it:

**Always check:**
- `.ai/plans/status.md` - update "In Progress", "Recently Completed", and "Blocked/Pending"
- `README.md` - update if the tool surface, setup, configuration, or features changed
- `CHANGELOG.md` - add an entry under `[Unreleased]` for any user-visible change (Keep a Changelog format)

**Check if src/ changed:**
- `.ai/memory.md` - update if module structure, tool surface, storage schema, or release state changed
- `.ai/specs/01-architecture.md` - update if a module/tool was added or the storage model changed
- `.ai/specs/dataflow.md` - update if the store/embed/recall/context flow, relations, or spreading activation changed
- `.ai/specs/product-spec.md` - update the tool/feature status if something shipped, got blocked, or was descoped

**Check if conventions or standards changed:**
- `.ai/agents/` - update the relevant agent file if a tech stack decision, directory structure, or rule changed
- `.ai/standards/` - update if testing, code, or database discipline changed

### 3. Commit the docs updates

If any `.ai/` files, `README.md`, or `CHANGELOG.md` were updated, stage and commit them alongside the work:

```bash
git add .ai/ README.md CHANGELOG.md
git commit -m "docs: update .ai knowledge base - <brief reason>"
```

### 4. Push the branch

```bash
git push -u origin <branch-name>
```

### 5. Open the PR

```bash
gh pr create \
  --title "<type>: <what changed>" \
  --body "<description of what changed, why, and which .ai/ files were updated>" \
  --base main \
  --head <branch-name>
```

PR body must include:
- What changed and why
- Which `.ai/` files were updated (or an explicit statement that none needed updating and why)
- Test status for the changed surface

### 6. Share the PR URL

Always provide the PR URL to the user at the end.
