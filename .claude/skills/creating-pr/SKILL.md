---
description: Create a PR in gingugu, including the mandatory `.ai/` knowledge base assessment. Use whenever opening a pull request, and read it before cutting a second branch in the same session.
---

## Creating a PR in gingugu

Follow every step in order. Do not skip the `.ai/` assessment - it is mandatory.
This is a GitHub repo - use `gh`, not `glab`.

### 0. Opening more than one PR this session? Stack them

Decide this **before** cutting the second branch. Step 2 makes a
`.ai/plans/status.md` update mandatory on every PR, so two PRs both cut from
`main` in the same session will collide on that file. Not "might" - will.

Cut the second branch from the first, and target the first:

```bash
git checkout feature/pr-one          # not main
git checkout -b chore/pr-two
gh pr create --base feature/pr-one   # not main
```

**If you are stacking, or merging a stack, read `stacking-prs.md` now.** It
carries the merge order that keeps child PRs alive, the incident this repo was
burned by, and the two alternatives that look reasonable and are not.

### 1. Understand what changed

```bash
git diff main...HEAD
```

Identify which layers were touched: server/handlers, storage/search/relations,
embeddings, UI (`ui/`), tests, CI, or documentation.

### 2. Assess `.ai/` files that need updating

**Always check, on every PR without exception:**

- `.ai/plans/status.md` - update "In Progress", "Recently Completed", and "Blocked/Pending"
- `README.md` - update if the tool surface, setup, configuration, or features changed
- `CHANGELOG.md` - add an entry under `[Unreleased]` for any user-visible change (Keep a Changelog format)

**Then work `ai-assessment-checklist.md`** for the conditional files - what to
check when `src/` changed, and what to check when conventions or standards
changed. Do not skip it because the change looks small; that judgment is what
the checklist exists to replace.

The `ai-docs-auditor` subagent does this assessment properly and returns
`path:line` drift. Prefer it over eyeballing the tree.

### 3. Commit the docs updates

```bash
git add .ai/ README.md CHANGELOG.md
git commit -m "docs: update .ai knowledge base - <brief reason>"
```

### 4. *** STOP. Get explicit approval before anything leaves this machine ***

Everything above is local and reversible. Everything below is not.

Pushing a branch and opening a PR are writes to an external system on a
**public** repository, and this repo's standing rule is that those are
approval-gated without exception. Present:

- the branch name and the exact `gh pr create` command you intend to run,
- what the PR contains, in one or two lines,
- anything in the diff you are unsure about.

Then **wait for an explicit go**. Not a summary that implies consent, not a
question the user answered about something else. An explicit go, for this push.

Do not proceed to step 5 without it.

### 5. Push the branch

```bash
git push -u origin <branch-name>
```

### 6. Open the PR

```bash
gh pr create \
  --title "<type>: <what changed>" \
  --body "<description of what changed, why, and which .ai/ files were updated>" \
  --base main \
  --head <branch-name>
```

PR body must include:

- What changed and why
- Which `.ai/` files were updated, or an explicit statement that none needed
  updating and why
- Test status for the changed surface

This is a public repo. Frame the body around what the work adds. Never write a
process confession into it.

### 7. Share the PR URL

Always provide the PR URL to the user at the end.
