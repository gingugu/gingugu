# Stacking PRs, and merging a stack without killing it

Read this when opening a second PR in one session, or when merging a stack.

## Why stacking is not optional here

Step 2 of the skill makes a `.ai/plans/status.md` update mandatory on every PR,
and every PR writes its entry at the same anchor. Two branches cut from `main`
in the same session therefore conflict on that file when the second one merges.
Every time. This is structural, not bad luck.

Stacking removes the conflict instead of managing it:

```bash
git checkout feature/pr-one          # not main
git checkout -b chore/pr-two
gh pr create --base feature/pr-one   # not main
```

PR 2 then already contains PR 1's `status.md` entry, so there is nothing to
collide with. It also reflects reality: PR 2 was written knowing what PR 1 said.

**Cost:** if PR 1 changes materially in review, PR 2 needs a rebase. That is
cheaper than a guaranteed conflict.

## Merging a stack: never `--delete-branch` the parent

GitHub retargets a child PR to `main` when its parent merges **only if the
parent branch still exists**. Merging with `--delete-branch` instead **closes**
every child PR, and GitHub refuses to reopen a PR whose base branch is gone.

This repo has been burned by it: `gh pr merge 12 --merge --delete-branch --admin`
closed stacked PR #13, which had to be re-raised as #15.

Merge bottom-up, and pick one:

```bash
# preferred - retarget children to main BEFORE merging the parent
gh pr edit <child-pr> --base main
gh pr merge <parent-pr> --merge --admin --delete-branch

# or - merge without deleting, clean the branch up afterwards
gh pr merge <parent-pr> --merge --admin
```

## Rejected alternatives - do not reach for these

- **`.ai/plans/status.md merge=union` in `.gitattributes`.** Union merge is
  built for append-only files. `status.md` is not: its "In Progress" and
  "Blocked/Pending" sections are edited in place, and union merge would keep
  both versions of an edited entry with no conflict marker. That trades a loud,
  trivial conflict for silent doc corruption.
- **`git rerere`.** It replays conflict resolutions; it does not prevent the
  conflict.
