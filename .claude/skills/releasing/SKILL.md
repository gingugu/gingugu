---
name: releasing
description: Cut a gingugu release to PyPI and roll it out to every machine. Use whenever asked to release, publish, tag, or ship a version, or to upgrade gingugu on the Pi or the other Macs. Runs scripts/release.py and scripts/fleet.py; do not reconstruct the steps by hand.
---

## Releasing gingugu

Two scripts do the mechanical work. Do not re-derive the procedure from old
memories or `release.yml` - run them.

### 1. Decide the version

Semver against `CHANGELOG.md` `[Unreleased]`: breaking for users -> major (or
minor while 0.x), new behaviour -> minor, fixes only -> patch.

### 2. Write the two pieces that are judgment, on `main`

- `CHANGELOG.md` `[Unreleased]` holds every user-visible change (it usually
  does already - each PR adds its entry).
- `.ai/memory.md` **Release State**: a new first bullet,
  `- Current version: **X.Y.Z** (PyPI, YYYY-MM-DD). <one line on what shipped>`,
  and the previous current-version bullet demoted to `- A.B.C: ...`.
- A commit body in a scratch file: what shipped, and an **Upgrading:** line if
  machines need more than the upgrade below. No Claude attribution.

Commit those (`docs: release state for X.Y.Z`) and push - the script refuses
a dirty tree or a `main` that is not level with `origin/main`.

### 3. Dry run, then release (approval-gated)

```bash
uv run python scripts/release.py X.Y.Z --summary "<subject>" --body-file <file> --dry-run
```

The dry run is preflight + every gate `release.yml` runs (tests, lint, UI
build, a wheel that bundles the UI). Green? Then present the real command and
**wait for an explicit go** - it pushes `main` and a tag to a public repo and
publishes to PyPI, which cannot be taken back:

```bash
uv run python scripts/release.py X.Y.Z --summary "<subject>" --body-file <file>
```

It bumps, relocks, rolls the CHANGELOG, commits, tags, pushes, watches
`release.yml`, and returns once PyPI's index serves the wheel.

### 4. Roll out

```bash
uv run python scripts/fleet.py status
uv run python scripts/fleet.py upgrade X.Y.Z [names...]
```

Machines are listed in `~/.config/gingugu/fleet.json` (never in this repo).
The Pi is installed as its service user, restarted, and health-checked.

**Do not upgrade the machine this session runs on mid-session** - its gingugu
is this session's live MCP server, and replacing it under a running process can
break the memory tools before `/sink-the-ship`. Upgrade it last, after the
session's saves, and tell the user to restart their client.

A brain restart drops derived tokens: the proxy's next call re-derives and
retries once. Expected, not a failure.
