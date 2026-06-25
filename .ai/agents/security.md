# Agent: Security

## Scope

Secrets handling, the credential vault, data locality, and the fact that this is
a **public** repository.

## Rules

- **Never commit secrets** — credentials, API keys, tokens. The `.gitignore`
  blocks common token files; do not override it.
- **Credential vault = OS keychain only.** `credentials.py` reads/writes secret
  values in the keychain. Secret values never touch the SQLite DB, log files,
  chat, or the repo. `credential_list` returns only non-secret metadata.
- **Local data stays local.** The memory DB lives in the platform data dir, never
  in the repo. No telemetry, no cloud calls, no phone-home.
- **Public repo hygiene.** Never embed internal URLs, customer names, employer
  details, or PII in code, docs, examples, `.ai/`, or test fixtures. This repo
  is open source.
- **Path safety.** Validate/normalize any filesystem path derived from user
  content — no traversal.
- **External writes are approval-gated.** GitHub API, PyPI, npm publishes:
  present the exact command + blast radius, wait for explicit approval.

## Definition of done

- No secret or private detail in the diff (grep before commit).
- Vault changes keep secret values out of the DB and logs.
- Any publish/mutation was explicitly approved.
