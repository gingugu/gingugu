# gingugu

> A local MCP server that gives AI coding assistants persistent, structured, searchable long-term memory. One SQLite file, no cloud, no telemetry.

---

## What This Repo Does

- Exposes a **Model Context Protocol (MCP)** server — **stdio** by default, or
  **streamable HTTP** via `gingugu serve` (Bearer-token auth: the owner token has full
  access; per-client scoped tokens carry per-namespace read/write grants) for a
  hosted/central instance — that any MCP client (Claude Code, Claude Desktop, Cursor, Windsurf,
  Cline, …) can use as long-term memory.
- Stores memories in a single local **SQLite** database (FTS5 full-text +
  semantic embeddings) at the platform data dir — never inside the repo.
- Organizes memory in **three layers**: a shared `crow` namespace (the user's
  rules, preferences, cross-project lessons), an optional persona namespace
  named by `MEMORY_PERSONA` (an agent's own self), and one project namespace per
  repo. Loaded in that order.
- Models memory as a **graph** — memories link via typed relations, and recall
  uses spreading activation to wake related context.
- **Never forgets**: dormancy is a resting signal, not deletion. Only explicit
  `memory_forget` removes anything.
- Ships a **Memory Explorer** web UI (`ui/`) for browsing the graph and stats,
  backed by a read-only observer API (`ui_api/`) that serves either the local DB
  (`gingugu ui`) or a remote brain (`/ui/api/*` on `gingugu serve`, owner tokens only).
  Views: 3D brain (namespace lobes), virtualized list, detail drawer, Dashboard,
  Timeline, Trust Map; deep links `?view=<tab>&m=<id>`.

**Not** a cloud service. No backend servers, no API keys, no accounts.

---

## Tech Stack

- **Python** `>=3.11`; CI matrix ubuntu/macos/windows × 3.11–3.13
- **MCP** Python SDK 2.x (`mcp>=2.3,<3`, `MCPServer`); **stdio** (default) + **streamable HTTP**
  (`gingugu serve`, via `starlette` + `uvicorn`); **httpx2** (the SDK only takes httpx2
  clients and auth) + **anyio** for the remote-mode proxy and the hooks; **psutil** for
  the hooks to find their client's servers
- **SQLite + FTS5** (WAL mode); semantic embeddings for hybrid retrieval
- **platformdirs** for the cross-platform DB path
- **uv**-managed; `ruff` + `black`; `pytest` + `pytest-asyncio` + `pytest-timeout`
  (suite is offline and bounded — see `.ai/standards/01-code-and-testing.md`)
- **UI:** React + Vite + Tailwind (`ui/`) with `react-force-graph-3d` + `three` (3D brain, bloom), `@tanstack/react-virtual`, `react-markdown` + `remark-gfm`, `recharts`; `vitest` unit tests (`cd ui && npm test`, `npm run typecheck`); Node 22 in CI. Served by `gingugu ui` (`webui.py`, loopback-only, local DB or remote brain per request), built bundle ships in the wheel; `ui/api.py` is a thin dev shim. The public GitHub Pages demo is static JSON from `ui/dump_static.py --db <fictional demo db>`
- Released to **PyPI** via Trusted Publishing (OIDC) on git tag

---

## Module Map (`src/gingugu/`)

| Module | Responsibility |
|---|---|
| `server.py` | MCP server entrypoint; `gingugu` (stdio, or the remote-proxy path when a remote brain is on) / `serve` / `token` / `remote` / `promote` / `init` / `harness` / `ui` / `dream` / `embed` / `hook` (`prompt` | `tool` | `subagent`) dispatch; tool registration; `main` publishes the server's grant for the client's hooks before serving, stdio and proxy (`_publish_grant`); `build` makes one `BoardBus`, serves it as `MCPServer(subscriptions=bus)` (it was `subscriptions=False`) and sets `ctx.bus`; must never crash |
| `serve.py` | `gingugu serve`: streamable-HTTP transport + `/healthz`. `BearerAuthMiddleware` resolves the `Authorization` header to a grant - the owner token (`MEMORY_SERVE_TOKEN` or the persisted `serve_token`) is `FULL`, a scoped token resolves through `TokenStore` - and attaches it as `request.state.grant`; no match is a 401. `_identify` also returns the token kind (`request.state.token_kind`: owner, derived, scoped); derived tokens resolve through `DerivedTokens`. `build_app` assembles the app: MCP at `/mcp`, `POST /token/derive`, the hook routes and `GET /ui/api/*` (`serve_ui.py`) when given the server's `ctx` (`server.build` returns `(mcp, ctx)`; `build_server` wraps it), the middleware. `serve()` hands `serve_tls.resolve`'s cert + key to uvicorn and logs what a client needs (`_log_tls`) |
| `serve_tls.py` | TLS for `gingugu serve`: `MEMORY_SERVE_TLS` auto (default) / on / off, anything else raises. auto = plain http on loopback (`is_loopback`), else a self-signed EC P-256 leaf (CA:FALSE, `generate`) at `<db-dir>/tls/serve_{cert,key}.pem`, key via `write_private` (0600), generated once and reused, both regenerated if either is missing. `MEMORY_SERVE_TLS_CERT` + `_KEY` bring your own (both or neither, must exist). `cert_pem` is what the SSH `cert` verb prints: the generated cert only, never a brought one, never generates, ignores the bind host. `fingerprint`: SHA-256 colon hex |
| `serve_derive.py` | `POST /token/derive`: trades an owner token for a derived persona token. Body (all optional): `grant` spec, `home`, `ttl` (default and max 3600), `name`. Only `request.state.token_kind == OWNER` may call it (scoped and derived get 403); names are `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; a scoped grant must be able to read its `home`; 400 on bad input, 429 at the live-token cap. Reply `{token, expires_in}`, `Cache-Control: no-store` |
| `serve_hooks.py` | Remote-mode hook routes, all `POST`, owner token only (403 otherwise): `/hook/recall` `{prompt, session_id, namespaces, suppressed, config?}` -> `{context, ids}` (the brain's embedder, `recall_pick.pick`, a `query_log` row), `/hook/tripwires` `{namespaces}` -> rule dicts, `/hook/trip` -> a `tool='tripwire'` row, `/hook/warmup` `{spec, task_hint, agent_type, namespaces}` -> `{context}`. Each route is a `(parse, run)` pair: validation on the event loop (400, at most 16 names, 1000 ids, per-field length caps), work in `anyio.to_thread`: reads on its own read-only connection, the only writes the `query_log` rows (`log_prompt`, `_log_trip`, short best-effort connections), never `ctx.conn`; 500 on an internal failure. Keeps no session state |
| `derived_tokens.py` | `DerivedTokens`: in-memory map of SHA-256 -> (`Grant`, expiry). `MAX_TTL`/`DEFAULT_TTL` 3600, `MAX_LIVE` 256 (`OverflowError` past it). Lookup compares every entry in constant time. Never on disk, so a server restart drops them all and there is nothing to revoke |
| `proxy.py` | Remote mode: a bare `gingugu` relays stdio <-> streamable HTTP to the brain and never opens the local DB. `preflight` (in `proxy_preflight.py`, re-exported) refuses to start (malformed, blank or `*=write` `MEMORY_GRANT`, `MEMORY_GRANT` with credentials on, no keychain token, unreachable after a 15s retry budget; warns on plain http to a non-loopback host). `_Session` runs one reader of the client plus a connect loop: refreshes the token at half the TTL, backs off 0.25s -> 5s, replays the client's `initialize` itself. Requests the last link had refused unrun (`_Session.replay`) are re-sent once on the next if under `_REPLAY_MAX_AGE` (10s) old, else errored (the client may have re-sent it itself); a new request waits up to `_HOLD_SECONDS` (5s) for a reconnecting link before it is refused. `credential_*` on `tools/call` is handed to the local vault's own session through `VaultLink.submit` (credentials on; it never awaits the vault's answer, since the person's reply arrives on the same client reader) or refused, never relayed; `read_client` first checks `VaultLink.claims` and routes the client's answer to one of the vault's questions back through `answer`. A request before the client's `initialize` (Claude Code's `server/discover` probe) gets `-32601` locally, never relayed. `serve` (body of `serve_stdio`, optional stdin/stdout for tests) runs `run` in the SDK's `stdio_server`, closes the write stream when `run` ends so stdin EOF exits the process, and re-raises a bare `ProxyLost` out of the transport's task group |
| `proxy_session.py` | Building blocks for `proxy.py`: `derive()` (the only place the proxy sends the owner token - the remote-mode hooks also send it, to `/hook/*`; verifies through `remote_tls.verify_for`; 401/403, a corrupt pin, or a certificate failure (named by `remote_tls.explain`) -> `ProxyLost`; 5xx/429/network, or a clock failure (`is_clock`: not yet valid / expired) -> `DeriveTransient`), `Link` (one live connection: tracks in-flight requests, strips the brain's `credential_*` from `tools/list` and appends the local vault's, patches the local vault's summary into `memory_stats` replies, a request answered `-32600` with one of `_SESSION_TERMINATED_TEXT` (the SDK client's 404 stand-in `"Session terminated"`, or a brain's session-404 body - `"Session not found"` and two transport texts, identical in SDK 1.30 and 2.3, relayed verbatim by the 2.x client) - unknown session, the brain never ran it; any other `-32600` is an ordinary error and is relayed moves to `refused` and the link drains the rest of its in-flight replies for up to `_DRAIN_SECONDS` before raising `SessionTerminated`; `fail_pending` errors requests of unknown fate and never replays them), `replay_handshake`, `fatal_in` |
| `proxy_auth.py` | `SessionTokens` (the live session token: reused across reconnects, `renew(stale)` derives once however many requests saw a 401, and only if the token was ever accepted or `_RENEW_GAP` 10s passed - a brain refusing every fresh token cannot drain the live-token cap) and `SessionAuth` (httpx2 `Auth` for `/mcp`: token per request, renew + retry once on a 401; a 401 is pre-dispatch, so the retry never writes twice), and `brain_client` (the proxy's `/mcp` httpx2 client, verifying through `remote_tls.verify_for`), and `derive_name` (the name the derived token carries: `MEMORY_PERSONA`, else the home namespace; the brain reads it as the board's author) |
| `proxy_preflight.py` | `ProxyRefused` and `preflight` (remote mode's startup gate), split out of `proxy.py` for size; `proxy` re-exports both. `_await_brain` probes `/healthz` for up to `REACH_BUDGET_S` (15s), backing off 0.5s -> 2s (`MAX_BACKOFF_S`), and starts no probe after the deadline - an MCP client never retries a server that exits at startup. A brain that stays unreachable is refused with `remote_tls.diagnose`'s reason when it has one: for https a 2s probe names a TLS failure, for http a TLS handshake on the same port (`speaks_tls`) says to log in again over https. `_monotonic`/`_sleep` are test seams |
| `proxy_vault.py` | `LocalVault`: the machine's credential vault served by the proxy in remote mode - `handlers/credentials.py` on a private `MCPServer` over the local DB (`transport="stdio"`, so `into` writes locally; registered through `_OnLoop` so each tool runs on the event loop, since SDK 2.x runs a plain `def` tool on a worker thread and the sqlite3 connection is thread-bound); `screen(req_id, params)` refuses `credential_get` `reveal` and non-object `arguments` before the vault sees the call; the `server` property exposes the low-level server that `VaultLink` runs over in-memory streams; `health()` + `patch_stats` give `memory_stats` this machine's credential summary; `open_vault` turns an open failure into `ProxyLost` |
| `proxy_vault_link.py` | `VaultLink`: runs the `LocalVault`'s server behind an in-memory MCP session (`create_client_server_memory_streams`), handshaken with the client's own `initialize` so the vault sees the client's capabilities and can elicit. `submit` hands a `tools/call` over without awaiting it; the vault's pump forwards replies to the client as they are and renames the vault's own requests `vault-<nonce>:<id>` (a per-process nonce) (brain and vault both number their first request 1); `claims(msg)`/`answer(item)` recognise the client's answer to one of those and restore the vault's id. A dead or not-yet-initialized vault answers with a structured error |
| `confirm.py` | Destructive-tool confirmation. `ask(ctx, message)` is the resolver half: `Elicit(Confirm)`, or a plain marker (`NOT_NEEDED`, `OPTED_OUT`, `CANNOT_ASK`, `FENCED`); `refusal(answer, what)` is the body half: None only on accept with `confirm` true (or opted out), else the reason. Decline, cancel (a headless client's answer), `confirm=false` and a client without form elicitation (`can_ask`) all refuse; an unrecognised answer refuses too (fails closed). `MEMORY_CONFIRM_DESTRUCTIVE` (`off`/`false`/`0`/`no`, case-insensitive; anything else keeps it on) is read straight from the environment by `opted_out`, not `config.py`. Resolvers run before the tool wrapper, so they call `fenced` themselves and are `async` (a plain `def` resolver runs on a worker thread where the sqlite connection is unusable) |
| `serve_tokens.py` | Serve token store. Scoped tokens (per-namespace grant) and named owner tokens (`add_owner`, one full-access token per machine, `replace=True` rotates). `TokenStore` keeps only SHA-256 hashes in `<db-dir>/serve_tokens.json` (0600, atomic replace); the plaintext is printed once. Re-reads the file when it changes on disk, so a revoke lands on the next request with no restart; an unreadable file resolves to no grant (fails closed); `owner` counts only when literally `true` |
| `token_cli.py` | The `gingugu token add NAME --ns a=write,b=read \| --owner` / `list` / `revoke NAME` / `ssh-mint` CLI. `ssh-mint` is an `authorized_keys` forced command: it accepts only `mint NAME` in `SSH_ORIGINAL_COMMAND` and prints only the rotated machine token, or exactly `cert`, which prints `serve_tls.cert_pem` (nothing when there is none) for any key, bound or not, and rotates nothing |
| `remote.py` | (parsing and the mint ssh argv, `ssh_argv`, in `remote_args.py`) `gingugu remote login/on/off/status`: which brain a machine talks to. Local by default (no setting); machine setting `<user-data-dir>/remote.json` (0600, never in a repo); `MEMORY_REMOTE_URL` overrides per client (`off` = local). `normalize_url` refuses a URL with userinfo (never echoed). `server.main()` consults `remote.active()` and hands a remote target to `proxy.py`. `login` mints the machine token over SSH (`-F /dev/null`, restricted per-machine key `~/.ssh/gingugu_mint`) into the OS keychain (service `gingugu-remote`), after `remote_tls.prepare_login` has settled TLS (cert over SSH, live check, pin written - a failure rotates nothing): the URL becomes https, the token is filed under it, and a machine setting on the same brain over http is upgraded. `active()` and `on` refuse http to a pinned host:port (`remote_tls.refuse_downgrade`). `_reachable` verifies through the pin; `status` adds a `hint:` line from `remote_tls.diagnose` when unreachable. A bad URL or setting raises - never a silent local fallback |
| `remote_tls.py` | The client half of TLS: pins at `<user-data-dir>/remote-tls/<host>_<port>.pem` (0600, atomic; beside `remote.settings_path()`, so the conftest sandbox covers it), `load_pin` (a corrupt pin raises, never reads as none), `verify_for(url)` (https + pin: `create_default_context(cadata=pin)`, the pin alone; else `True`), `refuse_downgrade` (loads the pin whatever the scheme), `https_of`, `fetch_cert` (exit 2 = an older brain without the verb = no TLS), `prepare_login` (cert -> live `probe` with the exact trust -> pin written or a stale one cleared, all before the mint), `tls_failure` / `explain` / `diagnose` (a cert failure named with its fix; for http, `speaks_tls` - an unverified handshake that sends no request and no token - turns a TLS-only port into "log in over https"), `is_clock` (verify codes 9/10: retried, "check the clock"). `parse_cert` lives in `serve_tls` (exactly one PEM CERTIFICATE, nothing around it, re-encoded) |
| `grants.py` | `Grant` (namespace name -> `read` or `write`, `*` matches any), `FULL`, the `bind`/`current` contextvar, `AccessDenied`, and the store-side helpers (`scope_clause`, `readable_memories`, `require_memory_write`). `current()` is None on stdio without `MEMORY_GRANT`, CLI, background passes, and the owner token - those run unfenced; a stdio server started with `MEMORY_GRANT` binds its grant like a scoped token. Out-of-grant reads as not found, never forbidden. `Grant.home` (derived tokens only) is the namespace a call that names none defaults to, via `grants.home()` (a separate contextvar, so a full grant still carries it); `NamespaceManager.resolve_name` and `handlers/scope.read_scope` prefer it over the server's configured namespace. `Grant.derived` marks a token minted at `/token/derive` |
| `handlers/fence.py` | Tool-level half of the fence: `request_grant` reads the grant off the HTTP request the call arrived on, via `inbound.current()` (stdio = the process grant from `stdio_grant(MEMORY_GRANT)`, `FULL` when unset; HTTP with no grant = refused), `refusal` closes whole-brain tools to any scoped grant, HTTP or stdio (`memory_export`, `memory_import`, `memory_dream`, every `credential_*`, every `memory_namespaces` action except `list`). A derived grant is refused every `credential_*` even when full |
| `webui.py` | `gingugu ui`: serves the built Memory Explorer bundle + the `/api/*` read API on one port (prod, no Node), or spawns the Vite dev server (`--dev`); assets ship in the wheel at `gingugu/_ui_dist`. `--host` must be loopback (exit 2 otherwise), every request needs a loopback `Host` (403), no CORS. Each request asks `remote.active()`: local DB via `ui_api.dispatch`, or `webui_remote.forward`. Unknown `/api` routes 404 before any forward; every body is stamped `mode`, `/api/info` also `brain` and `persona` (the viewer's `MEMORY_PERSONA`). The legacy `/api/export` route is removed (404) |
| `webui_remote.py` | `gingugu ui` against a remote brain: forwards `/api/*` to `/ui/api/*` with the keychain owner token in the `Authorization` header only, pinned TLS via `remote_tls.verify_for`, never follows redirects, errors carry exception class names only. Also `is_loopback`, `host_header_is_loopback`, and the lazy `local_embedder` |
| `serve_ui.py` | `GET /ui/api/{rest:path}` on `gingugu serve`: owner tokens only (scoped or derived 403, none 401), runs `ui_api.dispatch` in a worker thread. Wired by `serve.build_app` when given `ctx` |
| `ui_api/` | The Explorer's read API: one `dispatch(path, params, env)` returning `(status, body)` and never raising, plus the `known_route` allowlist. Read-only connection (`db.py` `open_readonly`, `mode=ro`, never migrates or creates); no access credit, no `query_log` row, no spreading activation. `env.py` (`UiEnv`, `ApiError`), `graph.py` (`/info` and `/graph`, both carrying `graph_version` so the client refetches `/graph` only on change: light nodes without bodies, relations, IDF tag edges), `tag_edges.py` (sum of shared-tag IDF `ln(N/df)`, top-k per node union, `max_df` 200, date tags opt-in via `dates=1`), `detail.py` (`/memory/<id>`: body, tags, relations in/out, claims, tripwires, access history), `lens.py` (`/search` via `search.search()` with the score breakdown; `/stats` with `prune=False`), `events.py` (`/events`: `access_log` + `query_log` merged, cursor `after`, replay `from`/`to`; a page never splits a same-timestamp batch) |
| `promote.py` | `gingugu promote`: MCP client that promotes local "gold" to a central brain (filter + provenance + idempotent store) — not part of the server. Its MCP client verifies through `remote_tls.verify_for`, so a pinned brain is reached through its pin |
| `bootstrap/` | `gingugu init`: writes packaged hook/skill/rules templates into a target repo (Claude Code hooks + the `/sink-the-ship` skill at `.claude/skills/sink-the-ship/SKILL.md` + non-destructive settings merge, or a `--client` rules file). Retires a legacy `.claude/commands/sink-the-ship.md` only when it is byte-identical to a shipped template - an edited one is kept and reported - not part of the server |
| `bootstrap/harness.py` | `gingugu harness`: runs `init`, then installs the harness from `bootstrap/templates/harness/` (guard + `log_event` + `pre_compact` hooks, three fenced minions with `{{namespace}}` rendered from the folder name, `/creating-pr`, create-only `.ai/` scaffold, a managed `GINGUGU HARNESS` block in the repo `CLAUDE.md` - left out when the file has no block but already has its own `## AI Knowledge Base Enforcement` section - deny rules). A missing `CLAUDE.md` is created by `init` (`create_rules=True`). Dispatches `--migrate` / `--prune`. Refuses the home directory and invalid namespace names. Warns when Claude Code has not trusted the repo (`claude_trust`) |
| `bootstrap/harness_migrate.py` | `gingugu harness --migrate`: unwires the legacy per-event hook kit (name + fingerprint, exact basename - not `settings._matching_commands`' substring), resets gingugu's own hook commands to canonical, retires kit scripts and known `utils/` files to `.claude/hooks/retired/` behind exit-0 stubs, hashed into a manifest. Hooks this run replaces with ours (`harness._HOOKS`) do not hold `utils/` back, so `--dry-run` reports the move the real run makes |
| `bootstrap/harness_prune.py` | `gingugu harness --prune`: deletes unedited, unwired stubs (checks project, local and user settings) and manifest files whose hash still matches; empty dirs only; leaves a manifest-less `retired/` alone. A stub the run removes (or, in `--dry-run`, would remove) no longer protects its original, so the dry run lists what the real run deletes |
| `bootstrap/harness_retired.py` | What `--migrate` puts in `.claude/hooks/retired/` and how `--prune` trusts it: `LEGACY_SCRIPTS` / `KIT_UTILS` names, the sha256 `MANIFEST`, `read_manifest` (drops entries that are not kit files), `no_symlink_between`. Shared by migrate and prune |
| `bootstrap/harness_settings.py` | Harness settings wiring, merged additively: `log_event` on every logged event, `HARNESS_DENY` rules, plus the command parsing (`script_tokens`, `project_hook`, `hook_entries`) migrate and prune use to tell a project's own hook script from anything else |
| `bootstrap/gitignore.py` | `GITIGNORE_ENTRIES` (re-exported by `bootstrap`) and `ensure_gitignore`: missing rules join the block an earlier `init` wrote, under its header and after our rules there, so a rerun opens no second block and a user's `!negation` under it still wins |
| `bootstrap/global_rules.py` | Manages the memory protocol inside a marked block in the user-level `~/.claude/CLAUDE.md` AND a repo's own `CLAUDE.md`/`AGENTS.md` (existing files only, except `create=True` - `gingugu harness` - which creates a missing `CLAUDE.md`). Append-only outside the markers; refreshes only its own block; refuses on an unmanaged protocol unless `--adopt` wraps it in first (heading-title match, not body) |
| `bootstrap/_files.py` | Shared `read_template` / `safe_read` helpers plus the write primitives (`write_file`, `retire_file`, `TEMPLATE_SIGNATURE`), split out so `global_rules` doesn't import the package `__init__` that imports it. The primitives sit together because they carry the one guarantee every install path shares: never destroy the user's bytes without a copy. `write_file` writes nothing over identical bytes (`no change`, even under `--force`) and a dry run over an existing file says `would overwrite` |
| `bootstrap/settings.py` | Non-destructive `.claude/settings.json` merge. `declared_flags()` reads a hook's real `add_argument` flags off disk, so the "wired for a different script" warning reflects the installed script rather than our template's flag set |
| `config.py` | Config + cross-platform DB path (platformdirs); transport + credentials-flag settings; `serve_tls` / `serve_tls_cert` / `serve_tls_key` / `serve_tls_san` (`MEMORY_SERVE_TLS*`, read by `serve_tls`; a set-but-blank mode is kept so it is refused); `grant` (raw `MEMORY_GRANT`, set-but-blank kept so the fence can refuse it) |
| `database.py` | The SQLite connection alone: WAL, foreign keys, busy timeout. Opening one runs `migrate()`; it owns no schema |
| `migrations/` | Schema migrations keyed off `PRAGMA user_version`. `__init__` holds the ordered `MIGRATIONS` registry, `LATEST_SCHEMA_VERSION`, the WAL-aware pre-migration backup, and `migrate()` |
| `migrations/schema.py` | Structural migrations (001-004, 008): tables, columns, indexes, FTS5 triggers. Each keeps its DDL beside the function that applies it, so a table's rationale cannot drift from the table |
| `migrations/claim_derivation.py` | Row migrations (005-007, 009, 010) + `_backfill_claims`. Claims are stored rows, so improving the extractor changes nothing already on disk - every fix needs a migration that re-reads prose which never changed. Five exist for that one reason |
| `migrations/runtime.py` | Coordination migrations (012): `activity`, `dream_lock`. Split from `schema.py` on the line between *state about memories* and *state about the processes touching them* - these carry no knowledge, survive no export, and a store that lost them would lose nothing a person put there |
| `migrations/fields.py` | Migration 014: `provenance` and `about`, two write-time declared columns on `memories`. Both start NULL, no backfill - a guessed provenance would be the model-free-but-invented judgment the field exists to rule out. Rebuilds `memories_fts` (and its three sync triggers) in one transaction to index `about` alongside `title`/`content`, then runs FTS5's `rebuild` command. Re-runnable: checks `pragma_table_info` before each `ADD COLUMN` |
| `migrations/queries.py` | Migration 015: `query_log`, the questions asked of the store. Its own module because it is neither structure, re-derivation, coordination state, nor a declared field - and because `access_log` cannot hold it: that table has one row per *returned memory*, so it never sees the question or a miss |
| `migrations/board.py` | Migration 017: `board_posts` (id, namespace_id cascade, parent_id cascade, author, author_session, recipient = persona or `*`, kind message\|task, status open\|claimed\|done, claimed_by, body, timestamps; indexes `idx_board_posts_inbox(namespace_id, recipient, status)`, `idx_board_posts_parent`) and `board_reads` (post_id cascade, reader, read_at; PK `(post_id, reader)`). Own tables so posts stay out of the graph and the ranking; `kind`/`status` validated in Python, not `CHECK`. Never pruned. Does not touch `memories` or `memories_fts` |
| `migrations/tripwires.py` | Migration 016: `tripwires`, explicit per-memory triggers (tool-name regex + input regex) for the `PreToolUse` hook. Its own table because one memory can guard several calls and a trigger has its own lifecycle; `ON DELETE CASCADE` so a hard-deleted memory takes its tripwires with it. Does not touch `memories` or `memories_fts` |
| `board.py` | The agent board store. `post` (validates kind, body <= `MAX_BODY` 16,000, recipient = persona regex or `*`; a reply joins its parent's thread, namespace and defaults its recipient to the parent's author; threads are one level deep; needs write on the namespace), `inbox` (unread via anti-join on `board_reads`; addressed to the reader or `*`; not the reader's own posts; not done; open, or claimed by the reader; oldest first; scoped by the grant), `unread_count`, `thread` (root + replies, marks all read, `INSERT OR IGNORE`), `claim` (compare-and-set `UPDATE ... WHERE status='open'`, tasks only), `done` (author or claimer only). A post outside the grant raises `BoardError` "not found", the same as a missing one. `inbox_uri`/`touched_uris` name the resources a write makes stale |
| `board_events.py` | `BoardBus`: the in-process `SubscriptionBus` for `subscriptions/listen`, plus a synchronous `emit` (tool bodies run sync and cannot await the SDK's async `publish`); one raising listener never starves the rest. One instance per server: `server.build` passes it as `MCPServer(subscriptions=bus)` and `ctx.bus` |
| `query_log.py` | `record(conn, *, tool, query, result_ids, namespaces, session_id=None)`: one row per query. Never commits, swallows `sqlite3.Error` (logs a warning) so retrieval never fails over bookkeeping. Blank queries are skipped; text is stripped and capped at `MAX_QUERY_CHARS` (2000). Never pruned |
| `models.py` | Memory / namespace / relation data models. Also owns `Provenance` (the `provenance` vocabulary: user-asserted/measured/file-derived/self-concluded, enforced at the application layer, never a SQL `CHECK`) and `MEMORY_COLUMNS` - the one declared `memories` column list - plus `memory_columns_sql()` / `memory_placeholders_sql()`. Every module that reads or inserts a memory row derives its SQL from these; private copies drifted and silently dropped `pinned`. Field normalizers live here too: `normalize_tag`, `normalize_metadata` |
| `storage.py` | CRUD for the `memories` row itself, and the transaction boundary around it. Owns nothing else |
| `storage_derived.py` | `DerivedTables`, the delegation surface for the four satellite tables a memory drags along (tags, access log, embeddings, claims), plus `log_query` for the query log (which hangs off a question, not a memory row; it stamps the MCP session id and commits). Mixed into `MemoryStore` ahead of `TransactionParticipant`, so it declares what it borrows under `TYPE_CHECKING` only - a real stub would sit earlier in the MRO and shadow the `_commit` it means to call |
| `tags.py` | The `tags` / `memory_tags` tables. Split from `storage.py` because `memory_import` writes tag rows too and had grown a byte-identical private copy of `get_or_create` |
| `access.py` | The `access_log` table and the two ways to touch a memory: `record` (a real access, bumps `access_count`) vs `touch` (reactivation only, refreshes the dormancy clock). Conflating them lets a well-connected memory inflate its own ranking |
| `search.py` | True hybrid engine: BM25 pool, RRF fusion, composite re-rank. Ties break on id, never on iteration order |
| `semantic_pool.py` | The cosine ranking cohort, split out when `search.py` crossed 300 lines. `SEMANTIC_COHORT` / `ENTRANT_CAP` are FIXED constants: sizing them by `limit` made a memory's relevance depend on how many rows the caller asked for |
| `search_common.py` | Shared SQL columns + WHERE-fragment builders |
| `search_filters.py` | `advanced_search`: picks the retrieval strategy `sort_by` asks for - the hybrid engine, or one of the ordered listings |
| `search_listing.py` | The ordered-retrieval strategies: by column, by composite score, by FTS match set, by exact id. Each selects rows in the order it returns them; none re-sorts a pool truncated on another axis |
| `embeddings.py` | Semantic vector generation: providers and the encoders themselves. No longer owns the text recipe - see `embedding_text.py` |
| `embedding_text.py` | `embedding_input(title, content, about=None)` - the one text recipe the write path and any compare path must share. `about` sits between title and content, inside the encoder's window; without it the text is byte-identical to before the field existed, so no stored vector or piece span went stale |
| `embedding_sync.py` | Keeps `memory_embeddings` in step with `memories`. Extracted from `storage.py` because the invariant belongs to whoever writes a memory row, and `MemoryStore` is not the only one - `memory_import` writes them too. There are NO triggers for embeddings; a vector exists only where code deliberately wrote one. Also calls `chunking.persist_pieces` on every write, so pieces stay in step with the head vector |
| `chunking.py` | Pieces: vectors for a memory's tail, past the encoder's fixed token window (measured: 79.1% of a real brain is longer than bge-small's 512-token window). Splits on the encoder's own tokens into overlapping windows; piece 0 is the head, whose vector already lives in `memory_embeddings`, so its row here carries only the span and marks the memory as pieced. `cohort_vectors()` scores a keyword-pool member on exactly ONE piece - the one holding the most distinct query words, ties to the head - never the best-matching one; cosine never chooses. Entrants outside the pool keep their head vector. Only a backend exposing `token_offsets` + `max_tokens` gets pieced; Ollama and disabled embeddings stay head-only |
| `embed_cli.py` | `gingugu embed`: runs the embedding backfill (vectors and pieces) to completion in one run, instead of one small batch per server start - the tool for giving an existing brain its pieces after upgrading. Safe to interrupt and re-run; every batch commits |
| `similarity.py` | ABSOLUTE payload-vs-memory similarity for the write-time hints: cosine, or token Jaccard without embeddings. Cutoffs are calibrated against a real corpus, never inherited from a ranking score |
| `context.py` | Session priming (`memory_context`): the pinned tier + three quota'd intent buckets, plus spreading activation. Decides how buckets are scored, quota'd and presented |
| `context_buckets.py` | Where those buckets' rows come from: one SQL fetcher per intent (recency, pins, cross-namespace), each ordered by its own native signal. Split from `context.py` when it crossed 300 lines; the seam is "where rows come from" vs "how they are combined" |
| `excerpt.py` | Reading *inside* one memory: `clamp_range`, `find_matches`, `line_of`. Literal substring scanning and character offsets - no ranking, no stemming, no model, same answer every time |
| `relations.py` | Typed graph edges + hub-dampened 1-hop traversal (`dampened_neighbour_ids`: per-neighbour, ranked confidence ▸ `RELATION_WEIGHT` ▸ low degree ▸ recency ▸ id) and enumeration (`list_edges`) |
| `relation_repair.py` | Edge repair mixed into `RelationManager`: `retype_relation`, `reverse_relation`, `delete_relation`, `delete_edges`. Every op is an UPDATE/DELETE on the existing row, so id / `created_at` / metadata survive a correction |
| `consolidation.py` | merge / summarize / deduplicate clusters. The whole `1 + 2N` write runs inside `transactions.atomic()`, so a consolidation applies whole or not at all |
| `duplicate_scan.py` | The read-only *suggest* half: pairwise-cosine near-duplicate clusters, with an exact-title fallback when a namespace has no embeddings. Nothing here writes |
| `proposals.py` | The dream pass's queue. Owns the `proposals` table and touches nothing else - that boundary is what lets the pass run unattended. A decided proposal is kept, never cleaned up: the rejection is what stops tomorrow's identical computation raising it again, and what the governance-bands board item will count as precedent |
| `dream/` | Deterministic consolidation. `graph.py` loads the undirected relation graph once per run (an edge counts only when BOTH endpoints are in scope, so a namespace's ranks cannot be inflated from outside it), plus each node's tags and their corpus frequency - tags are the one signal about a memory independent of *when* it was written, and edges are not, since relations get laid down between memories saved in one sitting; `__init__.py` runs the passes and stages findings, skipping a pass that raises rather than losing the others |
| `dream/centrality.py` | PageRank over the graph - a computed answer to "what is core", where degree would only count neighbours. Skips what is already pinned: the finding is the memory the graph ranks alongside the tier that nobody nominated |
| `dream/clusters.py` | Label propagation, deliberately not Louvain - Louvain's resolution parameter is a preference about coarseness dressed as a setting. Sweep order and tie-break are fixed so a run is reproducible, which published propagation deliberately is not. Ranked by `tag_score` (coverage x IDF over the members' existing tags, date-shaped ones excluded), because density does not discriminate - every community clearing the floor scores exactly 1.0. A group whose strongest tag is already on every member is not staged at all: accepting it could apply nothing. Measured at AUC 0.750 against 15 hand-decided clusters, against ~0.500 for the arbitrary order it replaces |
| `dream/orphans.py` | Reconnection candidates for memories no edge touches. Two stages matching `handlers/hints.py`: retrieval narrows, then `similarity.payload_similarity` rescores absolutely against the same calibrated `RELATION_MIN_SIMILARITY` floor. Proposes the pair; never the relation type |
| `dream_cli.py` | `gingugu dream`: the pass from a shell or a scheduler. `--if-idle[=MINUTES]` is the form to schedule - it exits 0 without running when the brain is in use, so a skip is a success and a cron job stays silent |
| `dream_schedule.py` | `guarded_run()` - the whole scheduling policy, shared by the CLI and `memory_dream`. Idle gate (scheduled runs only; a hand-run is deliberate by definition) plus the lock (always). The same threshold serves as the gate and the between-pass cancellation check, so the two cannot drift apart. The embedder arrives as a factory, called only after both guards pass: on a 15-minute timer the common outcome is a skip, and a skip must not load a model to discard it |
| `activity.py` | The heartbeat: when the brain was last *used*. One row, stamped by the server on every tool call, because a running MCP server is not an active user - an editor holds it open all day untouched. Unknown never reads as idle: no evidence of absence is not evidence of absence, and that asymmetry is the point |
| `dream_lock.py` | Single-instance lock for the pass. A DB row rather than a lockfile - identical on every OS, and recoverable via an expiry rather than by reasoning about orphaned file handles. Acquired under `BEGIN IMMEDIATE`; the token identifies the *lease*, not the process, so two leases from the same long-lived server cannot steal from each other |
| `inbound.py` | The request scope SDK 2.x no longer provides (1.x had `request_ctx`): `middleware`, passed as `MCPServer(middleware=[...])`, sets a ContextVar `Inbound(request, session_id)` for each message and resets it after, even on a raise; `current()` reads it (None outside a message). Session id: one per process on stdio, a SHA-256 digest of the `Mcp-Session-Id` header on legacy streamable HTTP (2.x builds a fresh `ServerSession` per message, so the object no longer names a conversation), None on a sessionless 2026-07-28 HTTP client. The SDK marks middleware provisional; `tests/test_inbound.py` pins the contract |
| `session.py` | `current_session_id()` - the access log's session id, read from `inbound.current()` so no handler signature or tool schema changes. `None` outside a request, and that `None` is stored verbatim |
| `transactions.py` | `atomic()` - one `BEGIN IMMEDIATE` across components sharing a connection, with each participant's own `commit()` gated off for the duration. Embedding writes are queued to `_after_commit` instead of joining the transaction: they are best-effort by design, and a vector written for a row the block later rolls back is an orphan |
| `decay.py` | Composite scoring, the `reference_timestamp()` freshness anchor (MAX, not COALESCE), dormancy as a resting signal (never auto-forgets), and `relative_age()`/`age_label()`, the derived-at-read `age` string. `composite_score`/`score_memory` are summed from `composite_parts`/`score_parts`, so the `explain` breakdown and the score it explains are the same arithmetic |
| `stats.py` | Health stats (counts, confidence, dormancy, hygiene, review sweep). `compute_stats` / `compute_global_stats` take `prune=True`; the Explorer passes `prune=False` so a read never prunes `access_log` |
| `graph_stats.py` | Relation-graph health: edges, degree, type mix, orphans, and edges stranded past `SPREAD_PER_SEED`. Also `orphan_sample` (the orphans behind the count, costliest first) and the shared `orphan_filter()` predicate behind `memory_search(orphans=True)` |
| `size_stats.py` | Character cost the counts do not show: `total_chars`, `mean_chars`, `pinned_chars`, `largest_pinned_chars`. `pinned_chars` is the only recurring context cost in the store, since pins load at every session start exempt from ranking; `largest_pinned_chars` is the skew check, because a tier dominated by one entry is not fixed by adding better ones |
| `hygiene_stats.py` | Store hygiene for `memory_stats`: ghost namespaces (global calls only) and duplicate-title clusters. Split out of `stats.py`, whose `compute_global_stats` (inventory, `access_log_rows`, `query_log_rows`, `credentials`) is computed once per call so a multi-namespace `memory_stats` does not repeat it (`prune=False` skips the `access_log` prune, for the Explorer) |
| `recall_gate.py` | Involuntary recall, decision half: pure arithmetic over scored candidates. Length floor, affect stripping, absolute bar, **margin vs the sweep median** (the gate that stops the cap from doing the threshold's job), lexical requirement, cap, per-session suppression. No I/O, so it is unit-testable without a brain |
| `recall_sweep.py` | Involuntary recall, I/O half: read-only (`mode=ro`) cosine sweep + BM25 `lexical_matches`. Also `sqlite_uri(db_path, mode)`, the one place a `file:` URI is built (resolved path, percent-encoded, so a `#` or `?` in a path cannot end the filename early and drop `mode=`). Excludes deprecated, **pinned** (already loaded unconditionally at session start) and **superseded** (replaced knowledge arriving as current) in SQL |
| `prompt_hook.py` | `gingugu hook prompt`: the `UserPromptSubmit` entry point. `namespaces_for(cwd)` = `crow`, then `persona()` (`MEMORY_PERSONA`, validated `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, malformed ignored), then the cwd directory name, deduped, then narrowed to what the client's `MEMORY_GRANT` can read (`hook_grant.client_grant`; a grant that cannot be honoured gives `[]` and the hook exits quiet). Orchestrates gate + sweep, writes `hookSpecificOutput.additionalContext`, keeps suppression state beside the DB. The sweep stays read-only; its one write is `log_prompt`, which records the prompt and the ids injected to `query_log` (`tool='hook'`) over its own short best-effort connection, even when nothing surfaced. In remote mode (`hook_remote.target()`) it never opens the local DB or encoder: `hook_remote_flows.remote_recall` sends the prompt, namespaces, local suppression and gate config, and only the suppression file is written here. Exits 0 on every path |
| `recall_pick.py` | `pick()`: encode, sweep, lexical confirm, `select`, `log_prompt` - the involuntary-recall pipeline shared by the local hook and `/hook/recall`, so one prompt picks the same memories wherever the brain lives |
| `hook_remote.py` | The hooks' transport: `target()` (`remote.active()`), `post(target, path, body, timeout)` with the keychain owner token as Bearer, verifying through `remote_tls.verify_for`; `None` on any failure (no token, network, non-200, non-object). Timeouts under the hook limits: recall 10s, tripwires 5s, trip 2s, warm-up 15s |
| `hook_remote_flows.py` | One function per round trip: `remote_recall`, `remote_warmup`, `remote_rules` (60s cache per URL + namespace set at `hook-sessions/tripwire-rules-<sha>.json`, 0600, stale copy while the brain is away), `report_trip`. Every namespace list sent passes `_sendable` (the routes' name rule, `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`), so a folder like `My Repo` is left out instead of 400ing the request. Validates reply shapes; never opens the local DB |
| `capability.py` | Capability pointers: `check` (a memory is `capability` exactly when `metadata.capability` holds a valid `{run, path?}`; unknown fields refused), `describe` (read shape; `exists` stats the path at read time, relative paths against the namespace repo path, `null` when unresolvable or on a non-local transport), `lane` (capability-only cosine pass for `memory_recall`, floor `LANE_BAR` 0.68 measured on real recall queries, best 2). No migration: the structure lives in metadata |
| `tripwire.py` | Tripwires, decision half: `match_text` (Bash -> command; Edit/Write/Read -> `file_path`; NotebookEdit -> `notebook_path`; else sorted-JSON input), full-match of `tool_pattern` on the tool name, search of `input_pattern` in the text. One hit per memory, at most `MAX_TRIPPED` (3), patterns capped at 500 chars. `load_tripwires` skips deprecated and superseded memories, **not pinned ones**, and reads SQLite read-only; locally the hook calls it, remotely the brain calls it inside `/hook/tripwires` and the client matches the returned rules |
| `minion_fence.py` | The minion fence, called first in `tool_hook.run` and only when the payload carries `agent_id` (inside a subagent). Denies file tools (Read/Edit/Write/NotebookEdit/Grep/Glob) on the brain data dir (holds: the path is a parameter); denies shell commands naming the data dir, the database file, the sqlite CLI, or the `gingugu` CLI in command position (speed bump only); denies the WRITE tools of the inherited `mcp__gingugu__` server and every `credential_*` (reads stay open). Own switch `MEMORY_MINION_FENCE=off`, independent of `MEMORY_TRIPWIRES` |
| `hook_grant.py` | The client's grant as the hooks apply it: what the client's gingugu servers published (`client_grants`), which the hook's own `MEMORY_GRANT`, if set, must agree with. `client_grant()`: None when nothing is published and `MEMORY_GRANT` is unset (unfenced); `ValueError` (hooks quiet, logged) on a grant `stdio_grant` rejects, a disagreement, an unreadable publication or process tree, or a fenced client with no live server. `narrow_spec(spec)`: a minion agent file's grant cut to what the client can read (`*` kept, since the warm-up confines it to the already-narrowed namespace list); None means no warm-up |
| `client_grants.py` | Each server publishes its grant for its client's hooks: `publish(spec)` writes `<user_runtime_dir>/gingugu/clients/<pid>.json` (`grant`, `role`, `created`, `chain` of `(pid, start)` links; 0600, `O_EXCL|O_NOFOLLOW`, owner-checked; sweeps dead files), `release`/`release_on_signal` withdraw it on a clean stop (return, SIGTERM, SIGINT) - a crash leaves a fence. `client_publications()`: the publications meeting the hook at its nearest ancestor, never above its client (`find_client`); a client under `STARTUP_WINDOW_S` (3s) is waited on; `role: minion` skipped only beside a non-minion one. Uses `psutil` |
| `claude_trust.py` | Whether Claude Code trusts a folder (`~/.claude.json` `projects[<dir>].hasTrustDialogAccepted`, a parent counts). `untrusted_note` feeds the warm-up note and the `gingugu harness` warning: an untrusted repo's agent-file `brain` servers do not load. Advisory only |
| `subagent_hook.py` | `gingugu hook subagent`: the `SubagentStart` warm-up. `SubagentStart` carries no task, so `tool_hook` `stash`es the spawning `Agent` call's prompt (`hook-sessions/spawns-<session>.json`, 300s TTL) and this takes it as `task_hint`. Reads the agent file's `MEMORY_GRANT`, ranks `build_context`-style on a read-only connection under `grants.bind`, injects compact lines as `additionalContext`. No grant in the agent file = no warm-up. A project agent file in a folder Claude Code has not trusted gets a note that its `brain` will not load (`claude_trust`). The agent file's spec is first narrowed to what the client's own `MEMORY_GRANT` can read (`hook_grant.narrow_spec`); nothing left, or a client grant that cannot be honoured, = no warm-up. In remote mode the ranking runs on the brain (`/hook/warmup`). Exits 0 on every path |
| `subagent_warmup.py` | `warmup()` (re-exported by `subagent_hook`): the read-only, grant-bound ranking. `scope` (namespace names) limits a wildcard read; locally it is `namespaces_for(cwd)`, remotely the client sends it |
| `tool_hook.py` | `gingugu hook tool`: the `PreToolUse` entry point. A `PreToolUse` `additionalContext` lands next to the tool *result*, so the only way to speak before the call is to deny it: the first match per session per memory is denied with title + summary + "re-issue unchanged to pass", and the re-issue passes (state in `hook-sessions/tripwires-<session_id>.json`). `mcp__gingugu__*` never trips. Inside a subagent the minion fence runs first (and may deny those tools' writes); a main-thread `Agent` spawn for a fenced agent has its prompt stashed for the warm-up. Rules load for `namespaces_for(cwd)`, so a client `MEMORY_GRANT` narrows them; an empty list exits 0 before any network or DB work. `MEMORY_TRIPWIRES=off` disables tripwires only; every failure exits 0. Each trip is logged to `query_log` (`tool='tripwire'`) |
| `staleness.py` | Advisory review hints for point-in-time memories |
| `claims.py` | Extracts checkable state claims (repo-qualified PR/MR refs) from prose; ignores refs inside `[[wiki-links]]` |
| `claim_qualify.py` | Decides **which repo** a ref names: URL, then a repo the prose names next to it, then a binding stated elsewhere in the same memory, then the namespace default. Requires the `#`/`!` sigil so plan ordinals ("PR 1") are not refs |
| `claim_sync.py` | Claim **write** path: persistence, contradiction lookup, resolution, and the storage bridge; resolves a namespace's default repo |
| `claim_queries.py` | Claim **read** path: the `claims.sample` backlog enumeration and the shared `claim_filter()` predicate behind `memory_search(claims=…)` |
| `claim_rederive.py` | Claim re-derivation that **preserves** resolution state, whole-corpus or scoped to one `namespace_id` (`claim_sync.sync_claims` drops resolution by design) |
| `namespaces.py` | Namespace CRUD; a `default_repo` change re-derives that namespace's claims (best-effort) so the declaration is not inert |
| `credentials.py` | OS-keychain credential vault. `get` redacts secret values unless `reveal`; `write_secret` hands one secret to a file |
| `secret_file.py` | Safe owner-only file write for `credential_get(into=…)`: absolute path, no symlink/dir, parent must exist; `O_NOFOLLOW`, mode 0600 |
| `portability.py` | Export / import a namespace. `import_data` takes an `embedder` and embeds what it writes; vectors are recomputed, never carried in the payload (they are model-specific and derived) |
| `handlers/` | MCP tool handlers: `memory.py` (store/update), `forget.py` (`memory_forget`; a hard delete asks the user - see `confirm.py`), `hints.py` (write-time similar/relation hints), `recall.py` (recall/context), `search.py`, `stats.py` (`memory_stats`: single, unscoped, or a comma list returning `global` once plus `by_namespace`), `scope.py` (read scope for recall/search: all namespaces when unconfigured, widen an empty scoped lookup to every namespace), `excerpt.py` (`memory_excerpt`: one memory, never ranked), `relations.py` (relate/edges/unrelate) with `relation_ops.py` (batch parsing + per-edge dispatch for both; the relate batch is all-or-nothing inside one `atomic()`), `consolidate.py` (`keep_originals=False` asks the user), `dream.py` (run/read/decide the proposal queue; an accept that would need a judgment the pass declined to make is REFUSED, not defaulted, and `reverse=True` writes an edge object-to-subject since the orphan pass's subject order is an artifact of retrieval rather than a claim about direction), `admin.py` (a namespace cascade delete and an overwriting import ask the user), `credentials.py` (`credential_delete` asks the user), `tripwires.py` (`memory_tripwire`: add/list/remove/test; `test` is a dry run that never denies and never logs), `board.py` (the `board` tool and the `gingugu://board/inbox/{persona}` resource; `author_for` derives the author, `_emit` pushes resource-updated events), `capability_view.py` (stamps `capability: {run, path, exists}` onto every read and builds recall's `capabilities` lane entries), `fence.py` (see its own row), `summaries.py` (payload shapes: full summary, compact summary, the picker between them), `choices.py` (enum argument parsing shared across store/update: one error format naming the field, the bad value, and the vocabulary), `helpers.py`, `context_merge.py` (`merge_namespace_context`: pure merge of per-namespace context loads - pins first, then ranked tails interleaved by rank) |

Dev-only tooling at the repo root (never shipped in the wheel): **`bench/`** —
golden-set retrieval benchmark (Recall@K, MRR, precision, token cost;
deterministic, no LLM-as-judge). Committed synthetic fixture for CI regression;
real-brain golden sets + baseline reports live in gitignored `bench/local/`.
Run: `uv run python -m bench [--db <real-brain.db>]`.

`bench/probes.py` generates a labelled probe set from a real brain
(`--generate-probes <out>`), for measuring retrieval against **template
families** - groups of memories sharing a title shape, which a long-lived store
accumulates naturally. Labels are verified rather than judged: each question
asks for a phrase occurring in exactly one memory of its namespace, proven
against the corpus at generation time, which is what lets the set be far larger
than a hand-labelled one without losing trust. Each target is probed at up to
three positions - head, middle, tail - and `kind` names which, because the
embedder truncates at 512 tokens and a set asking only about openings cannot see
that. The fixture's `age_days` field
backdates a row so a graded-age cohort can be expressed; backdating is
deliberately a bench-layer concern and never a parameter on `MemoryStore.create`.
Generated sets hold real memory content and stay in `bench/local/`.

---

## MCP Tool Surface

- **Memory:** `memory_store`, `memory_update`, `memory_forget`, `memory_recall`,
  `memory_search`, `memory_context`, `memory_excerpt`, `memory_stats`
  (the three read surfaces take `explain=True` for a per-hit `score_breakdown`;
  `memory_excerpt` searches or slices inside one memory by character offset)
- **Graph:** `memory_relate`, `memory_edges` (enumerate, read-only),
  `memory_unrelate` (retype / reverse / remove, single or batch, `dry_run`)
- **Tripwires:** `memory_tripwire` (`add` / `list` / `remove` / `test`) - explicit
  triggers that stop a matching tool call once, with the memory as the reason.
  The `PreToolUse` hook (`gingugu hook tool`) reads them; `test` is a dry run
- **Board:** `board` (`post` / `inbox` / `read` / `reply` / `claim` / `done`) -
  posts agents leave each other; the author is derived server-side (HTTP token
  name minus `derived:`, stdio `MEMORY_PERSONA`, `minion`, else `local`), never an
  argument. Resource template `gingugu://board/inbox/{persona}`; every write
  emits `notifications/resources/updated` over `subscriptions/listen`
- **Lifecycle:** `memory_consolidate`, `memory_export`, `memory_import`,
  `memory_namespaces`
- **Destructive calls ask the user:** a hard `memory_forget`, a cascading
  `memory_namespaces` delete of a non-empty namespace, `memory_consolidate`
  with `keep_originals=False`, a `memory_import` with `on_conflict="replace"`
  that would overwrite existing ids, and `credential_delete` each show the user
  a confirmation through the client (MCP elicitation) and go ahead only on
  accept with the box ticked. The answer is a hidden parameter, absent from the
  tool's input schema, so the model cannot supply it. Decline, cancel, and a
  client that cannot ask are structured `ok: false` refusals.
  `MEMORY_CONFIRM_DESTRUCTIVE=off` on the server that executes the tool skips
  the dialog for automation (in remote mode: the brain for memory tools, the
  local proxy for the credential vault)
- **Consolidation (dream pass):** `memory_dream` (`run` / `list` / `accept` /
  `reject` / `stats`) - deterministic structure-finding over the relation
  graph, staged to a proposal queue. Also `gingugu dream --if-idle` for a
  scheduler - no daemon: the OS timer handles recurrence, the command decides
  whether now is the moment, gated on an `activity` heartbeat and serialized by
  a `dream_lock` row. Nothing in it writes to `memories` or `relations`;
  accepting is where a person supplies the judgment the arithmetic stopped
  short of
- **Credentials:** `credential_list`, `credential_get`, `credential_store`, `credential_delete`
  - `credential_get` redacts secrets by default; `into=<abs path>` writes one to
  a 0600 file (stdio only, refused under `serve`), `reveal=true` returns it
  inline. `credential_delete` asks the user to confirm (its `confirm` argument
  is optional, default true; `confirm=false` refuses outright). Gated by
  `MEMORY_CREDENTIALS_ENABLED` (default true); a shared/central
  instance runs with it `false` to omit the vault.

- **Warm minions (v0.20.0):** `MEMORY_GRANT="ns=read|write,..."` fences a
  stdio server exactly like a scoped serve token (`handlers/fence.stdio_grant`,
  `ServerContext.stdio_grant`); unset = full grant; a bad spec or `*=write`
  exits 2. Fenced agent files in `.claude/agents/*.md` declare an inline
  `brain` stdio server (`MEMORY_NAMESPACE`, `MEMORY_GRANT`,
  `MEMORY_CREDENTIALS_ENABLED=false`, `MEMORY_SERVER_ROLE=minion`), `tools: ... mcp__brain`, and
  `disallowedTools: Agent, mcp__gingugu` (subagents otherwise inherit the
  parent's full-brain server). `gingugu hook subagent` warms them; the minion
  fence (`MEMORY_MINION_FENCE=off` to disable) covers the rest. Hooks wired by
  `gingugu init` are now five: SessionStart, Stop, UserPromptSubmit,
  PreToolUse, SubagentStart.

Query text is recorded in `query_log`: every `memory_recall`, a `memory_search`
that carries a `query` (an `ids` fetch or a filter/sort listing is not logged),
and a `memory_context` given a `task_hint` (`tool='context'`; context still
does not bump `access_count` or write `access_log`). The involuntary-recall hook
logs its prompts too (`tool='hook'`), and each tripwire trip is logged as `tool='tripwire'`.
`memory_stats` reports `query_log_rows`.

`memory_store` / `memory_update` return non-blocking `similar_memories` (merge
candidates) and `suggested_relations` hints. The latter are candidates to
**examine for a directional relationship**, not links to create: similarity is
how they are found, never the reason to wire them. Both are found by hybrid
retrieval and then gated on an **absolute** similarity (`similarity.py`: cosine
over embeddings, or token Jaccard without them), reported as `similarity` +
`basis`. A retrieval score could not do this job: it ranks within a pool, so its
best hit approaches 1.0 for any payload. An empty list is the common case.
Both are **always compact** — title + a ~200-char `summary`, never full bodies,
regardless of any caller flag. They are unsolicited extras on a write, so they
stay cheap; `memory_recall` fetches the body when a candidate matters.

`memory_update` accepts `type`, so a misfiled memory can be retyped instead of
reworded — retyping to `pattern`/`preference` is the sanctioned way to clear a
gated review-hint false positive, and it does not re-embed (the vector derives
from title + about + content).

**Write-time declared fields.** `memory_store` / `memory_update` also take
`provenance` (how the writer came to believe the memory - `user-asserted`,
`measured`, `file-derived`, `self-concluded`; enforced at the application
layer, rejecting anything else) and `about` (what the memory is for, in the
user's own words for the thing rather than the words of what was done).
Neither advances `last_confirmed`. `about` is indexed on both sides of hybrid
search - an FTS5 column and part of the embedding text - so a query using the
user's word for a workstream reaches memories whose title/content never use
it; changing it re-embeds the memory. Both are omitted from a payload when
unset; `provenance` is surfaced even in compact payloads, so a self-concluded
claim arrives visibly contestable.

**State claims and the reconciliation loop.** `memory_store` / `memory_update`
also return `contradicted_memories` when the write resolves a ref another
memory still calls open — omitted, not empty, when there is nothing to report.
`memory_stats` carries a `claims` block (`open` / `open_actionable` /
`resolved` / `unverified` / `contradicted` plus a `review_limit`-capped `sample`
that **enumerates** the backlog, contradicted first, each row tagged). Reconcile with
`memory_update(resolve_claims="<ref>"|"all")`, which records the resolution and
leaves the body **byte-identical** — a dated log that said "PR #10 open" was
correct when written, so `content` is only for claims that were never true.
The loop: stats → `memory_search(claims="open")` (or `ids=…`) → `resolve_claims`.
`open` counts every unresolved claim; `open_actionable` excludes those on
deprecated memories and is what `sample` lists, so the two numbers explain any
gap between the count and the rows.

---

## Storage Model

- One SQLite file (default `~/.local/share/gingugu/memories.db`, platform-aware).
- `memories` table + FTS5 virtual table kept in sync by triggers.
- Each memory: `type` (fact/decision/pattern/bug/architecture/preference/workflow/context/capability),
  `confidence` (verified/inferred/stale/deprecated), namespace, tags, timestamps,
  access count, content/title.
- Relations table: directed typed edges (`supersedes`, `related_to`, `caused_by`,
  `contradicts`, `parent_of`, `child_of`).
- `memory_claims` table (migration 005): one row per repo-qualified state claim a
  memory makes (`kind`, `ref` like `gingugu#10`, `state`). `state` is what the
  memory ASSERTS and is never rewritten; resolution lives in `resolved_state` /
  `resolved_by` / `resolved_at` beside it, so a claim can go stale without the
  prose being edited. Derived from text, so re-synced on any title/content change.
  Refs inside `[[wiki-links]]` are ignored — a citation is not an assertion.
  `state` is `open`, `resolved`, or `unverified` (migration 009): a ref the prose
  names without ever saying what became of it. `unverified` is excluded from
  `open`, `open_actionable`, `sample`, and contradiction detection — measured on
  1161 memories, 185 such claims exist against 223 real ones and nearly all
  narrate finished work, so counting them as open would bury the backlog in
  history. Read them with `memory_search(claims="unverified")`.
- `namespaces.default_repo` (migration 007): what a bare "PR #12" means in a
  namespace. Unset falls back to the namespace's own name (the
  one-namespace-per-repo convention, load-bearing: 145 claims vs 26 without it);
  a slug overrides it; `""` declares the namespace is not a repo at all, so bare
  refs are dropped rather than mis-keyed. `crow` and `default` are seeded `""`.
- `memory_chunks` table (migration 013): pieces of a memory past the encoder's
  fixed token window - vectors for the tail an encoder silently truncates away.
  Purely additive; `memory_embeddings` (the head vector) is untouched. Chunk 0
  carries the span but no vector (NULL), doubling as the marker that a memory
  has been pieced. PK `(memory_id, chunk)`, `ON DELETE CASCADE`; spans are char
  offsets into `embedding_input(title, content, about)`, so a piece's text is
  re-derived rather than stored twice - every reader passes the same `about`
  the memory was stored with. See `chunking.py`.
- `memories.provenance` / `memories.about` (migration 014): two write-time
  declared TEXT columns, NULL on every row that predates them, no backfill.
  `provenance` is a controlled vocabulary (`models.Provenance`) enforced by
  the application layer rather than a `CHECK` constraint - SQLite cannot alter
  a constraint in place, so a vocabulary change would otherwise mean rebuilding
  `memories` and every trigger on it. `source` is untouched and stays a
  different axis: measured on a real brain it holds 1061 distinct values
  across 1233 rows and mostly records the occasion a memory was written.
  `about` joins the FTS5 index (`memories_fts` gains a third column, all three
  sync triggers rebuilt, then an FTS `rebuild`) and the embedding text. See
  `migrations/fields.py`.
- `query_log` table (migration 015): one row per query - `id`, `session_id`,
  `tool` (`recall` / `search` / `context` / `hook`), `query`, `namespaces`
  (comma-separated scope actually searched, or `*` for every namespace,
  including a scoped read that widened), `result_ids` (JSON array in rank
  order, de-duplicated), `created_at`; indexed on `created_at`. A zero-hit
  query is still a row - those misses are the point. Own table rather than an
  `access_log` column because `access_log` has one row per returned memory.
  `result_ids` is JSON, not a join table: written once, read whole, and its
  order is the point. **Never pruned**: `access_log` keeps a 90-day window
  because its aggregate lives on `access_count`, but nothing summarises
  `query_log`, so the rows are the only copy. See `migrations/queries.py`,
  `query_log.py`.
- `tripwires` table (migration 016): `id`, `memory_id` (`REFERENCES
  memories(id) ON DELETE CASCADE`), `tool_pattern` (regex, full-matched against
  the tool name), `input_pattern` (regex, searched in the call's match text),
  `created_at`; indexed on `memory_id`. Own table because one memory can guard
  several calls. See `migrations/tripwires.py`, `tripwire.py`.
- `board_posts` / `board_reads` tables (migration 017): `board_posts` has `id`,
  `namespace_id` (cascade), `parent_id` (self-FK, cascade), `author`,
  `author_session`, `recipient` (persona or `*`), `kind` (message|task), `status`
  (open|claimed|done), `claimed_by`, `body`, timestamps; `board_reads` is one row
  per `(post_id, reader)`, so unread is the absence of a row. Never pruned. See
  `migrations/board.py`, `board.py`.
- Schema versioned via `PRAGMA user_version` (**currently 17**); migrations
  additive by default. Migration 006 adds no schema — it re-runs the claims
  backfill to repair DBs that reached v5 from pre-fix code and so can never
  run 005 again. Migration 007 adds `default_repo` and re-derives every claim
  under the corrected extractor, **preserving resolution state**. Migration 008
  adds `memories.pinned` (+ a partial index on the pinned rows only): memories
  that always load in `memory_context`, exempt from ranking. Defaults to 0, so
  an existing store changes no behaviour until something is explicitly pinned.
  Migration 009 adds no schema either (`state` was always unconstrained TEXT) —
  it re-derives every claim so state-less refs record as `unverified`, again
  preserving resolution. The mirror of 007: that one only removed claims, this
  one only adds them. Migration 011 adds the `proposals` queue the dream pass
  stages to; its unique index uses `COALESCE(object_id, '')` because SQLite
  treats NULLs as distinct, so a plain index would let the same single-memory
  proposal re-insert on every run forever. Migration 012 adds `activity` and
  `dream_lock` - runtime coordination rather than content, which is why they
  live in `migrations/runtime.py`. Both are pinned to one row by
  `CHECK (id = 1)`: it makes the heartbeat the cheapest write SQLite has, and
  the lock unable to be held twice however badly a caller misuses it.
  `activity` is **seeded** at migration time, because an empty heartbeat table
  and a genuinely idle store are indistinguishable to a reader, and the safe
  reading of "I have never seen activity" is not "start work now". Migration 013
  adds `memory_chunks`: a store with no rows there searches exactly as it did
  before pieces existed. Migration 014 adds `provenance` and `about`, plus the
  FTS5 rebuild that indexes `about`. Re-runnable (checks `pragma_table_info`
  before each `ADD COLUMN`); measured on a copy of a 2716-memory real brain,
  ran in 0.30s with every row, every FTS hit, and every `source` value
  preserved. Migration 015 adds `query_log` (`migrations/queries.py`), purely
  additive: a store with no rows there behaves exactly as before. Migration 016
  adds `tripwires` (`migrations/tripwires.py`), likewise additive: no rows, no
  change in behaviour. Migration 017 adds `board_posts` and `board_reads`
  (`migrations/board.py`), additive too: empty tables, no change in behaviour.

---

## Release State

- Current version: **0.26.0** (PyPI, 2026-10-25). Minor: the Memory Explorer
  rebuilt - read-only `ui_api` served locally and as owner-only `/ui/api/*` on
  serve, `gingugu ui` picks local or remote per request (#129); 3D brain,
  list, detail drawer, deep links, UI CI job (#130). `--host` is loopback-only.
- 0.25.0: Minor: destructive tools
  ask the user through the client (MCP elicitation), incl. the proxy's local
  vault (#125); an http URL on a TLS port is named as such, and
  `fleet.py status` shows each machine's brain and reach (#124).
- 0.24.0: Minor: runs on the MCP
  Python SDK 2.x (`MCPServer`) and `httpx2`, with `serve` taking 64 MiB
  requests (#122); release and fleet-upgrade scripts (#123).
- 0.23.0: Minor: TLS on the brain
  (#116), hooks honour `MEMORY_GRANT` (#115), hooks follow the grant their
  client's servers publish plus the untrusted-minion warning (#119), and
  bookkeeping no longer moves `updated_at` (#118). New dependency `psutil`.
- 0.22.1: Patch: `gingugu harness` dry runs and
  reruns report what the real run does (`--migrate` `utils/` move, a missing
  `CLAUDE.md` created by `init` as a new file, identical bytes are `no change`,
  `would overwrite`), and a dangling `CLAUDE.md` symlink is skipped (#113).
- 0.22.0: minor: `gingugu harness` installs the
  Claude Code kit on top of `init` (guard, JSON-lines logging, transcript
  backup, three fenced minions, `/creating-pr`, `.ai/` scaffold, managed
  `CLAUDE.md` block, deny rules); `--migrate` / `--prune` move a repo off the
  older per-event hook kit; `init` from the home directory does user-level
  steps only; `.gitignore` reruns join the existing block (#109).
- 0.21.4: patch: the remote-mode proxy exits when
  its client closes stdin (it closed nothing, so the SDK's stdout writer kept
  the process alive), and a refused machine token exits 1 with its message
  instead of a traceback (#108).
- 0.21.3: patch: remote-mode preflight retries
  `/healthz` for up to 15s before refusing to start, so a brain slow to answer
  after idle (WiFi power save, cold `.local` lookup) no longer costs the
  session (#107).
- 0.21.2: patch: the remote-mode proxy survives a
  brain restart without losing calls - a 401 renews the token and retries in
  place, a 404'd request is re-sent once on the next session (if under 10s
  old), and new calls wait up to 5s for a reconnecting link (#106).
- 0.21.1: patch: the remote-mode proxy answers a
  request sent before `initialize` (Claude Code 2.1.287's `server/discover`
  probe) itself, so Claude Code connects in remote mode (#105).
- 0.21.0: the central brain: machine tokens minted
  over SSH (`gingugu token add --owner`, `token ssh-mint`, `gingugu remote`),
  remote mode (a bare `gingugu` is a stdio proxy to `gingugu serve`), persona
  tokens (`POST /token/derive`) and `MEMORY_PERSONA` (three-layer memory), the
  hooks following the brain (`POST /hook/*`), and the credential vault staying
  on the machine in remote mode (#99-#103).
- Previous: **0.20.0**. Warm minions: a subagent gets its own
  fenced, pre-loaded brain - `MEMORY_GRANT` fences a stdio server like a scoped
  serve token, agent files declare an inline `brain` server, and the
  `SubagentStart` hook warms it with the memories its task woke (`gingugu init`
  wires five hooks). Fixes `gingugu serve` answering LAN clients with `421`
  (the server now passes its real bind host to the MCP SDK).
- Previous: **0.19.0**. The largest release to date: twenty-nine
  PRs (#65-#93). Adds per-client scoped tokens for `gingugu serve` (`gingugu
  token add|list|revoke`, per-namespace read/write grants enforced at the store),
  tripwires (`memory_tripwire` + the `PreToolUse` hook), capability pointers,
  write-time `provenance` and `about`, query-text logging, involuntary recall,
  tail-aware semantic search over window-sized pieces plus `gingugu embed`, the
  dream pass on a schedule, auto-widening of an empty scoped lookup, batch
  `memory_relate`, and multi-namespace `memory_stats`. **Breaking:**
  `credential_get` redacts secret values by default (`into=` / `reveal=true`).
- Previous: **0.18.0** - twelve PRs,
  seven of them correctness fixes. Adds `memory_excerpt` (read inside one
  memory by literal `query` scan and/or a `start`/`end` character range),
  `explain=True` per-hit score breakdowns on all three read paths, and repo
  `CLAUDE.md`/`AGENTS.md` management plus `--adopt` for `gingugu init`.
  Spreading activation now prefers directional relation types. Fixes:
  `gingugu init --force` backs up what it replaces (a data-loss defect in
  0.17.0 and earlier), `sort_by` orders the corpus instead of a pool truncated
  on another axis, relevance no longer varies with `limit`, `memory_context`
  presents pins first and orders its recency bucket by write recency, write-time
  hints report an absolute `similarity`, and `memory_import` embeds what it
  writes. Public repo `gingugu/gingugu`.
- **0.17.0** - the `unverified` claim state (a ref a memory names
  without ever saying what became of it - excluded from `claims.open` and read
  via `memory_search(claims="unverified")`) and the orphan-enumeration +
  edge-reversal work (`graph.orphan_sample`, `memory_search(orphans=True)`,
  `memory_unrelate(reverse=True)`).
- **0.16.0** - edge repair: `memory_unrelate` (retype in place or
  remove) and `memory_edges` (enumerate a memory's relations).
- **0.15.0** - pinned memories: `memory_update(pinned=True)` marks a memory as
  unconditionally loaded by `memory_context`, exempt from ranking.
- **0.14.0** - `gingugu init` gained user-level rules management: the memory
  protocol lives in a marked block in `~/.claude/CLAUDE.md` that `init`
  manages, strictly additive outside its markers. Shipped alongside it: the
  `age`/freshness-anchor fix (`age` is anchored on `reference_timestamp` and
  elaborates to `"7 weeks ago (updated just now)"`; the anchor became a MAX
  instead of a COALESCE; a title/content rewrite now advances
  `last_confirmed`), the relation-discipline guidance reversal, and two
  `gingugu init` bootstrap fixes. Existing installs need `gingugu init --force`
  to pick up template changes - and as of the 0.18.0 fix, that command backs up
  anything it replaces.
- **0.13.0** - introduced the derived `age` payload field and fixed the
  startup contract's workspace inference.
- Three-layer namespace convention (`crow` shared + persona via `MEMORY_PERSONA` +
  project) shipped in 0.21.0 (#100); before it, two layers
  (`crow` + project).
- Remote mode (0.21.0): a bare `gingugu` is a stdio proxy to `gingugu serve`
  when a remote brain is on; persona sessions use derived tokens from
  `POST /token/derive` (#100). The hooks follow it over `POST /hook/*` (#101),
  and the vault stays local (#102, #103).
- See `.ai/plans/status.md` for in-flight work and carry-overs.
