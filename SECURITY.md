# Security

Gingugu is local-first by design — your memories live in one SQLite file
on your machine. There is no cloud component, no telemetry, and no remote
service to compromise.

That doesn't mean there's no threat model. This document explains where
the trust boundaries actually are, what we protect against, and what we
don't.

## Reporting a Vulnerability

If you've found a security issue, please **do not open a public issue**.
Email **brian.speagle [at] gmail [dot] com** with details, or open a
private security advisory via GitHub:
<https://github.com/gingugu/gingugu/security/advisories/new>.

We'll acknowledge within 72 hours and aim to ship a fix or mitigation
within 7 days for anything exploitable.

## What Gingugu Protects

- **Memory data at rest.** Stored in a SQLite file at
  `~/.local/share/gingugu/memories.db` (configurable). Standard filesystem
  permissions apply — protected from other users on the same machine if
  your home directory is appropriately permissioned.
- **Credentials at rest.** Stored in your OS keychain (macOS Keychain,
  Windows Credential Manager, freedesktop Secret Service on Linux) via
  the `keyring` library. Never written to disk by Gingugu in plaintext.
- **Localhost-only UI API.** Binds to `127.0.0.1`, with CORS restricted
  to expected dev UI origins (not `*`). Localhost binding alone is not a
  defense against malicious in-browser content, which is why CORS is
  also constrained.
- **Concurrency safety.** WAL mode, busy timeout, and FTS sync triggers
  prevent corruption under contention from multiple local MCP processes.

## What Gingugu Does Not Protect Against

### Agent-mediated credential exposure

This is the most important boundary to understand.

The `credential_get` MCP tool returns secret values **in plaintext** to
whatever agent invoked it. The OS keychain protects credentials from
casual disk access — it does **not** protect them from a process the
keychain has authorized, which Gingugu is.

That means a credential you store via Gingugu can potentially reach the
agent (and therefore its model provider) via:

- An agent following an instruction in untrusted file content
  (prompt injection)
- A malicious issue body, README, or webpage the agent reads
- A poisoned MCP tool result from another server
- A compromised editor extension or plugin
- An agent mistake or overly broad tool call
- A user asking the assistant to "inspect" untrusted content

**Recommendation:** treat the credential vault as a developer-convenience
feature, not a hardened secret store. Don't use it for production
credentials, root API keys, or anything you wouldn't paste into a chat.
A separate, audited secret manager (Vault, 1Password CLI, AWS Secrets
Manager) is appropriate for those.

A future release will add per-service retrieval policies, interactive
approval for secret reads, and audit logging that excludes secret values
themselves. Until then, **the trust boundary is the agent**, not the
keychain.

### Untrusted memory content

Gingugu stores whatever it's told to store. If an agent ingests untrusted
content (e.g. summarizing a malicious README and saving the summary as a
memory), that content lives in your database and may be retrieved later.
The confidence lifecycle (`verified` / `inferred` / `stale` / `deprecated`)
is a tool, not a guarantee — it depends on agents and clients respecting it.

A future governance layer (see `docs/future-architecture.md`) is intended
to address this.

### Multi-process write contention beyond local development

WAL mode and the busy-timeout retry loop are appropriate for several local
MCP processes. They are not a substitute for a real distributed coordination
layer. Don't put a Gingugu DB on a network share with multiple writers.

### Backups and recovery

Gingugu does not yet perform automatic backups before destructive operations
(consolidations, large updates, schema migrations). v0.3.2 introduces an
automatic pre-migration backup as a first step. Until then, **back up your
DB file** before running consolidation or upgrades on data you care about.

## Dependencies

- We pin major versions in `pyproject.toml` and use a lockfile.
- Releases are published via PyPI Trusted Publishing (GitHub OIDC) — no
  long-lived API tokens are stored in CI.
- We do not yet run automated dependency vulnerability scanning. This is
  on the near-term roadmap.

## Out of Scope (for this version)

- Multi-tenant isolation (Gingugu is single-user)
- Encrypted-at-rest databases (use full-disk encryption)
- Agent identity verification (any process that can speak MCP can call any tool)
- Audit logs for memory reads (writes are timestamped; reads are not)

These are tracked in `docs/future-architecture.md` and the roadmap.
